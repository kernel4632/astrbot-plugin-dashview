"""服务测试：验证状态语义、配置错误和并发结果顺序。"""

import asyncio                                             # 模拟不同完成速度的服务

import httpx                                               # 使用内存 HTTP Transport

from dashview.collectors import services as service_checks # 测试公开入口和协议动作


# --- 区分完整健康与可达受限 ---
async def test_http_restricted_is_not_healthy() -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(403, request=request))
    async with httpx.AsyncClient(transport=transport) as client:
        result = await service_checks._check_http(client, {"name": "私有 API", "type": "http", "url": "https://example.test"})

    assert result["state"] == "restricted"                 # 403 只证明服务可达
    assert result["status_code"] == 403                    # 页面保留事实状态码


# --- 在网络调用前拒绝有副作用的方法 ---
async def test_http_rejects_post_health_check() -> None:
    async with httpx.AsyncClient() as client:
        result = await service_checks._check_http(client, {"name": "危险接口", "type": "http", "url": "https://example.test", "method": "POST"})

    assert result["state"] == "configuration"              # 配置错误不是目标宕机
    assert "GET" in result["reason"]                       # 原因给出可执行修正方向


# --- 并发完成后保持用户配置顺序 ---
async def test_service_results_keep_configuration_order(monkeypatch) -> None:
    async def fake_check(client, service, timeout):
        await asyncio.sleep(service["delay"])               # 第二项先完成以验证排序逻辑
        return {"name": service["name"]}

    monkeypatch.setattr(service_checks, "_check_service", fake_check)
    results = await service_checks.check_services(
        [{"name": "first", "delay": 0.02}, {"name": "second", "delay": 0.0}],
        timeout=1,
        concurrency=2,
    )
    assert [item["name"] for item in results] == ["first", "second"]
