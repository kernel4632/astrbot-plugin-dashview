from __future__ import annotations

import os
from pathlib import Path
from typing import Final

import astrbot.api.message_components as Comp
import httpx
from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register

from .test import build_overview, build_resource_cards, build_services, build_system_details
from .utils.background import Background, BackgroundRequest
from .utils.htmlBuilder import HtmlBuilder
from .utils.monitor import Monitor


PLUGIN_NAME: Final[str] = "astrbot_plugin_picstatus"
ALIASES: Final[set[str]] = {"状态", "zt", "yxzt", "status", "运行状态"}
CACHE_DIR = Path(__file__).parent / ".cache"


@register(
    PLUGIN_NAME,
    "Kernyr",
    "以图片形式显示当前设备的运行状态",
    "1.0.0",
)
class PicStatusPlugin(Star):
    def __init__(self, context: Context, config=None):
        super().__init__(context)
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        self.config = config if config is not None else {}

    async def initialize(self):
        logger.info("开始初始化 PicStatus 插件")
        cfg = getattr(self, "config", None)

        if not (hasattr(cfg, "get") and hasattr(cfg, "__setitem__")):
            logger.info("PicStatus 插件初始化完成")
            return

        if not isinstance(cfg.get("avatar"), dict):
            cfg["avatar"] = {}

        if not isinstance(cfg.get("background"), dict):
            cfg["background"] = {}

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
            bg_cfg = cfg.get("background") if isinstance(cfg, dict) else {}
            avatar_cfg = avatar_cfg if isinstance(avatar_cfg, dict) else {}
            bg_cfg = bg_cfg if isinstance(bg_cfg, dict) else {}

            logger.info("开始采集系统状态信息")
            result = Monitor.collect(services=self._buildServices(), timeout=5)
            computer = result["computer"]
            services = result["services"]
            summary = result["summary"]

            logger.info("开始检查消息内背景图片")
            background_bytes = await self._readMessageImage(event)

            logger.info("开始解析背景配置")
            background = await self._resolveBackground(background_bytes, bg_cfg, cfg)

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
                backgroundBytes=background.data,
                backgroundMime=background.mime,
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

    async def _readMessageImage(self, event: AstrMessageEvent) -> bytes | None:
        try:
            for seg in event.get_messages():
                if not isinstance(seg, Comp.Image):
                    continue

                file_url = getattr(seg, "file", None) or ""
                if not isinstance(file_url, str) or not file_url.startswith(("http://", "https://")):
                    continue

                async with httpx.AsyncClient(follow_redirects=True, timeout=5) as client:
                    response = await client.get(file_url)
                    response.raise_for_status()
                    return response.content
        except Exception as error:
            logger.warning(f"PicStatus: 读取消息图片失败: {error}")

        return None

    async def _resolveBackground(self, background_bytes: bytes | None, bg_cfg: dict, cfg: dict):
        provider = bg_cfg.get("bg_provider") or cfg.get("bg_provider") or os.getenv("PICSTATUS_BG_PROVIDER", "none")
        local_path = bg_cfg.get("bg_local_path") or cfg.get("bg_local_path") or os.getenv("PICSTATUS_BG_LOCAL_PATH", "")
        fallback_chain = bg_cfg.get("bg_fallback_chain") or cfg.get("bg_fallback_chain") or ""

        provider_chain: list[str] = []
        for item in [provider, *str(fallback_chain).split(",")]:
            name = str(item).strip().lower()
            if name and name not in provider_chain:
                provider_chain.append(name)

        if not provider_chain:
            provider_chain = ["none"]

        request = BackgroundRequest(
            provider_chain=tuple(provider_chain),
            local_path=Path(local_path) if local_path else None,
            timeout=self._parseInt(bg_cfg, cfg, "bg_req_timeout", 10),
            proxy=self._parseStr(bg_cfg, cfg, "bg_proxy") or None,
            preload_count=self._parseInt(bg_cfg, cfg, "bg_preload_count", 1),
            lolicon_r18_type=self._parseInt(bg_cfg, cfg, "bg_lolicon_r18_type", 0),
        )

        return await Background.resolve(prefer_bytes=background_bytes, request=request)

    async def _resolveAvatar(self, event: AstrMessageEvent, avatar_cfg: dict, cfg: dict) -> bytes | None:
        avatar_local_path = avatar_cfg.get("avatar_local_path") or cfg.get("avatar_local_path")
        if isinstance(avatar_local_path, str) and avatar_local_path.strip():
            try:
                return Path(avatar_local_path.strip()).read_bytes()
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

    def _parseInt(self, section: dict, cfg: dict, key: str, default: int) -> int:
        try:
            return int(section.get(key, cfg.get(key, default)))
        except Exception:
            return default

    def _parseStr(self, section: dict, cfg: dict, key: str) -> str:
        raw = section.get(key, cfg.get(key, ""))
        return raw.strip() if isinstance(raw, str) else ""

    async def terminate(self):
        logger.info("PicStatus 插件已卸载")
