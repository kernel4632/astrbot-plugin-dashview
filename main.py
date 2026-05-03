"""
这个文件是 AstrBot 插件的正式入口。

它只负责三类事件：
1. 用户发送“运行状态”，这里采集设备状态、服务状态、模型连通性，然后生成同一张仪表盘图片。
2. 用户发送“模型检测”，这里只换一个入口名，仍然复用同一条仪表盘渲染流程，让模型结果显示在模板底部卡片里。
3. 插件启动后的定时任务会按配置间隔自动生成同一张仪表盘图片，并推送到最近执行过命令的群聊或私聊。

事件 → 指令 → 数据 → 反馈 的链条非常清楚：
用户命令 → cmd_status() 或 cmd_model_test() → buildDashboardImage() → Monitor.collect() 与 ModelProbe.probe() → Data.buildCollected() → Render.build() → Image.build() → event.image_result()
定时任务 → runAutoDashboardOnce() → buildDashboardImage() → context.send_message()

如果你想改模型连通性怎么测，去看 utils/modelProbe.py。
如果你想改模型结果怎么整理，去看 data.py。
如果你想改模型卡片长什么样，去看 resources/templates/macros.html.jinja 和 resources/index.css。

常见调用流程：
用户发送“运行状态” → 生成设备状态 + 服务状态 + 模型连通性的一张总图。
用户发送“模型检测” → 也生成同一张总图，方便只关心模型状态时直接查看底部卡片。
用户发送过任意一个查询命令 → 插件记住当前会话 → 定时任务到点后自动把图片发回这个会话。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from time import time_ns
from typing import Any, Final

import httpx
from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register

from .data import Data
from .utils.image import Image
from .utils.modelProbe import ModelProbe
from .utils.monitor import Monitor
from .utils.render import Render


PLUGIN_NAME: Final[str] = "astrbot_plugin_dashview"
STATUS_ALIASES: Final[set[str]] = {"状态", "zt", "yxzt", "status", "运行状态"}
MODELTEST_ALIASES: Final[set[str]] = {"modelTest", "模型检测", "模型连通性", "检测模型"}
AUTO_OFF_ALIASES: Final[set[str]] = {"取消状态推送", "dashboardOff", "状态推送关闭"}
ROOT: Final[Path] = Path(__file__).parent
CACHE_FOLDER: Final[Path] = ROOT / "cache"


@register(
    PLUGIN_NAME,
    "Kernyr",
    "以图片形式显示当前设备运行状态仪表盘，底部展示模型连通性检测卡片，支持定时自动推送",
    "1.0.7",
)
class DashViewPlugin(Star):
    """
    这个对象就是 AstrBot 真正会加载的插件对象。

    你最需要记住的入口：
    1. initialize()：插件加载时启动定时推送任务。
    2. cmd_status()：收到“运行状态”后生成完整仪表盘图片，并记住当前会话。
    3. cmd_model_test()：收到“模型检测”后复用完整仪表盘图片，模型结果在底部卡片展示，并记住当前会话。
    4. cmd_auto_off()：当前会话不想再收定时图时，用它取消。
    """

    def __init__(self, context: Context, config: dict | None = None):
        super().__init__(context)
        self.config = config if config is not None else {}
        self.autoDashboardTask: asyncio.Task | None = None

    async def initialize(self):
        """这个函数在插件加载时运行，读取配置后启动定时推送任务。"""
        logger.info("开始初始化 DashView 插件")
        self.startAutoDashboardTask()
        logger.info("DashView 插件初始化完成")


    # =================================================================
    # 命令入口
    # =================================================================

    @filter.command("运行状态", alias=STATUS_ALIASES)
    async def cmd_status(self, event: AstrMessageEvent):
        """收到运行状态命令后，生成包含模型连通性卡片的完整仪表盘图片。"""
        logger.info("收到运行状态查询命令")

        async for result in self.sendDashboardImage(event, "运行状态"):
            yield result

    @filter.command("模型检测", alias=MODELTEST_ALIASES)
    async def cmd_model_test(self, event: AstrMessageEvent):
        """收到模型检测命令后，复用仪表盘渲染流程，并在底部展示模型连通性卡片。"""
        logger.info("收到模型连通性检测命令")
        yield event.plain_result("正在检测模型连通性并生成仪表盘图片，请稍等...")

        async for result in self.sendDashboardImage(event, "模型检测"):
            yield result

    @filter.command("取消状态推送", alias=AUTO_OFF_ALIASES)
    async def cmd_auto_off(self, event: AstrMessageEvent):
        """当前群聊或私聊不想继续接收定时仪表盘时，用这个命令移除当前会话。"""
        origin = self.eventOrigin(event)
        if not origin:
            yield event.plain_result("无法识别当前会话，取消定时推送失败。")
            return

        removed = await self.forgetDashboardTarget(origin)
        if removed:
            yield event.plain_result("已取消当前群聊/私聊的仪表盘定时推送。")
            return

        yield event.plain_result("当前群聊/私聊没有开启仪表盘定时推送。")


    # =================================================================
    # 仪表盘生成指令
    # =================================================================

    async def sendDashboardImage(self, event: AstrMessageEvent, actionName: str):
        """这个函数执行完整图片反馈流程：记住会话 → 构建图片 → 失败返回文字 → 成功返回图片。"""
        await self.rememberDashboardTargetFromEvent(event)
        imageToSend: str | None = None

        try:
            imageToSend = await self.buildDashboardImage(event)
        except Exception:
            logger.exception(f"{actionName}图片生成失败")
            yield event.plain_result(f"{actionName}图片生成失败，请检查后台输出")
            return

        if imageToSend is None:
            logger.error(f"{actionName}图片生成失败：imageToSend 未被设置")
            yield event.plain_result(f"{actionName}图片生成失败，请检查后台输出")
            return

        yield event.image_result(imageToSend)
        logger.info(f"{actionName}图片已成功发送")

    async def buildDashboardImage(self, event: AstrMessageEvent | None = None) -> str:
        """
        这个函数把系统检测、服务检测、模型检测、数据整理、HTML 渲染、图片保存串成一条线。
        event 可以为空，定时任务没有用户事件时就只跳过 QQ 头像读取。
        """
        config = getattr(self, "config", None) or {}

        logger.info("开始采集系统状态信息")
        servicesToCheck = self.readServices(config)
        timeout = self.readInt(config, "timeout", 5)
        result = Monitor.collect(services=servicesToCheck, timeout=timeout)

        logger.info("开始检测模型连通性")
        modelReport = await self.buildModelReport(config)

        logger.info("开始解析头像配置")
        avatarBytes = await self.resolveAvatar(event, config)

        collected = Data.buildCollected(
            computer=result["computer"],
            services=result["services"],
            summary=result["summary"],
            nickname=self.readText(config, "nickname", "阿柯AKer"),
            success_text=self.readText(config, "success_text", "阿柯牛逼"),
            fail_text=self.readText(config, "fail_text", "阿柯死了"),
            model_report=modelReport,
        )

        logger.info("开始生成单文件 HTML")
        html = Render.build(collected=collected, avatarBytes=avatarBytes)

        logger.info("开始使用本地 HTML 渲染器生成图片")
        imageBytes = await Image.build(html, width=900, quality=90)
        return self.saveImage(imageBytes)

    async def buildModelReport(self, config: dict[str, Any]) -> dict[str, Any]:
        """这个函数只负责读取模型检测配置，然后调用 ModelProbe 做连通性检测。"""
        probeConfig = {
            "timeout": self.readInt(config, "model_timeout", 30),
            "concurrency": self.readInt(config, "model_concurrency", 3),
            "slow_ms": self.readInt(config, "model_slow_ms", 8000),
            "max_models": self.readInt(config, "model_max_models", 0),
        }
        return await ModelProbe.probe(self.context, probeConfig)


    # =================================================================
    # 定时推送指令
    # =================================================================

    def startAutoDashboardTask(self) -> None:
        """这个函数在插件启动时创建定时任务；配置为 0 时不启动。"""
        intervalHours = self.autoDashboardIntervalHours()
        if intervalHours <= 0:
            logger.info("仪表盘定时推送未开启")
            return

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            logger.warning("仪表盘定时推送启动失败：当前没有运行中的事件循环")
            return

        self.autoDashboardTask = loop.create_task(self.autoDashboardLoop())
        logger.info(f"仪表盘定时推送已开启，间隔 {intervalHours:g} 小时")

    async def autoDashboardLoop(self) -> None:
        """这个函数长期运行：可选启动后立即推送一次，然后按固定间隔循环推送。"""
        if self.readBool(self.config, "auto_dashboard_run_on_start", False):
            await self.runAutoDashboardOnce()

        while True:
            intervalHours = self.autoDashboardIntervalHours()
            if intervalHours <= 0:
                logger.info("仪表盘定时推送已停止：间隔配置小于等于 0")
                return
            await asyncio.sleep(max(60.0, intervalHours * 3600))
            await self.runAutoDashboardOnce()

    async def runAutoDashboardOnce(self) -> None:
        """这个函数执行一次定时推送：读取目标 → 生成图片 → 发送到每个目标。"""
        try:
            targets = await self.dashboardTargets()
            if not targets:
                logger.info("仪表盘定时推送跳过：还没有记住任何会话，请先手动执行一次运行状态或模型检测")
                return

            imagePath = await self.buildDashboardImage(None)
            chain = self.imageMessageChain(imagePath)
            for target in targets:
                try:
                    sendResult = self.context.send_message(target, chain)
                    if hasattr(sendResult, "__await__"):
                        await sendResult
                except Exception as error:
                    logger.warning(f"仪表盘定时推送到 {target} 失败: {error}")

            logger.info(f"仪表盘定时推送完成，共发送 {len(targets)} 个会话")
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.warning(f"仪表盘定时推送失败: {error}")

    def autoDashboardIntervalHours(self) -> float:
        """这个函数读取定时推送间隔，单位是小时，0 表示关闭。"""
        return self.readFloat(self.config, "auto_dashboard_interval_hours", 0.0)

    async def rememberDashboardTargetFromEvent(self, event: AstrMessageEvent) -> None:
        """这个函数参考 connect：用户执行命令后，记住当前群聊或私聊，后续定时图就发到这里。"""
        if not self.readBool(self.config, "auto_dashboard_remember_command_session", True):
            return

        origin = self.eventOrigin(event)
        if not origin:
            return

        try:
            targets = await self.get_kv_data("dashboard_send_targets", [])
            if not isinstance(targets, list):
                targets = []
            normalized = [str(item).strip() for item in targets if str(item).strip()]
            if origin not in normalized:
                normalized.append(origin)
                normalized = normalized[-20:]  # 最多保留最近 20 个会话，避免长期无限增长。
                await self.put_kv_data("dashboard_send_targets", normalized)
        except Exception as error:
            logger.warning(f"记住仪表盘定时推送会话失败: {error}")

    async def forgetDashboardTarget(self, origin: str) -> bool:
        """这个函数从定时推送目标中移除一个会话，返回是否真的删掉了。"""
        origin = str(origin or "").strip()
        if not origin:
            return False

        try:
            targets = await self.get_kv_data("dashboard_send_targets", [])
            if not isinstance(targets, list):
                return False
            normalized = [str(item).strip() for item in targets if str(item).strip()]
            filtered = [target for target in normalized if target != origin]
            if len(filtered) == len(normalized):
                return False
            await self.put_kv_data("dashboard_send_targets", filtered)
            return True
        except Exception as error:
            logger.warning(f"取消仪表盘定时推送会话失败: {error}")
            return False

    async def dashboardTargets(self) -> list[str]:
        """这个函数读取所有定时推送目标，并去重后返回。"""
        if not self.readBool(self.config, "auto_dashboard_remember_command_session", True):
            return []

        try:
            targets = await self.get_kv_data("dashboard_send_targets", [])
            if not isinstance(targets, list):
                return []
            seen: set[str] = set()
            result: list[str] = []
            for target in targets:
                text = str(target).strip()
                if text and text not in seen:
                    seen.add(text)
                    result.append(text)
            return result
        except Exception as error:
            logger.warning(f"读取仪表盘定时推送会话失败: {error}")
            return []

    def eventOrigin(self, event: Any) -> str:
        """这个函数兼容不同 AstrBot 事件对象，尽量拿到统一会话标识。"""
        for name in ("unified_msg_origin", "get_unified_msg_origin"):
            value = getattr(event, name, "")
            if callable(value):
                try:
                    value = value()
                except Exception:
                    value = ""
            text = str(value or "").strip()
            if text:
                return text
        return ""

    def imageMessageChain(self, imagePath: str) -> Any:
        """这个函数把本地图片路径包装成 AstrBot 可发送的消息链，逻辑参考 connect 文件。"""
        isUrl = imagePath.startswith(("http://", "https://"))
        try:
            from astrbot.api.event import MessageChain

            chain = MessageChain()
            if isUrl:
                if hasattr(chain, "url_image"):
                    return chain.url_image(imagePath)
                raise AttributeError("MessageChain.url_image 不可用")
            return chain.file_image(imagePath)
        except Exception:
            from astrbot.api import message_components as Comp

            if isUrl and hasattr(Comp.Image, "fromURL"):
                return [Comp.Image.fromURL(imagePath)]
            return [Comp.Image.fromFileSystem(imagePath)]


    # =================================================================
    # 配置读取与工具方法
    # =================================================================

    def readServices(self, config: dict[str, Any]) -> list[dict[str, Any]]:
        """这个函数读取服务列表；配置缺失或格式不对时，回退到内置服务。"""
        services = config.get("services") if isinstance(config, dict) else []
        return services if isinstance(services, list) else self.buildServices()

    def readInt(self, config: dict[str, Any], key: str, default: int) -> int:
        """这个函数把配置值安全转成整数，避免 WebUI 配置为空或字符串时报错。"""
        try:
            return int(config.get(key, default))
        except Exception:
            return default

    def readFloat(self, config: dict[str, Any], key: str, default: float) -> float:
        """这个函数把配置值安全转成小数，定时任务小时数会用到。"""
        try:
            return float(config.get(key, default))
        except Exception:
            return default

    def readBool(self, config: dict[str, Any], key: str, default: bool) -> bool:
        """这个函数把配置值安全转成布尔值，兼容 WebUI 里可能出现的字符串。"""
        value = config.get(key, default) if isinstance(config, dict) else default
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.lower() in ("1", "true", "yes", "on", "是", "开启")
        return bool(value)

    def readText(self, config: dict[str, Any], key: str, default: str) -> str:
        """这个函数读取文本配置，空字符串会回退到默认值。"""
        value = config.get(key) if isinstance(config, dict) else ""
        text = str(value).strip() if value is not None else ""
        return text if text else default

    def saveImage(self, imageBytes: bytes) -> str:
        """这个函数把渲染出的图片字节保存成本地文件，因为 AstrBot 的 image_result() 需要 URL 或路径字符串。"""
        CACHE_FOLDER.mkdir(parents=True, exist_ok=True)
        imagePath = CACHE_FOLDER / f"dashview_{time_ns()}.jpg"
        imagePath.write_bytes(imageBytes)
        return str(imagePath)

    def buildServices(self) -> list[dict[str, Any]]:
        """这个函数统一放要检测的服务列表，后面改目标网站时只需要改这里。"""
        return [
            {"name": "超级主核API", "type": "http", "url": "https://api.hujiarong.site/"},
            {"name": "主核Kernyr网站", "type": "http", "url": "https://www.hujiarong.site/"},
        ]

    async def resolveAvatar(self, event: AstrMessageEvent | None, config: dict[str, Any]) -> bytes | None:
        """这个函数按“本地路径 → 配置 URL → QQ 头像”的顺序解析最终头像；定时任务没有事件时跳过 QQ 头像。"""
        avatarLocalPath = config.get("avatar_local_path")
        if isinstance(avatarLocalPath, str) and avatarLocalPath.strip():
            try:
                return Path(avatarLocalPath.strip()).read_bytes()  # 本地路径最快也最稳定，所以优先读它。
            except Exception as error:
                logger.warning(f"DashView: 读取本地头像失败 {avatarLocalPath}: {error}")

        avatarUrl = config.get("avatar_url")
        if isinstance(avatarUrl, str) and avatarUrl.strip():
            return await self.downloadBytes(avatarUrl.strip(), "配置头像")

        if event is None:
            return None

        try:
            selfId = event.get_self_id()
            adapter = event.get_platform_name() or "AstrBot"
            if "qq" in adapter.lower() or "aiocqhttp" in adapter.lower():
                qqAvatarUrl = f"https://q1.qlogo.cn/g?b=qq&nk={selfId}&s=640"
                return await self.downloadBytes(qqAvatarUrl, "QQ头像")
        except Exception:
            return None  # 平台不支持或事件对象没有这些方法时，直接回退到默认头像即可。

        return None

    async def downloadBytes(self, url: str, name: str) -> bytes | None:
        """这个函数专门下载二进制内容，头像和其他远程资源都可以复用它。"""
        try:
            async with httpx.AsyncClient(follow_redirects=True, timeout=5) as client:
                response = await client.get(url)
                response.raise_for_status()  # 这里直接抛错最清楚，失败原因会被外层日志完整记录。
                logger.info(f"成功获取{name}")
                return response.content
        except Exception as error:
            logger.warning(f"DashView: 获取{name}失败 {url}: {error}")
            return None

    async def terminate(self):
        """这个函数在插件卸载时运行，会取消定时任务，避免卸载后后台还在循环。"""
        if self.autoDashboardTask and not self.autoDashboardTask.done():
            self.autoDashboardTask.cancel()
            try:
                await self.autoDashboardTask
            except asyncio.CancelledError:
                pass
        logger.info("DashView 插件已卸载")
