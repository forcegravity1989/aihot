from __future__ import annotations

from . import aihot, builders, cc_prompts, company, insights, local, plugins_official

# 键名即 source_kind；测试锁死为这五只
EYES = {
    "aihot": aihot.fetch,
    "builders": builders.fetch,
    "company": company.fetch,
    "local": local.fetch,
    "insights": insights.fetch,
}

# 变更情报源产出 source_kind=local，不符合核心眼「键名=source_kind」的桶模型，故单列
CHANGE_EYES = {
    "cc_prompts": cc_prompts.fetch,
    "plugins_official": plugins_official.fetch,
}

__all__ = ["EYES", "CHANGE_EYES"]
