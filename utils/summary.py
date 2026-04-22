"""
这个文件只负责把服务检测结果统计成一个简短摘要。

它不采集电脑信息，也不真正去探测网站。
它只是把“已经拿到的服务结果”算成总数、成功数、失败数。

最常见的调用方式：
Summary.build([])
Summary.build(serviceResults)
"""

from __future__ import annotations

from typing import Any


class Summary:
    """这个对象专门负责把服务结果统计成摘要。"""

    @classmethod
    def build(cls, services: list[dict[str, Any]]) -> dict[str, Any]:
        """这个函数把服务结果列表统计成总数、成功数和失败数。"""
        serviceCount = len(services)
        serviceOkCount = sum(1 for item in services if item.get("ok") is True)
        serviceFailCount = serviceCount - serviceOkCount
        return {
            "serviceCount": serviceCount,
            "serviceOkCount": serviceOkCount,
            "serviceFailCount": serviceFailCount,
        }
