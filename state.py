"""
DashView 状态存储：定义并持久化插件唯一的一份运行状态。

资源历史、模型历史和最近模型报告写在同一个 KV 文档中，锁保证并发任务不会互相覆盖。
调用示例：state = DashboardState(plugin.get_kv_data, plugin.put_kv_data)
"""

from __future__ import annotations                         # 允许现代类型注解

import asyncio                                             # 用锁串行化同一份 KV 文档的修改
from copy import deepcopy                                  # 返回副本，避免读取方偷偷修改内存状态
from typing import Any, Awaitable, Callable                 # 描述 AstrBot KV 读写函数


STATE_KEY = "dashboard_state_v2"                           # 新架构唯一使用的 KV 键，不迁移旧格式
STATE_VERSION = 2                                          # 状态结构版本，便于识别损坏或错误数据


# --- 创建一份完整空状态 ---
def create_empty_state() -> dict[str, Any]:
    return {
        "version": STATE_VERSION,                          # 只接受当前结构，不猜测旧数据形状
        "resource_history": {},                            # resource_id -> 真实采样列表
        "latest_computer": None,                           # 上一次主机快照用于计算网络速率
        "model_history": {},                               # route_id -> 真实探测列表
        "latest_model_report": None,                       # 状态命令只读取最近报告，不主动花费模型额度
    }


# --- 集中管理插件 KV 状态 ---
class DashboardState:
    def __init__(
        self,
        read_value: Callable[[str, Any], Awaitable[Any]],   # AstrBot 提供的 KV 读取函数
        write_value: Callable[[str, Any], Awaitable[None]], # AstrBot 提供的 KV 写入函数
    ) -> None:
        self._read_value = read_value                       # 保存 KV 读取边界
        self._write_value = write_value                     # 保存 KV 写入边界
        self._lock = asyncio.Lock()                          # 所有读改写动作共享同一把锁

    # --- 读取当前状态快照 ---
    async def read(self) -> dict[str, Any]:
        async with self._lock:                              # 避免读到另一个指令写到一半的数据
            current = await self._read_current()
            return deepcopy(current)                        # 调用方只能修改自己的副本

    # --- 原子修改并保存状态 ---
    async def update(self, change: Callable[[dict[str, Any]], None]) -> dict[str, Any]:
        async with self._lock:                              # 一次更新完整覆盖“读、改、写”三个动作
            current = deepcopy(await self._read_current())  # 修改副本，回调失败不污染 KV 返回对象
            change(current)                                 # 业务指令明确描述要修改的字段
            committed = deepcopy(_sanitize_state(current))  # 外部引用不能在写入后继续修改状态
            await self._write_value(STATE_KEY, committed)   # 只在修改成功后写回完整文档
            return deepcopy(committed)                      # 返回值与写入对象再次隔离

    # --- 从 KV 读取并校验状态结构 ---
    async def _read_current(self) -> dict[str, Any]:
        current = await self._read_value(STATE_KEY, None)   # None 能区分“首次使用”和空字典
        if current is None:                                 # 首次使用时创建干净状态
            return create_empty_state()
        if not isinstance(current, dict) or current.get("version") != STATE_VERSION:
            raise ValueError("DashView 状态数据已损坏或版本不匹配，请清理 dashboard_state_v2")
        return _sanitize_state(current)                     # 单个损坏字段不应卡死全部长期监控


# --- 把状态文档恢复成当前版本的完整结构 ---
def _sanitize_state(current: dict[str, Any]) -> dict[str, Any]:
    clean = create_empty_state()                            # 从确定结构开始，不保留未知顶层字段
    clean["resource_history"] = _sanitize_history(current.get("resource_history"))
    clean["model_history"] = _sanitize_history(current.get("model_history"))
    latest_computer = current.get("latest_computer")       # 快照损坏时等待下一次资源采样恢复
    latest_report = current.get("latest_model_report")     # 报告损坏时等待下一次模型探测恢复
    clean["latest_computer"] = deepcopy(latest_computer) if isinstance(latest_computer, dict) else None
    clean["latest_model_report"] = deepcopy(latest_report) if isinstance(latest_report, dict) else None
    return clean


# --- 清理历史容器中的结构损坏 ---
def _sanitize_history(value: Any) -> dict[str, list[dict[str, Any]]]:
    if not isinstance(value, dict):
        return {}                                           # 错误容器不能传播到业务指令
    history: dict[str, list[dict[str, Any]]] = {}
    for key, samples in value.items():
        if not isinstance(key, str) or not isinstance(samples, list):
            continue                                        # 历史键必须稳定且样本容器必须是列表
        history[key] = [deepcopy(sample) for sample in samples if isinstance(sample, dict)]
    return history                                          # 字段级数值验证由对应业务层负责
