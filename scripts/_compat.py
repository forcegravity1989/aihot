from __future__ import annotations

import importlib
import sys
from pathlib import Path

# 让直接 python scripts/x.py 也能 import 到 qianliyan 包
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def forward(module_name: str) -> int:
    module = importlib.import_module("qianliyan.cli.{0}".format(module_name))
    return module.main(sys.argv[1:])
