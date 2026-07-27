"""
图片展示器：复用一个受控 Chromium，把离线 HTML 渲染成清晰 JPEG。

浏览器不会运行期下载、不会关闭沙箱、不会访问网络；渲染串行执行并限制页面最大高度。
调用示例：image_bytes = await render_image(html); await close_browser()
"""

from __future__ import annotations                         # 允许现代类型注解

import asyncio                                             # 保护浏览器启动与页面渲染生命周期
from typing import Any                                     # 保存 Playwright 动态对象

from playwright.async_api import Browser, Playwright, async_playwright # 使用真实浏览器渲染 CSS


PAGE_WIDTH = 900                                           # 与静态仪表盘设计宽度一致
START_HEIGHT = 720                                         # 首次布局使用足够的可视高度
MAX_HEIGHT = 2800                                          # 覆盖八服务八模型的最坏布局并阻止失控图片
DEVICE_SCALE = 1.5                                         # 1350px 输出兼顾聊天清晰度和文件体积

_playwright: Playwright | None = None                       # 进程内复用的 Playwright 驱动
_browser: Browser | None = None                             # 进程内复用的 Chromium 浏览器
_start_lock = asyncio.Lock()                                # 多个首请求只允许启动一次浏览器
_render_lock = asyncio.Lock()                               # 单浏览器串行截图避免抢占内存


# --- 把离线 HTML 渲染成 JPEG ---
async def render_image(html: str, quality: int = 88) -> bytes:
    async with _render_lock:                                # 一次只渲染一张，限制峰值内存
        browser = await _get_browser()                      # 启动也纳入渲染锁，关闭不会中途抢占
        page = await browser.new_page(
            viewport={"width": PAGE_WIDTH, "height": START_HEIGHT},
            device_scale_factor=DEVICE_SCALE,               # 输出像素高于 CSS 像素以适配聊天缩放
        )
        try:
            await page.route("**/*", lambda route: route.abort()) # 单文件页面不允许任何网络请求
            await page.set_content(html, wait_until="load") # 内联资源加载完成即可，无需等待网络空闲
            await page.evaluate("document.fonts.ready")    # 等系统中文字体完成排版再测量高度
            height = await page.evaluate("Math.ceil(document.documentElement.scrollHeight)")
            if int(height) > MAX_HEIGHT:
                raise ValueError(f"仪表盘内容高度 {height}px 超过 {MAX_HEIGHT}px，请降低详情显示上限")
            await page.set_viewport_size({"width": PAGE_WIDTH, "height": max(START_HEIGHT, int(height))})
            return await page.screenshot(type="jpeg", quality=quality, full_page=True)
        finally:
            await page.close()                              # 页面是一次性资源，浏览器继续复用


# --- 获取或启动共享浏览器 ---
async def _get_browser() -> Browser:
    global _playwright, _browser                            # 生命周期集中在本模块两个变量
    if _browser is not None and _browser.is_connected():
        return _browser                                     # 正常浏览器直接复用
    async with _start_lock:                                 # 再次确认避免等待锁期间重复启动
        if _browser is not None and _browser.is_connected():
            return _browser
        old_browser, old_playwright = _browser, _playwright # 断开实例必须先完整回收
        _browser = None
        _playwright = None
        try:
            if old_browser is not None:
                await old_browser.close()                   # 清理失联浏览器残余子进程
        finally:
            if old_playwright is not None:
                await old_playwright.stop()                 # 驱动始终与旧浏览器一起回收

        playwright = await async_playwright().start()       # 先保存在局部变量，成功后才发布
        try:
            browser = await playwright.chromium.launch(headless=True)
        except BaseException:
            await playwright.stop()                         # 启动失败或取消时不泄漏驱动进程
            raise
        _playwright = playwright                            # 两项都成功后形成可复用全局状态
        _browser = browser
        return browser


# --- 关闭共享浏览器 ---
async def close_browser() -> None:
    global _playwright, _browser                            # 卸载插件时释放浏览器进程
    async with _render_lock:                                # 等当前截图完成后才允许关闭浏览器
        async with _start_lock:
            browser, playwright = _browser, _playwright     # 先清空全局，关闭失败也可重新启动
            _browser = None
            _playwright = None
            try:
                if browser is not None:
                    await browser.close()                   # 先关闭页面与 Chromium 子进程
            finally:
                if playwright is not None:
                    await playwright.stop()                 # 浏览器关闭失败也必须停止驱动
