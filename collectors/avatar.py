"""
头像采集器：按本地配置、远程配置、QQ 机器人头像、默认头像的顺序读取图片。

所有外部图片都限制协议、响应类型和字节大小；失败只返回 None，由展示层使用默认头像。
调用示例：avatar = await collect_avatar(event, settings)
"""

from __future__ import annotations                         # 允许现代类型注解

import asyncio                                             # 本地文件读取放入工作线程
from pathlib import Path                                   # 安全读取用户明确配置的文件
from typing import Any                                     # 兼容不同 AstrBot 消息事件

import httpx                                               # 异步下载远程头像

from ..config import Settings                              # 使用已验证的头像配置


# --- 解析最终头像 ---
async def collect_avatar(event: Any | None, settings: Settings) -> bytes | None:
    if settings.avatar_local_path:                         # 本地头像是用户最明确的选择
        local_bytes = await _read_local_avatar(settings.avatar_local_path, settings.avatar_max_bytes)
        if local_bytes:
            return local_bytes                             # 有效本地图片不再触发网络请求

    avatar_url = settings.avatar_url or _qq_avatar_url(event) # 未配置 URL 时仅对 QQ 尝试自动头像
    if avatar_url:
        return await _download_avatar(avatar_url, settings.avatar_max_bytes)
    return None                                            # 展示层会嵌入仓库内默认头像


# --- 读取受限大小的本地头像 ---
async def _read_local_avatar(file_name: str, max_bytes: int) -> bytes | None:
    path = Path(file_name).expanduser()                     # 允许用户使用家目录路径
    try:
        if not path.is_file() or path.stat().st_size > max_bytes:
            return None                                    # 非文件和超大文件都不读入内存
        return await asyncio.to_thread(path.read_bytes)     # 文件系统延迟不阻塞事件循环
    except OSError:
        return None                                        # 权限或路径错误回退默认头像


# --- 下载受限大小的远程头像 ---
async def _download_avatar(url: str, max_bytes: int) -> bytes | None:
    if not url.startswith(("http://", "https://")):       # 头像只允许标准 Web 协议
        return None
    try:
        async with httpx.AsyncClient(timeout=5, follow_redirects=True) as client:
            async with client.stream("GET", url) as response:
                response.raise_for_status()                 # 非成功响应不作为图片处理
                if not response.headers.get("content-type", "").lower().startswith("image/"):
                    return None                             # 防止把 HTML 或其他文件嵌进页面
                chunks: list[bytes] = []                    # 分块读取才能在下载途中限制大小
                size = 0                                    # 记录实际收到的字节数
                async for chunk in response.aiter_bytes():
                    size += len(chunk)                      # 每个分块到达后立刻更新总量
                    if size > max_bytes:
                        return None                         # 超限立即停止读取
                    chunks.append(chunk)                    # 合法分块等待最终合并
                return b"".join(chunks) or None             # 空响应不能作为头像
    except httpx.HTTPError:
        return None                                        # 网络失败由默认头像兜底


# --- 从 QQ 事件生成机器人头像地址 ---
def _qq_avatar_url(event: Any | None) -> str:
    if event is None:
        return ""                                          # 后台任务没有消息平台上下文
    try:
        platform_name = str(event.get_platform_name() or "").lower()
        self_id = str(event.get_self_id() or "").strip()
    except Exception:
        return ""                                          # 不同平台缺少接口时安静回退
    if self_id and ("qq" in platform_name or "aiocqhttp" in platform_name):
        return f"https://q1.qlogo.cn/g?b=qq&nk={self_id}&s=640"
    return ""                                              # 非 QQ 平台使用内置默认头像
