# DashView

DashView 是一个 AstrBot 运行状态仪表盘插件。它会收集当前机器的 CPU、内存、磁盘和你指定的服务状态，再把这些真实数据整理成一个单文件 HTML，最后交给 AstrBot 渲染成图片发送出去。

## 效果预览

![DashView 效果预览](demo.jpg)

## 核心设计理念

这个项目现在的重点不是"炫技"，而是三件事：

1. **真数据，不做假监控** - 所有展示的数据都是实时采集的真实信息
2. **单文件输出，方便截图和部署** - 生成独立的 HTML 文件，无需额外依赖
3. **结构清楚，适合新人边看边学** - 代码组织清晰，注释详细，便于学习

## 完整工作流程

你触发"运行状态"命令后，完整流程遵循 **事件 → 指令 → 数据 → 反馈** 的设计模式：

1. 用户发送命令
2. `main.py` 收到命令并触发监控流程
3. `utils/monitor.py` 调用监控模块采集真实数据
4. `data.py` 把原始数据整理成页面要用的结构（翻译层）
5. `utils/render.py` 把模板、CSS、头像打包成单文件 HTML
6. AstrBot 把 HTML 渲染成图片并返回给用户

如果你是刚学插件开发，这条链路很值得多看几遍，因为它就是这个项目最核心的设计思路。

## 项目结构

当前项目结构尽量按"真实对象"来拆，不用太多抽象词：

- `main.py`
  - AstrBot 插件正式入口
  - 接收命令、调用采集、调用渲染、返回图片
- `data.py`
  - 页面数据翻译层
  - 把监控原始数据整理成模板直接可用的结构
- `test.py`
  - 本地测试入口
  - 直接生成 `output_test.html` 方便预览
- `demo.jpg`
  - 效果预览图
- `utils/monitor.py`
  - 监控总入口
  - 负责把电脑采集、服务检测、摘要统计串起来
- `utils/computer.py`
  - 收集电脑自己的状态
  - 包含主机名、系统、CPU、内存、磁盘、开机时间
- `utils/service.py`
  - 检测服务状态
  - 当前支持 HTTP 和 TCP
- `utils/render.py`
  - 把模板、CSS、头像打包成单文件 HTML
- `resources/templates/index.html.jinja`
  - 页面模板入口
- `resources/templates/macros.html.jinja`
  - 页面模板块
- `resources/index.css`
  - 页面样式
- `resources/avatar.jpg`
  - 默认头像
- `output_test.html`
  - 本地测试时生成的预览文件

## 快速开始

### 安装依赖

如果你只想本地测试 HTML，可以先安装项目依赖：

```bash
pip install -r requirements.txt
```

如果你使用 uv，也可以根据 `pyproject.toml` 安装：

```bash
uv sync
```

### 本地测试

这个项目已经保留了最简单的本地测试入口：

```bash
python test.py
```

运行后会发生三件事：
1. 采集当前机器真实状态
2. 检测 `test.py` 里配置的服务
3. 生成 `output_test.html`

然后你直接用浏览器打开 `output_test.html` 就能看效果。

### 在 AstrBot 里使用

插件入口在 `main.py`，注册名是 `astrbot_plugin_dashview`。

当前命令别名写在 `ALIASES` 里，常用的是：
- `运行状态`
- `状态`
- `status`

如果你后面要改命令名，直接看 `main.py` 里的命令装饰器就行。

## 配置服务监控

最直接的地方是修改 `main.py` 或 `test.py` 中的服务配置：

```python
# HTTP 服务配置
{"name": "超级主核API", "type": "http", "url": "https://api.hujiarong.site/"}
{"name": "主核Kernyr网站", "type": "http", "url": "https://www.hujiarong.site/"}

# TCP 服务配置
{"name": "Redis", "type": "tcp", "host": "127.0.0.1", "port": 6379}
```

## 自定义指南

按目的去找文件，会很清楚：

### 1. 想改页面显示哪些字段

看 `data.py` - 这是"原始数据 → 页面数据"的翻译层。比如 CPU 卡片显示什么文案、服务卡片显示什么状态字、右侧摘要显示什么，主要都在这里改。

### 2. 想改监控怎么采集

看：
- `utils/monitor.py` - 总入口和汇总统计
- `utils/computer.py` - 电脑信息采集
- `utils/service.py` - 服务检测逻辑

### 3. 想改 HTML 打包逻辑

看 `utils/render.py`，这个文件负责：
- 读模板
- 读 CSS
- 内联样式
- 内联头像
- 输出单文件 HTML

### 4. 想改页面结构和样式

看：
- `resources/templates/index.html.jinja` - 页面结构
- `resources/templates/macros.html.jinja` - 可复用的模板块
- `resources/index.css` - 页面样式

## 适合新手的阅读顺序

如果你是第一次看 AstrBot 插件项目，推荐按这个顺序读：

1. **先看 `main.py`** - 知道命令从哪里进来
2. **再看 `utils/monitor.py`** - 知道真实数据怎么被采集
3. **再看 `data.py`** - 知道原始数据怎么变成页面数据
4. **再看 `utils/render.py`** - 知道 HTML 怎么被打包
5. **最后看模板和 CSS** - 知道页面最终为什么长这样

这个顺序和项目真实运行顺序基本一致，所以读起来最不容易乱。

## 设计原则

### 1. 只保留一个稳定总入口

外部只需要记住三个接口：

```python
Monitor.collect()      # 采集监控数据
Render.build(...)      # 构建 HTML
Data.buildCollected(...)  # 转换数据格式
```

新人不用一上来就记很多文件。

### 2. 文件按真实职责拆开

监控模块被拆成了：
- `computer.py` - 收集电脑状态
- `service.py` - 检测服务状态  
- `monitor.py` - 总入口和汇总统计

这样你只看文件名，大概就知道这个文件负责什么。

### 3. 让本地测试尽量简单

直接运行 `python test.py` 就能看到结果，这对调样式和查数据非常重要。

## 扩展建议

如果你未来还要继续加功能，建议继续保持现在这个思路：

- 新增一种服务检测方式 → 加到 `service.py`
- 新增一种机器信息 → 加到 `computer.py`
- 新增一种页面卡片 → 改 `data.py`
- 新增模板结构 → 改模板文件
- **不要**把采集、翻译、渲染重新混成一个大文件

这样项目会一直比较稳，也更适合开源阅读。

## 快速定位表

如果你现在只是想快速定位改动位置，可以记住这张表：

| 改动目的     | 文件位置                                |
| ------------ | --------------------------------------- |
| 改命令入口   | `main.py`                               |
| 改监控采集   | `utils/computer.py`、`utils/service.py` |
| 改汇总逻辑   | `utils/monitor.py`                      |
| 改页面数据   | `data.py`                               |
| 改 HTML 打包 | `utils/render.py`                       |
| 改模板       | `resources/templates/*.jinja`           |
| 改样式       | `resources/index.css`                   |
| 本地测试     | `test.py`                               |

这就是 DashView 现在最重要的阅读地图。