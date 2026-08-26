"""scripts/_compat.py —— 统一转发器（spec §10.7）。

历史脚本入口 ``scripts/*.py`` 只是薄壳——真正实现全在 ``qianliyan.cli.*``。
``forward(module_name)`` 把当前进程 ``sys.argv[1:]`` 原样转给
``qianliyan.cli.<module_name>.main()`` 并返回其退出码；顺带把仓库根目录塞进
``sys.path``，保证不论从哪个工作目录直接 ``python scripts/x.py`` 都能 import 到
``qianliyan`` 包。
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def forward(module_name: str) -> int:
    """转发到 ``qianliyan.cli.<module_name>.main(sys.argv[1:])``，返回其退出码。"""
    module = importlib.import_module("qianliyan.cli.{0}".format(module_name))
    return module.main(sys.argv[1:])
