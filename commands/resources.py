"""
资源采样指令：采集主机事实并把百分比历史原子写入 DashView 状态。

网络速率由连续两次累计流量计算；首次采样保持未知，不编造 0 B/s。
调用示例：computer, history = await sample_resources(state, settings)
"""

from __future__ import annotations                         # 允许现代类型注解

import asyncio                                             # 手动和后台采样共享单飞锁
from copy import deepcopy                                  # 写入状态前保留无副作用的快照
from typing import Any                                     # 描述主机快照和历史结构

from ..collectors.computer import collect_computer         # 获取本机真实资源数据
from ..config import Settings                              # 读取历史保留数量
from ..state import DashboardState                         # 原子修改唯一状态文档


RESOURCE_IDS = ("cpu", "memory", "swap", "disk")          # 具备百分比和趋势意义的资源项


# --- 采集并保存一次主机资源 ---
async def sample_resources(state: DashboardState, settings: Settings, sample_lock: asyncio.Lock) -> tuple[dict[str, Any], dict[str, Any]]:
    async with sample_lock:                                 # 采集到提交完整串行，防止快照时间倒退
        computer = await collect_computer()                 # 先在工作线程取得同批次主机事实

        def save_sample(current: dict[str, Any]) -> None:
            previous = current.get("latest_computer")       # 上次累计流量只用于计算本次速率
            _apply_network_rate(computer, previous)          # 首次采样会明确留下 None
            history = current.setdefault("resource_history", {})
            for resource_id in RESOURCE_IDS:                # 每项资源维护独立真实时间序列
                resource = computer.get(resource_id, {})
                percent = resource.get("percent")
                if percent is None:
                    continue                                # 缺失测量不写成 0%，避免污染统计
                samples = history.setdefault(resource_id, [])
                samples.append({"observed_at": computer["observed_at"], "percent": round(float(percent), 1)})
                history[resource_id] = samples[-settings.resource_history_size:]
            current["latest_computer"] = deepcopy(computer) # 留给下一次计算网络速率

        saved = await state.update(save_sample)             # 一次 KV 写入完成全部资源修改
        return computer, saved["resource_history"]         # 仪表盘使用刚落盘的确定历史


# --- 根据两次累计流量计算真实网络速率 ---
def _apply_network_rate(computer: dict[str, Any], previous: dict[str, Any] | None) -> None:
    network = computer["network"]                           # 当前累计发送和接收流量
    network["sent_per_second"] = None                       # 首次或计数器重置时保持未知
    network["received_per_second"] = None                   # 未知与真实 0 流量语义不同
    if not isinstance(previous, dict):
        return                                              # 没有前一采样就无法计算时间差

    previous_network = previous.get("network", {})          # 读取上次累计流量
    if not isinstance(previous_network, dict):
        return                                              # 损坏旧快照由本次新快照自然修复
    try:
        elapsed_seconds = (computer["observed_at"] - int(previous.get("observed_at", 0))) / 1000
        sent_delta = network["sent"] - int(previous_network.get("sent", network["sent"]))
        received_delta = network["received"] - int(previous_network.get("received", network["received"]))
    except (TypeError, ValueError, OverflowError):
        return                                              # 单个损坏计数不影响当前资源面板
    if elapsed_seconds <= 0 or sent_delta < 0 or received_delta < 0:
        return                                              # 时钟或系统计数器重置时不制造异常速率
    network["sent_per_second"] = round(sent_delta / elapsed_seconds)
    network["received_per_second"] = round(received_delta / elapsed_seconds)
