"""模型测试：验证当前路由发现、响应内容校验与未知空态。"""

from types import SimpleNamespace                          # 构造 AstrBot Provider 元数据
from typing import Any                                     # 描述内存 KV 状态

import pytest                                              # 验证发现失败边界

from dashview.collectors.providers import ProviderDiscoveryError, probe_providers # 被测真实功能探针入口
from dashview.commands.models import MODEL_INTERVAL_MS, _merge_hourly_samples, _reconcile_report, model_probe_due, probe_models # 被测小时级历史规则
from dashview.config import Settings                       # 执行正式模型指令
from dashview.state import DashboardState                  # 验证发现失败不覆盖 KV


class FakeProvider:
    """最小 Provider：提供 AstrBot 探针实际使用的三个公开动作。"""

    def __init__(self, provider_id: str, model: str, reply: str, config_id: str = "") -> None:
        self.provider_id = provider_id                      # 测试路由唯一身份
        self.model = model                                  # 当前实际模型
        self.reply = reply                                  # 模拟供应商响应
        self.provider_config = {"display_name": provider_id, **({"id": config_id} if config_id else {})}

    def meta(self):
        return SimpleNamespace(id=self.provider_id, type="chat") # 模拟 AstrBot ProviderMeta

    def get_model(self) -> str:
        return self.model                                   # 只发现当前真实路由

    async def text_chat(self, **kwargs) -> str:
        return self.reply                                   # 不访问真实供应商


class FakeContext:
    """最小 Context：只暴露模型采集器需要的 Provider 列表。"""

    def __init__(self, providers) -> None:
        self.providers = providers                          # 保留测试指定路由顺序

    def get_all_providers(self):
        return self.providers                               # 模拟 AstrBot 公开发现入口


class FakeSpeechProvider:
    """非聊天 Provider：即使带模型名，也没有 text_chat 调用能力。"""

    def meta(self):
        return SimpleNamespace(id="speech", type="speech_to_text") # 模拟语音识别 Provider

    def get_model(self) -> str:
        return "whisper-1"                                 # 模型名不能让它混入聊天模型监控


class BrokenContext:
    """发现入口损坏时不得返回干净空列表。"""

    def get_all_providers(self):
        raise RuntimeError("context unavailable")           # 模拟 AstrBot 上下文临时异常


# --- 只有正确 OK 响应才算可用 ---
async def test_probe_validates_reply_content() -> None:
    context = FakeContext([
        FakeProvider("good", "gpt", "OK"),                # 完整功能链路应通过
        FakeProvider("bad", "other", "hello"),            # 有响应但不符合探针要求
    ])
    report = await probe_providers(context, timeout=1, concurrency=2, slow_ms=10_000)

    assert report["available_count"] == 1                  # 只统计经过内容验证的响应
    assert report["invalid_count"] == 1                    # 错误内容独立于连接异常
    assert report["state"] == "critical"                   # 功能探针失败影响综合模型状态


# --- 未发现路由不冒充全部健康 ---
async def test_empty_provider_list_is_unknown() -> None:
    report = await probe_providers(FakeContext([]), timeout=1, concurrency=1, slow_ms=1000)
    assert report["state"] == "unknown"                    # 空观测与健康严格区分
    assert report["route_count"] == 0                      # 没有制造默认模型


# --- 非聊天 Provider 不进入 API 模型监控 ---
async def test_non_chat_provider_is_ignored() -> None:
    report = await probe_providers(FakeContext([FakeSpeechProvider()]), timeout=1, concurrency=1, slow_ms=1000)
    assert report["route_count"] == 0                      # 语音模型不能被误报为聊天模型异常


# --- Provider 发现失败与干净空列表严格区分 ---
async def test_discovery_failure_raises() -> None:
    with pytest.raises(ProviderDiscoveryError, match="context unavailable"):
        await probe_providers(BrokenContext(), timeout=1, concurrency=1, slow_ms=1000)


# --- 发现失败不会覆盖最近报告和历史 ---
async def test_discovery_failure_preserves_state() -> None:
    original = {"version": 2, "resource_history": {}, "latest_computer": None, "model_history": {"p::m": [{"observed_at": 1, "state": "available", "latency_ms": 10}]}, "latest_model_report": {"observed_at": 1, "routes": []}}
    values = {"dashboard_state_v2": original}

    async def read(key: str, default: Any) -> Any:
        return values.get(key, default)

    async def write(key: str, value: Any) -> None:
        values[key] = value

    with pytest.raises(ProviderDiscoveryError):
        await probe_models(BrokenContext(), DashboardState(read, write), Settings())
    assert values["dashboard_state_v2"] == original          # 没有发生任何状态提交


# --- 路由身份不依赖 Provider 枚举顺序 ---
async def test_route_identity_is_stable_across_order() -> None:
    first = [FakeProvider("chat", "gpt", "OK", "openai-a"), FakeProvider("chat", "claude", "OK", "anthropic-b")]
    forward = await probe_providers(FakeContext(first), timeout=1, concurrency=2, slow_ms=1000)
    backward = await probe_providers(FakeContext(list(reversed(first))), timeout=1, concurrency=2, slow_ms=1000)
    assert {route["route_id"] for route in forward["routes"]} == {route["route_id"] for route in backward["routes"]}


# --- 重复稳定身份必须失败而不是追加顺序后缀 ---
async def test_duplicate_route_identity_fails() -> None:
    providers = [FakeProvider("chat", "gpt", "OK", "same"), FakeProvider("chat", "gpt", "OK", "same")]
    with pytest.raises(ProviderDiscoveryError, match="重复模型路由"):
        await probe_providers(FakeContext(providers), timeout=1, concurrency=2, slow_ms=1000)


# --- 排除规则不会产生真实模型调用 ---
async def test_excluded_route_is_not_probed() -> None:
    provider = FakeProvider("chat", "o3", "OK", "openai-main")
    report = await probe_providers(FakeContext([provider]), timeout=1, concurrency=1, slow_ms=1000, exclude_patterns=("*::o3",))
    assert report["route_count"] == 0                       # 通配符命中后目标不会进入探测任务


# --- 最近报告未满一小时不会重复产生 API 调用 ---
def test_model_probe_is_due_once_per_hour() -> None:
    observed_at = 1_700_000_000_000                         # 模拟最近一次真实报告时间
    report = {"observed_at": observed_at}
    assert model_probe_due(report, observed_at + MODEL_INTERVAL_MS - 1) is False
    assert model_probe_due(report, observed_at + MODEL_INTERVAL_MS) is True
    assert model_probe_due(None, observed_at) is True       # 首次运行必须建立基线
    assert model_probe_due({"observed_at": observed_at + MODEL_INTERVAL_MS}, observed_at) is True # 明显未来报告需重建基线


# --- 同小时保留最差状态且历史固定为 24 个小时桶 ---
def test_model_history_keeps_one_sample_per_hour() -> None:
    end = 25 * MODEL_INTERVAL_MS                            # 构造 25 小时历史和一次同小时更新
    existing = [{"observed_at": hour * MODEL_INTERVAL_MS, "state": "available", "latency_ms": hour} for hour in range(1, 26)]
    route = {"observed_at": end + 30_000, "state": "slow", "latency_ms": 9000}
    merged = _merge_hourly_samples(existing, route, end + 30_000)

    buckets = [sample["observed_at"] // MODEL_INTERVAL_MS for sample in merged]
    assert len(merged) == 24                                # 24h 面板不会出现多余数据点
    assert len(set(buckets)) == 24                          # 每个小时严格只有一个点
    assert merged[-1]["state"] == "slow"                  # 同小时较差状态覆盖健康结果


# --- 同小时成功重试不能抹掉先前故障 ---
def test_same_hour_success_cannot_erase_failure() -> None:
    end = 100 * MODEL_INTERVAL_MS
    existing = [{"observed_at": end + 1_000, "state": "unavailable", "latency_ms": 30_000}]
    route = {"observed_at": end + 2_000, "state": "available", "latency_ms": 500}
    merged = _merge_hourly_samples(existing, route, end + 2_000)
    assert merged[-1]["state"] == "unavailable"            # 小时槽表达该小时曾发生真实故障


# --- 消失路由保留一天并转为未知 ---
def test_missing_route_is_retained_then_expires() -> None:
    observed_at = 200 * MODEL_INTERVAL_MS
    previous_route = {"route_id": "p::m", "provider_id": "p", "provider_name": "P", "model_name": "m", "state": "available", "latency_ms": 100, "observed_at": observed_at - 23 * MODEL_INTERVAL_MS, "last_seen_at": observed_at - 23 * MODEL_INTERVAL_MS}
    previous = {"routes": [previous_route]}
    empty_report = {"observed_at": observed_at, "duration_ms": 1, "routes": []}

    retained = _reconcile_report(empty_report, previous)
    expired = _reconcile_report({**empty_report, "observed_at": observed_at + MODEL_INTERVAL_MS}, previous)
    assert retained["routes"][0]["state"] == "unknown"     # 暂时消失不沿用旧健康或旧故障
    assert retained["retained_count"] == 1
    assert expired["routes"] == []                          # 满 24 小时后自然移除
