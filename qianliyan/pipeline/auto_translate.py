"""pipeline/auto_translate.py —— 英→中标题/摘要翻译（spec §9.5）。

原地为 item 写 ``extra.title_zh`` / ``extra.summary_zh``，并把结果缓存到
``$QLY_DATA_DIR/translations.json``（``{sig: {title_zh, summary_zh}}``）避免重复调用。

**回退公约（spec §5）**：
  * 标题 CJK 占比 > 0.3 视为已是中文 —— 直接跳过，不浪费额度；
  * 命中缓存 —— 直接回填；
  * LLM 不可用或任何异常 —— **不译**，渲染层自然落回英文原文，**绝不向上抛**。
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Sequence

from ..core import llm_client, paths, storage

logger = logging.getLogger(__name__)

__all__ = ["CACHE_NAME", "BATCH_SIZE", "CJK_RATIO_THRESHOLD", "cjk_ratio", "is_chinese", "translate"]

CACHE_NAME = "translations.json"
BATCH_SIZE = 10
CJK_RATIO_THRESHOLD = 0.3
SUMMARY_MAX_CHARS = 400

SYSTEM_PROMPT = (
    "你是科技情报编辑，把英文 AI 资讯标题与摘要翻译成简体中文。"
    "要求：术语准确（模型名、产品名、公司名保留英文原样），标题不超过 40 字，"
    "摘要不超过 80 字，不加任何解释。只输出 JSON。"
)


def _is_cjk(char: str) -> bool:
    code = ord(char)
    return (
        0x4E00 <= code <= 0x9FFF        # CJK 统一表意文字
        or 0x3400 <= code <= 0x4DBF     # 扩展 A
        or 0x3000 <= code <= 0x303F     # CJK 标点
        or 0xFF00 <= code <= 0xFFEF     # 全角字符
    )


def cjk_ratio(text: Any) -> float:
    """CJK 字符占非空白字符的比例（空文本返回 0.0）。"""
    chars = [c for c in str(text or "") if not c.isspace()]
    if not chars:
        return 0.0
    return sum(1 for c in chars if _is_cjk(c)) / float(len(chars))


def is_chinese(text: Any) -> bool:
    """CJK 占比 > 0.3 即视为中文，无需翻译。"""
    return cjk_ratio(text) > CJK_RATIO_THRESHOLD


def _load_cache(cache_path: Any) -> Dict[str, Dict[str, str]]:
    data = storage.read_json(cache_path, default={})
    if not isinstance(data, dict):
        logger.warning("翻译缓存格式非法，已重置: %s", cache_path)
        return {}
    cache: Dict[str, Dict[str, str]] = {}
    for sig, value in data.items():
        if isinstance(value, dict):
            cache[str(sig)] = {
                "title_zh": str(value.get("title_zh") or ""),
                "summary_zh": str(value.get("summary_zh") or ""),
            }
    return cache


def _apply(item: Dict[str, Any], title_zh: str, summary_zh: str) -> None:
    extra = item.get("extra")
    if not isinstance(extra, dict):
        extra = {}
        item["extra"] = extra
    if title_zh:
        extra["title_zh"] = title_zh
    if summary_zh:
        extra["summary_zh"] = summary_zh


def _build_prompt(batch: Sequence[Dict[str, Any]]) -> str:
    payload = [
        {
            "i": index,
            "title": str(item.get("title") or ""),
            "summary": str(item.get("summary") or "")[:SUMMARY_MAX_CHARS],
        }
        for index, item in enumerate(batch)
    ]
    return (
        "把下列条目翻译成简体中文。\n"
        "输出 JSON 数组，每个元素形如 "
        '{"i": 原序号, "title_zh": "中文标题", "summary_zh": "中文摘要"}，'
        "数组长度必须与输入一致。\n\n输入：\n"
        + json.dumps(payload, ensure_ascii=False)
    )


def _parse_reply(reply: Any, batch_size: int) -> Dict[int, Dict[str, str]]:
    """把模型回复归一为 ``{下标: {title_zh, summary_zh}}``（脏数据一律丢弃）。"""
    rows: List[Any]
    if isinstance(reply, list):
        rows = reply
    elif isinstance(reply, dict):
        candidate = reply.get("items") or reply.get("data") or reply.get("results")
        rows = candidate if isinstance(candidate, list) else [reply]
    else:
        return {}

    parsed: Dict[int, Dict[str, str]] = {}
    for order, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        try:
            index = int(row.get("i", order))
        except (TypeError, ValueError):
            index = order
        if not (0 <= index < batch_size):
            continue
        parsed[index] = {
            "title_zh": str(row.get("title_zh") or "").strip(),
            "summary_zh": str(row.get("summary_zh") or "").strip(),
        }
    return parsed


def translate(items: Sequence[Dict[str, Any]], cache_path: Any = None) -> None:
    """原地翻译（spec §9.5）：命中缓存直接回填，缺失部分批量调 LLM；任何失败静默回退。"""
    rows = [it for it in (items or []) if isinstance(it, dict)]
    if not rows:
        return

    if cache_path is None:
        try:
            cache_path = paths.data_path(CACHE_NAME)
        except Exception as exc:  # noqa: BLE001 - 路径解析失败也不许外溢
            logger.warning("翻译缓存路径解析失败，本轮不翻译: %s", exc)
            return

    cache = _load_cache(cache_path)
    pending: List[Dict[str, Any]] = []

    for item in rows:
        if is_chinese(item.get("title")):
            continue
        sig = str(item.get("sig") or "")
        hit = cache.get(sig)
        if hit and (hit.get("title_zh") or hit.get("summary_zh")):
            _apply(item, hit.get("title_zh", ""), hit.get("summary_zh", ""))
            continue
        pending.append(item)

    if not pending:
        return

    client = None
    try:
        client = llm_client.LLMClient.from_env()
        available = client.is_available()
    except Exception as exc:  # noqa: BLE001
        logger.warning("LLM 可用性判定异常，按不可用处理: %s", exc)
        available = False
    if not available or client is None:
        logger.info("LLM 不可用，%d 条保留英文原文（渲染层回退）", len(pending))
        return

    batches = [pending[i:i + BATCH_SIZE] for i in range(0, len(pending), BATCH_SIZE)]
    try:
        replies = client.batch_json([_build_prompt(b) for b in batches], system=SYSTEM_PROMPT)
    except Exception as exc:  # noqa: BLE001 - 回退公约：绝不向上抛
        logger.warning("批量翻译失败，全部保留英文原文: %s", exc)
        return

    translated = 0
    for batch, reply in zip(batches, replies or []):
        if reply is None:
            continue
        try:
            parsed = _parse_reply(reply, len(batch))
        except Exception as exc:  # noqa: BLE001
            logger.warning("翻译结果解析失败，跳过该批: %s", exc)
            continue
        for index, values in parsed.items():
            item = batch[index]
            if not (values.get("title_zh") or values.get("summary_zh")):
                continue
            _apply(item, values.get("title_zh", ""), values.get("summary_zh", ""))
            cache[str(item.get("sig") or "")] = values
            translated += 1

    if translated:
        try:
            storage.write_json(cache_path, cache)
        except Exception as exc:  # noqa: BLE001
            logger.warning("翻译缓存写盘失败（不影响本轮结果）: %s", exc)
    logger.info("auto_translate: 新译 %d 条 / 待译 %d 条", translated, len(pending))
