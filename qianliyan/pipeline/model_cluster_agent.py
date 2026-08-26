"""pipeline/model_cluster_agent.py —— 模型族聚类标注（spec §9.5）。

原地为 item 写 ``extra.cluster_key``（如 ``claude`` / ``gpt`` / ``qwen``），
让「同一模型族的多条动态」在渲染与选稿阶段可以收拢。

**回退公约（spec §5）**：LLM 不可用或任何异常 → 走正则族名匹配，**取标题中最先出现的
已知族名**；一条都没命中则写空串（键始终存在，下游可无条件读）。绝不向上抛。
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Sequence, Tuple

from ..core import llm_client

logger = logging.getLogger(__name__)

__all__ = ["MODEL_FAMILIES", "BATCH_SIZE", "fallback_cluster_key", "annotate"]

BATCH_SIZE = 20

#: 已知模型族 → 匹配正则（顺序即同位置命中时的优先级）
MODEL_FAMILIES: Tuple[Tuple[str, str], ...] = (
    ("claude", r"claude"),
    ("gpt", r"\bgpt[-\s]?\d*\w*|\bchatgpt\b"),
    ("gemini", r"gemini"),
    ("llama", r"\bllama\s?\d*"),
    ("qwen", r"qwen|通义千问"),
    ("deepseek", r"deepseek|深度求索"),
    ("mistral", r"mistral|mixtral"),
    ("grok", r"\bgrok\b"),
    ("kimi", r"\bkimi\b"),
    ("glm", r"\bglm[-\s]?\d*|智谱"),
    ("phi", r"\bphi[-\s]?\d"),
    ("yi", r"\byi[-\s]?\d+[bB]?\b"),
    ("ernie", r"ernie|文心"),
    ("doubao", r"doubao|豆包"),
    ("hunyuan", r"hunyuan|混元"),
    ("command-r", r"command[-\s]?r\b"),
    ("nova", r"\bnova\s?(?:pro|lite|micro)\b"),
)

_COMPILED = tuple(
    (name, re.compile(pattern, re.IGNORECASE)) for name, pattern in MODEL_FAMILIES
)

SYSTEM_PROMPT = (
    "你是 AI 模型情报分析员。给定条目标题，判断它主要在讲哪个模型族"
    "（如 claude / gpt / gemini / llama / qwen / deepseek / mistral / grok / kimi / glm）。"
    "与具体模型族无关的条目返回空字符串。只输出 JSON。"
)


def _text_of(item: Dict[str, Any]) -> str:
    extra = item.get("extra") if isinstance(item.get("extra"), dict) else {}
    return "{0} {1}".format(item.get("title") or "", extra.get("title_zh") or "")


def fallback_cluster_key(item: Dict[str, Any]) -> str:
    """规则回退：取标题中**最先出现**的已知族名；无命中返回空串。"""
    text = _text_of(item)
    if not text.strip():
        return ""
    best_pos = -1
    best_name = ""
    for order, (name, pattern) in enumerate(_COMPILED):
        match = pattern.search(text)
        if match is None:
            continue
        if best_pos < 0 or match.start() < best_pos:
            best_pos = match.start()
            best_name = name
    return best_name


def _set_key(item: Dict[str, Any], value: str) -> None:
    extra = item.get("extra")
    if not isinstance(extra, dict):
        extra = {}
        item["extra"] = extra
    extra["cluster_key"] = value


def _build_prompt(batch: Sequence[Dict[str, Any]]) -> str:
    payload = [
        {"i": index, "title": str(item.get("title") or "")}
        for index, item in enumerate(batch)
    ]
    return (
        "判断每个条目所属的模型族。\n"
        '输出 JSON 数组，元素形如 {"i": 原序号, "cluster_key": "族名小写或空字符串"}，'
        "长度与输入一致。\n\n输入：\n" + json.dumps(payload, ensure_ascii=False)
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
            parsed[index] = str(row.get("cluster_key") or "").strip().casefold()
    return parsed


def annotate(items: Sequence[Dict[str, Any]]) -> None:
    """原地写 ``extra.cluster_key``；LLM 结果为空时逐条落回规则值。"""
    rows = [it for it in (items or []) if isinstance(it, dict)]
    if not rows:
        return

    # 先无条件铺一层规则值 —— 任何后续失败都已经有兜底
    for item in rows:
        _set_key(item, fallback_cluster_key(item))

    client = None
    try:
        client = llm_client.LLMClient.from_env()
        available = client.is_available()
    except Exception as exc:  # noqa: BLE001
        logger.warning("LLM 可用性判定异常，按不可用处理: %s", exc)
        available = False
    if not available or client is None:
        logger.debug("model_cluster_agent: LLM 不可用，全部使用正则族名回退")
        return

    batches = [rows[i:i + BATCH_SIZE] for i in range(0, len(rows), BATCH_SIZE)]
    try:
        replies = client.batch_json([_build_prompt(b) for b in batches], system=SYSTEM_PROMPT)
    except Exception as exc:  # noqa: BLE001 - 回退公约：绝不向上抛
        logger.warning("模型族聚类调用失败，沿用正则回退: %s", exc)
        return

    for batch, reply in zip(batches, replies or []):
        if reply is None:
            continue
        try:
            parsed = _parse_reply(reply, len(batch))
        except Exception as exc:  # noqa: BLE001
            logger.warning("模型族聚类结果解析失败，沿用正则回退: %s", exc)
            continue
        for index, value in parsed.items():
            if value:
                _set_key(batch[index], value)
