from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from . import utils

logger = logging.getLogger(__name__)

REQUIRED_FIELDS = ("sig", "title", "url", "source", "source_kind", "backend", "weight", "date")

SOURCE_KINDS = ("aihot", "builders", "company", "local", "insights")
BACKENDS = ("rest", "raw_json", "cdp", "rss", "git", "html", "sitemap", "arxiv")
BADGES = ("heavy", "flash")


# 源给到时分 / 只给到天 / 压根没给（被补成当前时刻）
DATE_PRECISIONS = ("exact", "day", "unknown")


def _date_precision(date: Any) -> str:
    """精度必须在归一化这步记下：缺 date 会被补成当前时刻、只给到天的是 00:00，下游无从分辨。"""
    if date is None:
        return "unknown"
    if isinstance(date, str) and not date.strip():
        return "unknown"
    parsed = date if isinstance(date, datetime) else utils.parse_date(date)
    if parsed is None:
        return "unknown"
    if (parsed.hour, parsed.minute, parsed.second) == (0, 0, 0):
        return "day"
    return "exact"


def _norm_date(date: Any) -> str:
    if date is None:
        return utils.iso(utils.now_utc())
    if isinstance(date, datetime):
        return utils.iso(date)
    parsed = utils.parse_date(date)
    if parsed is not None:
        return utils.iso(parsed)
    return str(date)


def make_item(
    *,
    title: str,
    url: str,
    source: str,
    source_kind: str,
    backend: str,
    weight: float,
    date: Any = None,
    summary: str = "",
    tags: Optional[List[str]] = None,
    metrics: Optional[Dict[str, Any]] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    try:
        weight_value = float(weight)
    except (TypeError, ValueError):
        logger.warning("make_item 收到非法 weight=%r，按 0.0 处理（title=%r）", weight, title)
        weight_value = 0.0

    item: Dict[str, Any] = {
        "sig": "",
        "title": title or "",
        "url": url or "",
        "summary": summary or "",
        "source": source or "",
        "source_kind": source_kind or "",
        "backend": backend or "",
        "weight": weight_value,
        "date": _norm_date(date),
        "cross_refs": 0,
        "source_list": [source] if source else [],
        "hotness": 0.0,
        "badges": [],
        "tags": list(tags or []),
        "metrics": dict(metrics or {}),
        "extra": _extra_with_precision(extra, date),
        "sync_run_id": "",
        "fetched_at": utils.iso(utils.now_utc()),
    }
    item["sig"] = utils.item_signature(item)
    return item


def _extra_with_precision(extra: Optional[Dict[str, Any]], date: Any) -> Dict[str, Any]:
    """只在有损时写 date_precision（缺省即 exact）；适配器已自行判定的尊重它。"""
    merged = dict(extra or {})
    if "date_precision" in merged:
        return merged
    precision = _date_precision(date)
    if precision != "exact":
        merged["date_precision"] = precision
    return merged


def validate_item(item: Dict[str, Any]) -> List[str]:
    problems: List[str] = []
    if not isinstance(item, dict):
        return ["item 不是 dict: {0}".format(type(item).__name__)]

    for field in REQUIRED_FIELDS:
        if field not in item:
            problems.append("缺少必填字段: {0}".format(field))
        elif item.get(field) in (None, ""):
            problems.append("必填字段为空: {0}".format(field))

    weight = item.get("weight")
    if weight is not None:
        try:
            weight_value = float(weight)
        except (TypeError, ValueError):
            problems.append("weight 不是数值: {0!r}".format(weight))
        else:
            if not (0.0 <= weight_value <= 1.0):
                problems.append("weight 越界(需 0–1): {0!r}".format(weight))

    source_kind = item.get("source_kind")
    if source_kind and source_kind not in SOURCE_KINDS:
        problems.append("source_kind 非法: {0!r}".format(source_kind))

    backend = item.get("backend")
    if backend and backend not in BACKENDS:
        problems.append("backend 非法: {0!r}".format(backend))

    date = item.get("date")
    if date and utils.parse_date(date) is None:
        problems.append("date 无法解析: {0!r}".format(date))

    for field in ("tags", "source_list", "badges"):
        if field in item and not isinstance(item.get(field), list):
            problems.append("{0} 应为 list: {1!r}".format(field, item.get(field)))

    for field in ("metrics", "extra"):
        if field in item and not isinstance(item.get(field), dict):
            problems.append("{0} 应为 dict: {1!r}".format(field, item.get(field)))

    cross_refs = item.get("cross_refs")
    if cross_refs is not None and not isinstance(cross_refs, int):
        problems.append("cross_refs 应为 int: {0!r}".format(cross_refs))

    for badge in item.get("badges") or []:
        if badge not in BADGES:
            problems.append("badge 非法: {0!r}".format(badge))

    return problems
