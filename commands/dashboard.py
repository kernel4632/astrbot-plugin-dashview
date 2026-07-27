"""
仪表盘指令：并行采集当前事实、写入资源历史，并生成一张可发送图片。

状态查看只读取最近模型报告，不主动调用任何模型；完整数据流在本文件一眼可追踪。
调用示例：image_path = await build_dashboard(event, state, settings)
"""

from __future__ import annotations                         # 允许现代类型注解

import asyncio                                             # 并行执行彼此独立的采集动作
from typing import Any                                     # 兼容 AstrBot 消息事件

from ..collectors.avatar import collect_avatar             # 获取平台或配置头像
from ..collectors.services import check_services           # 并发检测本次服务状态
from ..config import Settings                              # 读取全部经过验证的运行参数
from ..presentation.artifacts import save_image            # 保存受数量限制的发送图片
from ..presentation.html import render_html                # 构建完全离线的页面
from ..presentation.image import render_image              # 复用 Chromium 截图
from ..presentation.view import build_dashboard_view       # 把真实事实整理成可信视图
from ..state import DashboardState                         # 读取最近模型报告与资源历史
from .resources import sample_resources                    # 采集主机并写入真实资源历史


# --- 生成当前状态仪表盘 ---
async def build_dashboard(event: Any | None, state: DashboardState, settings: Settings, resource_lock: asyncio.Lock) -> str:
    resource_task = sample_resources(state, settings, resource_lock) # 与后台任务共享完整采样单飞锁
    service_task = check_services(settings.services, settings.service_timeout, settings.service_concurrency)
    avatar_task = collect_avatar(event, settings)          # 头像失败不会影响状态事实
    resource_result, services, avatar = await asyncio.gather(resource_task, service_task, avatar_task)
    computer, resource_history = resource_result           # 读取刚写入的主机事实与历史

    current_state = await state.read()                      # 资源提交后读取一致的完整状态
    view = build_dashboard_view(
        computer=computer,                                  # 本次主机快照
        resource_history=resource_history,                  # 包含本次样本的真实趋势
        services=services,                                  # 本次并发服务检测结果
        model_report=current_state["latest_model_report"],  # 最近模型探测，不额外调用模型
        model_history=current_state["model_history"],       # 与报告同一文档提交的历史
        settings=settings,                                  # 阈值、文案和行数限制
    )
    html = render_html(view, avatar)                        # 页面不含任何外部网络依赖
    image_bytes = await render_image(html)                  # 共享浏览器生成最终 JPEG
    return save_image(image_bytes, settings.cache_keep_count)
