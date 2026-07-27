"""
DashView AstrBot 入口：接收消息触发、调用一个业务指令、把结果反馈给用户。

完整链条保持固定：消息事件 → dashboard/models 指令 → DashboardState 修改 → 文字或图片反馈。
业务采集、状态计算和页面渲染均不写在入口中，便于沿调用路径单独理解和替换。
"""

from __future__ import annotations                         # 允许现代类型注解

import asyncio                                             # 防止多个用户同时触发昂贵截图和模型调用
from typing import Final                                   # 标记不会在运行中变化的插件常量

from astrbot.api import logger                             # 所有运行反馈进入 AstrBot 日志
from astrbot.api.event import AstrMessageEvent, filter     # 接收聊天命令并构造消息结果
from astrbot.api.star import Context, Star, register       # 注册 AstrBot 插件生命周期

from .commands.dashboard import build_dashboard            # 状态指令生成可信图片
from .commands.models import model_probe_due, probe_models # 模型指令执行小时级真实功能探针
from .commands.resources import sample_resources           # 后台资源任务复用同一采样指令
from .commands.schedule import Schedule                    # 集中管理两个后台副作用循环
from .config import Settings                               # 把 WebUI 配置转换成可靠参数
from .presentation.image import close_browser              # 卸载时关闭共享 Chromium
from .state import DashboardState                          # 集中持久化历史和最近模型报告


PLUGIN_NAME: Final[str] = "astrbot_plugin_dashview"        # AstrBot 市场和 KV 使用的插件身份
PLUGIN_VERSION: Final[str] = "2.0.0"                       # 与 metadata 和 pyproject 保持一致
STATUS_ALIASES: Final[tuple[str, ...]] = ("状态", "zt", "yxzt", "status")
MODEL_ALIASES: Final[tuple[str, ...]] = ("模型连通性", "检测模型", "modeltest")


@register(
    PLUGIN_NAME,
    "Kernyr",
    "一图展示主机资源、服务健康与 AstrBot 模型路由连通性",
    PLUGIN_VERSION,
)
class DashViewPlugin(Star):
    # --- 建立插件运行依赖 ---
    def __init__(self, context: Context, config: dict | None = None) -> None:
        super().__init__(context)                           # 先建立 AstrBot Star 基础能力
        self.settings = Settings.from_dict(config)          # 后续代码只读取验证后的不可变配置
        self.state = DashboardState(self.get_kv_data, self.put_kv_data) # 单一 KV 文档保存全部历史
        self.operation_lock = asyncio.Lock()                # 用户命令单飞，防止重复模型计费和浏览器堆积
        self.model_probe_lock = asyncio.Lock()              # 后台和手动探测共享单飞锁，杜绝重复供应商调用
        self.resource_sample_lock = asyncio.Lock()          # 后台和手动资源快照按观察时间顺序提交
        self.stopping = False                               # 卸载开始后拒绝创建新的用户工作
        self.schedule = Schedule(
            settings=self.settings,                         # 后台循环使用同一配置快照
            sample_resources=self._sample_resources,        # 资源副作用回到资源指令
            probe_models=self._probe_models_if_due,         # 后台只在距上次满一小时时调用模型
            logger=logger,                                  # 后台结果统一进入 AstrBot 日志
        )

    # --- 启动后台采样任务 ---
    async def initialize(self) -> None:
        self.schedule.start()                               # 配置为 0 的循环不会创建
        logger.info("DashView %s 初始化完成", PLUGIN_VERSION)

    # --- 响应运行状态命令 ---
    @filter.command("运行状态", alias=STATUS_ALIASES)
    async def status_command(self, event: AstrMessageEvent):
        if self.stopping:
            yield event.plain_result("DashView 正在停止，请稍后重试。")
            return                                          # 卸载期间不启动新截图
        if self.operation_lock.locked():                    # 已有用户生成面板时拒绝重复重任务
            yield event.plain_result("DashView 正在处理上一项请求，请稍后再试。")
            return

        async with self.operation_lock:                     # 在第一次 yield 前原子占用命令名额
            if self.stopping:
                yield event.plain_result("DashView 正在停止，请稍后重试。")
                return                                      # 等锁期间进入卸载则不再启动工作
            yield event.plain_result("正在采集主机与服务状态并生成面板...")
            async for result in self._send_dashboard(event, "运行状态"):
                yield result                                # 入口只把指令结果交回消息平台

    # --- 响应模型检测命令 ---
    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("模型检测", alias=MODEL_ALIASES)
    async def model_command(self, event: AstrMessageEvent):
        if self.stopping:
            yield event.plain_result("DashView 正在停止，请稍后重试。")
            return                                          # 卸载期间不调用任何供应商
        if self.operation_lock.locked():                    # 防止同一批 Provider 被重复并发计费
            yield event.plain_result("DashView 正在处理上一项请求，请稍后再试。")
            return

        async with self.operation_lock:                     # 先占名额再向聊天发送进度
            if self.stopping:
                yield event.plain_result("DashView 正在停止，请稍后重试。")
                return                                      # 等锁期间进入卸载则不产生费用
            yield event.plain_result("正在向每条未排除的聊天模型发送一次真实、可能计费的探测请求...")
            try:
                report = await self._probe_models()         # 模型指令先原子保存本次报告和历史
                logger.info(
                    "DashView 模型检测完成：正常 %s，较慢 %s，异常 %s",
                    report["available_count"],
                    report["slow_count"],
                    report["invalid_count"] + report["unavailable_count"],
                )
            except Exception:
                logger.exception("DashView 模型检测失败")
                yield event.plain_result("模型检测失败，旧报告未被覆盖，请检查后台日志。")
                return

            async for result in self._send_dashboard(event, "模型检测"):
                yield result                                # 图片展示刚提交的模型报告

    # --- 执行图片指令并构造用户反馈 ---
    async def _send_dashboard(self, event: AstrMessageEvent, action_name: str):
        try:
            image_path = await build_dashboard(event, self.state, self.settings, self.resource_sample_lock)
        except Exception:
            logger.exception("DashView %s面板生成失败", action_name)
            yield event.plain_result(f"{action_name}面板生成失败，请检查后台日志。")
            return
        yield event.image_result(image_path)                # 只有完整图片落盘后才反馈成功
        logger.info("DashView %s面板已发送", action_name)

    # --- 为后台任务执行一次资源采样 ---
    async def _sample_resources(self):
        return await sample_resources(self.state, self.settings, self.resource_sample_lock) # 与手动面板共享完整单飞边界

    # --- 为用户和后台任务执行一次模型探测 ---
    async def _probe_models(self):
        async with self.model_probe_lock:                   # 任意来源同时只执行一批真实模型探针
            return await probe_models(self.context, self.state, self.settings, source="manual") # 手动结果可在关闭后台时临时展示

    # --- 到期后执行一次后台模型探测 ---
    async def _probe_models_if_due(self):
        async with self.model_probe_lock:                   # 到期判断和模型调用必须处于同一单飞边界
            current = await self.state.read()               # 读取最近报告决定本小时是否已经采样
            if not model_probe_due(current.get("latest_model_report")):
                return False                                # 未满一小时不调用供应商也不写历史
            await probe_models(self.context, self.state, self.settings, source="scheduled")
            return True                                     # 调度器只在真实采样后记录完成日志

    # --- 停止全部副作用并卸载插件 ---
    async def terminate(self) -> None:
        self.stopping = True                                # 先阻止新的命令进入生命周期
        await self.schedule.stop()                          # 先停止可能还会创建采集和渲染的任务
        async with self.operation_lock:                     # 等待正在反馈的用户命令完成
            async with self.model_probe_lock:               # 确认没有手动模型探针仍在调用上下文
                async with self.resource_sample_lock:       # 确认没有资源工作线程准备提交快照
                    await close_browser()                   # 最后释放共享 Chromium 进程
        logger.info("DashView %s 已卸载", PLUGIN_VERSION)
