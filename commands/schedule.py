"""
后台定时指令：独立调度资源采样与模型探测，并在插件卸载时完整停止。

每个循环重新读取固定 Settings，异常只影响当前轮次，取消信号始终向上传递。
调用示例：schedule.start(); await schedule.stop()
"""

from __future__ import annotations                         # 允许现代类型注解

import asyncio                                             # 创建、休眠并取消后台任务
from typing import Any, Awaitable, Callable                 # 描述两个独立业务动作和日志对象

from ..config import Settings                              # 读取两个定时间隔


# --- 管理两个独立后台循环 ---
class Schedule:
    def __init__(
        self,
        settings: Settings,                                 # 插件初始化时固定的验证配置
        sample_resources: Callable[[], Awaitable[Any]],     # 一次资源采样业务动作
        probe_models: Callable[[], Awaitable[Any]],         # 一次模型探测业务动作
        logger: Any,                                        # 使用 AstrBot 统一日志输出
    ) -> None:
        self._settings = settings                           # 两个循环共享同一配置快照
        self._sample_resources = sample_resources           # 调度层不关心采样内部数据
        self._probe_models = probe_models                   # 调度层不关心 Provider 细节
        self._logger = logger                               # 后台失败统一反馈到 AstrBot 日志
        self._tasks: list[asyncio.Task[Any]] = []           # stop() 集中回收全部任务

    # --- 启动已启用的后台循环 ---
    def start(self) -> None:
        if self._tasks:
            return                                          # 重复 initialize 不创建孤儿任务
        if self._settings.resource_interval_minutes > 0:
            self._tasks.append(asyncio.create_task(self._resource_loop(), name="dashview-resource"))
        if self._settings.model_monitor_enabled:
            self._tasks.append(asyncio.create_task(self._model_loop(), name="dashview-model"))

    # --- 停止并等待全部后台循环 ---
    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()                                   # 先向全部任务发取消，避免串行等待延迟
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True) # 确认所有任务真正退出
        self._tasks.clear()                                 # 允许同一对象未来安全重启

    # --- 定时采集主机资源 ---
    async def _resource_loop(self) -> None:
        await self._run_once(self._sample_resources, "系统资源采样") # 启动后立即建立第一个真实样本
        while True:
            await asyncio.sleep(max(60.0, self._settings.resource_interval_minutes * 60))
            await self._run_once(self._sample_resources, "系统资源采样")

    # --- 定时探测模型路由 ---
    async def _model_loop(self) -> None:
        while True:
            await self._run_once(self._probe_models, "模型连通性探测") # 指令读取最后报告，只有到期才调用 API
            await asyncio.sleep(60.0)                       # 每分钟检查到期时间，实际探测仍严格至少间隔一小时

    # --- 执行一次后台动作并记录反馈 ---
    async def _run_once(self, action: Callable[[], Awaitable[Any]], action_name: str) -> None:
        try:
            changed = await action()                        # False 表示该小时尚未到期且没有副作用
            if changed is not False:
                self._logger.info("DashView %s完成", action_name)
        except asyncio.CancelledError:
            raise                                           # 取消必须终止循环，不能当普通失败吞掉
        except Exception:
            self._logger.exception("DashView %s失败", action_name) # 下一轮仍会继续运行
