"""
这个文件是 AstrBot 插件的正式入口。

它只负责三件事：
1. 接收“运行状态”命令
2. 调用其他文件收集数据、整理页面数据、生成 HTML
3. 把 HTML 交给本地渲染器截图，保存成本地图片后发回去

如果你想看“页面要显示哪些内容”，去看 data.py。
如果你想看“怎么检测系统和服务”，去看 utils/monitor.py。
如果你想看“怎么把模板打包成单文件 HTML”，去看 utils/render.py。
如果你想看“怎么把 HTML 渲染成图片”，去看 utils/image.py。

最常见的调用流程是这样的：
用户发送“运行状态” → 这里收到命令 → Monitor.collect() 拿到真实数据 → Data.buildCollected() 整理成模板需要的结构 → Render.build() 生成单文件 HTML → Image.build() 本地截图 → saveImage() 保存图片路径 → event.image_result() 返回。
"""

from __future__ import annotations

from pathlib import Path
from time import time_ns
from typing import Final

import httpx
from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register

from .data import Data
from .utils.image import Image
from .utils.monitor import Monitor
from .utils.render import Render


PLUGIN_NAME: Final[str] = "astrbot_plugin_dashview"
ALIASES: Final[set[str]] = {"状态", "zt", "yxzt", "status", "运行状态"}
ROOT: Final[Path] = Path(__file__).parent
CACHE_FOLDER: Final[Path] = ROOT / "cache"


@register(
    PLUGIN_NAME,
    "Kernyr",
    "以图片形式显示当前设备的运行状态仪表盘",
    "1.0.4",
)
class DashViewPlugin(Star):
    """
    这个对象就是 AstrBot 真正会加载的插件对象。

    你最需要记住的入口有两个：
    1. initialize()：插件加载时做配置兜底
    2. cmdStatus()：收到命令后生成状态图

    如果以后你要加新命令，继续在这个对象里新增方法即可。
    """

    def __init__(self, context: Context, config=None):
        super().__init__(context)
        self.config = config if config is not None else {}

    async def initialize(self):
        """这个函数在插件加载时运行，当前只保留日志，避免把不该暴露的字段写进 WebUI 配置。"""
        logger.info("开始初始化 DashView 插件")
        logger.info("DashView 插件初始化完成")

    @filter.command("运行状态", alias=ALIASES)
    async def cmd_status(self, event: AstrMessageEvent):
        """这个函数接收命令，然后完整执行一次“采集 → 整理 → 渲染 → 返回”。"""
        logger.info("收到运行状态查询命令")
        imageToSend: str | None = None

        try:
            config = getattr(self, "config", None) or {}

            logger.info("开始采集系统状态信息")
            servicesToCheck = config.get("services") if isinstance(config, dict) else []
            servicesToCheck = servicesToCheck if isinstance(servicesToCheck, list) else self.buildServices()
            timeout = config.get("timeout") if isinstance(config, dict) else 5
            timeout = int(timeout) if isinstance(timeout, (int, float)) else 5

            result = Monitor.collect(services=servicesToCheck, timeout=timeout)
            computer = result["computer"]
            services = result["services"]
            summary = result["summary"]

            logger.info("开始解析头像配置")
            avatarBytes = await self.resolveAvatar(event, config)

            nickname = config.get("nickname") if isinstance(config, dict) else ""
            nickname = str(nickname) if nickname else "阿柯AKer"
            successText = config.get("success_text") if isinstance(config, dict) else ""
            successText = str(successText) if successText else "阿柯牛逼"
            failText = config.get("fail_text") if isinstance(config, dict) else ""
            failText = str(failText) if failText else "阿柯死了"

            collected = Data.buildCollected(
                computer=computer,
                services=services,
                summary=summary,
                nickname=nickname,
                success_text=successText,
                fail_text=failText,
            )

            logger.info("开始生成单文件 HTML")
            html = Render.build(collected=collected, avatarBytes=avatarBytes)

            logger.info("开始使用本地 HTML 渲染器生成图片")
            imageBytes = await Image.build(html, width=900, quality=90)
            imageToSend = self.saveImage(imageBytes)
        except Exception:
            logger.exception("生成运行状态图片失败")
            yield event.plain_result("获取运行状态图片失败，请检查后台输出")
            return

        if imageToSend is None:
            logger.error("图片生成失败：imageToSend 未被设置")
            yield event.plain_result("图片生成失败，请检查后台输出")
            return

        yield event.image_result(imageToSend)
        logger.info("运行状态图片已成功发送")

    def saveImage(self, imageBytes: bytes) -> str:
        """这个函数把渲染出的图片字节保存成本地文件，因为 AstrBot v4.23.5 的 image_result() 需要 URL 或路径字符串。"""
        CACHE_FOLDER.mkdir(parents=True, exist_ok=True)
        imagePath = CACHE_FOLDER / f"dashview_{time_ns()}.jpg"
        imagePath.write_bytes(imageBytes)
        return str(imagePath)

    def buildServices(self) -> list[dict]:
        """这个函数统一放要检测的服务列表，后面改目标网站时只需要改这里。"""
        return [
            {"name": "超级主核API", "type": "http", "url": "https://api.hujiarong.site/"},
            {"name": "主核Kernyr网站", "type": "http", "url": "https://www.hujiarong.site/"},
        ]

    async def resolveAvatar(self, event: AstrMessageEvent, config: dict) -> bytes | None:
        """这个函数按“本地路径 → 配置 URL → QQ 头像”的顺序解析最终头像。"""
        avatarLocalPath = config.get("avatar_local_path")
        if isinstance(avatarLocalPath, str) and avatarLocalPath.strip():
            try:
                return Path(avatarLocalPath.strip()).read_bytes()  # 本地路径最快也最稳定，所以优先读它。
            except Exception as error:
                logger.warning(f"DashView: 读取本地头像失败 {avatarLocalPath}: {error}")

        avatarUrl = config.get("avatar_url")
        if isinstance(avatarUrl, str) and avatarUrl.strip():
            return await self.downloadBytes(avatarUrl.strip(), "配置头像")

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
        """这个函数在插件卸载时运行，当前只保留日志提示。"""
        logger.info("DashView 插件已卸载")
