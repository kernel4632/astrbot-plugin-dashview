# DashView

DashView 是一个 AstrBot 运行状态仪表盘插件，实时采集系统状态并生成精美报告。

![DashView 效果预览](demo.jpg)

## 特点

- **真数据**：实时采集 CPU、内存、磁盘和服务状态
- **单文件输出**：完整 HTML 打包，便于截图和部署
- **结构清晰**：代码组织合理，适合学习参考

## 快速开始

### 安装依赖

```bash
pip install -r requirements.txt
# 或使用 uv
uv sync
```

### 本地测试

```bash
python test.py
```

运行后打开 `output_test.html` 查看效果。

### 在 AstrBot 使用

注册名：`astrbot_plugin_dashview`

命令别名：`运行状态` / `状态` / `status`

## 项目结构

```
├── main.py           # 插件入口
├── data.py           # 页面数据翻译层
├── test.py           # 本地测试入口
├── demo.jpg          # 效果预览图
├── utils/
│   ├── monitor.py    # 监控总入口
│   ├── computer.py   # 电脑状态采集
│   ├── service.py    # 服务检测
│   └── render.py     # HTML 打包
└── resources/        # 模板和样式
```

## 配置服务

在 `main.py` 或 `test.py` 中配置：

```python
# HTTP 服务
{"name": "官网", "type": "http", "url": "https://example.com"}
# TCP 服务
{"name": "Redis", "type": "tcp", "host": "127.0.0.1", "port": 6379}
```

## 许可证

采用 **GNU Affero General Public License v3.0**，详见 [LICENSE](LICENSE)。