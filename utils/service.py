"""
这个文件只负责检测你指定的服务。

它不采集电脑本身的信息，也不负责总入口调度。
它的职责只有一件事：把服务列表逐个检测完，然后返回统一结构的结果。

当前支持两种服务：
1. http / https
2. tcp

最常见的调用方式：
Service.collect([] , timeout=3)
Service.collect([{"name": "官网", "type": "http", "url": "https://example.com"}], timeout=3)
Service.collect([{"name": "Redis", "type": "tcp", "host": "127.0.0.1", "port": 6379}], timeout=3)
"""

from __future__ import annotations

import socket
import time
import urllib.error
import urllib.request
from typing import Any


class Service:
    """这个对象专门负责服务检测。"""

    defaultHttpMethod = "GET"

    @classmethod
    def collect(cls, services: list[dict[str, Any]], timeout: int) -> list[dict[str, Any]]:
        """这个函数按顺序检测所有服务，并返回统一结构的结果列表。"""
        print("开始检测指定服务")

        if not services:
            print("没有传入服务配置，跳过服务检测")
            return []

        results = []
        for service in services:
            results.append(cls.check(service, timeout))

        print(f"服务检测完成，共检测 {len(results)} 个服务")
        return results

    @classmethod
    def check(cls, service: dict[str, Any], timeout: int) -> dict[str, Any]:
        """这个函数根据 type 把服务分发到对应的检测逻辑。"""
        print(f"开始检测服务：{service.get('name', '未命名服务')}")

        if "type" not in service:
            return cls.buildError(service, "缺少 type，无法判断检测方式")

        serviceType = str(service["type"]).lower()

        if serviceType in ["http", "https"]:
            return cls.checkHttp(service, timeout)

        if serviceType == "tcp":
            return cls.checkTcp(service, timeout)

        return cls.buildError(service, f"不支持的服务类型: {serviceType}")

    @classmethod
    def checkHttp(cls, service: dict[str, Any], timeout: int) -> dict[str, Any]:
        """这个函数检测网站或 HTTP 接口是否可达，并记录状态码与耗时。"""
        if "url" not in service:
            return cls.buildError(service, "http 检测缺少 url")

        url = str(service["url"])
        name = str(service.get("name", url))
        method = str(service.get("method", cls.defaultHttpMethod)).upper()
        startTime = time.time()

        try:
            request = urllib.request.Request(url=url, method=method)
            with urllib.request.urlopen(request, timeout=timeout) as response:
                endTime = time.time()
                statusCode = getattr(response, "status", None) or response.getcode()
                durationMs = round((endTime - startTime) * 1000, 2)
                isOk = 200 <= int(statusCode) < 400
                result = {
                    "name": name,
                    "type": "http",
                    "target": url,
                    "ok": isOk,
                    "statusCode": int(statusCode),
                    "durationMs": durationMs,
                    "message": "正常" if isOk else "HTTP 状态码异常",
                }
                print(f"服务检测完成：{name}，结果 {'成功' if isOk else '失败'}")
                return result
        except urllib.error.HTTPError as error:
            endTime = time.time()
            durationMs = round((endTime - startTime) * 1000, 2)
            statusCode = int(error.code)
            isReachable = statusCode in [401, 403, 405]
            result = {
                "name": name,
                "type": "http",
                "target": url,
                "ok": isReachable,
                "statusCode": statusCode,
                "durationMs": durationMs,
                "message": "服务可达，但拒绝了当前探测请求" if isReachable else f"HTTP 错误: {error.reason}",
            }
            print(f"服务检测完成：{name}，结果 {'成功' if isReachable else '失败'}")
            return result
        except Exception as error:
            endTime = time.time()
            durationMs = round((endTime - startTime) * 1000, 2)
            result = {
                "name": name,
                "type": "http",
                "target": url,
                "ok": False,
                "statusCode": None,
                "durationMs": durationMs,
                "message": str(error),
            }
            print(f"服务检测完成：{name}，结果失败")
            return result

    @classmethod
    def checkTcp(cls, service: dict[str, Any], timeout: int) -> dict[str, Any]:
        """这个函数检测 TCP 端口是否能连通。"""
        if "host" not in service:
            return cls.buildError(service, "tcp 检测缺少 host")

        if "port" not in service:
            return cls.buildError(service, "tcp 检测缺少 port")

        host = str(service["host"])
        port = int(service["port"])
        name = str(service.get("name", f"{host}:{port}"))
        startTime = time.time()

        try:
            with socket.create_connection((host, port), timeout=timeout):
                endTime = time.time()
                durationMs = round((endTime - startTime) * 1000, 2)
                result = {
                    "name": name,
                    "type": "tcp",
                    "target": f"{host}:{port}",
                    "ok": True,
                    "statusCode": None,
                    "durationMs": durationMs,
                    "message": "端口可连接",
                }
                print(f"服务检测完成：{name}，结果成功")
                return result
        except Exception as error:
            endTime = time.time()
            durationMs = round((endTime - startTime) * 1000, 2)
            result = {
                "name": name,
                "type": "tcp",
                "target": f"{host}:{port}",
                "ok": False,
                "statusCode": None,
                "durationMs": durationMs,
                "message": str(error),
            }
            print(f"服务检测完成：{name}，结果失败")
            return result

    @classmethod
    def buildError(cls, service: dict[str, Any], message: str) -> dict[str, Any]:
        """这个函数统一生成服务配置错误时的失败结果。"""
        return {
            "name": str(service.get("name", "未命名服务")),
            "type": str(service.get("type", "unknown")),
            "target": service.get("url") or f"{service.get('host', '')}:{service.get('port', '')}",
            "ok": False,
            "statusCode": None,
            "durationMs": 0,
            "message": message,
        }
