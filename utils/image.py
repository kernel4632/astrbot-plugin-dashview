"""
这个文件是本地 HTML 转图片渲染器，用来替代已经失效的官方 t2i 接口。

它只接收完整 HTML 字符串，然后用 Playwright 启动本地无头 Chromium 按真实浏览器规则渲染页面，最后截图成 JPEG 字节返回。
这样模板、CSS、渐变、圆角、布局都继续由 resources/templates 和 resources/index.css 定义，不在这里重写视觉效果。

Docker 里会自动安装 requirements.txt 里的 playwright。第一次渲染时如果还没有 Chromium，这个文件会自动执行 python -m playwright install chromium 下载浏览器。
如果 Docker 完全禁止运行期下载浏览器，就需要提前在镜像里执行同一条安装命令，或者把 PLAYWRIGHT_BROWSERS_PATH 指到已有浏览器缓存目录。

最常见的调用方式：
imageBytes = await Image.build(html)
imageBytes = await Image.build(html, width=900, quality=95)
await Image.save(html, Path("output_test.jpg"))
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import async_playwright


class Image:
    """这个对象专门负责把 HTML 原样渲染成图片。"""

    width = 900
    minHeight = 480
    scale = 2
    waitMilliseconds = 300
    browserArgs = [
        "--no-sandbox",
        "--disable-setuid-sandbox",
        "--disable-dev-shm-usage",
        "--disable-gpu",
        "--hide-scrollbars",
        "--font-render-hinting=none",
    ]

    @classmethod
    async def build(cls, html: str, width: int | None = None, quality: int = 95) -> bytes:
        """这个函数是统一入口，完整执行一次“打开浏览器 → 填入 HTML → 计算高度 → 截图 → 返回字节”。"""
        try:
            return await cls.render(html=html, width=width, quality=quality)
        except PlaywrightError as error:
            if "Executable doesn't exist" not in str(error):
                raise
            await cls.installBrowser()
            return await cls.render(html=html, width=width, quality=quality)

    @classmethod
    async def render(cls, html: str, width: int | None = None, quality: int = 95) -> bytes:
        """这个函数执行真正的浏览器渲染；拆出来是为了缺浏览器时安装后可以重试一次。"""
        finalWidth = int(width or cls.width)
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True, args=cls.browserArgs)
            try:
                page = await browser.new_page(viewport={"width": finalWidth, "height": cls.minHeight, "device_scale_factor": cls.scale})
                await page.set_content(html, wait_until="networkidle")
                await page.wait_for_timeout(cls.waitMilliseconds)
                height = await cls.measureHeight(page)
                await page.set_viewport_size({"width": finalWidth, "height": height})
                imageBytes = await page.screenshot(type="jpeg", quality=quality, full_page=True)
                return imageBytes
            finally:
                await browser.close()

    @classmethod
    async def save(cls, html: str, outputPath: Path, width: int | None = None, quality: int = 95) -> Path:
        """这个函数用于本地测试，把 HTML 渲染后的图片写入文件，方便直接打开看真实效果。"""
        outputPath.write_bytes(await cls.build(html=html, width=width, quality=quality))
        return outputPath

    @classmethod
    async def measureHeight(cls, page: Any) -> int:
        """这个函数读取页面真实内容高度，让截图刚好包住完整仪表盘。"""
        height = await page.evaluate(
            """() => Math.max(
                document.body.scrollHeight,
                document.body.offsetHeight,
                document.documentElement.clientHeight,
                document.documentElement.scrollHeight,
                document.documentElement.offsetHeight
            )"""
        )
        return max(int(height), cls.minHeight)

    @classmethod
    async def installBrowser(cls):
        """这个函数在缺少 Chromium 时自动安装一次，避免 Docker 只装 Python 包后第一次运行就失败。"""
        process = await asyncio.create_subprocess_exec(sys.executable, "-m", "playwright", "install", "chromium")
        code = await process.wait()
        if code != 0:
            raise RuntimeError("Playwright Chromium 安装失败，请在 Docker 镜像里预先执行 python -m playwright install chromium")

    @classmethod
    def buildSync(cls, html: str, width: int | None = None, quality: int = 95) -> bytes:
        """这个函数给同步环境使用；插件命令本身是 async，所以优先用 await Image.build(html)。"""
        return asyncio.run(cls.build(html=html, width=width, quality=quality))
