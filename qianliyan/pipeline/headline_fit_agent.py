"""pipeline/headline_fit_agent.py —— 头条适配度打分（spec §9.5）。

原地为 item 写 ``extra.headline_fit``（0–1 浮点），回答「这条适不适合当日报头条」。
hotness 衡量的是热度，headline_fit 衡量的是**可读性与叙事价值**——两者互补。

**回退公约（spec §5）**：LLM 不可用或任何异常 → 规则回退
``headline_fit = min(1.0, hotness / max_hotness)``（池内最大热度归一化；
max_hotness 为 0 时全部记 0.0）。绝不向上抛。
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Sequence

from ..core import llm_client

logger = logging.getLogger(__name__)

__all__ = ["BATCH_SIZE", "FIELD", "fallback_scores", "annotate"]

BATCH_SIZE = 20
FIELD = "headline_fit"

SYSTEM_PROMPT = (
    "你是日报主编，为候选条目打「头条适配度」分：0–1 的小数。"
    "1.0 = 重大且人人关心的行业事件；0.5 = 值得一读的常规更新；"
    "0.1 = 琐碎或高度小众。只输出 JSON。"
)


def _hotness(item: Dict[str, Any]) -> float:
    try:
        return float(item.get("hotness") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _clamp(value: Any) -> float:
    try:
        score = float(value)
    except (TypeError, ValueError):
        return 0.0
    if score < 0.0:
        return 0.0
    if score > 1.0:
        return 1.0
    return round(score, 4)


def fallback_scores(items: Sequence[Dict[str, Any]]) -> List[float]:
    """规则回退分：``min(1.0, hotness / max_hotness)``，保序返回。"""
    rows = [it for it in (items or []) if isinstance(it, dict)]
    if not rows:
        return []
    max_hotness = max(_hotness(it) for it in rows)
    if max_hotness <= 0.0:
        return [0.0] * len(rows)
    return [_clamp(_hotness(it) / max_hotness) for it in rows]


def _set_fit(item: Dict[str, Any], value: float) -> None:
    extra = item.get("extra")
    if not isinstance(extra, dict):
        extra = {}
        item["extra"] = extra
    extra[FIELD] = value


def _build_prompt(batch: Sequence[Dict[str, Any]]) -> str:
    payload = [
        {
            "i": index,
            "title": str(item.get("title") or ""),
            "sources": len(item.get("source_list") or []),
            "hotness": _hotness(item),
        }
        for index, item in enumerate(batch)
    ]
    return (
        "为下列条目打头条适配度分。\n"
        '输出 JSON 数组，元素形如 {"i": 原序号, "headline_fit": 0.0-1.0}，长度与输入一致。\n\n'
        "输入：\n" + json.dumps(payload, ensure_ascii=False)
    )


def _parse_reply(reply: Any, batch_size: int) -> Dict[int, float]:
    rows: List[Any]
    if isinstance(reply, list):
        rows = reply
    elif isinstance(reply, dict):
        candidate = reply.get("items") or reply.get("data") or reply.get("results")
        rows = candidate if isinstance(candidate, list) else [reply]
    else:
        return {}

    parsed: Dict[int, float] = {}
    for order, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        try:
            index = int(row.get("i", order))
        except (TypeError, ValueError):
            index = order
        if not (0 <= index < batch_size):
            continue
        raw = row.get(FIELD, row.get("score"))
        if raw is None:
            continue
        parsed[index] = _clamp(raw)
    return parsed


def annotate(items: Sequence[Dict[str, Any]]) -> None:
    """原地写 ``extra.headline_fit``；先铺规则分，LLM 成功的部分再覆盖。"""
    rows = [it for it in (items or []) if isinstance(it, dict)]
    if not rows:
        return

    for item, score in zip(rows, fallback_scores(rows)):
        _set_fit(item, score)

    client = None
    try:
        client = llm_client.LLMClient.from_env()
        available = client.is_available()
    except Exception as exc:  # noqa: BLE001
        logger.warning("LLM 可用性判定异常，按不可用处理: %s", exc)
        available = False
    if not available or client is None:
        logger.debug("headline_fit_agent: LLM 不可用，全部使用 hotness 归一化回退")
        return

    batches = [rows[i:i + BATCH_SIZE] for i in range(0, len(rows), BATCH_SIZE)]
    try:
        replies = client.batch_json([_build_prompt(b) for b in batches], system=SYSTEM_PROMPT)
    except Exception as exc:  # noqa: BLE001 - 回退公约：绝不向上抛
        logger.warning("头条适配度打分失败，沿用规则回退: %s", exc)
        return

    for batch, reply in zip(batches, replies or []):
        if reply is None:
            continue
        try:
            parsed = _parse_reply(reply, len(batch))
        except Exception as exc:  # noqa: BLE001
            logger.warning("头条适配度结果解析失败，沿用规则回退: %s", exc)
            continue
        for index, score in parsed.items():
            _set_fit(batch[index], score)
