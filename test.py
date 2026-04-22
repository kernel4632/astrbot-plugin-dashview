import os
import sys
import asyncio
from datetime import datetime

# 添加项目路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from utils.htmlBuilder import HtmlBuilder
from utils.monitor import Monitor


def format_uptime(boot_time_str):
    """计算系统运行时间"""
    try:
        boot_time = datetime.strptime(boot_time_str, "%Y-%m-%d %H:%M:%S")
        now = datetime.now()
        delta = now - boot_time
        hours = delta.days * 24 + delta.seconds // 3600
        minutes = (delta.seconds % 3600) // 60
        seconds = delta.seconds % 60
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    except:
        return "00:00:00"


def format_bytes(bytes_value):
    """格式化字节数"""
    if bytes_value is None:
        return "0B"
    for unit in ["B", "KB", "MB", "GB"]:
        if bytes_value < 1024:
            return f"{bytes_value:.1f}{unit}"
        bytes_value /= 1024
    return f"{bytes_value:.1f}TB"


async def main():
    print("正在采集真实系统信息...")

    # 调用Monitor获取系统数据
    result = Monitor.collect(
        services=[
            {"name": "超级主核API", "type": "http", "url": "https://api.hujiarong.site/"},
            {"name": "主核Kernyr网站", "type": "http", "url": "https://www.hujiarong.site/"},
        ],
        timeout=5,
    )

    # 构建模板需要的数据格式
    computer = result["computer"]
    services = result["services"]
    summary = result["summary"]

    # 计算运行时间
    boot_time_str = computer.get("bootTime")
    system_run_time = format_uptime(boot_time_str) if boot_time_str else "00:00:00"

    # 准备数据
    collected = {
        # 头部信息
        "hostname": computer.get("hostName", "主服务器"),
        "os_info": f"{computer.get('system', 'Unknown')} ({computer.get('machine', '')})",
        "time": result["checkedAt"],
        "astrbot_version": "v1",
        "system_run_time": system_run_time,
        # CPU信息
        "cpu_percent": computer["cpu"].get("percent", 0),
        "cpu_count": computer["cpu"].get("logicalCount", os.cpu_count() or 1),
        "cpu_count_logical": computer["cpu"].get("logicalCount", os.cpu_count() or 1),
        # 内存信息
        "memory_stat": type("obj", (object,), {"used": computer["memory"].get("used", 0), "total": computer["memory"].get("total", 0), "percent": computer["memory"].get("percent", 0)}),
        # 磁盘信息
        "disk_usage": [type("obj", (object,), {"name": computer["disk"].get("path", "/"), "used": computer["disk"].get("used", 0), "total": computer["disk"].get("total", 0), "percent": computer["disk"].get("percent", 0)})],
        # 其他系统信息
        "python_version": computer.get("pythonVersion", ""),
        "system_name": computer.get("system", ""),
        # 服务状态
        "services_status": [
            {"name": s["name"], "url": s.get("target", ""), "type": "api" if "API" in s["name"] else "web", "status": "normal" if s["ok"] else "error", "ping": int(s.get("durationMs", 0)), "availability": 99.99 if s["ok"] else 0, "icon_color": "cyan" if i % 2 == 0 else "purple"} for i, s in enumerate(services)
        ],
        # 汇总信息
        "summary": summary,
    }

    # 读取背景图和头像
    resources_dir = os.path.join(os.path.dirname(__file__), "resources", "assets")
    background_path = os.path.join(resources_dir, "default_bg.webp")
    avatar_path = os.path.join(resources_dir, "avatar.webp")

    background_bytes = b""
    background_mime = "image/webp"
    avatar_bytes = None

    if os.path.exists(background_path):
        with open(background_path, "rb") as f:
            background_bytes = f.read()
        print(f"加载背景图: {background_path}")

    if os.path.exists(avatar_path):
        with open(avatar_path, "rb") as f:
            avatar_bytes = f.read()
        print(f"加载头像: {avatar_path}")

    # 渲染HTML
    html_content = HtmlBuilder.build(collected=collected, backgroundBytes=background_bytes, backgroundMime=background_mime, avatarBytes=avatar_bytes)

    # 保存HTML到文件
    html_output_path = os.path.join(os.path.dirname(__file__), "output_test.html")
    with open(html_output_path, "w", encoding="utf-8") as f:
        f.write(html_content)
    print(f"\nHTML文件已生成: {html_output_path}")

    # 打印采集的真实数据
    print("\n=== 采集的真实数据 ===")
    print(f"检测时间: {result['checkedAt']}")
    print(f"主机名: {computer.get('hostName')}")
    print(f"系统: {computer.get('system')} {computer.get('machine')}")
    print(f"本地IP: {computer.get('localIp')}")
    print(f"Python版本: {computer.get('pythonVersion')}")
    print(f"开机时间: {computer.get('bootTime')}")
    print(f"运行时长: {system_run_time}")
    print(f"\nCPU使用率: {computer['cpu'].get('percent')}%")
    print(f"CPU核心数: {computer['cpu'].get('logicalCount')}")
    print(f"内存使用率: {computer['memory'].get('percent')}% ({format_bytes(computer['memory'].get('used'))} / {format_bytes(computer['memory'].get('total'))})")
    print(f"磁盘使用率: {computer['disk'].get('percent')}% ({format_bytes(computer['disk'].get('used'))} / {format_bytes(computer['disk'].get('total'))})")
    print(f"\n服务监控 ({summary['serviceCount']}个):")
    for service in services:
        status = "正常" if service["ok"] else "失败"
        print(f"  - {service['name']}: {status} ({service.get('durationMs', 0)}ms)")


if __name__ == "__main__":
    asyncio.run(main())
