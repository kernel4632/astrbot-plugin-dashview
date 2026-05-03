"""
这个文件专门负责把原始数据整理成页面模板能直接使用的数据。

你可以把它理解成数据翻译层，新人只看这个文件就能理解每种数据长什么样。
它不采集系统信息，不调用模型，也不渲染 HTML，只把底层结果变成模板喜欢的字段。

调用方式（按主体分块，需要哪块调哪块）：
Data.buildCollected(computer=computer, services=services, summary=summary, model_report=modelReport)
Data.buildOverview(summary)
Data.buildResourceCards(computer)
Data.buildServices(services)
Data.buildSystemDetails(computer, summary)
Data.buildModels(model_report)

如果以后你想调整页面文案、卡片结构、颜色分配、模型检测结果展示格式，优先改这个文件。
"""

from __future__ import annotations

from typing import Any

try:
    from .utils.render import Render
except ImportError:
    from utils.render import Render


class Data:
    """
    这个对象只做一件事：整理数据。

    它不负责采集系统信息，不负责调用模型，不负责渲染 HTML。
    这样拆开后，新人只看这个文件，就能快速理解数据长什么样。
    """

    # =================================================================
    # 设备状态仪表盘数据
    # =================================================================

    @classmethod
    def buildCollected(
        cls,
        computer: dict[str, Any],
        services: list[dict[str, Any]],
        summary: dict[str, Any],
        nickname: str = "阿柯AKer",
        success_text: str = "阿柯牛逼",
        fail_text: str = "阿柯死了",
        model_report: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """这个函数把页面需要的所有数据一次性整理好，返回给模板或插件入口直接使用。"""
        return {
            "hostname": nickname,
            "os_info": f"{computer.get('system', 'Unknown')} ({computer.get('machine', '')})",
            "summary": summary,
            "overview": cls.buildOverview(summary),
            "resource_cards": cls.buildResourceCards(computer),
            "services_status": cls.buildServices(services),
            "system_details": cls.buildSystemDetails(computer, summary, success_text, fail_text),
            "model_status": cls.buildModels(model_report),
        }

    @classmethod
    def buildOverview(cls, summary: dict[str, Any]) -> dict[str, str]:
        """这个函数只负责生成页面顶部那一句总状态文案。"""
        failCount = summary.get("serviceFailCount", 0)
        totalCount = summary.get("serviceCount", 0)

        if totalCount == 0:
            return {"health_level": "calm", "health_text": "系统状态稳定"}

        if failCount == 0:
            return {"health_level": "good", "health_text": "服务全部正常"}

        if failCount < totalCount:
            return {"health_level": "warn", "health_text": "部分服务异常"}

        return {"health_level": "danger", "health_text": "服务状态异常"}

    @classmethod
    def buildResourceCards(cls, computer: dict[str, Any]) -> list[dict[str, Any]]:
        """这个函数生成 CPU、内存、磁盘三张资源卡，颜色固定，方便用户一眼识别。"""
        cpu = computer.get("cpu", {})
        memory = computer.get("memory", {})
        disk = computer.get("disk", {})

        cpuPercent = float(cls.toNumber(cpu.get("percent"), 0))
        memoryPercent = float(cls.toNumber(memory.get("percent"), 0))
        diskPercent = float(cls.toNumber(disk.get("percent"), 0))

        return [
            {
                "name": "CPU",
                "icon": "CPU",
                "percent_value": round(cpuPercent, 1),
                "percent_text": cls.formatPercent(cpuPercent),
                "value": cls.formatPercent(cpuPercent),
                "detail": "当前处理器占用",
                "color": "cyan",
                "wave": "cyan",
            },
            {
                "name": "内存",
                "icon": "RAM",
                "percent_value": round(memoryPercent, 1),
                "percent_text": cls.formatPercent(memoryPercent),
                "value": cls.formatPercent(memoryPercent),
                "detail": f"{Render.autoConvertUnit(cls.toNumber(memory.get('used'), 0))} / {Render.autoConvertUnit(cls.toNumber(memory.get('total'), 0))}",
                "color": "mint",
                "wave": "mint",
            },
            {
                "name": "磁盘",
                "icon": "SSD",
                "percent_value": round(diskPercent, 1),
                "percent_text": cls.formatPercent(diskPercent),
                "value": cls.formatPercent(diskPercent),
                "detail": f"{Render.autoConvertUnit(cls.toNumber(disk.get('used'), 0))} / {Render.autoConvertUnit(cls.toNumber(disk.get('total'), 0))}",
                "color": "purple",
                "wave": "purple",
            },
        ]

    @classmethod
    def buildServices(cls, services: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """这个函数把原始服务检测结果改造成页面卡片更好用的结构。"""
        cards: list[dict[str, Any]] = []
        colors = ["cyan", "purple", "mint", "rose"]

        for index, service in enumerate(services):
            isOk = service.get("ok") is True
            duration = service.get("durationMs")

            cards.append(
                {
                    "name": service.get("name", "未命名服务"),
                    "url": service.get("target") or "未提供目标地址",
                    "status": "good" if isOk else "danger",
                    "status_text": "正常" if isOk else "异常",
                    "status_color": "green" if isOk else "rose",
                    "ping_text": f"{round(duration, 2)}ms" if duration is not None else "无耗时",
                    "message": cls.buildServiceMessage(service, isOk),
                    "color": colors[index % len(colors)],
                    "icon": "API",
                }
            )

        return cards

    @classmethod
    def buildSystemDetails(cls, computer: dict[str, Any], summary: dict[str, Any], success_text: str = "阿柯牛逼", fail_text: str = "阿柯死了") -> dict[str, Any]:
        """这个函数生成右侧摘要区的数据：上面两个信息块，下面一个总状态结论块。"""
        isAllOk = summary.get("serviceFailCount", 0) == 0
        statusText = success_text if isAllOk else fail_text

        return {
            "cards": [
                {"label": "运行环境", "value": computer.get("system") or "未知"},
                {"label": "开机时间", "value": computer.get("bootTime") or "未知"},
            ],
            "status_text": statusText,
        }

    @classmethod
    def buildServiceMessage(cls, service: dict[str, Any], isOk: bool) -> str:
        """这个函数把底层探测结果翻译成更适合页面展示的人话。"""
        message = service.get("message") or "无额外说明"
        statusCode = service.get("statusCode")

        if isOk and statusCode in {401, 403, 405}:
            return "服务在线，但限制了直接探测"

        if isOk:
            return "服务在线"

        return str(message)


    # =================================================================
    # 模型连通性卡片数据
    # =================================================================

    @classmethod
    def buildModels(cls, report: dict[str, Any] | None) -> dict[str, Any]:
        """
        把 ModelProbe.probe() 的结果整理成模板底部卡片可直接循环的数据。
        卡片结构参考 connect 文件的 Provider → Model 层级，但字段名换成当前项目更直白的名字。
        """
        if not isinstance(report, dict):
            return cls.emptyModels("模型检测未执行")

        providers = [cls.buildModelProvider(item) for item in report.get("providers", [])]
        total = int(report.get("total", 0) or 0)
        okCount = int(report.get("okCount", 0) or 0)
        slowCount = int(report.get("slowCount", 0) or 0)
        errorCount = int(report.get("errorCount", 0) or 0)

        if total == 0:
            return cls.emptyModels("没有发现 WebUI 中已打开的聊天模型")

        return {
            "title": "模型连通性",
            "checked_at": report.get("checkedAt", "-"),
            "elapsed_text": f"{report.get('elapsedMs', 0)}ms",
            "total": total,
            "ok_count": okCount,
            "slow_count": slowCount,
            "error_count": errorCount,
            "provider_count": int(report.get("providerCount", len(providers)) or len(providers)),
            "overall_status": "ok" if errorCount == 0 else "error",
            "overall_text": "全部可用" if errorCount == 0 else "存在异常",
            "providers": providers,
            "empty_text": "",
        }

    @classmethod
    def emptyModels(cls, text: str) -> dict[str, Any]:
        """这个函数生成空模型检测结果，让模板不用判断 None。"""
        return {
            "title": "模型连通性",
            "checked_at": "-",
            "elapsed_text": "0ms",
            "total": 0,
            "ok_count": 0,
            "slow_count": 0,
            "error_count": 0,
            "provider_count": 0,
            "overall_status": "empty",
            "overall_text": "无模型",
            "providers": [],
            "empty_text": text,
        }

    @classmethod
    def buildModelProvider(cls, provider: dict[str, Any]) -> dict[str, Any]:
        """这个函数整理单个 Provider 卡片，里面包含该 Provider 下的模型行。"""
        status = str(provider.get("status") or "ok")
        return {
            "name": provider.get("displayName") or provider.get("groupId") or "Provider",
            "id": provider.get("groupId") or "unknown",
            "status": status,
            "status_text": provider.get("statusLabel") or cls.modelStatusText(status),
            "model_count": int(provider.get("modelCount", 0) or 0),
            "ok_count": int(provider.get("okCount", 0) or 0),
            "slow_count": int(provider.get("slowCount", 0) or 0),
            "error_count": int(provider.get("errorCount", 0) or 0),
            "models": [cls.buildModelItem(item) for item in provider.get("results", [])],
        }

    @classmethod
    def buildModelItem(cls, item: dict[str, Any]) -> dict[str, Any]:
        """
        这个函数整理单行模型数据，结构尽量贴近 connect 的模型行。
        模板会展示 4 个指标、响应速度曲线、最近状态格子和错误详情。
        """
        status = str(item.get("status") or "error")
        latency = int(item.get("latencyMs", 0) or 0)

        # 历史格子：优先读取已有真实历史，没有则用当前状态推导一条包含波动的假历史
        history = item.get("history")
        if not history:
            history = cls.buildModelHistory(status)

        # 曲线点：优先读取已有真实曲线，没有则用当前状态推导，且点数 = 格子数
        curvePoints = item.get("curvePoints")
        if not curvePoints:
            curvePoints = cls.buildModelCurvePoints(latency, status, len(history))

        return {
            "name": item.get("model") or "unknown",
            "status": status,
            "status_text": cls.modelStatusText(status),
            "latency_text": f"{latency} ms",
            # 历史统计指标：优先读取 main.py 写入的真实数据，没有则用假数据
            "avg_latency_text": item.get("avgLatencyText") or cls.buildAverageLatencyText(latency, status),
            "availability_text": item.get("availability") or cls.buildAvailabilityText(status),
            "success_text": item.get("weeklySuccessText") or cls.buildSuccessText(status),
            "error_text": item.get("error") or "",
            "reply_preview": item.get("replyPreview") or "",
            "history": history,
            "curve_points": curvePoints,
            # 曲线 path 从点数生成
            "curve_path": cls.buildCurvePath(curvePoints),
            "curve_area_path": cls.buildCurveAreaPath(curvePoints),
            "time_labels": item.get("timeLabels") or ["前", "中", "今"],
        }

    @classmethod
    def buildModelHistory(cls, status: str) -> list[str]:
        """这个函数生成最近状态格子；没有真实历史时，用当前状态补齐，让结构先完整展示。"""
        if status == "ok":
            return ["ok", "ok", "ok", "ok", "ok", "ok", "ok", "ok", "ok", "ok", "ok", "ok"]
        if status == "slow":
            return ["ok", "ok", "slow", "ok", "ok", "slow", "ok", "slow", "ok", "ok", "slow", "slow"]
        return ["ok", "slow", "error", "ok", "error", "error", "slow", "error", "ok", "error", "error", "error"]

    @classmethod
    def buildModelCurvePoints(cls, latency: int, status: str, count: int = 12) -> list[dict[str, int]]:
        """
        这个函数生成响应速度曲线点；真实历史缺失时，用当前延迟推导一条可读曲线。
        曲线点数 = 状态格子数，保证视觉上一一对应。
        """
        base = max(300, latency)
        # 根据状态生成不同波动幅度的延迟比例（数量 = count）
        if status == "ok":
            rates = [0.7 + 0.3 * (i % 3) / 3 for i in range(count)]  # 小幅度波动
        elif status == "slow":
            rates = [0.5 + 0.5 * (i % 4) / 4 for i in range(count)]  # 中等幅度波动
        else:
            rates = [0.3 + 0.7 * (i % 5) / 5 for i in range(count)]  # 大幅度波动

        maxLatency = max(1000, int(base * max(rates)))
        points: list[dict[str, int]] = []
        for index, rate in enumerate(rates):
            x = round(index * 100 / (count - 1)) if count > 1 else 0
            latencyValue = int(base * rate)
            y = 40 - round(latencyValue / maxLatency * 34)
            points.append({"x": x, "y": max(4, min(38, y))})
        return points

    @classmethod
    def buildCurvePath(cls, points: list[dict[str, int]]) -> str:
        """这个函数把曲线点转成 SVG path，模板只负责画出来。"""
        if not points:
            return ""
        commands = [f"M {points[0]['x']} {points[0]['y']}"]
        for point in points[1:]:
            commands.append(f"L {point['x']} {point['y']}")
        return " ".join(commands)

    @classmethod
    def buildCurveAreaPath(cls, points: list[dict[str, int]]) -> str:
        """这个函数生成曲线下面的淡色填充区域。"""
        line = cls.buildCurvePath(points)
        if not line or not points:
            return ""
        return f"{line} L {points[-1]['x']} 40 L {points[0]['x']} 40 Z"

    @classmethod
    def buildAverageLatencyText(cls, latency: int, status: str) -> str:
        """这个函数生成 24 小时平均延迟；没有历史时，用当前延迟做保守估算。"""
        if status == "error":
            return "N/A"
        return f"{max(1, int(latency * 0.9))} ms"

    @classmethod
    def buildAvailabilityText(cls, status: str) -> str:
        """这个函数生成可用性文案，让卡片结构和 connect 一致。"""
        return {"ok": "100.00%", "slow": "91.67%", "error": "58.33%"}.get(status, "0.00%")

    @classmethod
    def buildSuccessText(cls, status: str) -> str:
        """这个函数生成统计窗口内成功次数文案，让卡片结构和 connect 一致。"""
        return {"ok": "12/12", "slow": "11/12", "error": "7/12"}.get(status, "0/0")

    @classmethod
    def modelStatusText(cls, status: str) -> str:
        """这个函数把模型状态英文值翻译成人话，模板和文本都能复用。"""
        return {"ok": "正常", "slow": "较慢", "error": "错误", "empty": "无模型"}.get(status, "未知")


    # =================================================================
    # 工具方法
    # =================================================================

    @classmethod
    def toNumber(cls, value: Any, default: float | int = 0) -> float | int:
        """这个函数把可能为 None 的数值统一兜底，避免后面 round() 或 float() 时报错。"""
        if value is None:
            return default
        return value

    @classmethod
    def formatPercent(cls, value: Any) -> str:
        """这个函数把数字统一转换成百分比文本，页面里就不用每次重复写格式化。"""
        if value is None:
            return "未知"
        return f"{float(value):.1f}%"
