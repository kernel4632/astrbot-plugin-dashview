"""
这个文件是本地测试入口，用来模拟插件真正运行时的数据流。

它只做四件事：
1. 调用 Monitor.collect() 采集本机真实状态和服务状态
2. 构造一份假的模型连通性检测结果，专门用来预览底部模型卡片
3. 调用 Data.buildCollected() 整理模板数据，再调用 Render.build() 生成单文件 HTML
4. 把 HTML 渲染成 output_test.jpg，让你直接打开图片确认最终效果

为什么模型检测这里用假数据：
本地运行 test.py 时通常没有 AstrBot 的 context，也拿不到 WebUI 里的 Provider。
所以这个文件不做真实模型请求，只模拟三种不同长度的随机大幅波动历史数据。
真实插件运行时，main.py 会定时调用 ModelProbe.probe() 写入历史，用户查看仪表盘时会看到同样结构的数据。

最常见的用法只有一个：
python test.py

运行后会生成两个文件：
output_test.html  可以用浏览器打开看页面结构
output_test.jpg   可以直接查看机器人最终会发送的图片效果
"""

from __future__ import annotations

import asyncio
import random
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


def randomHistory(count: int, baseStatus: str) -> list[str]:
    """
    这个函数随机生成指定数量的历史状态格子。
    baseStatus 是主状态，随机混入少量其他状态。
    """
    statuses: list[str] = []
    for _ in range(count):
        roll = random.random()
        if baseStatus == "ok" and roll < 0.15:
            statuses.append("slow")
        elif baseStatus == "slow" and roll < 0.3:
            statuses.append(random.choice(["ok", "error"]))
        elif baseStatus == "error" and roll < 0.25:
            statuses.append("slow")
        else:
            statuses.append(baseStatus)
    return statuses


def randomCurvePoints(count: int, baseLatency: int) -> list[dict[str, int]]:
    """
    这个函数生成大幅波动的随机曲线点。
    每个点的延迟在基准值的 0.4 到 1.6 倍之间大幅波动。
    曲线坐标映射到 0-100 宽 / 0-40 高的 SVG 坐标。
    """
    maxLatency = max(1000, int(baseLatency * 1.8))
    points: list[dict[str, int]] = []
    for index in range(count):
        x = round(index * 100 / (count - 1)) if count > 1 else 0
        # 大幅随机波动：基准值的 0.4 到 1.6 倍
        jitter = random.uniform(0.4, 1.6)
        latencyValue = int(baseLatency * jitter)
        y = 40 - round(latencyValue / maxLatency * 34)
        points.append({"x": x, "y": max(4, min(38, y))})
    return points


def randomTimeLabels(count: int) -> list[str]:
    """这个函数生成时间轴标签，这里用假数字表示探测序号。"""
    if count <= 2:
        return [f"#{i+1}" for i in range(count)]
    return [f"#{1}", f"#{count // 2 + 1}", f"#{count}"]


def buildFakeModelReport() -> dict[str, Any]:
    """
    这个函数构造一份和 ModelProbe.probe() 加历史合并之后同形状的假数据。

    三个模型分别用不同数量的历史格子（4、6、5），
    每个模型的延迟都大幅随机波动，曲线点数量等于格子数量。
    这样你能同时看到三种长度状态格子的展示效果。
    """
    startedAt = time()
    random.seed(42)

    # 三个模型分别定义基础参数
    modelDefs = [
        {"model": "gpt-4o-mini", "status": "ok", "baseLatency": 1200, "historyCount": 4},
        {"model": "gpt-4o", "status": "slow", "baseLatency": 5200, "historyCount": 6},
        {"model": "o3-mini", "status": "error", "baseLatency": 15000, "historyCount": 5},
    ]

    results: list[dict[str, Any]] = []
    for definition in modelDefs:
        history = randomHistory(definition["historyCount"], definition["status"])
        curvePoints = randomCurvePoints(definition["historyCount"], definition["baseLatency"])
        latencies = [int(point["y"] * definition["baseLatency"] / 34) for point in curvePoints]
        avgLatency = sum(latencies) // len(latencies) if latencies else definition["baseLatency"]
        okCount = sum(1 for s in history if s == "ok")
        errorCount = sum(1 for s in history if s == "error")

        results.append({
            "model": definition["model"],
            "status": definition["status"],
            "latencyMs": latencies[-1],
            "replyPreview": "OK" if definition["status"] != "error" else "",
            "avgLatencyText": f"{avgLatency} ms",
            "availability": f"{okCount / len(history) * 100:.2f}%",
            "weeklySuccessText": f"{okCount}/{len(history)}",
            "history": history,
            "curvePoints": curvePoints,
            "timeLabels": randomTimeLabels(definition["historyCount"]),
            "error": "TimeoutError: 连接超时" if definition["status"] == "error" else "",
        })

    okCount = sum(1 for r in results if r["status"] == "ok")
    slowCount = sum(1 for r in results if r["status"] == "slow")
    errorCount = sum(1 for r in results if r["status"] == "error")

    # Provider 分组状态取决于是否包含错误模型
    groupStatus = "error" if errorCount > 0 else ("slow" if slowCount > 0 else "ok")
    groupLabel = {"ok": "正常", "slow": "较慢", "error": "异常"}[groupStatus]

    providers = [
        {
            "groupId": "openai-main",
            "displayName": "OpenAI 主线路",
            "modelCount": len(results),
            "okCount": okCount,
            "slowCount": slowCount,
            "errorCount": errorCount,
            "status": groupStatus,
            "statusLabel": groupLabel,
            "results": results,
        },
    ]

    return {
        "title": "模型连通性",
        "checkedAt": "2026-05-03 20:00:00",
        "elapsedMs": int((time() - startedAt) * 1000) + 1250,
        "total": len(results),
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
        for item in provider.get("results", []):
            print(f"  {item.get('model')}: {item.get('status')} 延迟 {item.get('latencyMs')}ms 格子数 {len(item.get('history', []))}")


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
