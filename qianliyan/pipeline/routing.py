"""pipeline/routing.py —— 模型信源 tier 路由（spec §9.4）。

读 ``config/model-sources.yaml``（``tiers`` 权重表 + ``mapping`` 源名/子串 → tier），
为「模型相关」条目写 ``extra.tier``，供渲染层与选稿环节区分官方口径与二手转述。

mapping 采用**大小写不敏感子串**匹配；同时命中多条时取**最长 key**（更具体者胜）。
未命中默认 ``community``。本模块纯计算 + 读配置，不出网、不调 LLM。
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ..core import paths

logger = logging.getLogger(__name__)

__all__ = [
    "CONFIG_NAME",
    "DEFAULT_TIER",
    "DEFAULT_TIERS",
    "load_tiers",
    "classify_source",
    "tier_weight",
    "is_model_related",
    "annotate",
]

CONFIG_NAME = "model-sources.yaml"
DEFAULT_TIER = "community"
DEFAULT_TIERS = {"official": 1.0, "vendor": 0.9, "dev": 0.8, "kol": 0.7, "community": 0.5}

#: 判定「模型相关」用的标签与分类
_MODEL_TAGS = frozenset(["models", "model", "llm", "release", "modelscope"])
_MODEL_CATEGORIES = frozenset(["llm", "models", "model"])
#: 常见模型族关键词（与 model_cluster_agent 的族表同源，此处只用于相关性判定）
_MODEL_HINT_RE = re.compile(
    r"(claude|gpt|o\d\b|gemini|llama|qwen|deepseek|mistral|grok|kimi|glm|phi-\d|"
    r"command\s?r|yi-\d|ernie|doubao|hunyuan|大模型|模型)",
    re.IGNORECASE,
)


def load_tiers() -> Tuple[Dict[str, float], Dict[str, str]]:
    """读 model-sources.yaml，返回 ``(tiers, mapping)``。

    ``tiers``: ``{tier 名: 权重}``（配置缺失时用内置默认表）；
    ``mapping``: ``{源名或子串: tier 名}``（配置缺失时为空 dict）。
    """
    raw = paths.load_yaml_config(CONFIG_NAME)

    tiers: Dict[str, float] = {}
    for name, value in (raw.get("tiers") or {}).items():
        try:
            tiers[str(name)] = float(value)
        except (TypeError, ValueError):
            logger.warning("tier 权重非法，已忽略: %s=%r", name, value)
    if not tiers:
        tiers = dict(DEFAULT_TIERS)

    mapping: Dict[str, str] = {}
    for key, value in (raw.get("mapping") or {}).items():
        if not key or value is None:
            continue
        mapping[str(key)] = str(value)

    return tiers, mapping


def classify_source(
    source_name: Any,
    mapping: Optional[Dict[str, str]] = None,
) -> str:
    """按 mapping 子串匹配判定信源 tier；未命中返回 ``community``。

    多条命中时取最长 key（更具体的规则优先），保证结果与配置书写顺序无关。
    """
    if mapping is None:
        _, mapping = load_tiers()
    name = str(source_name or "").casefold()
    if not name:
        return DEFAULT_TIER

    best_key = ""
    best_tier = DEFAULT_TIER
    for key, tier in (mapping or {}).items():
        needle = str(key).casefold()
        if needle and needle in name and len(needle) > len(best_key):
            best_key = needle
            best_tier = str(tier)
    return best_tier


def tier_weight(tier: Any, tiers: Optional[Dict[str, float]] = None) -> float:
    """tier 名 → 权重（未知 tier 回落到 community 的权重，再兜底 0.5）。"""
    if tiers is None:
        tiers, _ = load_tiers()
    key = str(tier or DEFAULT_TIER)
    if key in tiers:
        return float(tiers[key])
    return float(tiers.get(DEFAULT_TIER, DEFAULT_TIERS[DEFAULT_TIER]))


def is_model_related(item: Dict[str, Any]) -> bool:
    """判定条目是否与模型相关（决定要不要写 ``extra.tier``）。

    命中任一即算：标签含 models/model/llm/release、``extra.category`` 属模型类、
    ``extra.cluster_key`` 已被模型族聚类标注、或标题命中常见模型族关键词。
    """
    if not isinstance(item, dict):
        return False
    tags = set(str(t).casefold() for t in (item.get("tags") or []))
    if tags & _MODEL_TAGS:
        return True
    extra = item.get("extra") if isinstance(item.get("extra"), dict) else {}
    if str(extra.get("category") or "").casefold() in _MODEL_CATEGORIES:
        return True
    if extra.get("cluster_key"):
        return True
    return bool(_MODEL_HINT_RE.search(str(item.get("title") or "")))


def annotate(items: Sequence[Dict[str, Any]]) -> None:
    """原地为模型相关条目写 ``extra.tier``（spec §9.4）。"""
    tiers, mapping = load_tiers()
    tagged = 0
    for item in items or []:
        if not isinstance(item, dict):
            continue
        if not is_model_related(item):
            continue
        extra = item.get("extra")
        if not isinstance(extra, dict):
            extra = {}
            item["extra"] = extra
        extra["tier"] = classify_source(item.get("source"), mapping)
        tagged += 1
    logger.debug("routing.annotate: %d/%d 条写入 extra.tier", tagged, len(items or []))


def sorted_tiers(tiers: Optional[Dict[str, float]] = None) -> List[str]:
    """tier 名按权重降序（渲染/选稿排序用）。"""
    if tiers is None:
        tiers, _ = load_tiers()
    return [name for name, _ in sorted(tiers.items(), key=lambda kv: (-float(kv[1]), kv[0]))]
