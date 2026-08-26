"""千里眼 (qianliyan) —— 五眼并行的 AI 情报采集与日报流水线。

四层管道：``eyes/`` 采集 → ``core/`` 底座 → ``pipeline/`` 加工渲染 → ``cli/`` 入口分发。
跨层依赖方向固定为 ``cli → pipeline → core`` 与 ``cli → eyes → engine → core``；
``core`` 不得 import 其余三层。
"""

from __future__ import annotations

__version__ = "0.2.0"

__all__ = ["__version__"]
