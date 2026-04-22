from __future__ import annotations

from typing import Final

import httpx
from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register

from .test import build_overview, build_resource_cards, build_services, build_system_details
from .utils.htmlBuilder import HtmlBuilder
from .utils.monitor import Monitor


PLUGIN_NAME: Final[str] = "astrbot_plugin_picstatus"
ALIASES: Final[set[str]] = {"状态", "zt", "yxzt", "status", "运行状态"}


@register(
    PLUGIN_NAME,
    "Kernyr",
    "以图片形式显示当前设备的运行状态",
    "1.0.0",
)
class PicStatusPlugin(Star):
    def __init__(self, context: Context, config=None):
        super().__init__(context)
        self.config = config if config is not None else {}

    async def initialize(self):
        logger.info("开始初始化 PicStatus 插件")
        cfg = getattr(self, "config", None)

        if not (hasattr(cfg, "get") and hasattr(cfg, "__setitem__")):
            logger.info("PicStatus 插件初始化完成")
            return

        if not isinstance(cfg.get("avatar"), dict):
            cfg["avatar"] = {}

        if not isinstance(cfg.get("use_t2i"), bool):
            cfg["use_t2i"] = True

        if hasattr(cfg, "save_config"):
            try:
                cfg.save_config()
            except Exception as error:
                logger.warning(f"PicStatus: 保存配置失败: {error}")

        logger.info("PicStatus 插件初始化完成")

    @filter.command("运行状态", alias=ALIASES)
    async def cmd_status(self, event: AstrMessageEvent):
        logger.info("收到运行状态查询命令")
        image_to_send: str | bytes | None = None

        try:
            cfg = getattr(self, "config", None) or {}
            avatar_cfg = cfg.get("avatar") if isinstance(cfg, dict) else {}
            avatar_cfg = avatar_cfg if isinstance(avatar_cfg, dict) else {}

            logger.info("开始采集系统状态信息")
            result = Monitor.collect(services=self._buildServices(), timeout=5)
            computer = result["computer"]
            services = result["services"]
            summary = result["summary"]

            logger.info("开始解析头像配置")
            avatar_bytes = await self._resolveAvatar(event, avatar_cfg, cfg)

            collected = {
                "hostname": computer.get("hostName", "主服务器"),
                "os_info": f"{computer.get('system', 'Unknown')} ({computer.get('machine', '')})",
                "summary": summary,
                "overview": build_overview(summary),
                "resource_cards": build_resource_cards(computer),
                "services_status": build_services(services),
                "system_details": build_system_details(computer),
            }

            logger.info("开始生成单文件 HTML")
            html = HtmlBuilder.build(
                collected=collected,
                avatarBytes=avatar_bytes,
            )

            logger.info("开始调用 AstrBot 渲染器")
            image_to_send = await self.html_render(
                html,
                {},
                return_url=True,
                options={
                    "type": "jpeg",
                    "quality": 90,
                    "full_page": True,
                    "device_scale_factor_level": "ultra",
                },
            )
        except Exception:
            logger.exception("生成运行状态图片失败")
            yield event.plain_result("获取运行状态图片失败，请检查后台输出")
            return

        if image_to_send is None:
            logger.error("图片生成失败：image_to_send 未被设置")
            yield event.plain_result("图片生成失败，请检查后台输出")
            return

        yield event.image_result(image_to_send)
        logger.info("运行状态图片已成功发送")

    def _buildServices(self) -> list[dict]:
        return [
            {"name": "超级主核API", "type": "http", "url": "https://api.hujiarong.site/"},
            {"name": "主核Kernyr网站", "type": "http", "url": "https://www.hujiarong.site/"},
        ]

    async def _resolveAvatar(self, event: AstrMessageEvent, avatar_cfg: dict, cfg: dict) -> bytes | None:
        avatar_local_path = avatar_cfg.get("avatar_local_path") or cfg.get("avatar_local_path")
        if isinstance(avatar_local_path, str) and avatar_local_path.strip():
            try:
                return __import__("pathlib").Path(avatar_local_path.strip()).read_bytes()
            except Exception as error:
                logger.warning(f"PicStatus: 读取本地头像失败 {avatar_local_path}: {error}")

        avatar_url = avatar_cfg.get("avatar_url") or cfg.get("avatar_url")
        if isinstance(avatar_url, str) and avatar_url.strip():
            return await self._downloadBytes(avatar_url.strip(), "配置头像")

        try:
            self_id = event.get_self_id()
            adapter = event.get_platform_name() or "AstrBot"
            if "qq" in adapter.lower() or "aiocqhttp" in adapter.lower():
                qq_avatar_url = f"https://q1.qlogo.cn/g?b=qq&nk={self_id}&s=640"
                return await self._downloadBytes(qq_avatar_url, "QQ头像")
        except Exception:
            return None

        return None

    async def _downloadBytes(self, url: str, name: str) -> bytes | None:
        try:
            async with httpx.AsyncClient(follow_redirects=True, timeout=5) as client:
                response = await client.get(url)
                response.raise_for_status()
                logger.info(f"成功获取{name}")
                return response.content
        except Exception as error:
            logger.warning(f"PicStatus: 获取{name}失败 {url}: {error}")
            return None

    async def terminate(self):
        logger.info("PicStatus 插件已卸载")
