"""
这个文件专门负责把“监控原始数据”整理成“页面模板能直接使用的数据”。

你可以把它理解成页面的数据翻译层：
Monitor.collect() 返回的数据更像底层采集结果，字段比较原始；
而模板真正想要的是标题、颜色、百分比文本、状态文案、卡片列表。
这个文件就是把两者接起来的那一层。

最常见的调用方式有这些：
Data.buildCollected(computer=computer, services=services, summary=summary)
Data.buildOverview(summary)
Data.buildResourceCards(computer)
Data.buildServices(services)
Data.buildSystemDetails(computer, summary)

如果以后你想调整页面文案、卡片结构、颜色分配，优先改这个文件。
"""

from __future__ import annotations

from typing import Any

try:
    from .utils.render import Render
except ImportError:
    from utils.render import Render


class Data:
    """
    这个对象只做一件事：整理页面数据。

    它不负责采集系统信息，也不负责渲染 HTML。
    这样拆开后，新人只看这个文件，就能快速理解“页面到底吃什么数据”。
    """

    @classmethod
    def buildCollected(cls, computer: dict[str, Any], services: list[dict[str, Any]], summary: dict[str, Any], nickname: str = "阿柯AKer", success_text: str = "阿柯牛逼", fail_text: str = "阿柯死了") -> dict[str, Any]:
        """这个函数把页面需要的所有数据一次性整理好，返回给模板或插件入口直接使用。"""
        return {
            "hostname": nickname,
            "os_info": f"{computer.get('system', 'Unknown')} ({computer.get('machine', '')})",
            "summary": summary,
            "overview": cls.buildOverview(summary),
            "resource_cards": cls.buildResourceCards(computer),
            "services_status": cls.buildServices(services),
            "system_details": cls.buildSystemDetails(computer, summary, success_text, fail_text),
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
