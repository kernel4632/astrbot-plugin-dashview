"""配置测试：验证松散 WebUI 值会变成边界安全的不可变 Settings。"""

from dashview.config import Settings                       # 被测配置数据类


# --- 收紧极端配置并过滤损坏服务 ---
def test_settings_clamps_values_and_filters_services() -> None:
    settings = Settings.from_dict({
        "service_timeout": -4,                             # 负超时必须提升到安全下限
        "service_concurrency": 999,                        # 极端并发必须压回上限
        "model_concurrency": "0",                         # 字符串数字也应正常解析
        "services": [None, {"name": "API", "type": "http"}],
    })

    assert settings.service_timeout == 0.5                  # 网络调用不会得到负超时
    assert settings.service_concurrency == 32               # 服务检测不会无限并发
    assert settings.model_concurrency == 1                  # 模型探测至少保留一个名额
    assert settings.services == ({"name": "API", "type": "http"},)


# --- 空服务列表保持用户明确语义 ---
def test_settings_keeps_empty_services() -> None:
    settings = Settings.from_dict({"services": []})        # 用户没有要求探测默认站点
    assert settings.services == ()                          # 插件不得偷偷请求作者服务


# --- 文本 false 能关闭小时级付费模型监控 ---
def test_settings_parses_false_string_safely() -> None:
    settings = Settings.from_dict({"model_monitor_enabled": "false"})
    assert settings.model_monitor_enabled is False          # 字符串本身非空也不能被当作 True


# --- 模型长期监控默认建立小时级证据 ---
def test_model_monitor_is_enabled_by_default() -> None:
    assert Settings.from_dict({}).model_monitor_enabled is True # 安装后自动建立 24h 状态轨迹


# --- 资源默认每小时采样并保留最近一天 ---
def test_resource_history_defaults_to_24_hourly_samples() -> None:
    settings = Settings.from_dict({})
    assert settings.resource_interval_minutes == 60
    assert settings.resource_history_size == 24


# --- WebUI 可停用单个服务并排除指定模型 ---
def test_settings_filters_disabled_services_and_model_patterns() -> None:
    settings = Settings.from_dict({
        "services": [
            {"enabled": False, "name": "停用服务", "type": "http"},
            {"enabled": True, "name": "启用服务", "type": "http"},
        ],
        "model_exclude_patterns": ["*::o3", "  expensive-*  ", ""],
    })
    assert [service["name"] for service in settings.services] == ["启用服务"]
    assert settings.services_configured is True             # 即使全部停用也能与未配置区分
    assert settings.model_exclude_patterns == ("*::o3", "expensive-*")


# --- 详情行数限制与 Chromium 最大高度相匹配 ---
def test_row_limits_are_render_safe() -> None:
    settings = Settings.from_dict({"max_service_rows": 99, "max_model_rows": 99})
    assert settings.max_service_rows == 8                   # 服务表最多八行
    assert settings.max_model_rows == 8                     # 八张模型图已通过最坏布局渲染
