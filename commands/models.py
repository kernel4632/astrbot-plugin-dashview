"""
模型探测指令：执行一次真实功能探针，并原子更新最近报告与各路由历史。

历史保留最近 24 个小时桶；临时消失的路由保留证据，同小时采用最差真实结果。
调用示例：report = await probe_models(context, state, settings)
"""

from __future__ import annotations                         # 允许现代类型注解

import time                                                # 计算统计窗口起点
from fnmatch import fnmatchcase                             # 配置排除后立即移除旧路由
from typing import Any                                     # 兼容 AstrBot Context 与状态字典

from ..collectors.providers import probe_providers         # 产生本次真实模型报告
from ..config import Settings                              # 读取探测和历史参数
from ..state import DashboardState                         # 原子修改唯一状态文档


MODEL_INTERVAL_MS = 3_600_000                              # 长期监控固定每小时产生一个时间点
MODEL_SLOT_COUNT = 24                                      # 面板固定观察当前小时和之前 23 小时
ROUTE_RETENTION_MS = MODEL_SLOT_COUNT * MODEL_INTERVAL_MS  # 消失路由保留一天后自然过期
SAMPLE_STATE_RANK = {"available": 0, "slow": 1, "invalid": 2, "unavailable": 3} # 同小时保留最差结果


# --- 判断小时级模型监控是否到期 ---
def model_probe_due(report: dict[str, Any] | None, now_ms: int | None = None) -> bool:
    current_ms = now_ms if now_ms is not None else int(time.time() * 1000) # 测试可传入确定时钟
    if not isinstance(report, dict) or not report.get("observed_at"):
        return True                                         # 从未检测时立即建立第一个真实基线
    try:
        observed_at = int(report["observed_at"])           # 损坏或未来报告不能永久阻塞监控
    except (TypeError, ValueError, OverflowError):
        return True
    if observed_at > current_ms + 300_000:
        return True                                         # 时钟回拨超过五分钟时重新建立可信基线
    return current_ms - observed_at >= MODEL_INTERVAL_MS    # 满一小时才再次产生 API 调用


# --- 探测模型并保存真实历史 ---
async def probe_models(context: Any, state: DashboardState, settings: Settings, source: str = "manual") -> dict[str, Any]:
    report = await probe_providers(
        context=context,                                    # AstrBot 上下文提供当前 Provider 列表
        timeout=settings.model_timeout,                     # 每条路由独立超时
        concurrency=settings.model_concurrency,             # 限制供应商调用压力
        slow_ms=settings.model_slow_ms,                     # 成功响应的慢速阈值
        exclude_patterns=settings.model_exclude_patterns,   # 排除用户明确不愿付费探测的路由
    )
    report["source"] = source                              # 区分后台报告与管理员即时手动结果

    def save_report(current: dict[str, Any]) -> None:
        reconciled = _reconcile_report(report, current.get("latest_model_report"), settings.model_exclude_patterns) # 区分消失路由和主动排除
        old_history = current.setdefault("model_history", {})
        history: dict[str, list[dict[str, Any]]] = {}        # 只保留当前或一天内暂时消失的路由
        for route in reconciled["routes"]:
            route_id = route["route_id"]                    # 每条路由拥有独立时间序列
            existing = old_history.get(route_id, [])
            if route["presence"] == "present":
                history[route_id] = _merge_hourly_samples(existing, route, int(reconciled["observed_at"]))
            else:
                history[route_id] = _prune_hourly_samples(existing, int(reconciled["observed_at"]))
        current["model_history"] = history                 # 过期路由随同历史一起移除
        current["latest_model_report"] = reconciled        # 与历史在同一次 KV 写入中提交

    saved = await state.update(save_report)                 # 写入失败会向上抛出，旧报告保持不变
    return saved["latest_model_report"]                    # 命令反馈使用真正落盘的协调报告


# --- 协调本次发现与上一份路由清单 ---
def _reconcile_report(report: dict[str, Any], previous: dict[str, Any] | None, exclude_patterns: tuple[str, ...] = ()) -> dict[str, Any]:
    observed_at = int(report["observed_at"])               # 本批次统一决定路由保留年龄
    current_routes = [{**route, "presence": "present", "last_seen_at": observed_at} for route in report.get("routes", [])]
    current_ids = {route["route_id"] for route in current_routes}
    retained: list[dict[str, Any]] = []                     # 本次未出现但仍在保留期的历史路由
    previous_routes = previous.get("routes", []) if isinstance(previous, dict) else []
    for old_route in previous_routes:
        if not isinstance(old_route, dict) or not old_route.get("route_id") or old_route.get("route_id") in current_ids:
            continue                                        # 当前已出现或损坏条目无需保留
        old_candidates = (str(old_route.get("route_id", "")), str(old_route.get("provider_id", "")), str(old_route.get("model_name", "")), str(old_route.get("provider_name", "")))
        if any(fnmatchcase(candidate, pattern) for pattern in exclude_patterns for candidate in old_candidates):
            continue                                        # WebUI 主动排除应立即停止展示和保留
        try:
            last_seen_at = int(old_route.get("last_seen_at") or old_route.get("observed_at") or 0)
        except (TypeError, ValueError, OverflowError):
            continue                                        # 无法确定年龄的路由不能无限保留
        if observed_at - last_seen_at >= ROUTE_RETENTION_MS:
            continue                                        # 满 24 小时后移除路由和历史
        retained.append({
            "route_id": old_route["route_id"],            # 使用原稳定身份续接历史
            "provider_id": old_route.get("provider_id", ""),
            "provider_name": old_route.get("provider_name", ""),
            "model_name": old_route.get("model_name", old_route["route_id"]),
            "state": "unknown",                          # 消失只代表未知，不冒充上次故障
            "latency_ms": None,
            "observed_at": observed_at,
            "reason": "本次未发现该模型",
            "presence": "missing",
            "last_seen_at": last_seen_at,
        })

    routes = current_routes + retained                     # 当前路由优先，未知保留路由随后展示
    available = sum(route["state"] == "available" for route in current_routes)
    slow = sum(route["state"] == "slow" for route in current_routes)
    invalid = sum(route["state"] == "invalid" for route in current_routes)
    unavailable = sum(route["state"] == "unavailable" for route in current_routes)
    state = "critical" if invalid or unavailable else "degraded" if slow else "unknown" if retained or not current_routes else "healthy"
    return {
        **report,                                           # 保留批次耗时和观察时间
        "route_count": len(current_routes),               # 真实执行探测的路由数量
        "retained_count": len(retained),                  # 本次消失但仍保留历史的路由数量
        "available_count": available,
        "slow_count": slow,
        "invalid_count": invalid,
        "unavailable_count": unavailable,
        "unknown_count": len(retained),
        "state": state,
        "routes": routes,
    }


# --- 把同一小时的模型样本保守归并为最差结果 ---
def _merge_hourly_samples(existing: list[Any], route: dict[str, Any], window_end: int) -> list[dict[str, Any]]:
    hourly = _hourly_samples(existing, window_end)          # 先清理并归并已有真实样本

    current_sample = {
        "observed_at": int(route["observed_at"]),         # 当前批次真实探测时间
        "state": route["state"],                          # 当前真实可用状态
        "latency_ms": route["latency_ms"],                # 当前真实响应耗时
    }
    bucket = current_sample["observed_at"] // MODEL_INTERVAL_MS
    hourly[bucket] = _more_conservative_sample(hourly.get(bucket), current_sample)
    ordered = sorted(hourly.values(), key=lambda sample: int(sample["observed_at"]))
    return ordered[-MODEL_SLOT_COUNT:]                      # 每小时一点，最多覆盖最近 24 小时


# --- 清理消失路由的历史但不制造未知探测样本 ---
def _prune_hourly_samples(existing: list[Any], window_end: int) -> list[dict[str, Any]]:
    hourly = _hourly_samples(existing, window_end)          # 未执行模型调用就不写新样本
    return sorted(hourly.values(), key=lambda sample: int(sample["observed_at"]))


# --- 清理历史并按小时归并 ---
def _hourly_samples(existing: list[Any], window_end: int) -> dict[int, dict[str, Any]]:
    end_bucket = window_end // MODEL_INTERVAL_MS            # 当前小时是第 24 个槽
    start_bucket = end_bucket - MODEL_SLOT_COUNT + 1        # 之前只保留 23 个小时桶
    hourly: dict[int, dict[str, Any]] = {}
    for raw_sample in existing if isinstance(existing, list) else []:
        sample = _valid_sample(raw_sample)                  # 单个损坏样本不会卡死整个监控
        if sample is None:
            continue
        bucket = sample["observed_at"] // MODEL_INTERVAL_MS
        if start_bucket <= bucket <= end_bucket:
            hourly[bucket] = _more_conservative_sample(hourly.get(bucket), sample)
    return hourly


# --- 验证一条持久化模型样本 ---
def _valid_sample(sample: Any) -> dict[str, Any] | None:
    if not isinstance(sample, dict) or sample.get("state") not in SAMPLE_STATE_RANK:
        return None                                         # 未知状态不参与真实可用性证据
    try:
        observed_at = int(sample["observed_at"])
        latency_ms = int(sample["latency_ms"])
    except (KeyError, TypeError, ValueError, OverflowError):
        return None                                         # 损坏时间或延迟只丢弃当前样本
    return {"observed_at": observed_at, "state": sample["state"], "latency_ms": max(0, latency_ms)}


# --- 同小时选择状态更差、延迟更高的真实样本 ---
def _more_conservative_sample(left: dict[str, Any] | None, right: dict[str, Any]) -> dict[str, Any]:
    if left is None:
        return right                                        # 当前小时尚无样本时直接保存
    left_key = (SAMPLE_STATE_RANK[left["state"]], int(left["latency_ms"]), int(left["observed_at"]))
    right_key = (SAMPLE_STATE_RANK[right["state"]], int(right["latency_ms"]), int(right["observed_at"]))
    return right if right_key >= left_key else left          # 成功重试不能抹掉同小时故障
