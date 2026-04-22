"""
这个文件用来把一套 Jinja2 模板打包成一个完整的单文件 HTML 字符串。

它只做和“打包 HTML”有关的事情：
1. 读取模板和 CSS
2. 去掉页面里原本依赖外部资源的部分
3. 把 CSS 直接内联到页面里
4. 把头像直接转成 base64 嵌进页面
5. 注册模板渲染时需要的过滤器
6. 用整理好的 collected 数据渲染出最终 HTML

你可以把它理解成一个网页装箱器：
原本页面需要模板文件、CSS 文件、头像接口才能完整显示；
经过这个文件处理后，最终只剩一个 HTML 字符串，更适合 AstrBot 截图，也更适合本地直接打开预览。

最常见的调用方式：
HtmlBuilder.build(collected=data)
HtmlBuilder.build(collected=data, avatarBytes=avatarBytes)
HtmlBuilder.autoConvertUnit(1073741824)
"""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any, Optional

import jinja2
from markupsafe import Markup


class HtmlBuilder:
    """这个对象专门负责把页面资源整理成单文件 HTML。"""

    root = Path(__file__).parent.parent
    templateFolder = root / "resources" / "templates"
    cssFile = root / "resources" / "index.css"
    defaultAvatarFile = root / "resources" / "avatar.jpg"
    pageWidth = 900
    fontImport = '@import url("https://fonts.googleapis.com/css2?family=Noto+Sans+SC:wght@400;500;700;800&display=swap");'
    defaultConfig = {
        "ps_default_components": ["header", "resources", "services"],
        "ps_default_additional_css": [],
        "ps_default_additional_script": [],
    }

    @classmethod
    def build(cls, collected: dict[str, Any], avatarBytes: Optional[bytes] = None) -> str:
        """这个函数是统一入口，它会完整执行一次“读取 → 内联 → 渲染 → 返回 HTML”。"""
        print("正在生成单文件 HTML")
        macrosText = cls.readText(cls.templateFolder / "macros.html.jinja")
        indexText = cls.readText(cls.templateFolder / "index.html.jinja")
        cssText = cls.readText(cls.cssFile)
        indexText = cls.removeFirstMacroImport(indexText)
        indexText = cls.fixViewportWidth(indexText)
        indexText = cls.removeExternalScripts(indexText)
        indexText = cls.inlineCss(indexText, cssText)
        macrosText = cls.inlineAvatar(macrosText, avatarBytes)
        fullTemplateText = cls.joinTemplates(macrosText, indexText)
        templateEnv = cls.createTemplateEnv()
        template = templateEnv.from_string(fullTemplateText)
        html = template.render(d=collected, config=cls.defaultConfig)
        print(f"HTML 生成完成，长度为 {len(html)} 个字符")
        return html

    @classmethod
    def readText(cls, filePath: Path) -> str:
        """这个函数专门读取 UTF-8 文本文件，避免主流程里反复出现同样的读取代码。"""
        return filePath.read_text(encoding="utf-8")

    @classmethod
    def removeFirstMacroImport(cls, indexText: str) -> str:
        """这个函数去掉首页模板第一行的宏导入，因为我们后面会把宏模板直接拼到前面。"""
        lines = indexText.splitlines()
        if lines and lines[0].lstrip().startswith("{% from"):
            return "\n".join(lines[1:])
        return indexText

    @classmethod
    def fixViewportWidth(cls, indexText: str) -> str:
        """这个函数把视口宽度固定成设计稿宽度，避免截图时被挤压变形。"""
        return indexText.replace(
            'content="width=device-width, initial-scale=1.0"',
            f'content="width={cls.pageWidth}, initial-scale=1.0"',
        )

    @classmethod
    def removeExternalScripts(cls, indexText: str) -> str:
        """这个函数删除旧模板残留的外部脚本引用，保证最终页面是纯单文件。"""
        return indexText.replace('<script src="/js/init-global.js"></script>', "").replace('<script src="/js/lazy-load.js"></script>', "").replace('<script src="/js/load-plugin.js"></script>', "")

    @classmethod
    def inlineCss(cls, indexText: str, cssText: str) -> str:
        """这个函数把字体导入、页面样式和固定宽度修正一起塞进 style 标签。"""
        pageFixCss = f"html,body{{margin:0;padding:0;width:{cls.pageWidth}px;}}"
        styleTag = f"<style>\n{cls.fontImport}\n{cssText}\n{pageFixCss}\n</style>"
        return indexText.replace('<link rel="stylesheet" href="/default/res/css/index.css" />', styleTag)

    @classmethod
    def inlineAvatar(cls, macrosText: str, avatarBytes: Optional[bytes] = None) -> str:
        """这个函数把头像图片直接内联进模板，这样截图时就不需要额外请求头像接口。"""
        print("正在处理头像资源")
        finalAvatarBytes = avatarBytes

        if finalAvatarBytes is None and cls.defaultAvatarFile.exists():
            finalAvatarBytes = cls.defaultAvatarFile.read_bytes()  # 调用方没传头像时，自动回退到项目默认头像。

        if not finalAvatarBytes:
            print("没有可用头像，保留原始头像标签")
            return macrosText

        avatarBase64 = base64.b64encode(finalAvatarBytes).decode("ascii")
        avatarTag = f'<img class="hero-avatar" src="data:image/jpeg;base64,{avatarBase64}" alt="Avatar" />'
        replacedText = macrosText.replace(
            '<img class="hero-avatar" data-src="/api/bot_avatar/{{ info.self_id }}" alt="Avatar" />',
            avatarTag,
        )
        print("头像资源处理完成")
        return replacedText

    @classmethod
    def joinTemplates(cls, macrosText: str, indexText: str) -> str:
        """这个函数把宏模板和首页模板拼成一个完整模板字符串。"""
        return f"{macrosText}\n\n{indexText}"

    @classmethod
    def createTemplateEnv(cls) -> jinja2.Environment:
        """这个函数创建模板环境，并注册页面里会用到的过滤器。"""
        env = jinja2.Environment(autoescape=True)
        env.filters["bytes_to_human"] = cls.bytesToHumanFilter
        env.filters["safe"] = Markup
        return env

    @classmethod
    def autoConvertUnit(cls, value: float | int) -> str:
        """这个函数把字节数自动转换成更适合人看的单位，比如 MB、GB、TB。"""
        units = ["B", "KB", "MB", "GB", "TB", "PB"]
        number = float(value)
        unitIndex = 0

        while number >= 1024 and unitIndex < len(units) - 1:
            number /= 1024
            unitIndex += 1

        return f"{number:.1f} {units[unitIndex]}"

    @classmethod
    def bytesToHumanFilter(cls, value: Any) -> str:
        """这个函数是给模板用的过滤器，遇到异常值时也不会让模板渲染失败。"""
        try:
            return cls.autoConvertUnit(float(value))
        except Exception:
            return str(value)
