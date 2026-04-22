from __future__ import annotations
import os
from pathlib import Path
from typing import Final

import astrbot.api.message_components as Comp
import httpx
from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register

from .bg_provider import BackgroundRequest, resolve_background
from .collectors import collect_all
from .utils import ensure_dir
from .py_renderer import render_py


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
        ensure_dir(CACHE_DIR)
        self.config = config if config is not None else {}

    async def initialize(self):
        logger.info("开始初始化 PicStatus 插件")
        cfg = getattr(self, "config", None)
        if not (hasattr(cfg, "get") and hasattr(cfg, "__setitem__")):
            logger.info("PicStatus 插件初始化完成")
            return

        # 确保 use_t2i 配置存在
        if not isinstance(cfg.get("use_t2i"), bool):
            cfg["use_t2i"] = True

        avatar_cfg = cfg.get("avatar")
        bg_cfg = cfg.get("background")
        changed = False
        logger.info("开始处理头像和背景配置")

        if not isinstance(avatar_cfg, dict):
            avatar_cfg = {}
            cfg["avatar"] = avatar_cfg
            changed = True

        if not isinstance(bg_cfg, dict):
            bg_cfg = {}
            cfg["background"] = bg_cfg
            changed = True

        def _get_str(v) -> str:
            return v.strip() if isinstance(v, str) else ""

        def _get_int(v):
            if isinstance(v, bool):
                return None
            if isinstance(v, int):
                return v
            if isinstance(v, str):
                try:
                    return int(v.strip())
                except Exception:
                    return None
            return None

        def _migrate_str(section: dict, key: str, *, default_new: str = ""):
            nonlocal changed
            old_val = _get_str(cfg.get(key))
            new_val = _get_str(section.get(key))
            if old_val and (not new_val or new_val == default_new):
                section[key] = old_val
                changed = True

        def _migrate_int(section: dict, key: str, *, default_new: int):
            nonlocal changed
            old_val = _get_int(cfg.get(key))
            new_val = _get_int(section.get(key))
            if old_val is None:
                return
            if old_val != default_new and (new_val is None or new_val == default_new):
                section[key] = old_val
                changed = True

        _migrate_str(avatar_cfg, "avatar_local_path", default_new="")
        _migrate_str(avatar_cfg, "avatar_url", default_new="")
        _migrate_str(avatar_cfg, "avatar_text", default_new="AstrBot")

        _migrate_str(bg_cfg, "bg_provider", default_new="loli")
        _migrate_str(bg_cfg, "bg_fallback_chain", default_new="local")
        _migrate_str(bg_cfg, "bg_local_path", default_new="")
        _migrate_str(bg_cfg, "bg_proxy", default_new="")
        _migrate_int(bg_cfg, "bg_preload_count", default_new=1)
        _migrate_int(bg_cfg, "bg_req_timeout", default_new=10)
        _migrate_int(bg_cfg, "bg_lolicon_r18_type", default_new=0)

        if changed and hasattr(cfg, "save_config"):
            logger.info("配置有变更，准备保存配置")
            try:
                cfg.save_config()
                logger.info("配置保存成功")
            except Exception as e:
                logger.warning(f"PicStatus: 保存配置失败: {e}")

        logger.info("PicStatus 插件初始化完成")

    @filter.command("运行状态", alias=ALIASES)
    async def cmd_status(self, event: AstrMessageEvent):
        """生成并发送当前服务器运行状态图片"""
        logger.info("收到运行状态查询命令")
        # t2i_error 用於標記 AstrBot t2i 渲染階段的錯誤，使外層錯誤處理可以給出更精準提示。
        t2i_error: Exception | None = None
        image_to_send: str | bytes | None = None
        try:
            logger.info("开始收集系统状态信息")
            collected = await collect_all(context=self.context)
            logger.info("系统状态信息收集完成")
            # Provide header bots info for template compatibility
            try:
                logger.info("开始处理 Bot 信息")
                self_id = event.get_self_id()
                adapter = event.get_platform_name() or "AstrBot"

                # 1) 头像右侧文字：留空使用默认 "AstrBot"，填写则使用用户配置
                cfg = getattr(self, "config", None) or {}
                avatar_cfg = cfg.get("avatar") if isinstance(cfg, dict) else None
                avatar_cfg = avatar_cfg if isinstance(avatar_cfg, dict) else {}
                bot_nick: str = "AstrBot"
                if hasattr(cfg, "get"):
                    raw = avatar_cfg.get("avatar_text") or cfg.get("avatar_text")
                    if isinstance(raw, str):
                        raw = raw.strip()
                        if raw:
                            bot_nick = raw

                bots = [
                    {
                        "self_id": self_id,
                        "nick": bot_nick,
                        "adapter": adapter,
                        "bot_connected": collected.get("bot_run_time", ""),
                        "msg_rec": 0,
                        "msg_sent": 0,
                    }
                ]
            except Exception:
                logger.warning("Bot 信息处理失败")
                bots = []
            collected.setdefault("bots", bots)

            # prefer user image in message chain
            bg_bytes = None
            logger.info("检查消息中是否包含用户提供的图片")
            try:
                for seg in event.get_messages():
                    if isinstance(seg, Comp.Image):
                        f = getattr(seg, "file", None) or ""
                        if isinstance(f, str) and f.startswith(("http://", "https://")):
                            async with httpx.AsyncClient(
                                follow_redirects=True, timeout=5
                            ) as cli:
                                r = await cli.get(f)
                                r.raise_for_status()
                                bg_bytes = r.content
                                logger.info("成功获取用户提供的背景图片")
                                break
            except Exception:
                logger.warning("处理用户图片失败")

            cfg = getattr(self, "config", None) or {}
            bg_cfg = cfg.get("background") if isinstance(cfg, dict) else None
            bg_cfg = bg_cfg if isinstance(bg_cfg, dict) else {}
            logger.info("开始解析背景图片配置")

            provider = (
                bg_cfg.get("bg_provider")
                or cfg.get("bg_provider")
                or os.getenv("PICSTATUS_BG_PROVIDER", "loli")
            )
            local_path = (
                bg_cfg.get("bg_local_path")
                or cfg.get("bg_local_path")
                or os.getenv("PICSTATUS_BG_LOCAL_PATH", "")
            )
            fallback_chain = (
                bg_cfg.get("bg_fallback_chain") or cfg.get("bg_fallback_chain") or ""
            )

            def _parse_int(key: str, default: int) -> int:
                try:
                    return int(bg_cfg.get(key, cfg.get(key, default)))
                except Exception:
                    return default

            def _parse_str(key: str) -> str:
                raw = bg_cfg.get(key, cfg.get(key, ""))
                return raw.strip() if isinstance(raw, str) else ""

            provider_chain: list[str] = []
            for item in [provider, *str(fallback_chain).split(",")]:
                name = str(item).strip().lower()
                if not name:
                    continue
                if name not in provider_chain:
                    provider_chain.append(name)
            if not provider_chain:
                provider_chain = ["loli"]

            logger.info(f"背景图片提供者链: {provider_chain}")
            request = BackgroundRequest(
                provider_chain=tuple(provider_chain),
                local_path=Path(local_path) if local_path else None,
                timeout=_parse_int("bg_req_timeout", 10),
                proxy=_parse_str("bg_proxy") or None,
                preload_count=_parse_int("bg_preload_count", 1),
                lolicon_r18_type=_parse_int("bg_lolicon_r18_type", 0),
            )
            resolved = await resolve_background(
                prefer_bytes=bg_bytes,
                request=request,
            )
            logger.info("背景图片解析完成")
            
            # 尝试获取 Bot 头像：优先用配置的本地/URL，其次自动推断 Bot 自己头像
            avatar_bytes = None
            avatar_url = None
            logger.info("开始处理 Bot 头像")

            avatar_cfg = cfg.get("avatar") if isinstance(cfg, dict) else None
            avatar_cfg = avatar_cfg if isinstance(avatar_cfg, dict) else {}

            avatar_local_path = avatar_cfg.get("avatar_local_path") or cfg.get(
                "avatar_local_path"
            )
            if isinstance(avatar_local_path, str) and avatar_local_path.strip():
                try:
                    avatar_bytes = Path(avatar_local_path.strip()).read_bytes()
                    logger.info(f"成功读取本地头像: {avatar_local_path}")
                except Exception as e:
                    logger.warning(
                        f"PicStatus: 读取本地头像失败 {avatar_local_path}: {e}"
                    )

            if avatar_bytes is None:
                cfg_url = avatar_cfg.get("avatar_url") or cfg.get("avatar_url")
                if isinstance(cfg_url, str) and cfg_url.strip():
                    avatar_url = cfg_url.strip()
                    logger.info(f"使用配置的头像 URL")


            if avatar_bytes is None and avatar_url is None:
                try:
                    if "qq" in adapter.lower() or "aiocqhttp" in adapter.lower():
                        avatar_url = (
                            f"https://q1.qlogo.cn/g?b=qq&nk={self_id}&s=640"
                        )
                        logger.info(f"使用 QQ 头像 URL")
                except Exception:
                    pass

            if avatar_bytes is None and avatar_url:
                try:
                    async with httpx.AsyncClient(
                        follow_redirects=True, timeout=5
                    ) as cli:
                        r = await cli.get(avatar_url)
                        r.raise_for_status()
                        avatar_bytes = r.content
                        logger.info("成功获取头像")
                except Exception as e:
                    logger.warning(f"PicStatus: 获取头像失败 {avatar_url}: {e}")
                    avatar_bytes = None

            # 根据配置开关选择渲染方式
            use_t2i = cfg.get("use_t2i", True) if isinstance(cfg, dict) else True
            logger.info(f"使用 t2i 渲染器: {use_t2i}")
            
            if use_t2i:
                # 使用 AstrBot t2i 渲染器
                try:
                    logger.info("开始尝试使用 AstrBot t2i 渲染器")
                    from .t2i_renderer import build_default_html

                    html = build_default_html(
                        collected, resolved.data, resolved.mime, avatar_bytes=avatar_bytes
                    )
                    logger.info("开始生成 HTML 页面")
                    # 使用 t2i ultra 档位提高清晰度，宽度仍由模板与 viewport 控制
                    options = {
                        "type": "jpeg",
                        "quality": 90,
                        "full_page": True,
                        "device_scale_factor_level": "ultra",
                    }
                    out_url = await self.html_render(
                        html, {}, return_url=True, options=options
                    )
                    image_to_send = out_url
                    logger.info("HTML 页面已渲染，准备发送图片")
                    logger.info("使用 AstrBot t2i 渲染器成功")
                except Exception as e:
                    t2i_error = e
                    logger.warning(f"PicStatus: AstrBot t2i renderer failed, reason: {e}")
            else:
                # 使用 Python PIL 渲染器
                try:
                    logger.info("开始尝试使用 Python PIL 渲染器")
                    image_bytes = render_py(
                        collected,
                        resolved.data,
                        avatar_bytes=avatar_bytes,
                        bots=collected.get("bots", []),
                    )
                    # 保存到临时文件
                    import tempfile
                    import uuid
                    temp_dir = CACHE_DIR / "temp"
                    ensure_dir(temp_dir)
                    temp_file = temp_dir / f"picstatus_{uuid.uuid4().hex}.png"
                    temp_file.write_bytes(image_bytes)
                    image_to_send = str(temp_file)
                    logger.info(f"Python PIL 渲染成功，保存到 {temp_file}")
                except Exception as e:
                    t2i_error = e
                    logger.warning(f"PicStatus: Python PIL renderer failed, reason: {e}")
        except Exception:
            logger.exception("生成运行状态图片失败")
            msg = "获取运行状态图片失败，请检查后台输出"
            if t2i_error:
                msg += "（渲染失败）"
            yield event.plain_result(msg)
            return

        if image_to_send is None:
            logger.error("图片生成失败：image_to_send 未被设置")
            yield event.plain_result("图片生成失败，请检查后台输出")
            return

        yield event.image_result(image_to_send)
        logger.info("运行状态图片已成功发送")

    async def terminate(self):
        logger.info("PicStatus 插件已卸载")
