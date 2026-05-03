"""
这个文件只做一件事：检测 AstrBot WebUI 里已打开模型的连通性。

它不负责渲染图片、不负责接收命令、不负责发消息。
它的职责是把所有 Provider 遍历一遍，对每个模型发一条探测消息，
然后返回统一的检测结果字典，谁调用谁决定怎么用。

核心流程只有三步：
1. 遍历所有 Provider，收集每个 Provider 下已配置的模型列表
2. 对每个模型异步调用 provider.text_chat()，记录延迟和状态
3. 按 Provider 分组、统计成功/较慢/错误数量，输出结果

调用方式非常简单：
    from .modelProbe import ModelProbe
    report = await ModelProbe.probe(context, config)
    # report 是一个字典，包含 providers / ok_count / slow_count / error_count 等字段

    from .modelProbe import ModelProbe
    report = await ModelProbe.probe(context, {"timeout": 15, "concurrency": 2})

    from .modelProbe import ModelProbe
    targets = ModelProbe.collectTargets(context, config)
    # targets 是 [(provider, model, provider_name), ...] 列表
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from astrbot.api import logger


# —— 探测消息和提示词可以在这里统一改 ——
PROBE_MESSAGE = "只回复 OK 两个字母。"  # 发给模型的探测消息，越短越快
PROBE_SYSTEM = "你是一个模型连通性探针。请只回复 OK，不要解释。"  # 系统提示词


class ModelProbe:
    """
    这个对象只做模型连通性检测这一件事。

    它没有实例状态，所有方法都是类方法。
    调用入口只有一个：ModelProbe.probe(context, config)。

    config 里可以放这些可选项：
    - timeout: 单个模型探针的超时秒数，默认 30
    - concurrency: 全局并发数，默认 3
    - slow_ms: 超过这个毫秒数就算"较慢"，默认 8000
    - probe_message: 自定义探测消息，不传就用默认的
    - probe_system: 自定义系统提示词
    - max_models: 每个 Provider 最多测几个模型，0 表示不限制，默认 0
    """

    # —— 默认配置，外部不传就用这些 ——
    defaultTimeout = 30
    defaultConcurrency = 3
    defaultSlowMs = 8000
    defaultMaxModels = 0

    # =================================================================
    # 公开入口
    # =================================================================

    @classmethod
    async def probe(
        cls,
        context: Any,
        config: dict | None = None,
    ) -> dict[str, Any]:
        """
        统一入口：收集目标 → 并发探测 → 统计分组 → 返回结果字典。

        参数 context 是 AstrBot 的插件上下文对象，必须有 context.get_all_providers()。
        参数 config 是可选的配置字典。

        返回的字典结构：
        {
            "title":          "模型连通性",
            "checkedAt":      "2025-01-01 12:00:00",
            "elapsedMs":      1234,
            "total":          10,
            "okCount":        8,
            "slowCount":      1,
            "errorCount":     1,
            "providerCount":  3,
            "providers":      [ 按 Provider 分组的结果列表 ],
            "allOk":          True/False,
        }
        """
        startedAt = time.perf_counter()
        cfg = config or {}

        logger.info("开始模型连通性检测")

        # 第一步：收集所有要测的目标
        targets, providerNames = cls._collect(context, cfg)
        if not targets:
            logger.info("没有发现可检测的模型")
            return cls._emptyReport(startedAt)

        logger.info(f"收集到 {len(targets)} 个检测目标")

        # 第二步：并发探测
        results = await cls._probeAll(targets, cfg)

        # 第三步：统计并分组
        report = cls._build(results, providerNames, startedAt, cfg)

        logger.info(
            f"模型连通性检测完成：ok={report['okCount']} "
            f"slow={report['slowCount']} error={report['errorCount']} "
            f"耗时 {report['elapsedMs']}ms"
        )
        return report

    @classmethod
    def collectTargets(
        cls,
        context: Any,
        config: dict | None = None,
    ) -> tuple[list[tuple[Any, str, str, str]], dict[str, str]]:
        """
        只收集检测目标，不做实际探测。
        返回 (targets, providerNames)，
        其中 targets 每个元素是 (provider实例, 模型名, provider分组名, provider显示名)。
        如果你只想拿模型列表做别的事（比如展示配置项候选），调这个方法就行。
        """
        return cls._collect(context, config or {})

    # =================================================================
    # 第一步：收集目标
    # =================================================================

    @classmethod
    def _collect(
        cls,
        context: Any,
        cfg: dict,
    ) -> tuple[list[tuple[Any, str, str, str]], dict[str, str]]:
        """
        从 context 获取所有 Provider，逐个提取已配置的模型名。
        返回 (目标列表, {分组id: 显示名}) 两个值。
        """
        try:
            providers = list(context.get_all_providers() or [])
        except Exception as exc:
            logger.warning(f"获取 Provider 列表失败: {exc}")
            return [], {}

        maxModels = int(cfg.get("max_models", cls.defaultMaxModels) or cls.defaultMaxModels)

        targets: list[tuple[Any, str, str, str]] = []  # (provider, model, groupId, displayName)
        providerNames: dict[str, str] = {}              # groupId → displayName
        seen: set[tuple[str, str]] = set()              # (groupId, model) 去重用

        for provider in providers:
            try:
                meta = provider.meta()
            except Exception as exc:
                logger.warning(f"跳过无法读取 meta 的 Provider: {exc}")
                continue

            # 获取分组标识和显示名
            groupId = cls._groupId(provider, meta)
            displayName = cls._displayName(provider, meta)

            if groupId not in providerNames:
                providerNames[groupId] = displayName

            # 获取这个 Provider 配置的模型列表
            models = cls._modelsFromProvider(provider, meta)

            if maxModels > 0 and len(models) > maxModels:
                models = models[:maxModels]  # 限制每个 Provider 最多测几个模型

            for model in models:
                key = (groupId, model)
                if key in seen:
                    continue  # 同一个分组下出现重复模型名就跳过
                seen.add(key)
                targets.append((provider, model, groupId, displayName))

        return targets, providerNames

    # =================================================================
    # 第二步：并发探测
    # =================================================================

    @classmethod
    async def _probeAll(
        cls,
        targets: list[tuple[Any, str, str, str]],
        cfg: dict,
    ) -> list[dict[str, Any]]:
        """
        用信号量控制并发数，对所有目标同时发起探测。
        每个目标单独处理，一个失败不影响其他。
        """
        concurrency = int(cfg.get("concurrency", cls.defaultConcurrency) or cls.defaultConcurrency)
        concurrency = max(1, concurrency)  # 至少允许 1 个并发

        semaphore = asyncio.Semaphore(concurrency)

        async def one(provider, model, groupId, displayName):
            async with semaphore:
                return await cls._probeOne(provider, model, groupId, displayName, cfg)

        tasks = [one(*t) for t in targets]
        gathered = await asyncio.gather(*tasks, return_exceptions=True)

        # 过滤掉在 gather 层面就炸掉的结果（极少情况）
        results: list[dict[str, Any]] = []
        for item in gathered:
            if isinstance(item, Exception):
                logger.warning(f"探测任务异常: {item}")
                results.append({
                    "model": "unknown",
                    "status": "error",
                    "latencyMs": 0,
                    "error": str(item),
                    "groupId": "unknown",
                    "displayName": "未知",
                })
            else:
                results.append(item)

        return results

    @classmethod
    async def _probeOne(
        cls,
        provider: Any,
        model: str,
        groupId: str,
        displayName: str,
        cfg: dict,
    ) -> dict[str, Any]:
        """
        对单个模型发送一次 text_chat 探测。
        记录延迟毫秒数，根据结果判断 ok / slow / error。
        """
        timeout = float(cfg.get("timeout", cls.defaultTimeout) or cls.defaultTimeout)
        slowMs = int(cfg.get("slow_ms", cls.defaultSlowMs) or cls.defaultSlowMs)
        prompt = str(cfg.get("probe_message") or PROBE_MESSAGE)
        system = str(cfg.get("probe_system") or PROBE_SYSTEM)

        started = time.perf_counter()
        try:
            # text_chat 是异步的，用 asyncio.wait_for 兜底超时
            response = await asyncio.wait_for(
                provider.text_chat(
                    prompt=prompt,
                    system_prompt=system,
                    model=model,
                ),
                timeout=timeout,
            )
            latencyMs = int((time.perf_counter() - started) * 1000)
            # 从响应里提取回复文本（不同 Provider 的返回结构不同，做个兜底）
            replyText = cls._extractReply(response)

            status = "slow" if latencyMs >= slowMs else "ok"
            return {
                "model": model,
                "status": status,
                "latencyMs": latencyMs,
                "replyPreview": replyText[:80],
                "error": "",
                "groupId": groupId,
                "displayName": displayName,
            }

        except asyncio.TimeoutError:
            latencyMs = int((time.perf_counter() - started) * 1000)
            return {
                "model": model,
                "status": "error",
                "latencyMs": latencyMs,
                "replyPreview": "",
                "error": f"超时（{timeout:.0f}秒）",
                "groupId": groupId,
                "displayName": displayName,
            }

        except Exception as exc:
            latencyMs = int((time.perf_counter() - started) * 1000)
            msg = str(exc).strip()
            # 截断过长的错误信息
            if len(msg) > 120:
                msg = msg[:117] + "..."
            return {
                "model": model,
                "status": "error",
                "latencyMs": latencyMs,
                "replyPreview": "",
                "error": msg,
                "groupId": groupId,
                "displayName": displayName,
            }

    # =================================================================
    # 第三步：统计分组
    # =================================================================

    @classmethod
    def _build(
        cls,
        results: list[dict[str, Any]],
        providerNames: dict[str, str],
        startedAt: float,
        cfg: dict,
    ) -> dict[str, Any]:
        """
        把扁平的探测结果按 groupId 分组，统计每个分组和整体的 ok/slow/error 数量。
        """
        # 按 groupId 分组
        groups: dict[str, list[dict[str, Any]]] = {}
        for item in results:
            gid = item.get("groupId", "unknown")
            if gid not in groups:
                groups[gid] = []
            groups[gid].append(item)

        # 构建每个 Provider 的汇总信息
        providers: list[dict[str, Any]] = []
        totalOk = 0
        totalSlow = 0
        totalError = 0

        for gid, items in groups.items():
            okCount = sum(1 for i in items if i["status"] == "ok")
            slowCount = sum(1 for i in items if i["status"] == "slow")
            errorCount = sum(1 for i in items if i["status"] == "error")

            # 确定这个 Provider 的整体状态
            if errorCount > 0:
                groupStatus = "error"
                groupLabel = "异常"
            elif slowCount > 0:
                groupStatus = "slow"
                groupLabel = "较慢"
            else:
                groupStatus = "ok"
                groupLabel = "正常"

            totalOk += okCount
            totalSlow += slowCount
            totalError += errorCount

            providers.append({
                "groupId": gid,
                "displayName": providerNames.get(gid, gid),
                "modelCount": len(items),
                "okCount": okCount,
                "slowCount": slowCount,
                "errorCount": errorCount,
                "status": groupStatus,
                "statusLabel": groupLabel,
                "results": items,
            })

        # 按显示名排序，让输出稳定
        providers.sort(key=lambda p: p["displayName"])

        elapsedMs = int((time.perf_counter() - startedAt) * 1000)
        allOk = totalError == 0

        return {
            "title": "模型连通性",
            "checkedAt": time.strftime("%Y-%m-%d %H:%M:%S"),
            "elapsedMs": elapsedMs,
            "total": len(results),
            "okCount": totalOk,
            "slowCount": totalSlow,
            "errorCount": totalError,
            "providerCount": len(providers),
            "providers": providers,
            "allOk": allOk,
        }

    @classmethod
    def _emptyReport(cls, startedAt: float) -> dict[str, Any]:
        """没有检测目标时返回的空报告，字段结构保持一致"""
        elapsedMs = int((time.perf_counter() - startedAt) * 1000)
        return {
            "title": "模型连通性",
            "checkedAt": time.strftime("%Y-%m-%d %H:%M:%S"),
            "elapsedMs": elapsedMs,
            "total": 0,
            "okCount": 0,
            "slowCount": 0,
            "errorCount": 0,
            "providerCount": 0,
            "providers": [],
            "allOk": True,
        }

    # =================================================================
    # 工具方法
    # =================================================================

    @classmethod
    def _groupId(cls, provider: Any, meta: Any) -> str:
        """
        获取 Provider 的分组标识。
        优先取 provider_source_id，没有就用 id，再没有就用 type。
        同一个分组的 Provider 会被合并到一起显示。
        """
        cfg = getattr(provider, "provider_config", {}) or {}
        sid = cfg.get("provider_source_id")
        if sid:
            return str(sid)
        mid = getattr(meta, "id", "")
        if mid:
            return str(mid)
        return str(getattr(meta, "type", "unknown"))

    @classmethod
    def _displayName(cls, provider: Any, meta: Any) -> str:
        """
        获取 Provider 的显示名称。
        优先取配置里的 display_name / name，再取 meta.id，最后用 type 兜底。
        """
        cfg = getattr(provider, "provider_config", {}) or {}
        for key in ("display_name", "name"):
            val = cfg.get(key)
            if val:
                return str(val)
        mid = getattr(meta, "id", "")
        if mid:
            return str(mid)
        return str(getattr(meta, "type", "unknown"))

    @classmethod
    def _modelsFromProvider(cls, provider: Any, meta: Any) -> list[str]:
        """
        从 Provider 对象里尽量挖掘出已配置的模型列表。
        按优先级尝试多个常见属性，不重复、不空字符串。
        """
        models: list[str] = []

        def add(val: Any) -> None:
            """递归地把各种形状的值变成字符串模型名加进列表"""
            if val is None or isinstance(val, bool):
                return
            if isinstance(val, (list, tuple, set)):
                for item in val:
                    add(item)
                return
            if isinstance(val, dict):
                # 字典里尝试常见 key
                for k in ("model", "model_id", "model_name", "id", "name"):
                    v = val.get(k)
                    if v:
                        add(v)
                        break
                return
            text = str(val).strip()
            if text:
                models.append(text)

        # 来源1：provider.get_model()
        try:
            getter = getattr(provider, "get_model", None)
            if callable(getter):
                add(getter())
        except Exception:
            pass

        # 来源2：meta.model
        try:
            add(getattr(meta, "model", ""))
        except Exception:
            pass

        # 来源3：常见配置属性
        for attr in ("models", "enabled_models", "configured_models", "model_list", "model", "model_config"):
            try:
                val = getattr(provider, attr, None)
                if val is not None:
                    add(val)
            except Exception:
                continue

        # 去重，保持顺序
        seen: set[str] = set()
        unique: list[str] = []
        for m in models:
            if m not in seen:
                seen.add(m)
                unique.append(m)

        return unique

    @classmethod
    def _extractReply(cls, response: Any) -> str:
        """
        从 text_chat 的返回值里提取回复文本。
        不同 Provider 的返回结构不一样，这里做多路径兜底。
        """
        # 路径1：标准属性 completion_text
        text = getattr(response, "completion_text", "")
        if text:
            return str(text)

        # 路径2：字典式 result["completion_text"]
        if isinstance(response, dict):
            text = response.get("completion_text") or response.get("content") or response.get("text")
            if text:
                return str(text)

        # 路径3：直接就是字符串
        if isinstance(response, str):
            return response

        # 路径4：有 message 属性
        msg = getattr(response, "message", None)
        if msg:
            if hasattr(msg, "content"):
                return str(msg.content)
            return str(msg)

        # 啥都拿不到，返回原始对象的字符串形式
        return str(response)
