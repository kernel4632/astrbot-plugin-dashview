"""
这个文件是 AstrBot 插件的正式入口。

它只负责三类事件：
1. 用户发送“运行状态”，这里采集设备状态、服务状态、模型连通性，然后生成同一张仪表盘图片。
2. 用户发送“模型检测”，这里只换一个入口名，仍然复用同一条仪表盘渲染流程，让模型结果显示在模板底部卡片里。
3. 插件启动后的定时任务只做模型探测和历史记录，不主动推送图片；用户手动查看仪表盘时会看到过去一段时间的模型状态格子和速度曲线。

事件 → 指令 → 数据 → 反馈 的链条非常清楚：
用户命令 → cmd_status() 或 cmd_model_test() → buildDashboardImage() → Monitor.collect() 与 runModelProbeWithHistory() → Data.buildCollected() → Render.build() → Image.build() → event.image_result()
定时任务 → autoModelProbeLoop() → runModelProbeWithHistory() → put_kv_data() 保存历史 → 等用户下次查看仪表盘时展示。

如果你想改模型连通性怎么测，去看 utils/modelProbe.py。
如果你想改模型历史怎么保存，优先看本文件的 runModelProbeWithHistory()。
如果你想改模型结果怎么整理，去看 data.py。
如果你想改模型卡片长什么样，去看 resources/templates/macros.html.jinja 和 resources/index.css。
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from pathlib import Path
from time import time_ns
from typing import Any, Final

import httpx
from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register

from .data import Data
from .utils.image import Image
from .utils.modelProbe import ModelProbe
from .utils.monitor import Monitor
from .utils.render import Render


PLUGIN_NAME: Final[str] = "astrbot_plugin_dashview"
STATUS_ALIASES: Final[set[str]] = {"状态", "zt", "yxzt", "status", "运行状态"}
MODELTEST_ALIASES: Final[set[str]] = {"modelTest", "模型检测", "模型连通性", "检测模型"}
ROOT: Final[Path] = Path(__file__).parent
CACHE_FOLDER: Final[Path] = ROOT / "cache"
MODEL_HISTORY_KEY: Final[str] = "model_probe_history"
LATEST_MODEL_REPORT_KEY: Final[str] = "latest_model_probe_report"


@register(
    PLUGIN_NAME,
    "Kernyr",
    "以图片形式显示当前设备运行状态仪表盘，底部展示模型连通性检测卡片，支持定时模型探测历史",
    "1.0.8",
)
class DashViewPlugin(Star):
    """
    这个对象就是 AstrBot 真正会加载的插件对象。

    你最需要记住的入口：
    1. initialize()：插件加载时启动“只探测模型、不推送图片”的后台定时任务。
    2. cmd_status()：收到“运行状态”后生成完整仪表盘图片。
    3. cmd_model_test()：收到“模型检测”后复用完整仪表盘图片，模型结果在底部卡片展示。
    """

    def __init__(self, context: Context, config: dict | None = None):
        super().__init__(context)
        self.config = config if config is not None else {}
        self.autoModelProbeTask: asyncio.Task | None = None
        self.modelProbeLock = asyncio.Lock()

    async def initialize(self):
        """这个函数在插件加载时运行，读取配置后启动定时模型探测任务。"""
        logger.info("开始初始化 DashView 插件")
        self.startAutoModelProbeTask()
        logger.info("DashView 插件初始化完成")


    # =================================================================
    # 命令入口
    # =================================================================

    @filter.command("运行状态", alias=STATUS_ALIASES)
    async def cmd_status(self, event: AstrMessageEvent):
        """收到运行状态命令后，生成包含模型连通性历史卡片的完整仪表盘图片。"""
        logger.info("收到运行状态查询命令")

        async for result in self.sendDashboardImage(event, "运行状态"):
            yield result

    @filter.command("模型检测", alias=MODELTEST_ALIASES)
    async def cmd_model_test(self, event: AstrMessageEvent):
        """收到模型检测命令后，复用仪表盘渲染流程，并在底部展示模型连通性历史卡片。"""
        logger.info("收到模型连通性检测命令")
        yield event.plain_result("正在检测模型连通性并生成仪表盘图片，请稍等...")

        async for result in self.sendDashboardImage(event, "模型检测"):
            yield result


    # =================================================================
    # 仪表盘生成指令
    # =================================================================

    async def sendDashboardImage(self, event: AstrMessageEvent, actionName: str):
        """这个函数执行完整图片反馈流程：构建图片 → 失败返回文字 → 成功返回图片。"""
        imageToSend: str | None = None

        try:
            imageToSend = await self.buildDashboardImage(event)
        except Exception:
            logger.exception(f"{actionName}图片生成失败")
            yield event.plain_result(f"{actionName}图片生成失败，请检查后台输出")
            return

        if imageToSend is None:
            logger.error(f"{actionName}图片生成失败：imageToSend 未被设置")
            yield event.plain_result(f"{actionName}图片生成失败，请检查后台输出")
            return

        yield event.image_result(imageToSend)
        logger.info(f"{actionName}图片已成功发送")

    async def buildDashboardImage(self, event: AstrMessageEvent | None = None) -> str:
        """
        这个函数把系统检测、服务检测、模型检测、数据整理、HTML 渲染、图片保存串成一条线。
        模型检测会写入历史，所以页面里的曲线和状态格子能体现过去一段时间的故障。
        """
        config = getattr(self, "config", None) or {}

        logger.info("开始采集系统状态信息")
        servicesToCheck = self.readServices(config)
        timeout = self.readInt(config, "timeout", 5)
        result = Monitor.collect(services=servicesToCheck, timeout=timeout)

        logger.info("开始检测模型连通性并合并历史")
        modelReport = await self.runModelProbeWithHistory(config)

        logger.info("开始解析头像配置")
        avatarBytes = await self.resolveAvatar(event, config)

        collected = Data.buildCollected(
            computer=result["computer"],
            services=result["services"],
            summary=result["summary"],
            nickname=self.readText(config, "nickname", "阿柯AKer"),
            success_text=self.readText(config, "success_text", "阿柯牛逼"),
            fail_text=self.readText(config, "fail_text", "阿柯死了"),
            model_report=modelReport,
        )

        logger.info("开始生成单文件 HTML")
        html = Render.build(collected=collected, avatarBytes=avatarBytes)

        logger.info("开始使用本地 HTML 渲染器生成图片")
        imageBytes = await Image.build(html, width=900, quality=90)
        return self.saveImage(imageBytes)


    # =================================================================
    # 模型探测与历史记录
    # =================================================================

    def startAutoModelProbeTask(self) -> None:
        """这个函数在插件启动时创建定时模型探测任务；配置为 0 时不启动。"""
        intervalHours = self.modelProbeIntervalHours()
        if intervalHours <= 0:
            logger.info("模型定时探测未开启")
            return

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            logger.warning("模型定时探测启动失败：当前没有运行中的事件循环")
            return

        self.autoModelProbeTask = loop.create_task(self.autoModelProbeLoop())
        logger.info(f"模型定时探测已开启，间隔 {intervalHours:g} 小时")

    async def autoModelProbeLoop(self) -> None:
        """这个函数长期运行：只定时探测模型并保存历史，不发送任何图片或消息。"""
        if self.readBool(self.config, "model_probe_run_on_start", False):
            await self.runAutoModelProbeOnce()

        while True:
            intervalHours = self.modelProbeIntervalHours()
            if intervalHours <= 0:
                logger.info("模型定时探测已停止：间隔配置小于等于 0")
                return
            await asyncio.sleep(max(60.0, intervalHours * 3600))
            await self.runAutoModelProbeOnce()

    async def runAutoModelProbeOnce(self) -> None:
        """这个函数执行一次后台模型探测，只保存历史和最近报告。"""
        try:
            report = await self.runModelProbeWithHistory(self.config)
            logger.info(
                "模型定时探测完成：正常 %s，较慢 %s，错误 %s，总数 %s",
                report.get("okCount"),
                report.get("slowCount"),
                report.get("errorCount"),
                report.get("total"),
            )
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.warning(f"模型定时探测失败: {error}")

    async def runModelProbeWithHistory(self, config: dict[str, Any]) -> dict[str, Any]:
        """
        这个函数执行一次模型探测，追加历史，再把历史指标补回报告。
        它既给手动仪表盘使用，也给后台定时探测使用，所以用锁避免并发写历史互相覆盖。
        """
        async with self.modelProbeLock:
            report = await self.buildModelReport(config)
            history = await self.loadModelHistory()
            now = datetime.now()
            historySize = self.readInt(config, "model_history_size", 12)
            statsHours = self.readInt(config, "model_stats_window_hours", 24)

            for provider in report.get("providers", []):
                for item in provider.get("results", []):
                    historyKey = self.modelHistoryKey(provider, item)
                    records = history.get(historyKey, [])
                    if not isinstance(records, list):
                        records = []
                    records = self.pruneModelHistory(records, now, statsHours, historySize)
                    records.append({
                        "status": item.get("status", "error"),
                        "latencyMs": int(item.get("latencyMs", 0) or 0),
                        "checkedAt": now.isoformat(timespec="seconds"),
                    })
                    records = records[-max(historySize, 1):]
                    history[historyKey] = records
                    self.applyModelHistory(item, records, historySize, statsHours, now)

            await self.saveModelHistory(history)
            await self.put_kv_data(LATEST_MODEL_REPORT_KEY, report)
            return report

    async def buildModelReport(self, config: dict[str, Any]) -> dict[str, Any]:
        """这个函数只负责读取模型检测配置，然后调用 ModelProbe 做连通性检测。"""
        probeConfig = {
            "timeout": self.readInt(config, "model_timeout", 30),
            "concurrency": self.readInt(config, "model_concurrency", 10),
            "slow_ms": self.readInt(config, "model_slow_ms", 8000),
            "max_models": self.readInt(config, "model_max_models", 0),
        }
        return await ModelProbe.probe(self.context, probeConfig)

    async def loadModelHistory(self) -> dict[str, Any]:
        """这个函数从 AstrBot KV 里读取模型探测历史。"""
        try:
            data = await self.get_kv_data(MODEL_HISTORY_KEY, {})
            return data if isinstance(data, dict) else {}
        except Exception as error:
            logger.warning(f"读取模型探测历史失败: {error}")
            return {}

    async def saveModelHistory(self, history: dict[str, Any]) -> None:
        """这个函数把模型探测历史写入 AstrBot KV。"""
        try:
            await self.put_kv_data(MODEL_HISTORY_KEY, history)
        except Exception as error:
            logger.warning(f"保存模型探测历史失败: {error}")

    def modelHistoryKey(self, provider: dict[str, Any], item: dict[str, Any]) -> str:
        """这个函数生成稳定的模型历史键，按 Provider 分组和模型名区分。"""
        providerId = provider.get("groupId") or provider.get("displayName") or "unknown"
        model = item.get("model") or "unknown"
        return f"{providerId}::{model}"

    def pruneModelHistory(self, records: list[dict[str, Any]], now: datetime, statsHours: int, historySize: int) -> list[dict[str, Any]]:
        """这个函数裁剪过旧历史，避免 KV 无限增大。"""
        start = now - timedelta(hours=max(1, statsHours))
        kept: list[dict[str, Any]] = []
        for record in records:
            checkedAt = self.parseTime(record.get("checkedAt"))
            if checkedAt is None or checkedAt >= start:
                kept.append(record)
        return kept[-max(1, historySize):]

    def applyModelHistory(self, item: dict[str, Any], records: list[dict[str, Any]], historySize: int, statsHours: int, now: datetime) -> None:
        """这个函数把历史记录转换成页面要显示的状态格子、平均延迟、可用性和曲线数据。"""
        recent = records[-max(1, historySize):]
        paddedStatuses = ["empty"] * max(0, historySize - len(recent))
        item["history"] = paddedStatuses + [str(record.get("status") or "error") for record in recent]
        item["curvePoints"] = self.modelCurvePoints(recent)
        item["timeLabels"] = self.modelTimeLabels(recent)

        windowStart = now - timedelta(hours=max(1, statsHours))
        window = [record for record in records if (self.parseTime(record.get("checkedAt")) or now) >= windowStart]
        success = [record for record in window if record.get("status") in ("ok", "slow")]
        latencies = [int(record.get("latencyMs", 0) or 0) for record in success]
        item["avgLatencyText"] = f"{sum(latencies) // len(latencies)} ms" if latencies else "N/A"
        item["availability"] = f"{len(success) / len(window) * 100:.2f}%" if window else "0.00%"
        item["weeklySuccessText"] = f"{len(success)}/{len(window)}"

    def modelCurvePoints(self, records: list[dict[str, Any]]) -> list[dict[str, int]]:
        """这个函数把历史延迟转换成 0-100 宽、0-40 高的 SVG 曲线点。"""
        if not records:
            return []
        latencies = [int(record.get("latencyMs", 0) or 0) for record in records]
        maxLatency = max(max(latencies), 1000)
        if len(latencies) == 1:
            y = 40 - round(latencies[0] / maxLatency * 34)
            return [{"x": 0, "y": y}, {"x": 100, "y": y}]

        points: list[dict[str, int]] = []
        for index, latency in enumerate(latencies):
            x = round(index * 100 / (len(latencies) - 1))
            y = 40 - round(latency / maxLatency * 34)
            points.append({"x": x, "y": max(4, min(38, y))})
        return points

    def modelTimeLabels(self, records: list[dict[str, Any]]) -> list[str]:
        """这个函数生成曲线下方时间标签，取最早、中间、最新三段。"""
        if not records:
            return ["前", "中", "今"]
        picks = [records[0], records[len(records) // 2], records[-1]]
        labels: list[str] = []
        for record in picks:
            checkedAt = self.parseTime(record.get("checkedAt"))
            labels.append(checkedAt.strftime("%H:%M") if checkedAt else "--:--")
        return labels

    def parseTime(self, value: Any) -> datetime | None:
        """这个函数把 ISO 时间字符串转回 datetime，解析失败就返回 None。"""
        if not value:
            return None
        try:
            return datetime.fromisoformat(str(value))
        except ValueError:
            return None

    def modelProbeIntervalHours(self) -> float:
        """这个函数读取模型定时探测间隔，单位是小时，0 表示关闭。"""
        return self.readFloat(self.config, "model_probe_interval_hours", 0.0)


    # =================================================================
    # 配置读取与工具方法
    # =================================================================

    def readServices(self, config: dict[str, Any]) -> list[dict[str, Any]]:
        """这个函数读取服务列表；配置缺失或格式不对时，回退到内置服务。"""
        services = config.get("services") if isinstance(config, dict) else []
        return services if isinstance(services, list) else self.buildServices()

    def readInt(self, config: dict[str, Any], key: str, default: int) -> int:
        """这个函数把配置值安全转成整数，避免 WebUI 配置为空或字符串时报错。"""
        try:
            return int(config.get(key, default))
        except Exception:
            return default

    def readFloat(self, config: dict[str, Any], key: str, default: float) -> float:
        """这个函数把配置值安全转成小数，定时任务小时数会用到。"""
        try:
            return float(config.get(key, default))
        except Exception:
            return default

    def readBool(self, config: dict[str, Any], key: str, default: bool) -> bool:
        """这个函数把配置值安全转成布尔值，兼容 WebUI 里可能出现的字符串。"""
        value = config.get(key, default) if isinstance(config, dict) else default
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.lower() in ("1", "true", "yes", "on", "是", "开启")
        return bool(value)

    def readText(self, config: dict[str, Any], key: str, default: str) -> str:
        """这个函数读取文本配置，空字符串会回退到默认值。"""
        value = config.get(key) if isinstance(config, dict) else ""
        text = str(value).strip() if value is not None else ""
        return text if text else default

    def saveImage(self, imageBytes: bytes) -> str:
        """这个函数把渲染出的图片字节保存成本地文件，因为 AstrBot 的 image_result() 需要 URL 或路径字符串。"""
        CACHE_FOLDER.mkdir(parents=True, exist_ok=True)
        imagePath = CACHE_FOLDER / f"dashview_{time_ns()}.jpg"
        imagePath.write_bytes(imageBytes)
        return str(imagePath)

    def buildServices(self) -> list[dict[str, Any]]:
        """这个函数统一放要检测的服务列表，后面改目标网站时只需要改这里。"""
        return [
            {"name": "超级主核API", "type": "http", "url": "https://api.hujiarong.site/"},
            {"name": "主核Kernyr网站", "type": "http", "url": "https://www.hujiarong.site/"},
        ]

    async def resolveAvatar(self, event: AstrMessageEvent | None, config: dict[str, Any]) -> bytes | None:
        """这个函数按“本地路径 → 配置 URL → QQ 头像”的顺序解析最终头像。"""
        avatarLocalPath = config.get("avatar_local_path")
        if isinstance(avatarLocalPath, str) and avatarLocalPath.strip():
            try:
                return Path(avatarLocalPath.strip()).read_bytes()  # 本地路径最快也最稳定，所以优先读它。
            except Exception as error:
                logger.warning(f"DashView: 读取本地头像失败 {avatarLocalPath}: {error}")

        avatarUrl = config.get("avatar_url")
        if isinstance(avatarUrl, str) and avatarUrl.strip():
            return await self.downloadBytes(avatarUrl.strip(), "配置头像")

        if event is None:
            return None

        try:
            selfId = event.get_self_id()
            adapter = event.get_platform_name() or "AstrBot"
            if "qq" in adapter.lower() or "aiocqhttp" in adapter.lower():
                qqAvatarUrl = f"https://q1.qlogo.cn/g?b=qq&nk={selfId}&s=640"
                return await self.downloadBytes(qqAvatarUrl, "QQ头像")
        except Exception:
            return None  # 平台不支持或事件对象没有这些方法时，直接回退到默认头像即可。

        return None

    async def downloadBytes(self, url: str, name: str) -> bytes | None:
        """这个函数专门下载二进制内容，头像和其他远程资源都可以复用它。"""
        try:
            async with httpx.AsyncClient(follow_redirects=True, timeout=5) as client:
                response = await client.get(url)
                response.raise_for_status()  # 这里直接抛错最清楚，失败原因会被外层日志完整记录。
                logger.info(f"成功获取{name}")
                return response.content
        except Exception as error:
            logger.warning(f"DashView: 获取{name}失败 {url}: {error}")
            return None

    async def terminate(self):
        """这个函数在插件卸载时运行，会取消模型定时探测任务，避免卸载后后台还在循环。"""
        if self.autoModelProbeTask and not self.autoModelProbeTask.done():
            self.autoModelProbeTask.cancel()
            try:
                await self.autoModelProbeTask
            except asyncio.CancelledError:
                pass
        logger.info("DashView 插件已卸载")
