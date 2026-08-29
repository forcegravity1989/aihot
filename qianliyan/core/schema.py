"""core/schema.py —— item 的构造与校验（贯穿全链路的 plain dict 契约）。

item 刻意不用 dataclass，以便直接 JSONL 存取。本模块只提供两个对外函数：
``make_item`` 构造标准 item、``validate_item`` 返回缺陷描述列表（空列表 = 合法）。
字段定义见 spec §1，任何新增字段一律放 ``extra``。
"""

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


#: date 的可信精度档：源给到时分 / 只给到天 / 压根没给（被补成当前时刻）
DATE_PRECISIONS = ("exact", "day", "unknown")


def _date_precision(date: Any) -> str:
    """判断这条 date 的**可信精度**，供渲染层决定显示到分、只显示日期、还是不显示时间。

    这不是锦上添花。源缺 date 时 :func:`_norm_date` 会补当前时刻——一批条目于是全撞在
    同一秒；只给到天的源则一律是 00:00。两种情况在时间轴上都会被画成一个精确到分钟的
    假时刻，读者无从分辨「凌晨 4:24 发布」和「根本不知道什么时候发布」。精度一旦丢在
    归一化这一步，下游再也补不回来，所以必须在这里就记下来。
    """
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
    """时间归一为 ISO 8601 UTC 字符串；缺省用当前 UTC；不可解析则原样保留。"""
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
    """构造一条标准 item（含全部字段），并自动填充 ``sig`` 与 ``fetched_at``。

    ``cross_refs`` 初始为 0、``source_list`` 初始为 ``[source]``、``hotness`` 为 0.0、
    ``badges`` 为空；``sync_run_id`` 留空由 sync 编排器统一盖章。
    """
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
    """精度**有损时**才写 ``date_precision``（day / unknown）；精确的不写。

    只给有损的打标有两个好处：item 契约里「extra 初始为空」这条不被破坏（绝大多数
    条目的 extra 仍然干干净净），落盘体积也不为一个恒真的默认值买单。缺省即精确——
    渲染层读不到这个键就按 exact 处理。

    适配器若已自行判定则尊重它（setdefault），包括显式写 ``"exact"`` 覆盖本函数的判断。
    """
    merged = dict(extra or {})
    if "date_precision" in merged:
        return merged
    precision = _date_precision(date)
    if precision != "exact":
        merged["date_precision"] = precision
    return merged


def validate_item(item: Dict[str, Any]) -> List[str]:
    """校验一条 item，返回缺陷描述列表；空列表表示合法。"""
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
