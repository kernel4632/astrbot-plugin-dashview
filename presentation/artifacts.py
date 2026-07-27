"""
图片产物：把待发送 JPEG 放进系统临时目录，并按数量清理旧文件。

插件目录不再积累图片，也不依赖旧版本缓存；AstrBot 发送期间最近文件始终保留。
调用示例：image_path = save_image(image_bytes, keep_count=3)
"""

from __future__ import annotations                         # 允许现代类型注解

import tempfile                                            # 使用操作系统认可的临时文件目录
from pathlib import Path                                   # 创建与清理图片路径
from time import time_ns                                   # 并发截图生成不重复文件名


CACHE_DIR = Path(tempfile.gettempdir()) / "astrbot_dashview" # 插件更新不会覆盖系统临时产物


# --- 保存发送图片并清理旧产物 ---
def save_image(image_bytes: bytes, keep_count: int) -> str:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)            # 首次发送时创建专用临时目录
    image_path = CACHE_DIR / f"dashview_{time_ns()}.jpg"   # 纳秒时间避免并发文件名冲突
    image_path.write_bytes(image_bytes)                     # 完整写入后才把路径交给 AstrBot

    images = sorted(CACHE_DIR.glob("dashview_*.jpg"), key=lambda path: path.stat().st_mtime, reverse=True)
    for old_image in images[max(1, keep_count):]:           # 始终保留当前图片和指定历史数量
        try:
            old_image.unlink()                              # 临时图片不应无限消耗磁盘
        except OSError:
            continue                                        # 清理失败不影响当前图片发送
    return str(image_path)                                  # AstrBot image_result 接收本地路径
