"""
这个文件用来把一套 Jinja2 模板打包成一个完整的单文件 HTML 字符串。

它会做下面几件事：
1. 读取主模板、宏模板、CSS 文件
2. 去掉模板里依赖外部文件的部分，比如外部 JS、外部 CSS
3. 把 CSS 直接写进 <style>，让页面不再依赖额外样式文件
4. 把背景图和头像转成 base64，直接塞进 HTML
5. 注册模板里会用到的过滤器
6. 用 collected 数据渲染模板，返回最终 HTML

你可以把它理解成一个“网页打包器”：
原本页面需要模板文件、CSS 文件、背景图、头像接口、JS 文件才能完整显示；
经过这里处理后，最终只返回一个 HTML 字符串，适合截图、导出、静态展示、无网络环境展示。

最常见的调用方式如下：

    from htmlBuilder import HtmlBuilder

    html = HtmlBuilder.build(
        collected=collected_data,
        backgroundBytes=Path("bg.jpg").read_bytes(),
        backgroundMime="image/jpeg",
        avatarBytes=Path("avatar.png").read_bytes(),
    )

    html = HtmlBuilder.build(
        collected=collected_data,
        backgroundBytes=b"",
        backgroundMime="image/jpeg",
        avatarBytes=None,
    )

    html = HtmlBuilder.build(
        collected={"info": {"name": "demo"}},
        backgroundBytes=Path("bg.webp").read_bytes(),
        backgroundMime="image/webp",
    )

改这个文件时可以按这条线去看：
调用 build() 是事件；
build() 里依次调用的每个方法是指令；
模板文本、CSS 文本、图片字节、配置字典是数据；
最后 return html 就是反馈。

如果以后你要改样式来源、改默认组件、改头像替换规则、改背景替换方式，
都可以直接在这个文件里搜索对应的方法名，不需要跳到很多文件里追踪。
"""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any, Optional

import jinja2
from markupsafe import Markup


class HtmlBuilder:
    """
    这个对象专门负责“生成最终 HTML”。

    你只需要关心一个主入口：

        HtmlBuilder.build(collected, backgroundBytes, backgroundMime, avatarBytes)

    它接收页面数据和可选图片，返回一个完整 HTML 字符串。

    常见调用例子：

        html = HtmlBuilder.build(
            collected=data,
            backgroundBytes=Path("bg.jpg").read_bytes(),
            backgroundMime="image/jpeg",
            avatarBytes=Path("avatar.webp").read_bytes(),
        )

        html = HtmlBuilder.build(
            collected=data,
            backgroundBytes=b"",
            backgroundMime="image/jpeg",
            avatarBytes=None,
        )

        html = HtmlBuilder.build(
            collected={"info": {"self_id": 10001}},
            backgroundBytes=Path("bg.png").read_bytes(),
            backgroundMime="image/png",
        )

        html = HtmlBuilder.build(
            collected=data,
            backgroundBytes=Path("bg.webp").read_bytes(),
            backgroundMime="image/webp",
            avatarBytes=Path("avatar.jpg").read_bytes(),
        )
    """

    # 这里保存当前文件所在目录，后续读模板和资源都从这里出发。
    root = Path(__file__).parent.parent

    # 宏模板和主页模板都放在这里。
    templateFolder = root / "resources" / "templates"

    # 默认 CSS 文件放在这里。
    cssFile = root / "resources" / "css" / "index.css"

    # 默认头像文件放在这里。如果调用时没有传头像，就尝试使用它。
    defaultAvatarFile = root / "resources" / "assets" / "default_avatar.webp"

    # 这个宽度不是随便写的。原始代码已经明确说明：
    # 固定为 900 可以避免 full_page 截图时右侧出现白边。
    pageWidth = 900

    # 这是模板渲染时附带的默认配置。
    # 模板里如果依赖 config.ps_default_components 之类的值，就从这里拿。
    defaultConfig = {
        "ps_default_components": ["header", "resources", "services"],
        "ps_default_additional_css": [],
        "ps_default_additional_script": [],
    }

    @classmethod
    def build(
        cls,
        collected: dict[str, Any],
        backgroundBytes: bytes,
        backgroundMime: str = "image/jpeg",
        avatarBytes: Optional[bytes] = None,
    ) -> str:
        """
        生成一个完整的单文件 HTML，并把它作为字符串返回。

        输入是什么：
        - collected 是模板渲染需要的数据，模板里通过 d 来读取
        - backgroundBytes 是背景图的二进制内容，可以为空
        - backgroundMime 是背景图的 MIME 类型，比如 image/jpeg、image/png
        - avatarBytes 是头像图的二进制内容，可以不传

        输出是什么：
        - 返回一个已经渲染完成的 HTML 字符串

        这个函数是整个文件的主入口，也是最符合“事件 → 指令 → 数据 → 反馈”的地方：
        - 事件：其他文件调用 HtmlBuilder.build(...)
        - 指令：按顺序处理模板、CSS、背景、头像、过滤器、渲染
        - 数据：模板文本、CSS 文本、图片字节、collected 数据、默认配置
        - 反馈：返回最终 html
        """
        print("正在生成单文件 HTML")

        # 先把模板和 CSS 从磁盘读出来，后面所有替换都基于这些原始文本进行。
        macrosText = cls.readText(cls.templateFolder / "macros.html.jinja")
        indexText = cls.readText(cls.templateFolder / "index.html.jinja")
        cssText = cls.readText(cls.cssFile)

        # 按自然生成顺序一步步处理主模板，让阅读者能顺着数据流看下去。
        indexText = cls.removeFirstMacroImport(indexText)
        indexText = cls.fixViewportWidth(indexText)
        indexText = cls.removeExternalScripts(indexText)
        indexText = cls.inlineCss(indexText, cssText)
        indexText = cls.inlineBackground(indexText, backgroundBytes, backgroundMime)

        # 头像定义写在宏模板里，所以头像内联要处理 macrosText，而不是处理 indexText。
        macrosText = cls.inlineAvatar(macrosText, avatarBytes)

        # 宏模板内容直接拼在主模板前面，这样 index 里调用 {{ header(d) }} 时能直接生效。
        fullTemplateText = cls.joinMacrosAndIndex(macrosText, indexText)

        # 模板环境和过滤器是“渲染规则”，单独集中放，方便以后扩展。
        templateEnv = cls.createTemplateEnv()
        template = templateEnv.from_string(fullTemplateText)

        # 模板里约定用 d 表示 collected，用 config 表示页面配置。
        html = template.render(d=collected, config=cls.defaultConfig)

        print(f"HTML 生成完成，长度为 {len(html)} 个字符")
        return html

    @classmethod
    def readText(cls, filePath: Path) -> str:
        """读取一个 utf-8 文本文件并返回字符串。"""
        return filePath.read_text(encoding="utf-8")

    @classmethod
    def removeFirstMacroImport(cls, indexText: str) -> str:
        """
        去掉主模板第一行的宏导入语句。

        原始模板通常会写类似：
            {% from "macros.html.jinja" import header %}

        但这里我们会把宏模板文本直接拼到主模板最前面，
        所以这个 import 就不再需要了。保留它反而可能导致引用路径问题。
        """
        lines = indexText.splitlines()

        # 只有第一行真的像 Jinja2 的 from-import 时才删，避免误删正文内容。
        if lines and lines[0].lstrip().startswith("{% from"):
            return "\n".join(lines[1:])

        return indexText

    @classmethod
    def fixViewportWidth(cls, indexText: str) -> str:
        """
        把移动端自适应 viewport 改成固定宽度 viewport。

        这不是为了“更现代”，而是为了“更稳定”：
        原始代码明确说明，固定为 900 宽可以减少 full_page 截图时右侧白边问题。
        所以这里保留这个行为，并把宽度统一收口到 pageWidth 这个常量。
        """
        return indexText.replace(
            'content="width=device-width, initial-scale=1.0"',
            f'content="width={cls.pageWidth}, initial-scale=1.0"',
        )

    @classmethod
    def removeExternalScripts(cls, indexText: str) -> str:
        """
        移除页面中依赖外部文件的 JS 标签。

        这个文件的目标是生成“单文件 HTML”。
        单文件的重点就是：拿到一个 html 字符串后，不再去请求别的脚本文件。
        所以这里主动删掉初始化脚本、懒加载脚本、插件加载脚本。
        """
        return indexText.replace('<script src="/js/init-global.js"></script>', "").replace('<script src="/js/lazy-load.js"></script>', "").replace('<script src="/js/load-plugin.js"></script>', "")

    @classmethod
    def inlineCss(cls, indexText: str, cssText: str) -> str:
        """
        把外链 CSS 改成内联 <style>。

        这样生成出来的 HTML 不需要再依赖 index.css 文件，
        同时顺手把 html 和 body 的宽度固定为 pageWidth，进一步减少截图白边。
        """
        pageFixCss = f"html,body{{margin:0;padding:0;width:{cls.pageWidth}px;}}"
        styleTag = f"<style>\n{cssText}\n{pageFixCss}\n</style>"

        return indexText.replace(
            '<link rel="stylesheet" href="/default/res/css/index.css" />',
            styleTag,
        )

    @classmethod
    def inlineBackground(cls, indexText: str, backgroundBytes: bytes, backgroundMime: str) -> str:
        """
        把背景图直接写进页面的 style 属性里。

        如果 backgroundBytes 为空，就不强行加图，
        而是继续使用 CSS 里本来定义好的渐变背景。
        这样做的好处是：即使调用方没有传图片，页面也不会变成空白背景。
        """

        # 没有背景图时直接返回原模板，保持默认 CSS 背景生效。
        if not backgroundBytes:
            return indexText

        backgroundBase64 = base64.b64encode(backgroundBytes).decode("ascii")
        backgroundStyle = f'<div class="main-background" style="background-image:url(\'data:{backgroundMime};base64,{backgroundBase64}\')">'

        return indexText.replace('<div class="main-background">', backgroundStyle)

    @classmethod
    def inlineAvatar(cls, macrosText: str, avatarBytes: Optional[bytes]) -> str:
        """
        把头像的懒加载地址改成真正的内联 src。

        原模板通常把头像写成 data-src，等 JS 去懒加载。
        但我们已经把外部 JS 删掉了，所以这里必须把头像直接换成 src，
        这样页面在没有 JS 的情况下仍然能正常显示头像。
        """
        print("正在处理头像资源")

        # 如果调用方没传头像，就尝试使用默认头像文件。
        if avatarBytes is None:
            avatarBytes = cls.readDefaultAvatarBytes()

        # 默认头像也没有时，不报错，直接保留原模板。
        # 这样做的目的是让“头像缺失”不会阻断整张页面生成。
        if not avatarBytes:
            print("没有可用头像，跳过头像内联")
            return macrosText

        avatarMime = cls.detectImageMime(avatarBytes)
        avatarBase64 = base64.b64encode(avatarBytes).decode("ascii")
        avatarSrc = f'src="data:{avatarMime};base64,{avatarBase64}"'

        replacedText = macrosText.replace(
            'data-src="/api/bot_avatar/{{ info.self_id }}"',
            avatarSrc,
        )

        print("头像资源处理完成")
        return replacedText

    @classmethod
    def readDefaultAvatarBytes(cls) -> bytes:
        """
        读取默认头像文件。

        这里故意做成“失败返回空字节”而不是抛异常，
        因为默认头像只是兜底资源，不应该让它决定主流程成败。
        """
        try:
            return cls.defaultAvatarFile.read_bytes()
        except Exception:
            return b""

    @classmethod
    def detectImageMime(cls, imageBytes: bytes) -> str:
        """
        根据图片字节开头的魔数，大致判断图片 MIME 类型。

        这是一个很实用的小技巧：
        文件后缀名可能不可靠，但文件头前几个字节通常更可信。
        这里支持 png、jpeg、webp，已经覆盖了常见头像格式。
        判断不了时默认按 png 返回，保证 data URL 仍然能构造出来。
        """

        # PNG 文件固定以这 8 个字节开头。
        if imageBytes.startswith(b"\x89PNG\r\n\x1a\n"):
            return "image/png"

        # JPEG 文件通常以 FF D8 FF 开头。
        if imageBytes.startswith(b"\xff\xd8\xff"):
            return "image/jpeg"

        # WEBP 常见特征是前面有 RIFF，前 16 字节里出现 WEBP。
        if imageBytes.startswith(b"RIFF") and b"WEBP" in imageBytes[:16]:
            return "image/webp"

        return "image/png"

    @classmethod
    def joinMacrosAndIndex(cls, macrosText: str, indexText: str) -> str:
        """
        把宏模板拼到主模板前面，组成最终模板文本。

        这是这份实现里最关键的“去外部依赖”动作之一：
        不再靠 Jinja2 去找单独的宏文件，而是把宏内容直接放到模板开头，
        这样 from_string() 就能一次性渲染完整模板。
        """
        return macrosText + "\n" + indexText

    @classmethod
    def createTemplateEnv(cls) -> jinja2.Environment:
        """
        创建 Jinja2 渲染环境，并把模板会用到的过滤器全部注册进去。

        过滤器集中注册有两个好处：
        1. 以后想加模板能力，只改这里就能找到入口
        2. 模板里出现 |percent_to_color 这类用法时，能快速反查实现位置
        """
        env = jinja2.Environment(
            autoescape=jinja2.select_autoescape(["html", "xml"]),
        )

        env.filters.update(
            percent_to_color=cls.percentToColor,
            auto_convert_unit=cls.autoConvertUnit,
            format_cpu_freq=cls.formatCpuFreq,
            br=cls.brFilter,
        )
        return env

    @classmethod
    def percentToColor(cls, percent: float) -> str:
        """
        按百分比返回进度颜色等级名。

        这是给模板用的简单规则：
        - 小于 70：低等级
        - 70 到 90 之间：中等级
        - 90 及以上：高等级
        """
        if percent < 70:
            return "prog-low"

        if percent < 90:
            return "prog-medium"

        return "prog-high"

    @classmethod
    def autoConvertUnit(
        cls,
        value: float,
        suffix: str = "",
        withSpace: bool = False,
        unitIndex: int | None = None,
    ) -> str:
        """
        把数字按 1024 进制自动转换成 B、KB、MB、GB、TB。

        这类格式化常用于内存、硬盘、网络流量展示。

        例子：
            autoConvertUnit(512) -> "512B"
            autoConvertUnit(2048) -> "2KB"
            autoConvertUnit(1048576, withSpace=True) -> "1 MB"
        """
        units = ["B", "KB", "MB", "GB", "TB"]
        index = 0
        number = float(value)

        # unitIndex 没指定时，自动往上换单位，直到数字小于 1024 或到达最大单位。
        while unitIndex is None and number >= 1024 and index < len(units) - 1:
            number /= 1024
            index += 1

        # unitIndex 指定时，强制使用指定单位下标。
        if unitIndex is not None:
            index = unitIndex

        space = " " if withSpace else ""
        return f"{number:.0f}{space}{units[index]}{suffix}"

    @classmethod
    def formatCpuFreq(cls, freq: Any) -> str:
        """
        把 psutil 风格的 CPU 频率对象格式化成更好读的文本。

        这里不强依赖具体类型，只要求 freq 有 current 和 max 这两个属性。
        这样即使外部传来的不是严格的 CpuFreq 类型，只要结构相同也能工作。

        显示规则：
        - 小于 1000MHz：显示为 XXXMHz
        - 大于等于 1000MHz：显示为 X.XXGHz
        - 如果同时有 max，就显示 “当前 / 最大”
        """

        def formatSingleValue(number: float | None) -> str:
            """把一个 MHz 数字格式化成 MHz 或 GHz。"""
            if not number:
                return "未知"

            if number >= 1000:
                return f"{number / 1000:.2f}GHz"

            return f"{number:.0f}MHz"

        currentText = formatSingleValue(getattr(freq, "current", None))
        maxValue = getattr(freq, "max", None)

        if maxValue not in (None, 0):
            return f"{currentText} / {formatSingleValue(maxValue)}"

        return currentText

    @classmethod
    def brFilter(cls, value: Any) -> Markup:
        """
        把字符串中的换行符替换成 <br />，再标记为安全 HTML。

        这个过滤器常用于模板里直接输出多行文本。
        如果不做这一步，浏览器默认会把换行折叠掉，看起来会像一整行。
        """
        if value is None:
            return Markup("")

        return Markup(str(value).replace("\n", "<br />"))
