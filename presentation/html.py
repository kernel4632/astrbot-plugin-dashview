"""
HTML 展示器：用 Jinja2 模板和内联 CSS 生成完全离线的单文件仪表盘。

模板负责结构，CSS 负责视觉，头像转成 data URL；浏览器渲染时不需要访问任何网络资源。
调用示例：html = render_html(view, avatar_bytes)
"""

from __future__ import annotations                         # 允许现代类型注解

import base64                                              # 把头像嵌入离线 HTML
from pathlib import Path                                   # 定位模板、CSS 和默认头像
from typing import Any                                     # 描述完整视图字典

import jinja2                                              # 安全渲染静态 HTML 模板


ROOT = Path(__file__).parent.parent                         # 插件根目录包含 resources
TEMPLATE_DIR = ROOT / "resources" / "templates"           # 页面结构文件目录
CSS_FILE = ROOT / "resources" / "index.css"               # 页面唯一样式文件
DEFAULT_AVATAR = ROOT / "resources" / "avatar.jpg"        # 所有平台都可回退的内置头像


# --- 渲染离线仪表盘 HTML ---
def render_html(view: dict[str, Any], avatar_bytes: bytes | None) -> str:
    css = CSS_FILE.read_text(encoding="utf-8")              # 样式内联后浏览器不访问文件系统
    css = css.replace("\tbackdrop-filter: blur(14px);\n", "").replace("\t-webkit-backdrop-filter: blur(14px);\n", "") # 长图截图禁用会产生白块的合成层
    final_avatar = avatar_bytes or DEFAULT_AVATAR.read_bytes() # 缺少平台头像时使用项目品牌头像
    avatar_base64 = base64.b64encode(final_avatar).decode("ascii")
    avatar_mime = _image_mime(final_avatar)                 # 正确 MIME 让 PNG/WebP 配置头像稳定显示
    environment = jinja2.Environment(
        loader=jinja2.FileSystemLoader(TEMPLATE_DIR),       # 模板 import 由标准加载器处理
        autoescape=jinja2.select_autoescape(("html", "jinja")), # 用户名称和错误文本默认转义
        trim_blocks=True,                                   # 减少无意义空白，控制 HTML 体积
        lstrip_blocks=True,                                 # 模板缩进不进入最终文本
    )
    template = environment.get_template("index.html.jinja")
    return template.render(d=view, css=css, avatar_base64=avatar_base64, avatar_mime=avatar_mime)


# --- 根据图片文件头识别内联 MIME ---
def _image_mime(image_bytes: bytes) -> str:
    if image_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"                                 # PNG 使用固定八字节签名
    if image_bytes.startswith(b"RIFF") and image_bytes[8:12] == b"WEBP":
        return "image/webp"                                # WebP 位于 RIFF 容器中
    if image_bytes.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"                                 # GIF 两种常见版本共享处理
    return "image/jpeg"                                    # 默认头像和最常见配置均为 JPEG
