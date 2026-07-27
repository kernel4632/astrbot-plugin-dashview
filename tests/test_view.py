"""视图测试：验证缺失数据不被伪造、综合状态覆盖三个健康领域。"""

from dashview.config import Settings                       # 构建默认阈值
from dashview.presentation.view import build_dashboard_view # 被测可信视图入口


def computer_fixture() -> dict:
    """返回字段完整但数值简单的确定主机快照。"""
    return {
        "observed_at": 1_700_000_000_000,
        "boot_at": 1_699_990_000_000,
        "hostname": "bot-host",
        "system": "Linux",
        "system_version": "6.1",
        "architecture": "x86_64",
        "process_count": 42,
        "cpu": {"percent": None, "logical_count": 8},    # 故意缺失以验证未知状态
        "memory": {"percent": 40.0, "used": 4, "total": 10},
        "swap": {"percent": 0.0, "used": 0, "total": 0},
        "disk": {"percent": 50.0, "used": 5, "total": 10, "path": "/"},
        "network": {"sent": 10, "received": 20, "sent_per_second": None, "received_per_second": None},
    }


# --- 缺失资源和模型历史显示暂无数据 ---
def test_missing_measurements_remain_unknown() -> None:
    report = {
        "state": "healthy", "observed_at": 1_700_000_000_000, "duration_ms": 20,
        "routes": [{"route_id": "p::m", "provider_id": "p", "provider_name": "P", "model_name": "m", "state": "available", "latency_ms": 20, "observed_at": 1_700_000_000_000, "reason": "响应正确"}],
    }
    view = build_dashboard_view(computer_fixture(), {}, [], report, {}, Settings(), now_ms=report["observed_at"])

    assert view["resources"][0]["current"] == "暂无数据"   # CPU 缺失不显示 0%
    assert all(slot["state"] == "unknown" for slot in view["models"]["rows"][0]["history_chart"]["slots"]) # 无历史显示 24 个未知格


# --- 任一模型功能异常进入综合严重状态 ---
def test_model_failure_affects_overall_health() -> None:
    observed_at = 1_700_000_000_000
    report = {"state": "critical", "observed_at": observed_at, "duration_ms": 20, "routes": []}
    view = build_dashboard_view(computer_fixture(), {}, [], report, {}, Settings(), now_ms=observed_at)
    assert view["overall"]["state"] == "critical"          # 总状态不再只观察服务


# --- 模型图固定 24 格且缺测小时明确为未知 ---
def test_model_history_has_24_slots_with_unknown_gaps() -> None:
    observed_at = 1_700_006_400_000                         # 使用整点保证小时桶边界确定
    route = {"route_id": "p::m", "provider_id": "p", "provider_name": "API", "model_name": "m", "state": "available", "latency_ms": 20, "observed_at": observed_at, "reason": "响应正确"}
    report = {"state": "healthy", "observed_at": observed_at, "duration_ms": 20, "routes": [route]}
    history = {"p::m": [
        {"observed_at": observed_at - 23 * 3_600_000, "state": "available", "latency_ms": 10},
        {"observed_at": observed_at - 17 * 3_600_000, "state": "slow", "latency_ms": 9000},
        {"observed_at": observed_at, "state": "available", "latency_ms": 20},
    ]}

    view = build_dashboard_view(computer_fixture(), {}, [], report, history, Settings(), now_ms=observed_at)
    slots = view["models"]["rows"][0]["history_chart"]["slots"]
    assert len(slots) == 24                                 # 每张图始终覆盖完整 24 小时
    assert sum(slot["known"] for slot in slots) == 3       # 只承认三个真实探测结果
    assert slots[1]["state"] == "unknown"                  # 漏测小时显示灰格而非成功


# --- 延迟线不会跨越失败或缺测小时 ---
def test_latency_curve_breaks_across_unknown_gap() -> None:
    observed_at = 1_700_006_400_000
    route = {"route_id": "p::m", "provider_id": "p", "provider_name": "API", "model_name": "m", "state": "available", "latency_ms": 20, "observed_at": observed_at, "reason": "响应正确"}
    report = {"state": "healthy", "observed_at": observed_at, "duration_ms": 20, "routes": [route]}
    history = {"p::m": [
        {"observed_at": observed_at - 2 * 3_600_000, "state": "available", "latency_ms": 100},
        {"observed_at": observed_at, "state": "available", "latency_ms": 120},
    ]}
    view = build_dashboard_view(computer_fixture(), {}, [], report, history, Settings(), now_ms=observed_at)
    assert view["models"]["rows"][0]["history_chart"]["curve_segments"] == [] # 中间缺测不能连成趋势


# --- 超过两小时的模型故障只作为历史，不算当前问题 ---
def test_stale_model_failure_becomes_unknown() -> None:
    observed_at = 1_700_006_400_000
    route = {"route_id": "p::m", "provider_id": "p", "provider_name": "API", "model_name": "m", "state": "unavailable", "latency_ms": 30_000, "observed_at": observed_at, "reason": "超时"}
    report = {"state": "critical", "observed_at": observed_at, "duration_ms": 30_000, "routes": [route]}
    view = build_dashboard_view(computer_fixture(), {}, [], report, {"p::m": [route]}, Settings(), now_ms=observed_at + 2 * 3_600_000 + 1)

    assert view["models"]["state"] == "unknown"             # 当前状态不沿用两小时前报告
    assert view["models"]["rows"][0]["visual_state"] == "unknown"
    assert all(issue["domain"] != "模型" for issue in view["issues"]) # 旧故障不再叫当前问题


# --- 资源阈值线与配置一致且横轴保留真实间隔 ---
def test_resource_chart_uses_configured_thresholds_and_time() -> None:
    computer = computer_fixture()
    computer["cpu"]["percent"] = 30.0
    start = computer["observed_at"] - 4_000
    history = {"cpu": [
        {"observed_at": start, "percent": 10},
        {"observed_at": start + 1_000, "percent": 20},
        {"observed_at": start + 4_000, "percent": 30},
    ]}
    view = build_dashboard_view(computer, history, [], None, {}, Settings(cpu_warning=70, cpu_critical=90), now_ms=computer["observed_at"])

    assert view["resources"][0]["points"].split()[1].startswith("25.0,") # 一秒只占四秒时间轴的四分之一
    assert view["resources"][0]["warning_y"] == 14.2      # 38 - 70% * 0.34
    assert view["resources"][0]["critical_y"] == 7.4      # 38 - 90% * 0.34


# --- 磁盘卡使用读写速率趋势而不是容量占用趋势 ---
def test_disk_card_uses_io_rate_curves() -> None:
    computer = computer_fixture()
    computer["disk"].update({"read_per_second": 4096, "write_per_second": 2048})
    history = {"disk": [
        {"observed_at": computer["observed_at"] - 3_600_000, "percent": 49, "read_per_second": 1024, "write_per_second": 512},
        {"observed_at": computer["observed_at"], "percent": 50, "read_per_second": 4096, "write_per_second": 2048},
    ]}
    view = build_dashboard_view(computer, history, [], None, {}, Settings(), now_ms=computer["observed_at"])
    disk = next(item for item in view["resources"] if item["id"] == "disk")
    assert disk["current"] == "↓ 4.0 KB/s"
    assert disk["secondary"] == "↑ 2.0 KB/s"
    assert disk["points"] == ""                           # 容量百分比不再作为磁盘主曲线
    assert disk["read_points"] and disk["write_points"]   # 两条曲线均来自真实速率样本


# --- 主动关闭模型监控不让其他健康领域变成未知 ---
def test_disabled_model_monitor_is_excluded_from_overall_health() -> None:
    computer = computer_fixture()
    computer["cpu"]["percent"] = 20.0                      # 四项资源全部可观测且健康
    view = build_dashboard_view(computer, {}, [], None, {}, Settings(model_monitor_enabled=False), now_ms=computer["observed_at"])
    assert view["models"]["state"] == "disabled"           # 与尚未检测严格区分
    assert view["overall"]["state"] == "healthy"           # 主动关闭的领域不冒充故障或缺测


# --- 关闭后台后旧定时报告失效，但新手动报告仍可查看 ---
def test_disabled_monitor_only_accepts_fresh_manual_report() -> None:
    computer = computer_fixture()
    computer["cpu"]["percent"] = 20.0
    observed_at = computer["observed_at"]
    route = {"route_id": "p::m", "provider_id": "p", "model_name": "m", "state": "unavailable", "latency_ms": 30_000, "observed_at": observed_at, "reason": "超时"}
    scheduled = {"source": "scheduled", "state": "critical", "observed_at": observed_at, "routes": [route]}
    manual = {**scheduled, "source": "manual"}
    settings = Settings(model_monitor_enabled=False)

    scheduled_view = build_dashboard_view(computer, {}, [], scheduled, {}, settings, now_ms=observed_at)
    manual_view = build_dashboard_view(computer, {}, [], manual, {}, settings, now_ms=observed_at)
    assert scheduled_view["models"]["state"] == "disabled" # 关闭配置立即退出旧后台状态
    assert manual_view["models"]["state"] == "critical"    # 管理员即时检测仍给出当前结果


# --- 全部停用服务与从未配置严格区分 ---
def test_disabled_services_have_explicit_empty_state() -> None:
    computer = computer_fixture()
    settings = Settings.from_dict({"services": [{"enabled": False, "name": "暂时停用"}], "model_monitor_enabled": False})
    view = build_dashboard_view(computer, {}, [], None, {}, settings, now_ms=computer["observed_at"])
    assert view["services"]["empty_text"] == "服务监控已全部停用"
    assert view["health_strip"][1]["value"] == "已关闭"     # 顶部摘要不误报未配置


# --- 同名模型只在必要时显示稳定配置 ID ---
def test_duplicate_model_names_use_stable_identity_hint() -> None:
    observed_at = computer_fixture()["observed_at"]
    routes = [
        {"route_id": "provider-a::gpt", "provider_id": "provider-a", "provider_name": "相同显示名", "model_name": "gpt", "state": "available", "latency_ms": 100, "observed_at": observed_at, "reason": "响应正确"},
        {"route_id": "provider-b::gpt", "provider_id": "provider-b", "provider_name": "相同显示名", "model_name": "gpt", "state": "available", "latency_ms": 100, "observed_at": observed_at, "reason": "响应正确"},
    ]
    report = {"state": "healthy", "source": "scheduled", "observed_at": observed_at, "routes": routes}
    view = build_dashboard_view(computer_fixture(), {}, [], report, {}, Settings(), now_ms=observed_at)
    assert {route["identity_hint"] for route in view["models"]["rows"]} == {"provider-a", "provider-b"}
