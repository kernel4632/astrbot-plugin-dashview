"""
测试入口：把带连字符的仓库目录挂载成可导入的 dashview 测试包。

生产插件仍由 AstrBot 使用正式包名加载；这里只解决 Python 不能直接 import 连字符目录的问题。
"""

from __future__ import annotations                         # 允许现代类型注解

import sys                                                 # 注册测试专用包名
from pathlib import Path                                   # 定位仓库根目录
from types import ModuleType                               # 创建不执行 AstrBot 入口的包对象


ROOT = Path(__file__).parent.parent                         # 所有被测模块都位于仓库根目录
package = ModuleType("dashview")                           # 不导入 main.py，测试无需安装 AstrBot
package.__path__ = [str(ROOT)]                              # 相对导入从真实仓库目录解析
sys.modules.setdefault("dashview", package)               # 每轮测试共享同一个包身份
