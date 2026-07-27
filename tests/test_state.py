"""状态测试：验证单 KV 文档原子修改、复制隔离和损坏数据拒绝策略。"""

from typing import Any                                     # 描述内存 KV 值

import pytest                                              # 验证损坏状态异常

from dashview.state import DashboardState, STATE_KEY       # 被测唯一状态存储


# --- 原子保存状态并隔离读取副本 ---
async def test_state_updates_one_document_and_returns_copies() -> None:
    values: dict[str, Any] = {}                             # 模拟 AstrBot KV 存储

    async def read(key: str, default: Any) -> Any:
        return values.get(key, default)                     # 返回当前内存值

    async def write(key: str, value: Any) -> None:
        values[key] = value                                 # 记录一次完整文档写入

    state = DashboardState(read, write)                     # 使用真实状态类操作内存 KV
    saved = await state.update(lambda current: current["resource_history"].update({"cpu": [{"percent": 20}]}))
    saved["resource_history"]["cpu"][0]["percent"] = 99   # 修改返回副本不应污染存储

    current = await state.read()                            # 再读必须仍是落盘值
    assert current["resource_history"]["cpu"][0]["percent"] == 20
    assert STATE_KEY in values                              # 全部状态只写入唯一新键


# --- 拒绝覆盖损坏或旧版本状态 ---
async def test_state_rejects_unknown_version() -> None:
    async def read(key: str, default: Any) -> Any:
        return {"version": 1}                              # 模拟不兼容的旧状态

    async def write(key: str, value: Any) -> None:
        raise AssertionError("损坏状态不应被覆盖")           # 失败关闭必须禁止写入

    with pytest.raises(ValueError):
        await DashboardState(read, write).read()            # 明确要求用户清理旧新键


# --- 损坏嵌套容器按字段恢复而不丢失有效样本 ---
async def test_state_sanitizes_nested_structures() -> None:
    async def read(key: str, default: Any) -> Any:
        return {
            "version": 2,
            "resource_history": {"cpu": [{"percent": 20}, "bad"], "memory": "bad"},
            "model_history": [],
            "latest_computer": "bad",
            "latest_model_report": {"state": "healthy"},
            "unexpected": "discard",
        }

    async def write(key: str, value: Any) -> None:
        raise AssertionError("纯读取不应写回状态")

    current = await DashboardState(read, write).read()
    assert current["resource_history"] == {"cpu": [{"percent": 20}]}
    assert current["model_history"] == {}                  # 错误容器恢复为空历史
    assert current["latest_computer"] is None              # 损坏快照等待下一次采样恢复
    assert "unexpected" not in current                     # 未知字段不进入当前状态契约


# --- 回调或写入失败不污染 KV 返回的共享对象 ---
async def test_failed_update_keeps_reader_owned_state_unchanged() -> None:
    backing = {"version": 2, "resource_history": {"cpu": [{"percent": 20}]}, "model_history": {}, "latest_computer": None, "latest_model_report": None}

    async def read(key: str, default: Any) -> Any:
        return backing                                      # 故意返回同一个共享对象

    async def write(key: str, value: Any) -> None:
        raise AssertionError("测试回调失败时不应写入")

    def fail(current: dict[str, Any]) -> None:
        current["resource_history"]["cpu"][0]["percent"] = 99
        raise RuntimeError("change failed")

    with pytest.raises(RuntimeError, match="change failed"):
        await DashboardState(read, write).update(fail)
    assert backing["resource_history"]["cpu"][0]["percent"] == 20 # 未提交修改没有泄漏


# --- 外部插入值和返回值都不能继续修改已写状态 ---
async def test_update_isolates_inserted_and_returned_values() -> None:
    values: dict[str, Any] = {}
    external = [{"percent": 30}]

    async def read(key: str, default: Any) -> Any:
        return values.get(key, default)

    async def write(key: str, value: Any) -> None:
        values[key] = value                                 # 模拟保存对象引用的 KV 后端

    saved = await DashboardState(read, write).update(lambda current: current["resource_history"].update({"cpu": external}))
    external[0]["percent"] = 80
    saved["resource_history"]["cpu"][0]["percent"] = 90
    assert values[STATE_KEY]["resource_history"]["cpu"][0]["percent"] == 30
