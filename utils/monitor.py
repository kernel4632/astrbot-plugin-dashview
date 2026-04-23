"""
这个文件是监控模块的总入口。

它本身不再塞满所有细节，而是把监控拆成了三块：
1. Computer 负责收集电脑自己的状态
2. Service 负责检测你指定的服务
3. Monitor 负责把服务结果统计成总结果

这样做之后，外部仍然只需要记住一个入口：
Monitor.collect()

最常见的调用方式：
Monitor.collect()
Monitor.collect(services=[{"name": "官网", "type": "http", "url": "https://example.com"}])
Monitor.collect(services=[{"name": "Redis", "type": "tcp", "host": "127.0.0.1", "port": 6379}], timeout=2)
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Optional

from .computer import Computer
from .service import Service


class _Monitor:
    """这个对象负责把一次完整的状态检测串起来。"""

    defaultTimeout = 3

    def collect(self, services: Optional[list[dict[str, Any]]] = None, timeout: Optional[int] = None) -> dict[str, Any]:
        """这个函数是统一入口，它会依次完成电脑采集、服务检测、摘要统计并返回总结果。"""
        print("开始执行一次状态检测")

        if timeout is None:
            timeout = self.defaultTimeout

        if services is None:
            services = []

        checkedAt = self.getNowText()
        computer = Computer.collect()
        serviceResults = Service.collect(services=services, timeout=timeout)
        summary = self.build(serviceResults)
        isAllOk = summary["serviceFailCount"] == 0

        result = {
            "ok": isAllOk,
            "checkedAt": checkedAt,
            "computer": computer,
            "services": serviceResults,
            "summary": summary,
        }

        print(f"状态检测完成，服务总数 {summary['serviceCount']}，失败 {summary['serviceFailCount']}")
        return result

    def getNowText(self) -> str:
        """这个函数统一生成当前时间文本，方便日志和结果记录保持一致。"""
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    def build(self, services: list[dict[str, Any]]) -> dict[str, Any]:
        """这个函数把服务结果列表统计成总数、成功数和失败数。"""
        serviceCount = len(services)
        serviceOkCount = sum(1 for item in services if item.get("ok") is True)
        serviceFailCount = serviceCount - serviceOkCount
        return {
            "serviceCount": serviceCount,
            "serviceOkCount": serviceOkCount,
            "serviceFailCount": serviceFailCount,
        }


Monitor = _Monitor()


if __name__ == "__main__":
    demoServices = [
        {"name": "Python 官网", "type": "http", "url": "https://www.python.org"},
        {"name": "本机 SSH", "type": "tcp", "host": "127.0.0.1", "port": 22},
    ]

    result = Monitor.collect(services=demoServices, timeout=3)
    print(json.dumps(result, ensure_ascii=False, indent=2))
