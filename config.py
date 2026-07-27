"""
DashView 配置：把 AstrBot WebUI 传入的松散字典变成可靠的运行参数。

所有边界值都在这里收紧，后续采集、指令和展示代码只读取已经验证的数据。
调用示例：settings = Settings.from_dict(plugin_config)
"""

from __future__ import annotations                         # 允许类型注解引用尚未定义的类

from dataclasses import dataclass, field                   # 用显式数据类描述完整配置
from typing import Any                                     # 接收 AstrBot 提供的动态配置值


# --- 将任意配置数字限制在安全范围内 ---
def _number(value: Any, default: float, minimum: float, maximum: float) -> float:
    try:
        number = float(value)                              # WebUI 可能把数字保存成字符串
    except (TypeError, ValueError):
        return default                                     # 无法解析时使用经过验证的默认值
    return min(max(number, minimum), maximum)              # 防止负数或极端值拖垮运行时


# --- 读取非空文本 ---
def _text(value: Any, default: str) -> str:
    text = str(value).strip() if value is not None else "" # 清理 WebUI 文本框产生的空白
    return text or default                                 # 空文本不覆盖可用默认值


# --- 读取有显示长度上限的文本 ---
def _limited_text(value: Any, default: str, maximum: int) -> str:
    return _text(value, default)[:maximum]                 # 防止极长 WebUI 文本撑破静态图片


# --- 读取 WebUI 布尔值 ---
def _boolean(value: Any, default: bool) -> bool:
    if isinstance(value, bool):                             # 原生布尔值直接使用
        return value
    if isinstance(value, str):                              # 配置文件可能把开关保存成文本
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "on", "是", "开启"}:
            return True                                    # 只接受明确肯定文本
        if normalized in {"false", "0", "no", "off", "否", "关闭"}:
            return False                                   # 字符串 false 绝不能触发模型费用
    return default                                         # 模糊值保持安全默认


# --- 描述插件全部运行参数 ---
@dataclass(frozen=True, slots=True)
class Settings:
    nickname: str = "阿柯AKer"                             # 图片顶部显示的机器人名称
    success_text: str = "阿柯牛逼"                        # 全部服务健康时的自定义结论
    fail_text: str = "阿柯死了"                           # 存在服务故障时的自定义结论
    avatar_local_path: str = ""                            # 用户明确配置的本地头像
    avatar_url: str = ""                                   # 用户明确配置的远程头像
    services: tuple[dict[str, Any], ...] = field(default_factory=tuple)  # 要检测的 HTTP/TCP 目标
    services_configured: bool = False                       # 区分未配置和全部暂时停用
    service_timeout: float = 5.0                            # 单个服务允许等待的秒数
    service_concurrency: int = 8                            # 同时检测的服务上限
    resource_interval_minutes: float = 10.0                 # 后台资源采样间隔，0 表示关闭
    resource_history_size: int = 72                         # 每项资源最多保留的真实样本数
    model_monitor_enabled: bool = True                      # 默认建立每小时一次的 24h API 模型监控
    model_exclude_patterns: tuple[str, ...] = field(default_factory=tuple) # 不产生探测调用的路由通配符
    model_timeout: float = 30.0                             # 单条模型探测允许等待的秒数
    model_concurrency: int = 6                              # 同时探测的模型路由上限
    model_slow_ms: int = 8000                               # 高于该延迟的成功响应标为较慢
    cpu_warning: float = 75.0                               # CPU 警告阈值
    cpu_critical: float = 90.0                              # CPU 严重阈值
    memory_warning: float = 80.0                            # 内存警告阈值
    memory_critical: float = 92.0                           # 内存严重阈值
    disk_warning: float = 80.0                              # 磁盘警告阈值
    disk_critical: float = 92.0                             # 磁盘严重阈值
    max_service_rows: int = 8                               # 图片最多展开的服务行数
    max_model_rows: int = 8                                 # 图片最多展开八张模型图
    cache_keep_count: int = 3                               # 本地只保留最近几张发送图片
    avatar_max_bytes: int = 2 * 1024 * 1024                 # 防止头像下载耗尽内存

    # --- 从 AstrBot 配置构建可靠参数 ---
    @classmethod
    def from_dict(cls, source: dict[str, Any] | None) -> "Settings":
        defaults = cls()                                    # slots 数据类默认值需从实例读取
        config = source if isinstance(source, dict) else {} # 插件无配置时仍能直接启动
        services = config.get("services", [])              # 空列表明确表示不检测外部服务
        safe_services = tuple(item for item in services if isinstance(item, dict) and _boolean(item.get("enabled"), True)) if isinstance(services, list) else ()
        services_configured = any(isinstance(item, dict) for item in services) if isinstance(services, list) else False
        raw_exclusions = config.get("model_exclude_patterns", []) # WebUI 列表只接受非空文本
        model_exclusions = tuple(str(item).strip() for item in raw_exclusions if str(item).strip()) if isinstance(raw_exclusions, list) else ()
        cpu_warning = _number(config.get("cpu_warning"), 75, 1, 99) # 先读警告值才能约束严重值
        memory_warning = _number(config.get("memory_warning"), 80, 1, 99)
        disk_warning = _number(config.get("disk_warning"), 80, 1, 99)

        return cls(
            nickname=_limited_text(config.get("nickname"), defaults.nickname, 64),
            success_text=_limited_text(config.get("success_text"), defaults.success_text, 64),
            fail_text=_limited_text(config.get("fail_text"), defaults.fail_text, 64),
            avatar_local_path=str(config.get("avatar_local_path") or "").strip(),
            avatar_url=str(config.get("avatar_url") or "").strip(),
            services=safe_services,
            services_configured=services_configured,
            service_timeout=_number(config.get("service_timeout", config.get("timeout")), 5.0, 0.5, 30.0),
            service_concurrency=int(_number(config.get("service_concurrency"), 8, 1, 32)),
            resource_interval_minutes=_number(config.get("resource_interval_minutes", config.get("resource_collect_interval_minutes")), 10.0, 0.0, 1440.0),
            resource_history_size=int(_number(config.get("resource_history_size"), 72, 2, 288)),
            model_monitor_enabled=_boolean(config.get("model_monitor_enabled"), True),
            model_exclude_patterns=model_exclusions,
            model_timeout=_number(config.get("model_timeout"), 30.0, 3.0, 120.0),
            model_concurrency=int(_number(config.get("model_concurrency"), 6, 1, 24)),
            model_slow_ms=int(_number(config.get("model_slow_ms"), 8000, 500, 60000)),
            cpu_warning=cpu_warning,
            cpu_critical=max(cpu_warning + 1, _number(config.get("cpu_critical"), 90, 2, 100)),
            memory_warning=memory_warning,
            memory_critical=max(memory_warning + 1, _number(config.get("memory_critical"), 92, 2, 100)),
            disk_warning=disk_warning,
            disk_critical=max(disk_warning + 1, _number(config.get("disk_critical"), 92, 2, 100)),
            max_service_rows=int(_number(config.get("max_service_rows"), 8, 1, 8)),
            max_model_rows=int(_number(config.get("max_model_rows"), 8, 1, 8)),
            cache_keep_count=int(_number(config.get("cache_keep_count"), 3, 1, 20)),
        )
