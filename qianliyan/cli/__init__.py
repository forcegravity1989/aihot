"""cli —— 入口分发层：编排器、四通道分发、API、日报、维护脚本。

边界铁律（spec §0）：本层负责**编排与入口**，不实现算法——依赖方向为
``cli → pipeline → core`` 与 ``cli → eyes → engine → core``。

子模块：
  * ``sync``             —— 主编排器（五眼并行抓取 → 合并打分 → 加工渲染）；
  * ``deliver``           —— 四通道分发（in-chat / html / welink / email）；
  * ``api_server``        —— FastAPI 服务（可选依赖，缺失时给出安装提示）；
  * ``daily_digest_all``  —— 日报编排（HTML 精选路线）；
  * ``data_prune``        —— 数据目录清理；
  * ``health_check``      —— 信源连通性探测。

每个 cli 模块都提供 ``main(argv=None) -> int``，允许 ``print`` 面向用户输出。
"""

from __future__ import annotations

__all__ = [
    "sync",
    "deliver",
    "api_server",
    "daily_digest_all",
    "data_prune",
    "health_check",
]
