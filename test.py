import os
import sys
import asyncio

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from utils.htmlBuilder import HtmlBuilder
from utils.monitor import Monitor


def to_number(value, default=0):
    if value is None:
        return default
    return value


def format_percent(value):
    if value is None:
        return "未知"
    return f"{float(value):.1f}%"


def pick_meter_color(percent):
    percent = to_number(percent)
    if percent >= 90:
        return "rose"
    if percent >= 75:
        return "orange"
    if percent >= 55:
        return "purple"
    return "cyan"


def build_overview(summary):
    fail_count = summary.get("serviceFailCount", 0)
    total_count = summary.get("serviceCount", 0)

    if total_count == 0:
        return {"health_level": "calm", "health_text": "系统状态稳定"}
    if fail_count == 0:
        return {"health_level": "good", "health_text": "服务全部正常"}
    if fail_count < total_count:
        return {"health_level": "warn", "health_text": "部分服务异常"}
    return {"health_level": "danger", "health_text": "服务状态异常"}


def build_resource_cards(computer):
    cpu = computer.get("cpu", {})
    memory = computer.get("memory", {})
    disk = computer.get("disk", {})

    cpu_percent = float(to_number(cpu.get("percent"), 0))
    memory_percent = float(to_number(memory.get("percent"), 0))
    disk_percent = float(to_number(disk.get("percent"), 0))

    return [
        {
            "name": "CPU",
            "icon": "CPU",
            "percent_value": round(cpu_percent, 1),
            "percent_text": format_percent(cpu_percent),
            "value": format_percent(cpu_percent),
            "detail": "当前处理器占用",
            "color": "cyan",
            "wave": "cyan",
        },
        {
            "name": "内存",
            "icon": "RAM",
            "percent_value": round(memory_percent, 1),
            "percent_text": format_percent(memory_percent),
            "value": format_percent(memory_percent),
            "detail": f"{HtmlBuilder.autoConvertUnit(to_number(memory.get('used'), 0))} / {HtmlBuilder.autoConvertUnit(to_number(memory.get('total'), 0))}",
            "color": "mint",
            "wave": "mint",
        },
        {
            "name": "磁盘",
            "icon": "SSD",
            "percent_value": round(disk_percent, 1),
            "percent_text": format_percent(disk_percent),
            "value": format_percent(disk_percent),
            "detail": f"{HtmlBuilder.autoConvertUnit(to_number(disk.get('used'), 0))} / {HtmlBuilder.autoConvertUnit(to_number(disk.get('total'), 0))}",
            "color": "purple",
            "wave": "purple",
        },
    ]


def build_service_message(service, is_ok):
    message = service.get("message") or "无额外说明"
    status_code = service.get("statusCode")

    if is_ok and status_code in {401, 403, 405}:
        return "服务在线，但限制了直接探测"

    if is_ok:
        return "服务在线"

    return message


def build_services(services):
    mapped = []

    for index, service in enumerate(services):
        is_ok = service.get("ok") is True
        status_code = service.get("statusCode")
        duration = service.get("durationMs")
        color = ["cyan", "purple", "mint", "rose"][index % 4]

        mapped.append(
            {
                "name": service.get("name", "未命名服务"),
                "url": service.get("target") or "未提供目标地址",
                "status": "good" if is_ok else "danger",
                "status_text": "正常" if is_ok else "异常",
                "status_color": "green" if is_ok else "rose",
                "ping_text": f"{round(duration, 2)}ms" if duration is not None else "无耗时",
                "code_text": "可访问" if is_ok else (f"HTTP {status_code}" if status_code is not None else "访问失败"),
                "message": build_service_message(service, is_ok),
                "color": color,
                "icon": "API",
            }
        )

    return mapped


def build_system_details(computer, summary):
    is_all_ok = summary.get("serviceFailCount", 0) == 0
    status_text = "阿柯牛逼" if is_all_ok else "阿柯死了"

    return {
        "cards": [
            {"label": "运行环境", "value": computer.get("system") or "未知"},
            {"label": "开机时间", "value": computer.get("bootTime") or "未知"},
        ],
        "status_text": status_text,
    }


async def main():
    print("正在采集真实系统信息...")

    result = Monitor.collect(
        services=[
            {"name": "超级主核API", "type": "http", "url": "https://api.hujiarong.site/"},
            {"name": "主核Kernyr网站", "type": "http", "url": "https://www.hujiarong.site/"},
        ],
        timeout=5,
    )

    computer = result["computer"]
    services = result["services"]
    summary = result["summary"]

    resources_dir = os.path.join(os.path.dirname(__file__), "resources")
    avatar_path = os.path.join(resources_dir, "avatar.jpg")

    avatar_bytes = None

    if os.path.exists(avatar_path):
        with open(avatar_path, "rb") as file:
            avatar_bytes = file.read()
        print(f"加载头像: {avatar_path}")

    collected = {
        "hostname": computer.get("hostName", "主服务器"),
        "os_info": f"{computer.get('system', 'Unknown')} ({computer.get('machine', '')})",
        "summary": summary,
        "overview": build_overview(summary),
        "resource_cards": build_resource_cards(computer),
        "services_status": build_services(services),
        "system_details": build_system_details(computer, summary),
    }

    html_content = HtmlBuilder.build(
        collected=collected,
        avatarBytes=avatar_bytes,
    )

    html_output_path = os.path.join(os.path.dirname(__file__), "output_test.html")
    with open(html_output_path, "w", encoding="utf-8") as file:
        file.write(html_content)

    print(f"\nHTML文件已生成: {html_output_path}")
    print("\n=== 采集的真实数据 ===")
    print(f"主机名: {computer.get('hostName')}")
    print(f"系统: {computer.get('system')} {computer.get('systemVersion')} {computer.get('machine')}")
    print(f"Python版本: {computer.get('pythonVersion')}")
    print(f"开机时间: {computer.get('bootTime')}")
    print(f"CPU信息: {computer.get('cpu')}")
    print(f"内存信息: {computer.get('memory')}")
    print(f"磁盘信息: {computer.get('disk')}")
    print(f"服务摘要: {summary}")

    for item in services:
        print(f"服务 {item.get('name')}: ok={item.get('ok')} statusCode={item.get('statusCode')} target={item.get('target')} message={item.get('message')}")


if __name__ == "__main__":
    asyncio.run(main())
