"""eyes/ —— 五眼并行采集层统一注册表。

每眼模块暴露 ``fetch(cfg, since=None) -> list[dict]``（item 用 ``core.schema.make_item`` 构造；
允许抛异常，由 ``cli.sync`` 统一 try/except 捕获记账——单眼故障不外溢）。可测试性铁律：网络壳
``fetch()`` 与解析纯函数 ``parse_payload(payload, cfg)`` 分离，单测直接用 fixtures 喂 parse。
"""

from __future__ import annotations

from . import aihot, builders, cc_prompts, company, insights, local, plugins_official

#: 五眼并行采集层的核心注册表（源显示名/source_kind 与键名一一对应，测试锁死为这五只）。
EYES = {
    "aihot": aihot.fetch,
    "builders": builders.fetch,
    "company": company.fetch,
    "local": local.fetch,
    "insights": insights.fetch,
}

#: 变更情报源（Wave H1，spec-v0.3 §19）。它们产出的 item 用合法 ``source_kind="local"``，
#: 与核心眼 1:1（键名=source_kind）的桶模型不同，故单列一张表；由 ``cli.sync`` 在全量同步时
#: 额外拉取、折入原始池（``source_kind=local`` 桶），再交由 ``pipeline.change_intel`` 加工。
CHANGE_EYES = {
    "cc_prompts": cc_prompts.fetch,
    "plugins_official": plugins_official.fetch,
}

__all__ = ["EYES", "CHANGE_EYES"]
