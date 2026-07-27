"""资源测试：验证后台与手动采样共享完整单飞边界。"""

import asyncio                                             # 构造两个重叠资源采样任务
from typing import Any                                     # 描述内存 KV 文档

import dashview.commands.resources as resources_module     # 替换采集器观察并发边界
from dashview.commands.resources import sample_resources   # 被测资源指令
from dashview.config import Settings                       # 使用正式历史配置
from dashview.state import DashboardState                  # 使用真实原子状态实现


# --- 构造最小完整主机快照 ---
def snapshot(observed_at: int, sent: int) -> dict[str, Any]:
    return {
        "observed_at": observed_at,
        "network": {"sent": sent, "received": sent},
        "cpu": {"percent": 20},
        "memory": {"percent": 30},
        "swap": {"percent": 0},
        "disk": {"percent": 40},
    }


# --- 两个采样从采集到提交始终串行 ---
async def test_sampling_lock_serializes_collection_and_commit(monkeypatch) -> None:
    values: dict[str, Any] = {}                             # 模拟 AstrBot KV
    entered = 0                                             # 记录实际进入采集器的次数
    active = 0                                              # 记录同时运行采集器数量
    peak_active = 0                                         # 并发峰值必须保持一
    release_first = asyncio.Event()                         # 人为阻塞第一次采集

    async def read(key: str, default: Any) -> Any:
        return values.get(key, default)

    async def write(key: str, value: Any) -> None:
        values[key] = value                                 # 保存完整状态文档

    async def collect() -> dict[str, Any]:
        nonlocal entered, active, peak_active
        entered += 1
        active += 1
        peak_active = max(peak_active, active)
        if entered == 1:
            await release_first.wait()                      # 第二个任务此时只能等待采样锁
        result = snapshot(entered * 1_000, entered * 100)
        active -= 1
        return result

    monkeypatch.setattr(resources_module, "collect_computer", collect)
    state = DashboardState(read, write)
    lock = asyncio.Lock()
    first = asyncio.create_task(sample_resources(state, Settings(), lock))
    await asyncio.sleep(0)                                  # 让第一次采样进入阻塞点
    second = asyncio.create_task(sample_resources(state, Settings(), lock))
    await asyncio.sleep(0)
    assert entered == 1                                     # 第二个采集器尚未进入

    release_first.set()
    await asyncio.gather(first, second)
    current = await state.read()
    assert peak_active == 1                                 # 采集本身也被单飞保护
    assert current["latest_computer"]["observed_at"] == 2_000
    assert current["latest_computer"]["network"]["sent_per_second"] == 100
