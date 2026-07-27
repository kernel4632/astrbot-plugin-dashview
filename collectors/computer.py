"""
主机采集器：一次读取机器人所在机器的资源与运行信息。

psutil 的采样会阻塞线程，因此公开入口通过 asyncio.to_thread 与 AstrBot 事件循环隔离。
调用示例：snapshot = await collect_computer()
"""

from __future__ import annotations                         # 允许现代类型注解

import asyncio                                             # 把阻塞的系统调用移到工作线程
import os                                                  # 获取处理器数量和系统磁盘根路径
import platform                                            # 获取操作系统与处理器架构
import shutil                                              # 跨平台读取磁盘容量
import socket                                              # 获取主机名称
import time                                                # 生成统一 UTC 时间戳
from typing import Any                                     # 描述包含多类指标的主机快照

import psutil                                              # 读取 CPU、内存、交换区、网络和进程状态


# --- 异步采集完整主机快照 ---
async def collect_computer() -> dict[str, Any]:
    return await asyncio.to_thread(_collect_computer_sync)  # 阻塞采样不占用 AstrBot 事件循环


# --- 在线程中读取系统指标 ---
def _collect_computer_sync() -> dict[str, Any]:
    observed_at = int(time.time() * 1000)                   # 全项目使用 UTC epoch 毫秒保存时间
    memory = psutil.virtual_memory()                        # 一次读取保证内存字段来自同一时刻
    swap = psutil.swap_memory()                             # 交换区独立展示，避免隐藏内存压力
    network = psutil.net_io_counters()                      # 累计流量用于后续采样计算速率
    disk_path = os.path.abspath(os.sep)                     # 默认观察系统根分区
    disk_total, disk_used, _ = shutil.disk_usage(disk_path) # 面板只需要总量与已用量

    return {
        "observed_at": observed_at,                        # 本次事实采集发生的时间
        "hostname": socket.gethostname(),                  # 机器人实际运行主机名
        "system": platform.system(),                       # 操作系统家族
        "system_version": platform.release(),              # 简洁系统版本，避免输出超长内核字符串
        "architecture": platform.machine(),                # 处理器架构
        "boot_at": int(psutil.boot_time() * 1000),         # 开机时间用于计算准确运行时长
        "process_count": len(psutil.pids()),               # 当前进程规模可辅助识别异常负载
        "cpu": {
            "percent": float(psutil.cpu_percent(interval=0.15)), # 短采样兼顾准确度与响应速度
            "logical_count": os.cpu_count(),               # 逻辑核心数帮助解释负载能力
        },
        "memory": {
            "percent": float(memory.percent),              # 系统口径的真实内存占用率
            "used": int(memory.used),                      # 当前已用字节数
            "total": int(memory.total),                    # 物理内存总字节数
        },
        "swap": {
            "percent": float(swap.percent),                # 交换区压力独立于物理内存
            "used": int(swap.used),                        # 当前已用交换区字节数
            "total": int(swap.total),                      # 交换区总字节数，0 表示未配置
        },
        "disk": {
            "percent": round(disk_used / disk_total * 100, 1) if disk_total else None,
            "used": int(disk_used),                        # 系统分区已使用字节数
            "total": int(disk_total),                      # 系统分区总字节数
            "path": disk_path,                             # 明确百分比对应哪个挂载点
        },
        "network": {
            "sent": int(network.bytes_sent),               # 开机以来累计发送字节数
            "received": int(network.bytes_recv),           # 开机以来累计接收字节数
        },
    }
