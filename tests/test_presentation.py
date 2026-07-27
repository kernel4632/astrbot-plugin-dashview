"""展示测试：验证模板契约和真实 Chromium 图片输出。"""

import importlib.util                                      # 从仓库根目录加载确定预览样本
from pathlib import Path                                   # 定位不属于安装包的 test.py

from dashview.config import Settings                       # 构造安全转义配置
from dashview.presentation.html import render_html          # 被测离线模板入口
from dashview.presentation.image import close_browser, render_image # 被测浏览器截图生命周期
from dashview.presentation.view import build_dashboard_view # 构造正式视图


PREVIEW_PATH = Path(__file__).parent.parent / "test.py"    # 预览脚本与插件根目录同级
PREVIEW_SPEC = importlib.util.spec_from_file_location("dashview_preview", PREVIEW_PATH)
assert PREVIEW_SPEC is not None and PREVIEW_SPEC.loader is not None # 测试环境必须包含预览脚本
PREVIEW = importlib.util.module_from_spec(PREVIEW_SPEC)
PREVIEW_SPEC.loader.exec_module(PREVIEW)                    # 只加载构造函数，不执行 main()


# --- 构造覆盖完整 24 小时模型槽的 HTML ---
def dashboard_html() -> str:
    computer = PREVIEW.build_computer()
    report, model_history = PREVIEW.build_models(computer)
    view = build_dashboard_view(
        computer,
        PREVIEW.build_resource_history(computer),
        PREVIEW.build_services(computer),
        report,
        model_history,
        Settings(nickname="<script>alert(1)</script>"),       # 验证用户文本不会进入 HTML 代码
        now_ms=computer["observed_at"],
    )
    return render_html(view, avatar_bytes=None)


# --- 模板输出固定 24 格并安全转义用户文本 ---
def test_html_renders_hour_slots_and_escapes_text() -> None:
    html = dashboard_html()
    assert html.count('class="availability-point ') == 4 * 24 # 四模型各有完整 24 个小时槽
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "当前延迟" not in html                           # 已确认无价值的模型数字不再渲染
    assert "Provider" not in html                           # API 类型汇总不再占据状态面板
    assert "backdrop-filter" not in html                    # 长图禁止使用会错位的 Chromium 合成层


# --- Chromium 能离线生成受控 JPEG ---
async def test_chromium_renders_dashboard_jpeg() -> None:
    try:
        image = await render_image(dashboard_html())
        assert image.startswith(b"\xff\xd8")               # 最终消息产物是真实 JPEG
        assert len(image) > 20_000                           # 非空白或异常占位图片
    finally:
        await close_browser()                               # 测试结束不保留浏览器子进程


# --- WebUI 允许的最大详情数量仍能生成单张图片 ---
async def test_maximum_rows_fit_image_height() -> None:
    computer = PREVIEW.build_computer()
    base_report, base_history = PREVIEW.build_models(computer)
    routes = []
    history = {}
    for index in range(8):                                  # 配置上限为八张模型图
        source = base_report["routes"][index % len(base_report["routes"])]
        route = {**source, "route_id": f"provider-{index}::model-{index}", "provider_id": f"provider-{index}", "model_name": f"model-{index}-" + "very-long-name-" * 8, "state": "unavailable", "reason": "very-long-failure-reason-" * 12}
        routes.append(route)
        history[route["route_id"]] = base_history[source["route_id"]]
    report = {**base_report, "routes": routes, "route_count": 8}
    services = [{**service, "id": f"service-{index}", "name": f"服务 {index}"} for index, service in enumerate((PREVIEW.build_services(computer) * 2)[:8])]
    view = build_dashboard_view(computer, PREVIEW.build_resource_history(computer), services, report, history, Settings(), now_ms=computer["observed_at"])

    try:
        image = await render_image(render_html(view, avatar_bytes=None))
        assert image.startswith(b"\xff\xd8")               # 最大合法配置不依赖高度异常作为流程控制
    finally:
        await close_browser()
