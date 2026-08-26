"""pipeline/headline_cluster_agent.py —— 同题聚合标注（spec §9.5）。

原地为 item 写 ``extra.story_key``：**同一件事**的多条报道共享一个 key，
使日报可以「一事一条 + 多源佐证」，而不是把同一事件铺满版面。

注意与 ``core.utils.dedup_and_score`` 的分工：后者按标题签名做**严格去重**，
本 agent 处理的是「标题不同但说的是同一件事」的软聚合。

**回退公约（spec §5）**：LLM 不可用或任何异常 → ``story_key = sig``（各自成题），
即退化为「不聚合」，功能仍可用。绝不向上抛。
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Sequence

from ..core import llm_client

logger = logging.getLogger(__name__)

__all__ = ["BATCH_SIZE", "FIELD", "fallback_story_key", "annotate"]

BATCH_SIZE = 30
FIELD = "story_key"

SYSTEM_PROMPT = (
    "你是新闻编辑，判断哪些条目在讲同一件事（同一次发布、同一份报告、同一起事件）。"
    "把讲同一件事的条目归入同一组，给该组一个稳定的英文小写 key（如 claude-opus-5-launch）。"
    "只讲自己事情的条目单独成组。只输出 JSON。"
)


def fallback_story_key(item: Dict[str, Any]) -> str:
    """规则回退：``story_key = sig``（各自成题）。"""
    return str(item.get("sig") or "")


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
    """原地写 ``extra.story_key``；先铺 sig 兜底，LLM 成功的部分再覆盖。"""
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
        logger.debug("headline_cluster_agent: LLM 不可用，story_key 全部回退为 sig")
        return

    batches = [rows[i:i + BATCH_SIZE] for i in range(0, len(rows), BATCH_SIZE)]
    try:
        replies = client.batch_json([_build_prompt(b) for b in batches], system=SYSTEM_PROMPT)
    except Exception as exc:  # noqa: BLE001 - 回退公约：绝不向上抛
        logger.warning("同题聚合调用失败，story_key 沿用 sig: %s", exc)
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
