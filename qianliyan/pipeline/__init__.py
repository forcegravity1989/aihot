"""pipeline —— 加工渲染层：频道路由 + Agent 标注 + HTML 简报渲染。

边界铁律（spec §0）：本层**只加工不采集**，依赖方向为 ``pipeline → core``，
不得 import ``eyes`` / ``engine`` / ``cli``。

子模块：
  * ``minitpl``               —— 自研极简模板引擎（无 jinja2、无 eval/exec）；
  * ``channels``              —— channels.yaml 规则路由 + 频道页写盘；
  * ``report``                —— 单文件自包含 HTML 简报渲染；
  * ``routing``               —— 模型信源 tier 标注；
  * ``auto_translate``        —— 英→中标题/摘要翻译（带缓存）；
  * ``model_cluster_agent``   —— 模型族聚类标注；
  * ``headline_fit_agent``    —— 头条适配度打分；
  * ``headline_cluster_agent``—— 同题聚合标注。

后四者统一遵循 **spec §5 Agent 回退公约**：LLM 不可用或任何异常一律走规则回退，
绝不向上抛异常。
"""

from __future__ import annotations

__all__ = [
    "minitpl",
    "channels",
    "report",
    "routing",
    "auto_translate",
    "model_cluster_agent",
    "headline_fit_agent",
    "headline_cluster_agent",
]
