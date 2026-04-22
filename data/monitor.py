"""
这个文件是一个单文件的通用监控脚本，适合做“一次性状态检测”。
你可以把它当成一个小工具库导入到别的 Python 文件里使用，也可以直接运行它看效果。

这个文件主要做两类事情：
1. 收集当前电脑或服务器的基础状态，比如系统信息、CPU、内存、磁盘、网络基础信息
2. 检测你指定的服务是否可用，目前支持两种服务：
   - http / https：检查网站接口是否能访问、返回码是多少、耗时是多少
   - tcp：检查某个主机端口是否能连通

最常用的调用方式有这些：

Monitor.collect()
Monitor.collect(services=[{"name": "官网", "type": "http", "url": "https://example.com"}])
Monitor.collect(services=[{"name": "MySQL", "type": "tcp", "host": "127.0.0.1", "port": 3306}])
Monitor.collect(services=[
    {"name": "首页", "type": "http", "url": "https://example.com"},
    {"name": "Redis", "type": "tcp", "host": "127.0.0.1", "port": 6379}
], timeout=3)

返回结果是一个 dict，结构大致像这样：

{
    "ok": True,
    "checkedAt": "2025-01-01 12:00:00",
    "computer": {...},
    "services": [...],
    "summary": {
        "serviceCount": 2,
        "serviceOkCount": 2,
        "serviceFailCount": 0
    }
}

这个文件尽量只用 Python 标准库，这样你复制一个 py 文件就能跑。
如果你的环境安装了 psutil，它会自动用更准确的数据；如果没有，也会自动降级，不影响基本使用。
"""

from __future__ import annotations

import json
import os
import platform
import shutil
import socket
import time
import urllib.error
import urllib.request
from datetime import datetime
from typing import Any, Dict, List, Optional


# 这里尝试导入 psutil。
# 它不是必须依赖，所以导入失败时不报错，只是后面少一些更精确的数据。
try:
    import psutil  # type: ignore
except Exception:
    psutil = None


class _Monitor:
    """
    这个对象负责把“状态检测”这件事完整做完。
    外部只需要记住一个入口：Monitor.collect()

    你可以这样用：

    Monitor.collect()
    Monitor.collect(services=[{"name": "官网", "type": "http", "url": "https://example.com"}])
    Monitor.collect(services=[{"name": "Redis", "type": "tcp", "host": "127.0.0.1", "port": 6379}], timeout=2)

    这个对象内部按“事件→指令→数据→反馈”的方式组织：
    - 事件：外部调用 collect()
    - 指令：collect() 内部依次执行电脑检测和服务检测
    - 数据：把检测结果整理成统一 dict
    - 反馈：把完整 dict 返回给调用者
    """

    """
    这些是默认超时设置。
    之所以集中放在这里，是因为“超时秒数”属于容易改的业务参数，
    集中写更容易找，也更不容易出现某个函数忘了同步修改的问题。
    """
    defaultTimeout = 3
    defaultHttpMethod = "GET"

    """
    这个是对外最重要的统一入口。
    它会先收集电脑状态，再检测指定服务，最后组合成一个总结果 dict 返回。

    services 的格式示例：
    [
        {"name": "官网", "type": "http", "url": "https://example.com"},
        {"name": "本地 Redis", "type": "tcp", "host": "127.0.0.1", "port": 6379}
    ]
    """
    def collect(self, services: Optional[List[Dict[str, Any]]] = None, timeout: Optional[int] = None) -> Dict[str, Any]:
        print("开始执行一次状态检测")

        # 没传超时时间时，使用统一默认值，避免每次调用都必须写。
        if timeout is None:
            timeout = self.defaultTimeout

        # 没传服务列表时，用空列表统一处理，后面就不用每次判断 None。
        if services is None:
            services = []

        checkedAt = self._getNowText()  # 记录这次检测发生的时间，便于日志和排查。
        computer = self._collectComputer()  # 收集当前机器状态。
        serviceResults = self._collectServices(services, timeout)  # 检测所有指定服务。
        summary = self._buildSummary(serviceResults)  # 生成简短汇总，方便一眼看结论。
        isAllOk = summary["serviceFailCount"] == 0  # 只要有一个服务失败，这次整体就不算全通过。

        result = {
            "ok": isAllOk,
            "checkedAt": checkedAt,
            "computer": computer,
            "services": serviceResults,
            "summary": summary,
        }

        print(f"状态检测完成，服务总数 {summary['serviceCount']}，失败 {summary['serviceFailCount']}")
        return result


    """
    这个函数专门收集电脑或服务器自己的状态。
    返回的是一个大字典，字段名尽量直接表达含义，便于外部直接取值。
    """
    def _collectComputer(self) -> Dict[str, Any]:
        print("开始收集电脑状态")

        hostName = socket.gethostname()  # 主机名通常能帮助快速定位是哪台机器。
        localIp = self._getLocalIp()  # 获取常用出口 IP，方便区分网络环境。
        system = platform.system()  # 例如 Windows、Linux、Darwin。
        systemVersion = platform.version()
        machine = platform.machine()  # 例如 x86_64、AMD64、arm64。
        pythonVersion = platform.python_version()

        cpu = self._getCpuInfo()  # CPU 信息会根据环境能力尽量多拿。
        memory = self._getMemoryInfo()  # 内存信息优先使用 psutil，拿不到就返回可用部分。
        disk = self._getDiskInfo()  # 磁盘使用率很适合做预警。
        bootTime = self._getBootTimeText()  # 开机时间可以辅助判断是否刚重启过。

        result = {
            "hostName": hostName,
            "localIp": localIp,
            "system": system,
            "systemVersion": systemVersion,
            "machine": machine,
            "pythonVersion": pythonVersion,
            "bootTime": bootTime,
            "cpu": cpu,
            "memory": memory,
            "disk": disk,
        }

        print("电脑状态收集完成")
        return result


    """
    这个函数负责依次检测所有服务。
    这里不用复杂并发，是为了保持脚本简单、稳定、容易看懂。
    对于“一次性巡检”场景，这种顺序检测通常已经够用。
    """
    def _collectServices(self, services: List[Dict[str, Any]], timeout: int) -> List[Dict[str, Any]]:
        print("开始检测指定服务")

        # 服务列表为空不是错误，只是代表这次只看机器状态不看网站或端口。
        if not services:
            print("没有传入服务配置，跳过服务检测")
            return []

        results = []

        # 这里一条条检测，是最直观也最容易调试的写法。
        for service in services:
            result = self._checkService(service, timeout)
            results.append(result)

        print(f"服务检测完成，共检测 {len(results)} 个服务")
        return results


    """
    这个函数根据服务 type 分发到不同检测逻辑。
    这样做的好处是入口统一，扩展也方便。
    以后你要加 ping、dns、数据库连接检测，也是在这里继续分发。
    """
    def _checkService(self, service: Dict[str, Any], timeout: int) -> Dict[str, Any]:
        print(f"开始检测服务：{service.get('name', '未命名服务')}")

        # 没有 type 时无法知道该怎么检测，所以直接返回失败结果。
        if "type" not in service:
            return self._buildServiceError(service, "缺少 type，无法判断检测方式")

        serviceType = str(service["type"]).lower()

        # 用 type 分发，而不是猜字段，是因为规则更明确，调用方也更容易理解。
        if serviceType in ["http", "https"]:
            return self._checkHttp(service, timeout)

        if serviceType == "tcp":
            return self._checkTcp(service, timeout)

        return self._buildServiceError(service, f"不支持的服务类型: {serviceType}")


    """
    这个函数检测网站或 HTTP 接口。
    它会尝试发起一次请求，并记录返回码、耗时、是否成功。
    """
    def _checkHttp(self, service: Dict[str, Any], timeout: int) -> Dict[str, Any]:
        # URL 是 HTTP 检测的核心字段，没有它就没法继续。
        if "url" not in service:
            return self._buildServiceError(service, "http 检测缺少 url")

        url = str(service["url"])
        name = str(service.get("name", url))
        method = str(service.get("method", self.defaultHttpMethod)).upper()
        startTime = time.time()

        try:
            request = urllib.request.Request(url=url, method=method)
            with urllib.request.urlopen(request, timeout=timeout) as response:
                endTime = time.time()
                statusCode = getattr(response, "status", None) or response.getcode()
                durationMs = round((endTime - startTime) * 1000, 2)
                isOk = 200 <= int(statusCode) < 400  # 2xx 和 3xx 对巡检来说通常都算正常。

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

        # HTTPError 代表服务器有响应，但返回的是 4xx 或 5xx。
        # 这种情况比“完全连不上”多一点信息，所以单独处理。
        except urllib.error.HTTPError as error:
            endTime = time.time()
            durationMs = round((endTime - startTime) * 1000, 2)

            result = {
                "name": name,
                "type": "http",
                "target": url,
                "ok": False,
                "statusCode": int(error.code),
                "durationMs": durationMs,
                "message": f"HTTP 错误: {error.reason}",
            }

            print(f"服务检测完成：{name}，结果失败")
            return result

        # 这里捕获更广的异常，是因为网络检查容易受 DNS、超时、证书、断网等多种因素影响。
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


    """
    这个函数检测 TCP 端口能否连接。
    它适合检查 Redis、MySQL、PostgreSQL、MQ、任意 TCP 服务是否“端口还活着”。
    """
    def _checkTcp(self, service: Dict[str, Any], timeout: int) -> Dict[str, Any]:
        # TCP 检测至少需要 host 和 port，缺任何一个都不能判断连通性。
        if "host" not in service:
            return self._buildServiceError(service, "tcp 检测缺少 host")

        if "port" not in service:
            return self._buildServiceError(service, "tcp 检测缺少 port")

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


    """
    这个函数统一生成“服务配置本身有问题”的失败结果。
    这样外部看到的返回结构始终一致，不需要因为错误场景写很多额外判断。
    """
    def _buildServiceError(self, service: Dict[str, Any], message: str) -> Dict[str, Any]:
        name = str(service.get("name", "未命名服务"))
        serviceType = str(service.get("type", "unknown"))

        return {
            "name": name,
            "type": serviceType,
            "target": service.get("url") or f"{service.get('host', '')}:{service.get('port', '')}",
            "ok": False,
            "statusCode": None,
            "durationMs": 0,
            "message": message,
        }


    """
    这个函数负责生成汇总信息。
    汇总的价值是让调用者不用自己再写一遍统计逻辑，拿来就能做展示和告警。
    """
    def _buildSummary(self, services: List[Dict[str, Any]]) -> Dict[str, Any]:
        serviceCount = len(services)
        serviceOkCount = sum(1 for item in services if item.get("ok") is True)
        serviceFailCount = serviceCount - serviceOkCount

        return {
            "serviceCount": serviceCount,
            "serviceOkCount": serviceOkCount,
            "serviceFailCount": serviceFailCount,
        }


    """
    这个函数拿 CPU 信息。
    如果有 psutil，就返回更完整的数据；没有时也尽量给出基础信息。
    """
    def _getCpuInfo(self) -> Dict[str, Any]:
        logicalCount = os.cpu_count()

        # getloadavg 主要在 Unix 系统上可用，Windows 常常没有，所以要兼容。
        try:
            load1, load5, load15 = os.getloadavg()
            loadAverage = {
                "load1": round(load1, 2),
                "load5": round(load5, 2),
                "load15": round(load15, 2),
            }
        except Exception:
            loadAverage = None

        # psutil.cpu_percent(interval=0.2) 会短暂停一下去采样，数据更像“当前值”。
        # 如果写 interval=0 虽然快，但第一次调用有时不够直观。
        if psutil:
            try:
                percent = psutil.cpu_percent(interval=0.2)
            except Exception:
                percent = None
        else:
            percent = None

        return {
            "logicalCount": logicalCount,
            "percent": percent,
            "loadAverage": loadAverage,
        }


    """
    这个函数拿内存信息。
    标准库没有特别统一的跨平台“内存使用率”接口，所以优先用 psutil。
    如果没有 psutil，就返回空值而不是假装返回不准确的数据。
    """
    def _getMemoryInfo(self) -> Dict[str, Any]:
        if not psutil:
            return {
                "total": None,
                "used": None,
                "free": None,
                "percent": None,
                "note": "未安装 psutil，无法提供精确内存信息",
            }

        try:
            memory = psutil.virtual_memory()

            return {
                "total": int(memory.total),
                "used": int(memory.used),
                "free": int(memory.available),  # available 比单纯 free 更接近“还可用”。
                "percent": float(memory.percent),
                "note": None,
            }
        except Exception as error:
            return {
                "total": None,
                "used": None,
                "free": None,
                "percent": None,
                "note": str(error),
            }


    """
    这个函数拿当前系统盘的磁盘信息。
    为了兼容性和简单性，这里默认取当前 Python 运行所在盘。
    在 Linux 下一般会是 /，在 Windows 下一般会是当前盘符。
    """
    def _getDiskInfo(self) -> Dict[str, Any]:
        path = os.path.abspath(os.sep)

        try:
            total, used, free = shutil.disk_usage(path)
            percent = round((used / total) * 100, 2) if total else None

            return {
                "path": path,
                "total": int(total),
                "used": int(used),
                "free": int(free),
                "percent": percent,
            }
        except Exception as error:
            return {
                "path": path,
                "total": None,
                "used": None,
                "free": None,
                "percent": None,
                "note": str(error),
            }


    """
    这个函数获取本机常用出口 IP。
    这里没有真正发网络数据，只是借助 UDP 套接字让系统帮我们选出常用出口地址。
    这种写法比只查 hostname 更常能拿到实际局域网 IP。
    """
    def _getLocalIp(self) -> Optional[str]:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.connect(("8.8.8.8", 80))
                return sock.getsockname()[0]
        except Exception:
            try:
                return socket.gethostbyname(socket.gethostname())
            except Exception:
                return None


    """
    这个函数获取开机时间文本。
    如果拿不到，就返回 None，让调用方知道这里确实没有数据。
    """
    def _getBootTimeText(self) -> Optional[str]:
        if not psutil:
            return None

        try:
            bootTime = datetime.fromtimestamp(psutil.boot_time())
            return bootTime.strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            return None


    """这个函数统一生成当前时间文本，方便日志展示和结果保存。"""
    def _getNowText(self) -> str:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# 这里暴露出一个可以直接导入使用的对象。
# 外部文件只需要 from monitor import Monitor，然后调用 Monitor.collect() 即可。
Monitor = _Monitor()


"""
直接运行这个文件时，会执行下面的演示代码。
这样做的好处是：这个文件既能当库被导入，也能自己单独跑起来看结果。
"""
if __name__ == "__main__":
    demoServices = [
        {"name": "Python 官网", "type": "http", "url": "https://www.python.org"},
        {"name": "本机 SSH", "type": "tcp", "host": "127.0.0.1", "port": 22},
    ]

    result = Monitor.collect(services=demoServices, timeout=3)
    print(json.dumps(result, ensure_ascii=False, indent=2))