"""调度测试：验证后台循环启停、幂等和反馈语义。"""

import asyncio                                             # 让真实后台任务进入并退出休眠

from dashview.commands.schedule import Schedule            # 被测后台调度器
from dashview.config import Settings                       # 控制定时任务开关


class FakeLogger:
    """只记录调度器会使用的日志动作。"""

    def __init__(self) -> None:
        self.info_messages = []                             # 成功动作记录
        self.exceptions = 0                                 # 失败动作计数

    def info(self, *args) -> None:
        self.info_messages.append(args)

    def exception(self, *args) -> None:
        self.exceptions += 1


# --- 全关闭配置不会创建后台任务 ---
async def test_start_respects_disabled_loops() -> None:
    async def action():
        return True

    schedule = Schedule(Settings(resource_interval_minutes=0, model_monitor_enabled=False), action, action, FakeLogger())
    schedule.start()
    assert schedule._tasks == []                            # 明确关闭后没有隐藏轮询
    await schedule.stop()                                   # 空停止保持幂等


# --- 启动幂等且停止等待并清空全部任务 ---
async def test_start_and_stop_manage_each_task_once() -> None:
    calls = {"resource": 0, "model": 0}

    async def resource():
        calls["resource"] += 1
        return True

    async def model():
        calls["model"] += 1
        return True

    schedule = Schedule(Settings(), resource, model, FakeLogger())
    schedule.start()
    task_ids = [id(task) for task in schedule._tasks]
    schedule.start()
    assert [id(task) for task in schedule._tasks] == task_ids # 重复初始化不创建孤儿任务
    await asyncio.sleep(0)
    assert calls == {"resource": 1, "model": 1}            # 两个循环启动后立即建立基线或检查到期
    await schedule.stop()
    assert schedule._tasks == []                            # 所有任务已取消并等待完成


# --- 未到期模型检查不记录虚假完成日志 ---
async def test_run_once_distinguishes_noop_and_failure() -> None:
    logger = FakeLogger()

    async def noop():
        return False                                        # 本小时未到期

    async def failure():
        raise RuntimeError("failed")                        # 当前轮失败不终止未来调度

    schedule = Schedule(Settings(), noop, noop, logger)
    await schedule._run_once(noop, "模型探测")
    await schedule._run_once(failure, "模型探测")
    assert logger.info_messages == []                       # 无副作用不能声称完成
    assert logger.exceptions == 1                           # 普通失败反馈到日志
