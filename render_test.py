import os
import sys
import asyncio

# 添加项目路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from renderers.t2i_renderer import build_default_html
from data.collectors import collect_all


async def main():
    print("正在采集真实系统信息...")

    # 调用真实的采集函数获取系统数据
    collected = await collect_all()

    # 添加hostname字段（模板需要）
    collected["hostname"] = "主服务器"

    # 添加os_info字段（模板需要）
    collected["os_info"] = collected.get("system_name", "Unknown")

    # 渲染HTML（使用空的背景字节，会使用CSS渐变背景）
    html_content = build_default_html(collected, b"", "image/jpeg")

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
