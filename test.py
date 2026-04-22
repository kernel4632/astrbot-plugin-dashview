"""
这个文件是本地测试入口，用来模拟插件真正运行时的数据流。

它只做四件事：
1. 调用 Monitor.collect() 采集真实数据
2. 调用 Data.buildCollected() 整理模板数据
3. 调用 Render.build() 生成单文件 HTML
4. 把 HTML 写到 output_test.html，方便你本地直接打开查看效果

如果你改了模板、CSS、文案、卡片结构，最方便的验证方式就是运行这个文件。
最常见的用法只有一个：
python test.py
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from data import Data
from utils.monitor import Monitor
from utils.render import Render


ROOT = Path(__file__).parent
RESOURCES = ROOT / "resources"
OUTPUT = ROOT / "output_test.html"
AVATAR = RESOURCES / "avatar.jpg"
SERVICES = [
    {"name": "超级主核API", "type": "http", "url": "https://api.hujiarong.site/"},
    {"name": "主核Kernyr网站", "type": "http", "url": "https://www.hujiarong.site/"},
]


def readAvatar() -> bytes | None:
    """这个函数读取本地测试头像，没有头像时直接返回 None。"""
    if not AVATAR.exists():
        return None

    avatarBytes = AVATAR.read_bytes()
    print(f"加载头像: {AVATAR}")
    return avatarBytes


def printResult(computer: dict, services: list[dict], summary: dict):
    """这个函数把本次真实采集结果打印出来，方便你一边看页面一边看底层数据。"""
    print("\n=== 采集的真实数据 ===")
    print(f"主机名: {computer.get('hostName')}")
    print(f"系统: {computer.get('system')} {computer.get('systemVersion')} {computer.get('machine')}")
    print(f"Python版本: {computer.get('pythonVersion')}")
    print(f"开机时间: {computer.get('bootTime')}")
    print(f"CPU信息: {computer.get('cpu')}")
    print(f"内存信息: {computer.get('memory')}")
    print(f"磁盘信息: {computer.get('disk')}")
    print(f"服务摘要: {summary}")

    for service in services:
        print(f"服务 {service.get('name')}: ok={service.get('ok')} statusCode={service.get('statusCode')} target={service.get('target')} message={service.get('message')}")


async def main():
    """这个函数完整执行一次本地测试流程，并生成 output_test.html。"""
    print("正在采集真实系统信息...")
    result = Monitor.collect(services=SERVICES, timeout=5)
    computer = result["computer"]
    services = result["services"]
    summary = result["summary"]
    avatarBytes = readAvatar()
    collected = Data.buildCollected(computer=computer, services=services, summary=summary)
    html = Render.build(collected=collected, avatarBytes=avatarBytes)
    OUTPUT.write_text(html, encoding="utf-8")
    print(f"\nHTML文件已生成: {OUTPUT}")
    printResult(computer=computer, services=services, summary=summary)


if __name__ == "__main__":
    asyncio.run(main())
