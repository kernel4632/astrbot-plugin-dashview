"""
DashView 本地预览：使用确定的完整状态样本生成 output_test.html 和 output_test.jpg。

它不访问外部服务、不调用真实模型、不写 AstrBot KV；用于快速检查模板布局和 Chromium 截图。
调用示例：uv run python test.py
"""

from __future__ import annotations                         # 允许现代类型注解

import asyncio                                             # 运行异步浏览器截图
import sys                                                 # 注册本地预览包名
from pathlib import Path                                   # 定位仓库与输出文件
from types import ModuleType                               # 避免导入需要 AstrBot 的 main.py


ROOT = Path(__file__).parent                               # 仓库根目录包含所有被预览模块
package = ModuleType("dashview")                           # 连字符目录不能直接作为 Python 包导入
package.__path__ = [str(ROOT)]                              # 相对导入从当前仓库解析
sys.modules.setdefault("dashview", package)               # 注册预览专用包身份

from dashview.config import Settings                       # 使用正式配置数据类
from dashview.presentation.html import render_html          # 使用正式离线 HTML 渲染器
from dashview.presentation.image import close_browser, render_image # 使用正式 Chromium 生命周期
from dashview.presentation.view import build_dashboard_view # 使用正式可信视图转换


OUTPUT_HTML = ROOT / "output_test.html"                    # 浏览器可直接检查的单文件页面
OUTPUT_IMAGE = ROOT / "output_test.jpg"                    # AstrBot 最终发送效果的 JPEG


# --- 构造覆盖健康、降级和异常的主机事实 ---
def build_computer() -> dict:
    observed_at = 1_785_088_800_000                         # 固定时间确保每次输出内容一致
    return {
        "observed_at": observed_at,
        "hostname": "astrbot-node-01",
        "system": "Linux",
        "system_version": "6.8.0",
        "architecture": "x86_64",
        "boot_at": observed_at - 9 * 86400 * 1000 - 5 * 3600 * 1000,
        "process_count": 86,
        "cpu": {"percent": 36.4, "logical_count": 16},
        "memory": {"percent": 68.2, "used": 23_430_000_000, "total": 34_360_000_000},
        "swap": {"percent": 12.5, "used": 1_070_000_000, "total": 8_590_000_000},
        "disk": {"percent": 83.7, "used": 431_640_000_000, "total": 515_400_000_000, "path": "/", "read": 8_000_000_000_000, "written": 5_000_000_000_000, "read_per_second": 18_400_000, "write_per_second": 7_200_000},
        "network": {"sent": 918_000_000_000, "received": 2_340_000_000_000, "sent_per_second": 821_000, "received_per_second": 4_280_000},
    }


# --- 构造最近 24 小时真实资源历史 ---
def build_resource_history(computer: dict) -> dict:
    observed_at = computer["observed_at"]
    cpu = [28 + (index % 6) * 5 + (-8 if index % 5 == 0 else 0) for index in range(24)]
    memory = [55 + index * 0.55 + (3 if index % 7 == 0 else 0) for index in range(24)]
    swap = [8 + index * 0.18 for index in range(24)]
    disk_read = [2_000_000 + (index % 5) * 3_200_000 + (12_000_000 if index in {7, 18} else 0) for index in range(24)]
    disk_write = [1_000_000 + (index % 4) * 2_000_000 + (8_000_000 if index in {11, 20} else 0) for index in range(24)]
    history = {}
    for resource_id, series in {"cpu": cpu, "memory": memory, "swap": swap}.items():
        history[resource_id] = [{"observed_at": observed_at - (23 - index) * 3_600_000, "percent": round(percent, 1)} for index, percent in enumerate(series)]
    history["disk"] = [{
        "observed_at": observed_at - (23 - index) * 3_600_000,
        "percent": round(80.1 + index * 0.16, 1),
        "read_per_second": disk_read[index],
        "write_per_second": disk_write[index],
    } for index in range(24)]
    return history


# --- 构造服务检测事实 ---
def build_services(computer: dict) -> list[dict]:
    base = {"observed_at": computer["observed_at"], "status_code": None}
    return [
        {**base, "id": "webui", "name": "AstrBot WebUI", "type": "http", "target": "http://127.0.0.1:6185", "state": "healthy", "duration_ms": 18, "status_code": 200, "reason": "服务响应正常"},
        {**base, "id": "openai", "name": "OpenAI API", "type": "http", "target": "https://api.openai.com/v1/models", "state": "restricted", "duration_ms": 124, "status_code": 401, "reason": "服务可达，但探测请求受限"},
        {**base, "id": "onebot", "name": "OneBot 接口", "type": "tcp", "target": "127.0.0.1:3001", "state": "healthy", "duration_ms": 3, "reason": "端口连接正常"},
        {**base, "id": "anthropic", "name": "Anthropic API", "type": "http", "target": "https://api.anthropic.com", "state": "down", "duration_ms": 5001, "reason": "连接超时"},
    ]


# --- 构造 AstrBot API Provider 报告和对应真实历史 ---
def build_models(computer: dict) -> tuple[dict, dict]:
    observed_at = computer["observed_at"]                   # 所有路由属于同一探测批次
    route_definitions = [
        ("openai::gpt-4o", "openai", "OpenAI API", "gpt-4o", "available", 1240, "响应正确"),
        ("anthropic::claude", "anthropic", "Anthropic API", "claude-sonnet-4", "slow", 9360, "响应较慢"),
        ("gemini::flash", "gemini", "Google Gemini API", "gemini-2.5-flash", "available", 680, "响应正确"),
        ("deepseek::chat", "deepseek", "DeepSeek API", "deepseek-chat", "unavailable", 30001, "超过 30 秒未响应"),
    ]
    routes = [{
        "route_id": route_id,
        "provider_id": provider_id,
        "provider_name": provider_name,
        "model_name": model_name,
        "state": state,
        "latency_ms": latency,
        "observed_at": observed_at,
        "reason": reason,
    } for route_id, provider_id, provider_name, model_name, state, latency, reason in route_definitions]

    history = {}
    for route in routes:
        states = ["available"] * 24                        # 完整覆盖 24 小时，每小时一个真实语义点
        if route["state"] == "slow":
            states[7], states[15], states[-1] = "slow", "slow", "slow"
        elif route["state"] == "unavailable":
            states[9], states[18], states[-1] = "unavailable", "slow", "unavailable"
        history[route["route_id"]] = [{
            "observed_at": observed_at - (len(states) - 1 - index) * 3_600_000,
            "state": state,
            "latency_ms": route["latency_ms"] if state == route["state"] else 9_200 if state == "slow" else max(300, min(1800, route["latency_ms"] // 2)),
        } for index, state in enumerate(states)]

    report = {
        "observed_at": observed_at,
        "duration_ms": 30_120,
        "route_count": len(routes),
        "available_count": 2,
        "slow_count": 1,
        "invalid_count": 0,
        "unavailable_count": 1,
        "state": "critical",
        "routes": routes,
    }
    return report, history


# --- 生成 HTML 和最终图片 ---
async def main() -> None:
    computer = build_computer()                             # 载入固定主机事实
    report, model_history = build_models(computer)          # 载入固定模型事实与历史
    view = build_dashboard_view(
        computer=computer,
        resource_history=build_resource_history(computer),  # 固定趋势确保截图可比较
        services=build_services(computer),                  # 同时覆盖三种服务状态
        model_report=report,                                # 同时覆盖正常、慢速和失败模型
        model_history=model_history,
        settings=Settings(),                                # 使用插件正式默认阈值和文案
        now_ms=computer["observed_at"],                     # 固定时钟保证预览新鲜度完全确定
    )
    html = render_html(view, avatar_bytes=None)             # 默认头像也必须能够离线渲染
    OUTPUT_HTML.write_text(html, encoding="utf-8")         # 输出单文件供浏览器检查结构
    OUTPUT_IMAGE.write_bytes(await render_image(html))      # 输出与机器人发送一致的 JPEG
    await close_browser()                                   # 本地脚本结束前主动释放 Chromium
    print(f"HTML: {OUTPUT_HTML}")                           # 告知用户两个实际输出路径
    print(f"Image: {OUTPUT_IMAGE}")


if __name__ == "__main__":
    asyncio.run(main())                                     # 从同步命令行进入异步截图流程
