"""
这个文件只负责收集“当前这台机器自己”的状态。

它不检测网站，也不统计汇总，只做电脑信息采集。
这样拆出来后，你想改 CPU、内存、磁盘、开机时间这些内容时，
只需要看这一个文件，不会和服务检测逻辑混在一起。

最常见的调用方式：
Computer.collect()
"""

from __future__ import annotations

import os
import platform
import shutil
import socket
from datetime import datetime
from typing import Any, Optional

try:
    import psutil  # type: ignore
except Exception:
    psutil = None


class Computer:
    """这个对象专门负责收集电脑或服务器自己的状态。"""

    @classmethod
    def collect(cls) -> dict[str, Any]:
        """这个函数统一收集主机名、系统、CPU、内存、磁盘和开机时间。"""
        print("开始收集电脑状态")
        result = {
            "hostName": socket.gethostname(),
            "localIp": cls.getLocalIp(),
            "system": platform.system(),
            "systemVersion": platform.version(),
            "machine": platform.machine(),
            "pythonVersion": platform.python_version(),
            "bootTime": cls.getBootTimeText(),
            "cpu": cls.getCpuInfo(),
            "memory": cls.getMemoryInfo(),
            "disk": cls.getDiskInfo(),
        }
        print("电脑状态收集完成")
        return result

    @classmethod
    def getCpuInfo(cls) -> dict[str, Any]:
        """这个函数收集 CPU 的线程数、负载和当前占用率。"""
        logicalCount = os.cpu_count()

        try:
            load1, load5, load15 = os.getloadavg()
            loadAverage = {
                "load1": round(load1, 2),
                "load5": round(load5, 2),
                "load15": round(load15, 2),
            }
        except Exception:
            loadAverage = None

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

    @classmethod
    def getMemoryInfo(cls) -> dict[str, Any]:
        """这个函数收集内存总量、已用、可用和占用率。"""
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
                "free": int(memory.available),
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

    @classmethod
    def getDiskInfo(cls) -> dict[str, Any]:
        """这个函数收集当前系统盘的总量、已用、剩余和占用率。"""
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

    @classmethod
    def getLocalIp(cls) -> Optional[str]:
        """这个函数尝试拿到这台机器更接近真实出口的局域网 IP。"""
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.connect(("8.8.8.8", 80))
                return sock.getsockname()[0]
        except Exception:
            try:
                return socket.gethostbyname(socket.gethostname())
            except Exception:
                return None

    @classmethod
    def getBootTimeText(cls) -> Optional[str]:
        """这个函数把开机时间转换成人能直接看的文本。"""
        if not psutil:
            return None

        try:
            bootTime = datetime.fromtimestamp(psutil.boot_time())
            return bootTime.strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            return None
