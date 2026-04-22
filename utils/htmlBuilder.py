"""
这个文件用来把一套 Jinja2 模板打包成一个完整的单文件 HTML 字符串。

它会做下面几件事：
1. 读取主模板、宏模板、CSS 文件
2. 去掉模板里依赖外部文件的部分，比如外部 JS、外部 CSS
3. 把 CSS 直接写进 <style>，让页面不再依赖额外样式文件
4. 处理头像资源，把头像直接塞进 HTML
5. 注册模板里会用到的过滤器
6. 用 collected 数据渲染模板，返回最终 HTML

你可以把它理解成一个“网页打包器”：
原本页面需要模板文件、CSS 文件、头像接口、JS 文件才能完整显示；
经过这里处理后，最终只返回一个 HTML 字符串，适合截图、导出、静态展示。
现在字体改成了 CDN 方案，页面会优先加载远程中文字体，加载失败时再回退到系统字体。
"""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any, Optional

import jinja2
from markupsafe import Markup


class HtmlBuilder:
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
    def build(
        cls,
        collected: dict[str, Any],
        avatarBytes: Optional[bytes] = None,
    ) -> str:
        print("正在生成单文件 HTML")
        macrosText = cls.readText(cls.templateFolder / "macros.html.jinja")
        indexText = cls.readText(cls.templateFolder / "index.html.jinja")
        cssText = cls.readText(cls.cssFile)
        indexText = cls.removeFirstMacroImport(indexText)
        indexText = cls.fixViewportWidth(indexText)
        indexText = cls.removeExternalScripts(indexText)
        indexText = cls.inlineCss(indexText, cssText)
        macrosText = cls.inlineAvatar(macrosText, avatarBytes)
        fullTemplateText = cls.joinMacrosAndIndex(macrosText, indexText)
        templateEnv = cls.createTemplateEnv()
        template = templateEnv.from_string(fullTemplateText)
        html = template.render(d=collected, config=cls.defaultConfig)
        print(f"HTML 生成完成，长度为 {len(html)} 个字符")
        return html

    @classmethod
    def readText(cls, filePath: Path) -> str:
        return filePath.read_text(encoding="utf-8")

    @classmethod
    def removeFirstMacroImport(cls, indexText: str) -> str:
        lines = indexText.splitlines()
        if lines and lines[0].lstrip().startswith("{% from"):
            return "\n".join(lines[1:])
        return indexText

    @classmethod
    def fixViewportWidth(cls, indexText: str) -> str:
        return indexText.replace(
            'content="width=device-width, initial-scale=1.0"',
            f'content="width={cls.pageWidth}, initial-scale=1.0"',
        )

    @classmethod
    def removeExternalScripts(cls, indexText: str) -> str:
        return indexText.replace('<script src="/js/init-global.js"></script>', "").replace('<script src="/js/lazy-load.js"></script>', "").replace('<script src="/js/load-plugin.js"></script>', "")

    @classmethod
    def inlineCss(cls, indexText: str, cssText: str) -> str:
        pageFixCss = f"html,body{{margin:0;padding:0;width:{cls.pageWidth}px;}}"
        styleTag = f"<style>\n{cls.fontImport}\n{cssText}\n{pageFixCss}\n</style>"
        return indexText.replace(
            '<link rel="stylesheet" href="/default/res/css/index.css" />',
            styleTag,
        )

    @classmethod
    def inlineAvatar(cls, macrosText: str, avatarBytes: Optional[bytes] = None) -> str:
        print("正在处理头像资源")
        finalAvatarBytes = avatarBytes

        if finalAvatarBytes is None and cls.defaultAvatarFile.exists():
            finalAvatarBytes = cls.defaultAvatarFile.read_bytes()

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
    def joinMacrosAndIndex(cls, macrosText: str, indexText: str) -> str:
        return f"{macrosText}\n\n{indexText}"

    @classmethod
    def createTemplateEnv(cls) -> jinja2.Environment:
        env = jinja2.Environment(autoescape=True)
        env.filters["bytes_to_human"] = cls.bytesToHumanFilter
        env.filters["safe"] = Markup
        return env

    @classmethod
    def autoConvertUnit(cls, value: float | int) -> str:
        units = ["B", "KB", "MB", "GB", "TB", "PB"]
        number = float(value)
        unitIndex = 0

        while number >= 1024 and unitIndex < len(units) - 1:
            number /= 1024
            unitIndex += 1

        return f"{number:.1f} {units[unitIndex]}"

    @classmethod
    def bytesToHumanFilter(cls, value: Any) -> str:
        try:
            return cls.autoConvertUnit(float(value))
        except Exception:
            return str(value)
