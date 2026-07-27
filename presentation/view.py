"""
仪表盘视图数据：把主机、服务、模型和历史事实整理成模板可直接阅读的结构。

所有统计都来自真实样本；缺失数据保持“暂无数据”，严重项优先且明示截断数量。
调用示例：view = build_dashboard_view(computer, history, services, report, model_history, settings)
"""

from __future__ import annotations                         # 允许现代类型注解

from datetime import datetime                              # 把 UTC 时间转成稳定展示文本
from typing import Any                                     # 描述跨采集器的组合数据

from ..config import Settings                              # 读取阈值、文案和展示上限


STATE_ORDER = {"critical": 0, "degraded": 1, "unknown": 2, "healthy": 3} # 详情始终把问题放前面
MODEL_SLOT_MS = 3_600_000                                  # 模型 Uptime 每格固定代表一小时
MODEL_SLOT_COUNT = 24                                      # 当前小时加之前 23 小时
MODEL_FRESHNESS_MS = 2 * MODEL_SLOT_MS                     # 连续错过两轮后当前状态转为未知
MODEL_STATE_RANK = {"available": 0, "slow": 1, "invalid": 2, "unavailable": 3} # 防御性归并最差样本


# --- 构建整张仪表盘的数据契约 ---
def build_dashboard_view(
    computer: dict[str, Any],                               # 本次主机真实快照
    resource_history: dict[str, list[dict[str, Any]]],      # 已保存的资源真实历史
    services: list[dict[str, Any]],                         # 本次服务检测结果
    model_report: dict[str, Any] | None,                    # 最近一次模型报告，可能尚未执行
    model_history: dict[str, list[dict[str, Any]]],         # 每条模型路由的真实历史
    settings: Settings,                                     # 已验证的显示与阈值配置
    now_ms: int | None = None,                              # 测试可注入统一时钟
) -> dict[str, Any]:
    current_ms = now_ms if now_ms is not None else int(datetime.now().timestamp() * 1000)
    resources = _build_resources(computer, resource_history, settings)
    service_view = _build_services(services, settings.max_service_rows, settings.services_configured)
    model_view = _build_models(model_report, model_history, settings, current_ms)
    issues = _build_issues(resources, services, model_view["all_routes"])
    overall = _build_overall(resources, services, model_view["state"])

    return {
        "version": "2.0.0",                               # 图片页脚显示当前重构版本
        "generated_at": _time_text(computer["observed_at"]),
        "nickname": settings.nickname,                    # 保留作者个性化默认文案
        "host": _build_host(computer),                     # 顶部紧凑主机说明
        "overall": overall,                               # 综合资源、服务和模型后的总状态
        "health_strip": _build_health_strip(resources, service_view, model_view),
        "issues": issues[:8],                             # 静态图片只展开最重要的八项
        "hidden_issue_count": max(0, len(issues) - 8),     # 明示还有多少问题未展开
        "resources": resources,                           # 四项资源真实趋势
        "network": _build_network(computer["network"]),  # 当前流量与速率
        "services": service_view,                         # 服务聚合与受控详情行
        "models": model_view,                             # Provider 聚合与关键模型行
        "conclusion": _conclusion_text(overall["state"], settings), # 未知和降级不再冒充成功
    }


# --- 整理主机身份与运行时长 ---
def _build_host(computer: dict[str, Any]) -> dict[str, str]:
    uptime_seconds = max(0, (computer["observed_at"] - computer["boot_at"]) // 1000)
    return {
        "name": computer.get("hostname") or "未知主机",    # 事实主机名与用户昵称分开
        "system": f"{computer.get('system', 'Unknown')} {computer.get('system_version', '')}".strip(),
        "architecture": str(computer.get("architecture") or "未知架构"),
        "uptime": _duration_text(uptime_seconds),          # 使用持续时间比开机日期更易扫描
        "processes": f"{computer.get('process_count', 0)} 个进程",
    }


# --- 构建四项资源卡片 ---
def _build_resources(computer: dict[str, Any], history: dict[str, list[dict[str, Any]]], settings: Settings) -> list[dict[str, Any]]:
    definitions = [
        ("cpu", "CPU", settings.cpu_warning, settings.cpu_critical),
        ("memory", "内存", settings.memory_warning, settings.memory_critical),
        ("disk", "磁盘", settings.disk_warning, settings.disk_critical),
        ("swap", "交换区", settings.memory_warning, settings.memory_critical),
    ]
    resources: list[dict[str, Any]] = []                    # 保持固定顺序便于快速形成视觉记忆
    for resource_id, label, warning, critical in definitions:
        current = computer.get(resource_id, {})             # 当前值来自同一主机快照
        percent = current.get("percent")
        samples = sorted(
            [sample for sample in history.get(resource_id, []) if _valid_resource_sample(sample)],
            key=lambda sample: int(sample["observed_at"]),  # 资源曲线按真实采样时刻推进
        )
        values = [float(sample["percent"]) for sample in samples]
        state = _threshold_state(percent, warning, critical)
        resources.append({
            "id": resource_id,                            # CSS 和问题列表使用稳定资源标识
            "label": label,                               # 面向用户的中文名称
            "state": state,                               # healthy/degraded/critical/unknown
            "current": _percent_text(percent),            # None 显示暂无数据而不是 0%
            "detail": _usage_text(current),               # CPU 没有容量时展示核心数
            "average": _percent_text(sum(values) / len(values) if values else None),
            "peak": _percent_text(max(values) if values else None),
            "sample_count": len(values),                  # 样本数让统计可信度显式可见
            "points": _resource_curve_points(samples),    # 横轴保留暂停和间隔变化
            "warning_y": _resource_threshold_y(warning),  # 阈值线与 WebUI 有效配置一致
            "critical_y": _resource_threshold_y(critical),
        })
    resources[0]["detail"] = f"{computer.get('cpu', {}).get('logical_count') or '?'} 线程"
    return resources


# --- 汇总并截取服务详情 ---
def _build_services(services: list[dict[str, Any]], row_limit: int, configured: bool) -> dict[str, Any]:
    state_rank = {"down": 0, "configuration": 1, "restricted": 2, "healthy": 3}
    ordered = sorted(services, key=lambda item: (state_rank.get(item["state"], 1), -item.get("duration_ms", 0)))
    counts = {state: sum(item["state"] == state for item in services) for state in ("healthy", "restricted", "down", "configuration")}
    rows = [{**item, "latency": _latency_text(item.get("duration_ms")), "observed": _time_text(item["observed_at"])} for item in ordered[:row_limit]]
    return {
        "total": len(services),                            # 0 明确表示用户未配置服务
        "healthy": counts["healthy"],                     # 完整健康服务数量
        "restricted": counts["restricted"],               # 可达但未验证业务健康
        "down": counts["down"] + counts["configuration"],
        "rows": rows,                                     # 问题优先的有限详情
        "hidden_count": max(0, len(ordered) - len(rows)),  # 不静默丢弃大量健康行
        "configured": configured,                          # 顶部摘要区分未配置和全部停用
        "empty_text": "服务监控已全部停用" if configured and not services else "未配置服务监控" if not services else "",
    }


# --- 汇总模型当前状态与 24 个小时槽 ---
def _build_models(report: dict[str, Any] | None, history: dict[str, list[dict[str, Any]]], settings: Settings, now_ms: int) -> dict[str, Any]:
    if not isinstance(report, dict):
        if not settings.model_monitor_enabled:
            return _empty_models("模型自动监控已关闭", state="disabled", monitor_text="仅管理员手动检测")
        return _empty_models("等待首次模型检测")            # 后台循环会建立第一个真实小时点
    try:
        report_observed_at = int(report.get("observed_at") or 0)
    except (TypeError, ValueError, OverflowError):
        return _empty_models("模型报告时间无效")             # 损坏报告不能参与当前健康判断
    future_report = report_observed_at > now_ms + 300_000   # 明显未来时间代表主机时钟或状态异常
    report_age_ms = max(0, now_ms - report_observed_at)
    time_fresh = bool(report_observed_at) and not future_report and report_age_ms <= MODEL_FRESHNESS_MS
    manual_report = report.get("source") == "manual"        # 关闭自动监控后只承认管理员刚执行的手动结果
    fresh = time_fresh and (settings.model_monitor_enabled or manual_report)

    raw_routes = [route for route in report.get("routes", []) if isinstance(route, dict) and route.get("route_id")]
    routes = [_build_model_route(route, history.get(str(route["route_id"]), []), settings, now_ms, fresh) for route in raw_routes]
    name_counts = {name: sum(route.get("model_name") == name for route in routes) for name in {route.get("model_name") for route in routes}}
    for route in routes:
        duplicate = name_counts.get(route.get("model_name"), 0) > 1 # 只有同名时才显示必要身份
        route["identity_hint"] = str(route.get("provider_id") or route.get("route_id") or "") if duplicate else ""
        route["display_name"] = f"{route.get('model_name', '未知模型')} · {route['identity_hint']}" if duplicate else str(route.get("model_name") or "未知模型")
    route_rank = {"unavailable": 0, "invalid": 1, "slow": 2, "unknown": 3, "available": 4}
    important_routes = sorted(routes, key=lambda item: (route_rank.get(item["state"], 3), -(item.get("latency_ms") or 0)))[:settings.max_model_rows]
    report_state = report.get("state", "unknown") if fresh else "disabled" if not settings.model_monitor_enabled else "unknown"
    return {
        "state": report_state,                             # 过期报告明确降为未知
        "fresh": fresh,                                   # 当前问题列表只使用新鲜报告
        "age": _age_text(report_observed_at, now_ms),      # 显示报告新鲜度而不是假装实时
        "monitor_text": "每小时自动检测" if settings.model_monitor_enabled else "仅手动检测",
        "total": len(routes),                              # 实际探测路由总数
        "available": sum(item["state"] == "available" for item in routes),
        "slow": sum(item["state"] == "slow" for item in routes),
        "failed": sum(item["state"] in {"invalid", "unavailable"} for item in routes),
        "rows": important_routes,                         # 仅展示异常和最慢路由
        "hidden_count": max(0, len(routes) - len(important_routes)),
        "empty_text": "未发现已加载的聊天模型路由" if not routes else "",
        "all_routes": routes,                             # 只供综合问题列表使用，不直接渲染
    }


# --- 计算单条模型路由的当前状态与历史图 ---
def _build_model_route(route: dict[str, Any], samples: list[dict[str, Any]], settings: Settings, now_ms: int, report_fresh: bool) -> dict[str, Any]:
    slots = _build_hourly_slots(samples, now_ms)             # 始终返回 24 格，缺测保持 unknown
    effective_state = route.get("state", "unknown") if report_fresh else "unknown"
    return {
        **route,                                            # 保留本次真实状态、延迟和原因
        "state": effective_state,                          # 旧报告只能作为历史证据
        "visual_state": _model_visual_state(effective_state),
        "history_chart": _model_history_chart(slots, settings.model_slow_ms),
    }


# --- 构建当前小时和之前 23 小时的固定槽 ---
def _build_hourly_slots(samples: list[dict[str, Any]], now_ms: int) -> list[dict[str, Any]]:
    end_bucket = now_ms // MODEL_SLOT_MS                    # 所有模型共享同一当前小时右边界
    start_bucket = end_bucket - MODEL_SLOT_COUNT + 1
    hourly: dict[int, dict[str, Any]] = {}
    for raw_sample in samples if isinstance(samples, list) else []:
        sample = _valid_model_sample(raw_sample)            # 损坏样本只影响自身
        if sample is None:
            continue
        bucket = sample["observed_at"] // MODEL_SLOT_MS
        if start_bucket <= bucket <= end_bucket:
            current = hourly.get(bucket)
            current_key = (MODEL_STATE_RANK[current["state"]], current["latency_ms"], current["observed_at"]) if current else None
            sample_key = (MODEL_STATE_RANK[sample["state"]], sample["latency_ms"], sample["observed_at"])
            if current_key is None or sample_key >= current_key:
                hourly[bucket] = sample                     # 防御性保留同小时最差证据

    slots = []
    for index in range(MODEL_SLOT_COUNT):
        bucket = start_bucket + index                       # 固定 24 个连续小时槽
        sample = hourly.get(bucket)
        raw_state = sample["state"] if sample else "unknown"
        slots.append({
            "x": round((index + 0.5) * 100 / MODEL_SLOT_COUNT, 2), # 曲线点与 Uptime 方块中心一致
            "bucket_start": bucket * MODEL_SLOT_MS,
            "observed_at": sample["observed_at"] if sample else None,
            "raw_state": raw_state,
            "state": _model_visual_state(raw_state),
            "latency_ms": sample["latency_ms"] if sample else None,
            "known": sample is not None,
        })
    return slots


# --- 把固定小时槽转换成不跨缺口的延迟曲线 ---
def _model_history_chart(slots: list[dict[str, Any]], slow_ms: int) -> dict[str, Any]:
    latency_values = [slot["latency_ms"] for slot in slots if slot["raw_state"] in {"available", "slow"}]
    chart_max = max([slow_ms, *latency_values], default=slow_ms) * 1.12 # 为最高点保留柔和顶部空间
    segments: list[str] = []                                # 未知或失败小时会断开延迟线
    current_segment: list[str] = []
    for slot in slots:
        if slot["raw_state"] in {"available", "slow"}:
            y = round(34 - slot["latency_ms"] / chart_max * 28, 2)
            current_segment.append(f"{slot['x']},{y}")
        else:
            if len(current_segment) >= 2:
                segments.append(" ".join(current_segment)) # 只绘制有趋势意义的连续片段
            current_segment = []
    if len(current_segment) >= 2:
        segments.append(" ".join(current_segment))

    slow_y = round(34 - slow_ms / chart_max * 28, 2)         # 慢速阈值与延迟使用相同纵轴
    return {
        "slots": slots,                                   # 24 格完整表达正常、慢、失败和缺测
        "curve_segments": segments,                       # 延迟线绝不跨越未知或失败小时
        "curve_paths": [_smooth_curve_path(segment) for segment in segments], # 圆滑路径弱化尖锐波峰波谷
        "slow_y": slow_y,                                 # 横向阈值线帮助识别延迟恶化
        "time_labels": [_axis_time_text(slots[0]["bucket_start"]), _axis_time_text(slots[11]["bucket_start"]), _axis_time_text(slots[-1]["bucket_start"])],
    }


# --- 把连续折线点转换成平滑二次贝塞尔路径 ---
def _smooth_curve_path(segment: str) -> str:
    points = [tuple(float(value) for value in point.split(",")) for point in segment.split()]
    if len(points) < 2:
        return ""                                          # 单点无法形成趋势曲线
    commands = [f"M {points[0][0]:g} {points[0][1]:g}"]
    for index in range(1, len(points)):
        previous_x, previous_y = points[index - 1]
        current_x, current_y = points[index]
        middle_x = (previous_x + current_x) / 2
        middle_y = (previous_y + current_y) / 2
        commands.append(f"Q {previous_x:g} {previous_y:g} {middle_x:g} {middle_y:g}")
    commands.append(f"T {points[-1][0]:g} {points[-1][1]:g}")
    return " ".join(commands)


# --- 验证一条模型历史样本 ---
def _valid_model_sample(sample: Any) -> dict[str, Any] | None:
    if not isinstance(sample, dict) or sample.get("state") not in MODEL_STATE_RANK:
        return None
    try:
        observed_at = int(sample["observed_at"])
        latency_ms = max(0, int(sample["latency_ms"]))
    except (KeyError, TypeError, ValueError, OverflowError):
        return None                                         # 单个损坏样本不能破坏整张面板
    return {"observed_at": observed_at, "state": sample["state"], "latency_ms": latency_ms}


# --- 统一模型状态到页面健康色语义 ---
def _model_visual_state(state: Any) -> str:
    if state in {"invalid", "unavailable"}:
        return "critical"                                  # 错误内容和连接失败都代表不可用
    if state == "slow":
        return "degraded"                                  # 响应正确但超过慢速阈值
    return "healthy" if state == "available" else "unknown" # 未知状态不能冒充健康


# --- 从三个领域生成当前问题列表 ---
def _build_issues(resources: list[dict[str, Any]], services: list[dict[str, Any]], routes: list[dict[str, Any]]) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []                       # 每项问题只保留静态图片所需字段
    for resource in resources:
        if resource["state"] in {"critical", "degraded"}:
            issues.append({"state": resource["state"], "domain": "资源", "name": resource["label"], "reason": f"当前占用 {resource['current']}"})
    for service in services:
        state = "critical" if service["state"] in {"down", "configuration"} else "degraded" if service["state"] == "restricted" else "healthy"
        if state != "healthy":
            issues.append({"state": state, "domain": "服务", "name": service["name"], "reason": service["reason"]})
    for route in routes:
        state = "critical" if route["state"] in {"invalid", "unavailable"} else "degraded" if route["state"] == "slow" else "healthy"
        if state != "healthy":
            issues.append({"state": state, "domain": "模型", "name": route.get("display_name", route["model_name"]), "reason": route["reason"]})
    return sorted(issues, key=lambda item: STATE_ORDER[item["state"]])


# --- 计算整张面板总状态 ---
def _build_overall(resources: list[dict[str, Any]], services: list[dict[str, Any]], model_state: str) -> dict[str, str]:
    has_critical = any(item["state"] == "critical" for item in resources) or any(item["state"] in {"down", "configuration"} for item in services)
    has_degraded = any(item["state"] == "degraded" for item in resources) or any(item["state"] == "restricted" for item in services)
    has_critical = has_critical or model_state == "critical" # 模型功能异常与服务宕机同级
    has_degraded = has_degraded or model_state == "degraded" # 慢模型会使整体进入降级
    if has_critical:
        return {"state": "critical", "label": "需要处理", "description": "发现影响可用性的异常"}
    if has_degraded:
        return {"state": "degraded", "label": "部分降级", "description": "系统可用，但存在受限或慢速项目"}
    has_unknown_resource = any(item["state"] == "unknown" for item in resources)
    if has_unknown_resource or model_state == "unknown":
        return {"state": "unknown", "label": "数据不完整", "description": "部分当前状态尚未确认"}
    return {"state": "healthy", "label": "运行正常", "description": "当前检查项均处于健康状态"}


# --- 根据综合状态选择不矛盾的结论文案 ---
def _conclusion_text(state: str, settings: Settings) -> str:
    if state == "critical":
        return settings.fail_text                           # 严重异常使用用户配置失败文案
    if state == "healthy":
        return settings.success_text                        # 只有完整健康才使用成功文案
    return "有项目需要关注" if state == "degraded" else "等待完整数据"


# --- 构建顶部四项摘要 ---
def _build_health_strip(resources: list[dict[str, Any]], services: dict[str, Any], models: dict[str, Any]) -> list[dict[str, str]]:
    worst_resource = min(resources, key=lambda item: STATE_ORDER[item["state"]])
    return [
        {"label": "资源", "value": f"{worst_resource['label']} {worst_resource['current']}", "state": worst_resource["state"]},
        {"label": "服务", "value": f"{services['healthy']}/{services['total']} 健康" if services["total"] else "已关闭" if services["configured"] else "未配置", "state": "critical" if services["down"] else "degraded" if services["restricted"] else "unknown" if not services["total"] else "healthy"},
        {"label": "模型", "value": "已关闭" if models["state"] == "disabled" else "状态未知" if models["state"] == "unknown" else f"{models['available'] + models['slow']}/{models['total']} 可用" if models["total"] else "未检测", "state": "unknown" if models["state"] == "disabled" else models["state"]},
        {"label": "主机", "value": "在线", "state": "healthy"},
    ]


# --- 构建网络摘要 ---
def _build_network(network: dict[str, Any]) -> dict[str, str]:
    return {
        "sent_rate": _bytes_text(network.get("sent_per_second"), suffix="/s"),
        "received_rate": _bytes_text(network.get("received_per_second"), suffix="/s"),
        "sent_total": _bytes_text(network.get("sent")),
        "received_total": _bytes_text(network.get("received")),
    }


# --- 构建模型空态 ---
def _empty_models(text: str, state: str = "unknown", monitor_text: str = "等待首次检测") -> dict[str, Any]:
    return {"state": state, "fresh": False, "age": "暂无", "monitor_text": monitor_text, "total": 0, "available": 0, "slow": 0, "failed": 0, "rows": [], "hidden_count": 0, "empty_text": text, "all_routes": []}


# --- 按阈值判断资源状态 ---
def _threshold_state(value: Any, warning: float, critical: float) -> str:
    if value is None:
        return "unknown"                                  # 采集失败不能冒充健康
    if float(value) >= critical:
        return "critical"                                 # 严重阈值优先
    if float(value) >= warning:
        return "degraded"                                 # 警告阈值代表降级
    return "healthy"                                      # 低于两个阈值才算健康


# --- 把百分比转换成页面文本 ---
def _percent_text(value: Any) -> str:
    return f"{float(value):.1f}%" if value is not None else "暂无数据"


# --- 把资源容量转换成页面文本 ---
def _usage_text(resource: dict[str, Any]) -> str:
    used = resource.get("used")                             # 容量资源提供已用字节
    total = resource.get("total")                          # 容量资源提供总字节
    if used is None or total is None:
        return "容量暂无数据"                              # CPU 等无容量资源不会显示 0 B
    return f"{_bytes_text(used)} / {_bytes_text(total)}"


# --- 把字节数转换成人类可读文本 ---
def _bytes_text(value: Any, suffix: str = "") -> str:
    if value is None:
        return "暂无数据"                                  # 首次网络采样没有速率
    number = float(value)                                   # 支持整数和浮点速率
    units = ("B", "KB", "MB", "GB", "TB")               # 面板最多展示到 TB
    unit = units[0]                                        # 小于 1024 时保持字节
    for unit in units:
        if abs(number) < 1024 or unit == units[-1]:
            break                                           # 找到适合静态图片的紧凑单位
        number /= 1024
    return f"{number:.1f} {unit}{suffix}"


# --- 把毫秒延迟转换成页面文本 ---
def _latency_text(value: Any) -> str:
    return f"{int(value)} ms" if value is not None else "暂无数据"


# --- 把 UTC 毫秒转换成时间文本 ---
def _time_text(value: Any) -> str:
    if not value:
        return "暂无"                                      # 未探测报告没有时间
    return datetime.fromtimestamp(int(value) / 1000).strftime("%Y-%m-%d %H:%M:%S")


# --- 把统一时间轴刻度转换成紧凑文本 ---
def _axis_time_text(value: int) -> str:
    return datetime.fromtimestamp(value / 1000).strftime("%m-%d %H:%M") # 三个刻度直接对应 0/50/100%


# --- 把报告时间转换成新鲜度文本 ---
def _age_text(value: Any, now_ms: int | None = None) -> str:
    if not value:
        return "暂无"
    current_seconds = (now_ms / 1000) if now_ms is not None else datetime.now().timestamp()
    seconds = max(0, int(current_seconds - int(value) / 1000))
    return f"{_duration_text(seconds)}前"


# --- 把秒数转换成紧凑持续时间 ---
def _duration_text(seconds: int) -> str:
    days, remainder = divmod(seconds, 86400)                # 天级运行时间最常见
    hours, remainder = divmod(remainder, 3600)              # 剩余部分换算小时
    minutes = remainder // 60                               # 秒级波动无需进入静态面板
    if days:
        return f"{days}天 {hours}小时"
    if hours:
        return f"{hours}小时 {minutes}分钟"
    return f"{minutes}分钟"


# --- 验证一条资源历史样本 ---
def _valid_resource_sample(sample: Any) -> bool:
    if not isinstance(sample, dict) or sample.get("percent") is None:
        return False                                        # 缺失百分比没有趋势意义
    try:
        int(sample["observed_at"])
        float(sample["percent"])
    except (KeyError, TypeError, ValueError, OverflowError):
        return False                                        # 单条损坏历史不阻断面板
    return True


# --- 把资源历史映射到真实时间横轴 ---
def _resource_curve_points(samples: list[dict[str, Any]]) -> str:
    if len(samples) < 2:
        return ""                                          # 一个样本不能称为趋势
    start = int(samples[0]["observed_at"])
    span = max(1, int(samples[-1]["observed_at"]) - start) # 暂停采样会自然留下横向空隙
    points = []
    for sample in samples:
        x = (int(sample["observed_at"]) - start) / span * 100
        y = 38 - max(0, min(100, float(sample["percent"]))) * 0.34
        points.append(f"{round(x, 2)},{round(y, 2)}")
    return " ".join(points)


# --- 把资源阈值映射到同一 0-100 纵轴 ---
def _resource_threshold_y(threshold: float) -> float:
    return round(38 - max(0, min(100, threshold)) * 0.34, 2)
