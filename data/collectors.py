from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
import psutil
from cpuinfo import get_cpu_info

try:
    from ..utils import CpuFreq, readable_python_version, system_name
    from ..version_resolver import resolve_astrbot_version
except ImportError:
    from utils import CpuFreq, readable_python_version, system_name
    from version_resolver import resolve_astrbot_version


def _dt_now() -> datetime:
    return datetime.now(timezone.utc).astimezone()


BOOT_TIME = datetime.fromtimestamp(psutil.boot_time(), tz=timezone.utc).astimezone()
ASTRBOT_START_TIME = _dt_now()


def _format_td(dt: timedelta) -> str:
    days = dt.days
    rest = dt - timedelta(days=days)
    hours, rem = divmod(rest.seconds, 3600)
    minutes, seconds = divmod(rem, 60)
    parts = []
    if days:
        parts.append(f"{days}天")
    parts.append(f"{hours:02d}:{minutes:02d}:{seconds:02d}")
    return " ".join(parts)


def get_cpu_brand() -> str:
    try:
        brand = str(get_cpu_info().get("brand_raw") or "")
    except Exception:
        return "Unknown CPU"
    brand = brand.strip()
    if brand.lower().endswith(("cpu", "processor")):
        brand = brand.rsplit(" ", 1)[0]
    return brand


def cpu_count() -> int | None:
    return psutil.cpu_count(logical=False)


def cpu_count_logical() -> int | None:
    return psutil.cpu_count()


def cpu_percent() -> float:
    # psutil averages across interval=0 (non-blocking) by last call; acceptable for on-demand snapshot
    return psutil.cpu_percent(interval=None)


def cpu_freq() -> CpuFreq:
    freq = psutil.cpu_freq()
    return CpuFreq(
        current=getattr(freq, "current", None),
        min=getattr(freq, "min", None),
        max=getattr(freq, "max", None),
    )


@dataclass
class MemStat:
    total: int
    used: int
    percent: float


def memory_stat() -> MemStat:
    m = psutil.virtual_memory()
    return MemStat(total=m.total, used=m.used, percent=m.percent)


def swap_stat() -> MemStat:
    s = psutil.swap_memory()
    return MemStat(total=s.total, used=s.used, percent=s.percent)


@dataclass
class DiskUsage:
    name: str
    used: int | None
    total: int | None
    percent: float | None
    exception: str | None = None


def disk_usage(ignore: list[str] | None = None) -> list[DiskUsage]:
    # 收集磁盘挂载信息，过滤掉非真正磁盘分区的挂载点
    ignore = ignore or []
    ret: list[DiskUsage] = []
    # 过滤规则：排除配置文件和小文件挂载点
    # 某些 Linux 系统会将 /etc/resolv.conf 等文件以特殊方式挂载，需要过滤
    skip_patterns = ["/etc/resolv.conf", "/etc/hostname", "/etc/hosts"]
    for part in psutil.disk_partitions(all=False):
        name = part.mountpoint
        # 跳过配置文件挂载点
        if any(pattern == name for pattern in skip_patterns):
            continue
        # 跳过忽略列表中的挂载点
        if any(x in name for x in ignore):
            continue
        try:
            u = psutil.disk_usage(name)
            # 过滤掉过小的挂载点（通常是临时文件系统或特殊挂载）
            # 真正的磁盘分区至少应该有几十 MB
            if u.total < 100 * 1024 * 1024:  # 小于 100MB 跳过
                continue
            ret.append(DiskUsage(name=name, used=u.used, total=u.total, percent=u.percent))
        except Exception as e:
            ret.append(
                DiskUsage(name=name, used=None, total=None, percent=None, exception=str(e)),
            )
    return ret


@dataclass
class DiskIO:
    name: str
    read: float
    write: float


_last_disk_io = (time.time(), psutil.disk_io_counters(perdisk=True))


def disk_io() -> list[DiskIO]:
    global _last_disk_io
    now = time.time()
    past_t, past = _last_disk_io
    now_c = psutil.disk_io_counters(perdisk=True)
    dt = max(1e-6, now - past_t)
    ret: list[DiskIO] = []
    for name, now_one in now_c.items():
        if name not in past:
            continue
        past_one = past[name]
        read = max(0.0, (now_one.read_bytes - past_one.read_bytes) / dt)
        write = max(0.0, (now_one.write_bytes - past_one.write_bytes) / dt)
        ret.append(DiskIO(name=name, read=read, write=write))
    _last_disk_io = (now, now_c)
    # Top a few entries for readability
    ret.sort(key=lambda x: x.read + x.write, reverse=True)
    return ret[:6]


@dataclass
class NetIO:
    name: str
    sent: float
    recv: float


_last_net_io = (time.time(), psutil.net_io_counters(pernic=True))


def network_io(ignore_names: list[str] | None = None) -> list[NetIO]:
    global _last_net_io
    ignore_names = ignore_names or []
    now = time.time()
    past_t, past = _last_net_io
    now_c = psutil.net_io_counters(pernic=True)
    dt = max(1e-6, now - past_t)
    ret: list[NetIO] = []
    for name, now_one in now_c.items():
        if any(Path(name).match(pat) for pat in ignore_names):
            continue
        if name not in past:
            continue
        past_one = past[name]
        sent = max(0.0, (now_one.bytes_sent - past_one.bytes_sent) / dt)
        recv = max(0.0, (now_one.bytes_recv - past_one.bytes_recv) / dt)
        ret.append(NetIO(name=name, sent=sent, recv=recv))
    _last_net_io = (now, now_c)
    ret.sort(key=lambda x: x.sent + x.recv, reverse=True)
    return ret[:6]


@dataclass
class ConnTest:
    name: str
    status: str
    reason: str
    delay: float
    error: str | None = None


async def connection_test() -> list[ConnTest]:
    sites = [
        ("超级主核API", "https://api.hujiarong.site/"),
        ("主核Kernyr网站", "https://www.hujiarong.site/"),
        # ("2-2服务器", "http://38.165.47.111/"),
        # ("16-16服务器", "http://154.12.30.80/"),
        # ("48-128服务器", "http://120.26.183.14:40741/"),
    ]
    out: list[ConnTest] = []
    async with httpx.AsyncClient(follow_redirects=True, timeout=5) as cli:
        for name, url in sites:
            start = time.perf_counter()
            try:
                resp = await cli.get(url)
                dt = (time.perf_counter() - start) * 1000
                out.append(
                    ConnTest(
                        name=name,
                        status=str(resp.status_code),
                        reason=resp.reason_phrase or "OK",
                        delay=dt,
                    ),
                )
            except Exception as e:
                dt = (time.perf_counter() - start) * 1000
                out.append(
                    ConnTest(
                        name=name,
                        status="ERR",
                        reason="",
                        delay=dt,
                        error=f"{e.__class__.__name__}: {e}",
                    ),
                )
    return out


@dataclass
class ProcStatus:
    name: str
    cpu: float
    mem: int


def process_status(n: int = 5) -> list[ProcStatus]:
    procs = []
    for p in psutil.process_iter(attrs=["name", "cpu_percent", "memory_info"]):
        try:
            cpu = p.info.get("cpu_percent") or 0.0
            mem = getattr(p.info.get("memory_info"), "rss", 0) or 0
            name = p.info.get("name") or str(p.pid)
            procs.append(ProcStatus(name=name, cpu=float(cpu), mem=int(mem)))
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    procs.sort(key=lambda x: (x.cpu, x.mem), reverse=True)
    return procs[:n]


async def collect_all(context: Any = None) -> dict[str, Any]:
    # 采集系统及运行状态信息，供前端模板使用
    # 先采集网络连接测试数据
    network_connection = await connection_test()

    # 将网络连接测试数据转换为服务监控格式
    services_status = []
    icon_colors = ["blue", "purple", "green", "cyan", "orange", "red"]
    for i, conn in enumerate(network_connection):
        # 判断状态：正常(2xx)、警告(3xx/4xx)、异常(ERR)
        if conn.status.startswith("2"):
            status = "normal"
        elif conn.status.startswith("3") or conn.status.startswith("4"):
            status = "warning"
        elif conn.status == "ERR":
            status = "error"
        else:
            status = "normal"

        # 根据名称判断服务类型
        if "API" in conn.name:
            service_type = "api"
        elif "网站" in conn.name or "主站" in conn.name:
            service_type = "web"
        else:
            service_type = "default"

        services_status.append(
            {
                "name": conn.name,
                "url": "",  # URL不显示在卡片上
                "type": service_type,
                "status": status,
                "ping": int(conn.delay),
                "availability": 99.99 if status == "normal" else (95.0 if status == "warning" else 0),
                "icon_color": icon_colors[i % len(icon_colors)],
            }
        )

    return {
        "cpu_percent": cpu_percent(),
        "cpu_count": cpu_count(),
        "cpu_count_logical": cpu_count_logical(),
        "cpu_freq": cpu_freq(),
        "cpu_brand": get_cpu_brand(),
        "memory_stat": memory_stat(),
        "swap_stat": swap_stat(),
        "disk_usage": disk_usage(),
        "disk_io": disk_io(),
        "network_io": network_io(),
        "network_connection": network_connection,
        "services_status": services_status,  # 添加服务监控数据
        "process_status": process_status(),
        # footer 信息：时间、Python 版本、系统名称、插件版本等
        "time": _dt_now().strftime("%Y-%m-%d %H:%M:%S"),
        "python_version": readable_python_version(),
        "system_name": system_name(),
        "astrbot_version": resolve_astrbot_version(context),
        # header：AstrBot / 机器人运行时长
        "bot_run_time": _format_td(_dt_now() - ASTRBOT_START_TIME),
        "system_run_time": _format_td(_dt_now() - BOOT_TIME),
    }
