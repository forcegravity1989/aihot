from __future__ import annotations

import json
import logging
import re
import unicodedata
from datetime import timedelta
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ..core import utils

from ..core import llm_client

logger = logging.getLogger(__name__)

__all__ = ["BATCH_SIZE", "FIELD", "fallback_story_key", "release_entity", "rule_merge", "annotate"]

BATCH_SIZE = 30
FIELD = "story_key"

SYSTEM_PROMPT = (
    "你是新闻编辑，判断哪些条目在讲同一件事（同一次发布、同一份报告、同一起事件）。"
    "把讲同一件事的条目归入同一组，给该组一个稳定的英文小写 key（如 claude-opus-5-launch）。"
    "只讲自己事情的条目单独成组。只输出 JSON。"
)


def fallback_story_key(item: Dict[str, Any]) -> str:
    return str(item.get("sig") or "")


# 长的在前：「claude code」要先于「claude」被匹配
_BRANDS = (
    "claude code", "gpt", "claude", "opus", "sonnet", "haiku", "fable", "mythos",
    "gemini", "gemma", "qwen", "glm", "kimi", "deepseek", "grok", "llama",
    "muse spark", "muse", "mimo", "step", "hunyuan", "hy", "doubao", "minimax",
    "mistral", "phi", "pytorch", "vllm", "sglang",
)
_VARIANTS = (
    "flashx", "flash", "sol", "luna", "astra", "pro", "max", "mini", "nano", "turbo",
    "preview", "cyber", "lite", "plus", "ultra", "air", "coder", "vl", "thinking",
)
# 连字符后任意词（Qwen3.8-Omni 是另一款模型），或空格后的已知型号词
_ENTITY_RE = re.compile(
    r"(?<![a-z0-9])({brands})[ -]?v?(\d+(?:\.\d+)*)(?:-([a-z]+)|[ ]({variants}))?(?![a-z0-9.])".format(
        brands="|".join(re.escape(b) for b in _BRANDS),
        variants="|".join(_VARIANTS),
    )
)
_NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)*")
# 出现这些词才算在报这次发布本身，而非借题评论
_LAUNCH_RE = re.compile(
    r"发布|上线|推出|开源|亮相|登场|introduc|launch|release|announc|unveil|debut|"
    r"now available|is here|\bmeet\b|\bships?\b"
)
_DASHES_RE = re.compile("[\u2010-\u2015\u2212]")
STORY_WINDOW = timedelta(hours=72)
# 只差「较/比」一个字的那种
NEAR_DUP_JACCARD = 0.7


def _norm(text: Any) -> str:
    text = unicodedata.normalize("NFKC", str(text or "")).casefold()
    return _DASHES_RE.sub("-", text)


def release_entity(title: Any) -> str:
    """取第一个：主语在前（「Claude Code v2.1.280 发布，新增 Opus 5.5」讲的是 Claude Code）。"""
    match = _ENTITY_RE.search(_norm(title))
    if not match:
        return ""
    brand, version = match.group(1), match.group(2)
    variant = match.group(3) or match.group(4) or ""
    return " ".join(part for part in (brand, version, variant) if part)


def _is_launch(title: Any) -> bool:
    return bool(_LAUNCH_RE.search(_norm(title)))


def _bigrams(title: Any) -> set:
    text = utils.normalize_title(title)
    return {text[i:i + 2] for i in range(len(text) - 1)}


def _when(item: Dict[str, Any]):
    return utils.parse_date(item.get("date"))


def _close_in_time(a, b) -> bool:
    if a is None or b is None:
        return True
    return abs(a - b) <= STORY_WINDOW


def rule_merge(items: Sequence[Dict[str, Any]]) -> int:
    """72 小时内同一发布实体的发布类标题、或同天 Jaccard ≥ 0.7 的近重复归一组；LLM 只能批内聚合，跨批靠这里接上。"""
    rows = [it for it in (items or []) if isinstance(it, dict)]
    parent = list(range(len(rows)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i: int, j: int) -> None:
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[max(ri, rj)] = min(ri, rj)

    when = [_when(it) for it in rows]
    by_entity: "Dict[str, List[int]]" = {}
    by_day: "Dict[str, List[int]]" = {}
    for idx, item in enumerate(rows):
        title = item.get("title")
        entity = release_entity(title)
        if entity and _is_launch(title):
            by_entity.setdefault(entity, []).append(idx)
        by_day.setdefault(str(item.get("date") or "")[:10], []).append(idx)

    for members in by_entity.values():
        for pos, i in enumerate(members):
            for j in members[pos + 1:]:
                if _close_in_time(when[i], when[j]):
                    union(i, j)

    # 近重复还要求数字一致：不同版本的提示词变更标题几乎相同，只是数字不同
    numbers = [tuple(_NUMBER_RE.findall(_norm(it.get("title")))) for it in rows]
    for members in by_day.values():
        grams = {i: _bigrams(rows[i].get("title")) for i in members}
        for pos, i in enumerate(members):
            for j in members[pos + 1:]:
                if numbers[i] != numbers[j]:
                    continue
                a, b = grams[i], grams[j]
                if a and b and len(a & b) / len(a | b) >= NEAR_DUP_JACCARD:
                    union(i, j)

    groups: "Dict[int, List[int]]" = {}
    for idx in range(len(rows)):
        groups.setdefault(find(idx), []).append(idx)

    merged = 0
    for members in groups.values():
        if len(members) < 2:
            continue
        keys = {str((rows[i].get("extra") or {}).get(FIELD) or fallback_story_key(rows[i])) for i in members}
        canonical = min(keys)
        for item in rows:
            if str((item.get("extra") or {}).get(FIELD) or "") in keys:
                _set_key(item, canonical)
        for i in members:
            _set_key(rows[i], canonical)
        merged += len(members) - 1
    return merged


def _set_key(item: Dict[str, Any], value: str) -> None:
    extra = item.get("extra")
    if not isinstance(extra, dict):
        extra = {}
        item["extra"] = extra
    extra[FIELD] = value


def _build_prompt(batch: Sequence[Dict[str, Any]]) -> str:
    payload = [
        {"i": index, "title": str(item.get("title") or "")}
        for index, item in enumerate(batch)
    ]
    return (
        "把下列条目按「是否在讲同一件事」分组。\n"
        '输出 JSON 数组，元素形如 {"i": 原序号, "story_key": "英文小写短横线 key"}，'
        "同一件事的条目必须给相同 key，长度与输入一致。\n\n输入：\n"
        + json.dumps(payload, ensure_ascii=False)
    )


def _parse_reply(reply: Any, batch_size: int) -> Dict[int, str]:
    rows: List[Any]
    if isinstance(reply, list):
        rows = reply
    elif isinstance(reply, dict):
        candidate = reply.get("items") or reply.get("data") or reply.get("results")
        rows = candidate if isinstance(candidate, list) else [reply]
    else:
        return {}

    parsed: Dict[int, str] = {}
    for order, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        try:
            index = int(row.get("i", order))
        except (TypeError, ValueError):
            index = order
        if 0 <= index < batch_size:
            parsed[index] = str(row.get(FIELD) or row.get("key") or "").strip().casefold()
    return parsed


def annotate(items: Sequence[Dict[str, Any]]) -> None:
    rows = [it for it in (items or []) if isinstance(it, dict)]
    if not rows:
        return

    for item in rows:
        _set_key(item, fallback_story_key(item))

    client = None
    try:
        client = llm_client.LLMClient.from_env()
        available = client.is_available()
    except Exception as exc:  # noqa: BLE001
        logger.warning("LLM 可用性判定异常，按不可用处理: %s", exc)
        available = False
    if not available or client is None:
        logger.debug("headline_cluster_agent: LLM 不可用，story_key 回退为 sig + 规则聚合")
        rule_merge(rows)
        return

    batches = [rows[i:i + BATCH_SIZE] for i in range(0, len(rows), BATCH_SIZE)]
    try:
        replies = client.batch_json([_build_prompt(b) for b in batches], system=SYSTEM_PROMPT)
    except Exception as exc:  # noqa: BLE001 - 回退公约：绝不向上抛
        logger.warning("同题聚合调用失败，story_key 沿用 sig: %s", exc)
        rule_merge(rows)
        return

    for batch, reply in zip(batches, replies or []):
        if reply is None:
            continue
        try:
            parsed = _parse_reply(reply, len(batch))
        except Exception as exc:  # noqa: BLE001
            logger.warning("同题聚合结果解析失败，story_key 沿用 sig: %s", exc)
            continue
        for index, value in parsed.items():
            if value:
                _set_key(batch[index], value)
    rule_merge(rows)
