# DashView 🔧

AstrBot 运行状态仪表盘插件，实时采集系统状态并生成精美监控报告。

![DashView 效果预览](demo.jpg)

## 功能特性

- **实时监控**：采集 CPU、内存、磁盘状态
- **服务检测**：支持 HTTP/TCP 服务健康检查
- **精美报告**：生成单文件 HTML 监控面板
- **多平台支持**：兼容所有主流 IM 平台

## 快速开始

### 本地测试

```bash
pip install -r requirements.txt
python test.py
```

打开 `output_test.html` 查看效果。

### 在 AstrBot 使用

**注册名**：`astrbot_plugin_dashview`

**命令别名**：`运行状态` / `状态` / `status`

## 服务配置

在 `main.py` 中配置监控目标：

```python
# HTTP 服务
{"name": "官网", "type": "http", "url": "https://example.com"}
# TCP 服务
{"name": "Redis", "type": "tcp", "host": "127.0.0.1", "port": 6379}
```

## 项目结构

```
├── main.py           # 插件入口
├── data.py           # 页面数据翻译层
├── test.py           # 本地测试入口
├── demo.jpg          # 效果预览图
├── metadata.yaml     # 插件元数据
├── utils/
│   ├── monitor.py    # 监控总入口
│   ├── computer.py   # 电脑状态采集
│   ├── service.py    # 服务检测
│   └── render.py     # HTML 打包
└── resources/        # 模板和样式
```

## 许可证

**GNU Affero General Public License v3.0**

详见 [LICENSE](LICENSE)。