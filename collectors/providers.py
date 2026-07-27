"""
模型路由采集器：发现 AstrBot 当前加载的聊天路由并验证每条路由能否按要求回复。

这里不猜测 Provider 的私有模型列表，只检测每个已加载路由当前实际使用的模型。
调用示例：report = await probe_providers(context, timeout=30, concurrency=6, slow_ms=8000)
"""

from __future__ import annotations                         # 允许现代类型注解

import asyncio                                             # 控制模型调用并发和单次超时
from fnmatch import fnmatchcase                             # 允许 WebUI 用通配符排除昂贵模型
import time                                                # 记录真实延迟与 UTC 观察时间
from typing import Any                                     # 兼容 AstrBot 的多种 Provider 实现


PROBE_PROMPT = "只回复 OK 两个字母。"                      # 最短业务请求减少模型调用成本
PROBE_SYSTEM = "你是连通性探针。请只回复 OK，不要解释。"   # 明确约束输出以验证功能链路


# --- Provider 发现不完整时阻止覆盖旧历史 ---
class ProviderDiscoveryError(RuntimeError):
    """AstrBot 无法完整列出聊天 Provider 时抛出。"""


# --- 发现并探测所有当前模型路由 ---
async def probe_providers(context: Any, timeout: float, concurrency: int, slow_ms: int, exclude_patterns: tuple[str, ...] = ()) -> dict[str, Any]:
    started_at = time.perf_counter()                        # 总耗时包含目标发现和所有调用
    observed_at = int(time.time() * 1000)                   # 报告内所有结果共享同一观察批次时间
    targets = _collect_targets(context, exclude_patterns)   # 每个已加载聊天 Provider 对应一条真实路由
    if not targets:
        return _empty_report(observed_at, started_at)       # 未发现目标是 unknown，不是假健康

    limit = asyncio.Semaphore(concurrency)                  # 控制模型供应商和本机连接压力

    async def probe_one(target: dict[str, Any]) -> dict[str, Any]:
        async with limit:                                   # 每次实际模型调用占用一个名额
            return await _probe_target(target, timeout, slow_ms, observed_at)

    results = await asyncio.gather(*(probe_one(target) for target in targets))
    return _build_report(results, observed_at, started_at)  # 汇总只使用本次真实结果


# --- 从 AstrBot 上下文收集当前路由 ---
def _collect_targets(context: Any, exclude_patterns: tuple[str, ...] = ()) -> list[dict[str, Any]]:
    try:
        providers = list(context.get_all_providers() or []) # AstrBot 公开入口给出已加载 Provider
    except Exception as error:
        raise ProviderDiscoveryError(_safe_discovery_error(error)) from error # 失败不能伪装成干净空列表

    targets: list[dict[str, Any]] = []                      # 保持 AstrBot 返回顺序便于排查配置
    route_ids: set[str] = set()                             # 重复稳定身份必须显式失败
    for provider in providers:
        if not callable(getattr(provider, "text_chat", None)):
            continue                                        # 语音、嵌入等非聊天 Provider 不属于模型探测目标
        try:
            meta = provider.meta()                          # Provider 元数据提供路由类型和 ID
            model_name = str(provider.get_model() or "").strip() # 只检测当前真正使用的模型
        except Exception as error:
            raise ProviderDiscoveryError(_safe_discovery_error(error)) from error # 部分发现不能证明其他路由已消失
        if not model_name:
            continue                                        # 没有当前模型就没有可执行探测目标

        provider_config = getattr(provider, "provider_config", {}) or {}
        if not isinstance(provider_config, dict):
            raise ProviderDiscoveryError("聊天 Provider 配置结构无效") # 无法读取稳定身份时拒绝部分报告
        provider_id = str(provider_config.get("id") or provider_config.get("provider_source_id") or getattr(meta, "id", "")).strip()
        if not provider_id:
            raise ProviderDiscoveryError("聊天 Provider 缺少稳定配置 ID") # 无稳定身份就无法安全续接历史
        display_name = str(provider_config.get("display_name") or provider_config.get("name") or provider_id)
        route_id = f"{provider_id}::{model_name}"           # 历史只依赖稳定配置身份和实际模型
        if _route_is_excluded(route_id, provider_id, model_name, display_name, exclude_patterns):
            continue                                        # 用户明确排除的付费路由不产生调用
        if route_id in route_ids:
            raise ProviderDiscoveryError(f"发现重复模型路由：{route_id}") # 禁止用枚举顺序制造不稳定后缀
        route_ids.add(route_id)
        targets.append({
            "route_id": route_id,                          # 新状态文档中的稳定模型历史键
            "provider_id": provider_id,                    # Provider 汇总分组标识
            "provider_name": display_name,                 # 页面显示名称
            "model_name": model_name,                      # 当前实际调用模型
            "provider": provider,                          # 仅采集阶段使用，不写入 KV
        })
    return targets


# --- 判断模型路由是否匹配 WebUI 排除规则 ---
def _route_is_excluded(route_id: str, provider_id: str, model_name: str, display_name: str, patterns: tuple[str, ...]) -> bool:
    candidates = (route_id, provider_id, model_name, display_name) # 任一用户可见身份均可匹配
    return any(fnmatchcase(candidate, pattern) for pattern in patterns for candidate in candidates)


# --- 把发现异常压缩成安全日志文本 ---
def _safe_discovery_error(error: Exception) -> str:
    message = str(error).replace("\n", " ").strip()[:96]   # 不让供应商异常撑爆日志和聊天反馈
    return message or error.__class__.__name__               # 空异常至少保留类型名称


# --- 探测一条模型路由 ---
async def _probe_target(target: dict[str, Any], timeout: float, slow_ms: int, observed_at: int) -> dict[str, Any]:
    started_at = time.perf_counter()                        # 单路由延迟不包含并发排队时间
    try:
        response = await asyncio.wait_for(
            target["provider"].text_chat(
                prompt=PROBE_PROMPT,                        # 用户消息验证完整聊天调用链
                system_prompt=PROBE_SYSTEM,                 # 系统约束让响应可以自动判定
                model=target["model_name"],                 # 明确指定发现到的当前模型
            ),
            timeout=timeout,                                # 供应商卡住时及时释放并发名额
        )
        latency_ms = round((time.perf_counter() - started_at) * 1000)
        reply = _extract_reply(response)                    # 统一不同 Provider 的响应形状
        is_valid = reply.strip().upper().rstrip(".!。！") == "OK"
        state = "slow" if is_valid and latency_ms >= slow_ms else "available" if is_valid else "invalid"
        reason = "响应较慢" if state == "slow" else "响应正确" if state == "available" else "响应内容不符合探针要求"
        return _probe_result(target, state, latency_ms, observed_at, reason)
    except asyncio.TimeoutError:
        latency_ms = round((time.perf_counter() - started_at) * 1000)
        return _probe_result(target, "unavailable", latency_ms, observed_at, f"超过 {timeout:g} 秒未响应")
    except Exception as error:
        latency_ms = round((time.perf_counter() - started_at) * 1000)
        reason = str(error).replace("\n", " ").strip()[:96] or error.__class__.__name__
        return _probe_result(target, "unavailable", latency_ms, observed_at, reason)


# --- 构建不含 Provider 实例的持久化结果 ---
def _probe_result(target: dict[str, Any], state: str, latency_ms: int, observed_at: int, reason: str) -> dict[str, Any]:
    return {
        "route_id": target["route_id"],                    # 对应模型历史的唯一键
        "provider_id": target["provider_id"],              # 用于 Provider 汇总
        "provider_name": target["provider_name"],          # 用户可识别名称
        "model_name": target["model_name"],                # 当前模型名称
        "state": state,                                    # available/slow/invalid/unavailable
        "latency_ms": latency_ms,                          # 包含失败等待时间的真实耗时
        "observed_at": observed_at,                        # UTC epoch 毫秒
        "reason": reason,                                  # 可安全展示的简短原因
    }


# --- 汇总本次探测报告 ---
def _build_report(results: list[dict[str, Any]], observed_at: int, started_at: float) -> dict[str, Any]:
    counts = {state: sum(item["state"] == state for item in results) for state in ("available", "slow", "invalid", "unavailable")}
    return {
        "observed_at": observed_at,                        # 报告批次观察时间
        "duration_ms": round((time.perf_counter() - started_at) * 1000),
        "route_count": len(results),                       # 本次实际尝试数量
        "available_count": counts["available"],            # 正确且低于慢阈值
        "slow_count": counts["slow"],                      # 正确但高于慢阈值
        "invalid_count": counts["invalid"],                # 返回内容不符合功能探针
        "unavailable_count": counts["unavailable"],        # 超时或调用异常
        "state": "critical" if counts["unavailable"] or counts["invalid"] else "degraded" if counts["slow"] else "healthy",
        "routes": results,                                 # 每条真实路由的本次结果
    }


# --- 构建未发现路由的未知报告 ---
def _empty_report(observed_at: int, started_at: float) -> dict[str, Any]:
    return {
        "observed_at": observed_at,                        # 仍记录发现动作发生时间
        "duration_ms": round((time.perf_counter() - started_at) * 1000),
        "route_count": 0,                                 # 0 不代表全部健康
        "available_count": 0,
        "slow_count": 0,
        "invalid_count": 0,
        "unavailable_count": 0,
        "state": "unknown",                               # 准确表达没有可观察目标
        "routes": [],
    }


# --- 从多种 Provider 返回值提取文本 ---
def _extract_reply(response: Any) -> str:
    direct_text = getattr(response, "completion_text", "") # AstrBot 标准聊天结果优先
    if direct_text:
        return str(direct_text)
    if isinstance(response, dict):                          # 部分 Provider 返回普通字典
        return str(response.get("completion_text") or response.get("content") or response.get("text") or "")
    if isinstance(response, str):                           # 测试 Provider 或简单实现直接返回字符串
        return response
    message = getattr(response, "message", None)            # 兼容带 message 包装的实现
    return str(getattr(message, "content", message) or "")
