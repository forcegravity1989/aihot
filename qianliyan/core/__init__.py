"""core —— 底座层：路径解析、item schema、去重打分算法、JSONL 存储、OG 图、LLM 客户端。

边界铁律：本层只做与信源语义无关的通用能力，**不得 import** ``eyes`` / ``engine`` /
``pipeline`` / ``cli`` 任何模块；也不做任何业务编排。
子模块：``paths`` ``schema`` ``utils`` ``storage`` ``og_image`` ``llm_client``。
"""

from __future__ import annotations

__all__ = ["paths", "schema", "utils", "storage", "og_image", "llm_client"]
