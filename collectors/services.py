"""
服务采集器：并发检测用户配置的 HTTP 与 TCP 目标。

每个目标都会返回统一状态；受限响应与真正健康分开，配置错误也不会拖垮整批检测。
调用示例：results = await check_services(settings.services, timeout=5, concurrency=8)
"""

from __future__ import annotations                         # 允许现代类型注解

import asyncio                                             # 并发执行多个独立服务检测
import time                                                # 记录单个检测耗时与观察时间
from typing import Any, Iterable                           # 描述外部配置与检测结果列表

import httpx                                               # 提供真正异步的 HTTP 请求


RESTRICTED_STATUS = {401, 403, 405}                        # 可达但不能证明业务健康的 HTTP 状态码


# --- 并发检测全部服务 ---
async def check_services(
    services: Iterable[dict[str, Any]],                     # 已由 Settings 过滤过的服务字典
    timeout: float,                                         # 每个目标的最长等待秒数
    concurrency: int,                                       # 同时占用网络连接的上限
) -> list[dict[str, Any]]:
    service_list = list(services)                           # 固定顺序，结果与 WebUI 配置一致
    if not service_list:                                    # 用户明确未配置服务时不制造默认目标
        return []

    limit = asyncio.Semaphore(concurrency)                  # 避免大量目标同时冲击网络
    client_timeout = httpx.Timeout(timeout)                 # 连接、读取和写入共享统一上限
    async with httpx.AsyncClient(timeout=client_timeout, follow_redirects=False) as client:
        async def check_one(index: int, service: dict[str, Any]) -> tuple[int, dict[str, Any]]:
            async with limit:                               # 每个检测占用一个并发名额
                result = await _check_service(client, service, timeout)
                return index, result                        # 保留原配置顺序，不受完成速度影响

        completed = await asyncio.gather(*(check_one(index, service) for index, service in enumerate(service_list)))
    return [result for _, result in sorted(completed)]      # 页面稳定展示用户配置顺序


# --- 按协议选择检测动作 ---
async def _check_service(client: httpx.AsyncClient, service: dict[str, Any], timeout: float) -> dict[str, Any]:
    service_type = str(service.get("type") or "").strip().lower() # 类型为空时进入明确配置错误
    if service_type in {"http", "https"}:                 # HTTP 与 HTTPS 使用同一检测规则
        return await _check_http(client, service)
    if service_type == "tcp":                              # TCP 只验证端口是否能建立连接
        return await _check_tcp(service, timeout)
    return _error_result(service, "configuration", "不支持的服务类型")


# --- 检测 HTTP 服务 ---
async def _check_http(client: httpx.AsyncClient, service: dict[str, Any]) -> dict[str, Any]:
    url = str(service.get("url") or "").strip()             # URL 直接来自插件配置
    method = str(service.get("method") or "GET").upper()   # 仅允许不会修改业务数据的方法
    if not url.startswith(("http://", "https://")):       # 阻止缺协议或其他协议进入请求库
        return _error_result(service, "configuration", "HTTP 地址必须以 http:// 或 https:// 开头")
    if method not in {"GET", "HEAD"}:                     # 状态探测不执行 POST 等有副作用的方法
        return _error_result(service, "configuration", "状态探测仅支持 GET 或 HEAD")

    headers = {str(key): str(value) for key, value in (service.get("headers") or {}).items()} if isinstance(service.get("headers"), dict) else {}
    started_at = time.perf_counter()                        # 使用单调时钟避免系统时间调整影响耗时
    try:
        response = await client.request(method, url, headers=headers)
        duration_ms = round((time.perf_counter() - started_at) * 1000)
        if 200 <= response.status_code < 400:               # 成功和标准重定向代表健康响应
            return _result(service, url, "healthy", duration_ms, response.status_code, "服务响应正常")
        if response.status_code in RESTRICTED_STATUS:       # 受限只证明目标可达，不冒充业务健康
            return _result(service, url, "restricted", duration_ms, response.status_code, "服务可达，但探测请求受限")
        return _result(service, url, "down", duration_ms, response.status_code, f"HTTP {response.status_code}")
    except httpx.TimeoutException:
        return _result(service, url, "down", round((time.perf_counter() - started_at) * 1000), None, "连接超时")
    except httpx.HTTPError as error:
        return _result(service, url, "down", round((time.perf_counter() - started_at) * 1000), None, _safe_error(error))


# --- 检测 TCP 端口 ---
async def _check_tcp(service: dict[str, Any], timeout: float) -> dict[str, Any]:
    host = str(service.get("host") or "").strip()           # 主机名直接来自插件配置
    try:
        port = int(service.get("port"))                     # 字符串端口也允许由 WebUI 传入
    except (TypeError, ValueError):
        return _error_result(service, "configuration", "TCP 端口必须是整数")
    if not host or not 1 <= port <= 65535:                  # 无效地址不进入网络连接阶段
        return _error_result(service, "configuration", "TCP 主机或端口无效")

    target = f"{host}:{port}"                              # 页面只显示不含凭据的 TCP 目标
    started_at = time.perf_counter()                        # 使用单调时钟统计连接耗时
    writer = None                                           # 连接成功后必须显式关闭写端
    try:
        _, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=timeout)
        duration_ms = round((time.perf_counter() - started_at) * 1000)
        return _result(service, target, "healthy", duration_ms, None, "端口连接正常")
    except asyncio.TimeoutError:
        return _result(service, target, "down", round((time.perf_counter() - started_at) * 1000), None, "连接超时")
    except OSError as error:
        return _result(service, target, "down", round((time.perf_counter() - started_at) * 1000), None, _safe_error(error))
    finally:
        if writer is not None:                              # 只关闭本次探测创建的成功连接
            writer.close()
            await writer.wait_closed()


# --- 构建统一检测结果 ---
def _result(service: dict[str, Any], target: str, state: str, duration_ms: int, status_code: int | None, reason: str) -> dict[str, Any]:
    return {
        "id": str(service.get("id") or service.get("name") or target), # 稳定标识用于后续统计
        "name": str(service.get("name") or target),         # 页面显示的业务名称
        "type": str(service.get("type") or "unknown").lower(),
        "target": target,                                  # 不包含请求头等秘密的展示目标
        "state": state,                                    # healthy/restricted/down/configuration
        "duration_ms": duration_ms,                        # 本次真实检测耗时
        "status_code": status_code,                        # TCP 没有 HTTP 状态码
        "reason": reason,                                  # 已清理并限制长度的人类可读原因
        "observed_at": int(time.time() * 1000),            # 本次状态事实产生的 UTC 时间
    }


# --- 构建配置错误结果 ---
def _error_result(service: dict[str, Any], state: str, reason: str) -> dict[str, Any]:
    target = str(service.get("url") or service.get("host") or "未配置")
    return _result(service, target, state, 0, None, reason) # 配置错误没有虚构的网络耗时


# --- 清理外部库错误信息 ---
def _safe_error(error: Exception) -> str:
    text = str(error).replace("\n", " ").strip()          # 单行文本避免撑高静态图片
    return text[:96] or error.__class__.__name__             # 限长并为无文本异常保留类型
