import os
import sys
import asyncio

# 添加项目路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from htmlBuilder import HtmlBuilder
from data.monitor import Monitor


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

    collected = {
        "hostname": computer.get("hostName", "主服务器"),
        "os_info": computer.get("system", "Unknown"),
        "cpu_percent": computer["cpu"].get("percent", 0),
        "cpu_count": os.cpu_count(),
        "cpu_count_logical": os.cpu_count(),
        "memory_stat": type("obj", (object,), {"used": computer["memory"].get("used", 0), "total": computer["memory"].get("total", 0), "percent": computer["memory"].get("percent", 0)}),
        "disk_usage": [type("obj", (object,), {"used": computer["disk"].get("used", 0), "total": computer["disk"].get("total", 0), "percent": computer["disk"].get("percent", 0)})],
        "time": result["checkedAt"],
        "python_version": computer.get("pythonVersion", ""),
        "system_name": computer.get("system", ""),
        "astrbot_version": "v1",
        "system_run_time": f"运行中",
        "services_status": [
            {"name": s["name"], "url": s.get("target", ""), "type": "api" if "API" in s["name"] else "web", "status": "normal" if s["ok"] else "error", "ping": int(s.get("durationMs", 0)), "availability": 99.99 if s["ok"] else 0, "icon_color": "blue" if i % 2 == 0 else "purple"} for i, s in enumerate(services)
        ],
    }

    # 渲染HTML（使用空的背景字节，会使用CSS渐变背景）
    html_content = HtmlBuilder.build(collected, b"", "image/jpeg")

    # 保存HTML到文件
    html_output_path = os.path.join(os.path.dirname(__file__), "output_test.html")
    with open(html_output_path, "w", encoding="utf-8") as f:
        f.write(html_content)
    print(f"HTML文件已生成: {html_output_path}")
    print("\n采集的真实数据：")
    print(f"CPU使用率: {collected['cpu_percent']}%")
    print(f"内存使用率: {collected['memory_stat'].percent}%")
    print(f"服务监控: {len(collected['services_status'])}个服务")
    for service in collected["services_status"]:
        print(f"  - {service['name']}: {service['status']} ({service['ping']}ms)")


if __name__ == "__main__":
    asyncio.run(main())
