"""
这个文件是本地测试入口，用来模拟插件真正运行时的数据流。

它只做四件事：
1. 调用 Monitor.collect() 采集本机真实状态和服务状态
2. 构造一份假的模型连通性检测结果，专门用来预览底部模型卡片
3. 调用 Data.buildCollected() 整理模板数据，再调用 Render.build() 生成单文件 HTML
4. 把 HTML 渲染成 output_test.jpg，让你直接打开图片确认最终效果

为什么模型检测这里用假数据：
本地运行 test.py 时通常没有 AstrBot 的 context，也拿不到 WebUI 里的 Provider。
所以这个文件不做真实模型请求，只模拟正常、较慢、错误三种状态，方便你看卡片样式。
真实插件运行时，main.py 会调用 ModelProbe.probe() 生成同样结构的数据。

最常见的用法只有一个：
python test.py

运行后会生成两个文件：
output_test.html  可以用浏览器打开看页面结构
output_test.jpg   可以直接查看机器人最终会发送的图片效果
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from time import time
from typing import Any

from data import Data
from utils.image import Image
from utils.monitor import Monitor
from utils.render import Render


ROOT = Path(__file__).parent
RESOURCES = ROOT / "resources"
OUTPUT_HTML = ROOT / "output_test.html"
OUTPUT_IMAGE = ROOT / "output_test.jpg"
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


def buildFakeModelReport() -> dict[str, Any]:
    """
    这个函数构造一份和 ModelProbe.probe() 返回值同形状的假数据。

    里面故意放了三类模型：
    - ok：正常模型，用绿色展示
    - slow：较慢模型，用黄色展示
    - error：异常模型，用红色展示，并显示错误原因

    这样你不用真的配置模型，也能看到模板底部所有状态的展示效果。
    """
    startedAt = time()
    providers = [
        {
            "groupId": "openai-main",
            "displayName": "OpenAI 主线路",
            "modelCount": 3,
            "okCount": 2,
            "slowCount": 1,
            "errorCount": 0,
            "status": "slow",
            "statusLabel": "较慢",
            "results": [
                {
                    "model": "gpt-4o-mini",
                    "status": "ok",
                    "latencyMs": 1260,
                    "replyPreview": "OK",
                    "avgLatencyText": "1130 ms",
                    "availability": "100.00%",
                    "weeklySuccessText": "12/12",
                    "history": ["ok", "ok", "ok", "ok", "ok", "ok", "ok", "ok", "ok", "ok", "ok", "ok"],
                    "curvePoints": [{"x": 0, "y": 27}, {"x": 16, "y": 20}, {"x": 33, "y": 25}, {"x": 50, "y": 13}, {"x": 66, "y": 22}, {"x": 83, "y": 10}, {"x": 100, "y": 18}],
                    "timeLabels": ["18:00", "19:00", "20:00"],
                    "error": "",
                },
                {
                    "model": "gpt-4o",
                    "status": "ok",
                    "latencyMs": 2380,
                    "replyPreview": "OK",
                    "avgLatencyText": "2140 ms",
                    "availability": "100.00%",
                    "weeklySuccessText": "12/12",
                    "history": ["ok", "ok", "ok", "ok", "slow", "ok", "ok", "ok", "ok", "ok", "ok", "ok"],
                    "curvePoints": [{"x": 0, "y": 30}, {"x": 16, "y": 23}, {"x": 33, "y": 19}, {"x": 50, "y": 26}, {"x": 66, "y": 15}, {"x": 83, "y": 21}, {"x": 100, "y": 16}],
                    "timeLabels": ["18:00", "19:00", "20:00"],
                    "error": "",
                },
                {
                    "model": "o3-mini",
                    "status": "slow",
                    "latencyMs": 9360,
                    "replyPreview": "OK",
                    "avgLatencyText": "8420 ms",
                    "availability": "91.67%",
                    "weeklySuccessText": "11/12",
                    "history": ["ok", "ok", "slow", "ok", "ok", "slow", "ok", "slow", "ok", "ok", "slow", "slow"],
                    "curvePoints": [{"x": 0, "y": 31}, {"x": 16, "y": 24}, {"x": 33, "y": 27}, {"x": 50, "y": 16}, {"x": 66, "y": 21}, {"x": 83, "y": 8}, {"x": 100, "y": 12}],
                    "timeLabels": ["18:00", "19:00", "20:00"],
                    "error": "",
                },
            ],
        },
    ]
    allModels = [model for provider in providers for model in provider["results"]]
    okCount = sum(1 for model in allModels if model["status"] == "ok")
    slowCount = sum(1 for model in allModels if model["status"] == "slow")
    errorCount = sum(1 for model in allModels if model["status"] == "error")

    return {
        "title": "模型连通性",
        "checkedAt": "2026-05-03 20:00:00",
        "elapsedMs": int((time() - startedAt) * 1000) + 1250,
        "total": len(allModels),
        "okCount": okCount,
        "slowCount": slowCount,
        "errorCount": errorCount,
        "providerCount": len(providers),
        "providers": providers,
        "allOk": errorCount == 0,
    }


def printResult(computer: dict[str, Any], services: list[dict[str, Any]], summary: dict[str, Any], modelReport: dict[str, Any]) -> None:
    """这个函数把本次采集和模拟模型结果打印出来，方便你一边看图片一边看底层数据。"""
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

    print("\n=== 模拟的模型连通性数据 ===")
    print(f"Provider 数: {modelReport.get('providerCount')}，模型数: {modelReport.get('total')}，正常: {modelReport.get('okCount')}，较慢: {modelReport.get('slowCount')}，错误: {modelReport.get('errorCount')}")
    for provider in modelReport.get("providers", []):
        print(f"Provider {provider.get('displayName')}: {provider.get('statusLabel')}，模型数 {provider.get('modelCount')}")


async def main() -> None:
    """这个函数完整执行一次本地测试流程，并同时生成 HTML 和由 HTML 渲染出来的图片。"""
    print("正在采集真实系统信息...")
    result = Monitor.collect(services=SERVICES, timeout=5)
    computer = result["computer"]
    services = result["services"]
    summary = result["summary"]

    print("正在构造模型连通性预览数据...")
    modelReport = buildFakeModelReport()
    avatarBytes = readAvatar()

    collected = Data.buildCollected(
        computer=computer,
        services=services,
        summary=summary,
        nickname="阿柯AKer",
        success_text="阿柯牛逼",
        fail_text="阿柯死了",
        model_report=modelReport,
    )
    html = Render.build(collected=collected, avatarBytes=avatarBytes)
    OUTPUT_HTML.write_text(html, encoding="utf-8")
    await Image.save(html=html, outputPath=OUTPUT_IMAGE, width=900, quality=95)

    print(f"\nHTML文件已生成: {OUTPUT_HTML}")
    print(f"HTML渲染图片已生成: {OUTPUT_IMAGE}")
    printResult(computer=computer, services=services, summary=summary, modelReport=modelReport)


if __name__ == "__main__":
    asyncio.run(main())
