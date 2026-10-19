from __future__ import annotations

import hashlib
import logging
import math
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Dict, Iterable, List, Optional, Sequence, Union

logger = logging.getLogger(__name__)

CROSS_BONUS = 0.35
HALF_LIFE_DAYS = 7.0
TITLE_SIG_MAXLEN = 50
SIG_HEX_LEN = 16
HEAVY_MIN_CROSS_REFS = 3
FLASH_MIN_WEIGHT = 0.95
FLASH_MAX_AGE_HOURS = 24.0

# 只保留数字、小写字母与 CJK 统一表意文字（casefold 之后再剔除）
_NON_SIG_CHARS_RE = re.compile(r"[^0-9a-z一-鿿]+")
# fromisoformat 在 3.9 上只认 3 位或 6 位小数秒，先把超长小数秒截断到 6 位
_LONG_FRACTION_RE = re.compile(r"\.(\d{6})\d+")

_FALLBACK_DATE_FORMATS = (
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S",
    "%Y/%m/%d %H:%M:%S",
    "%Y/%m/%d",
    "%Y-%m-%d",
    "%Y%m%d",
    "%d %b %Y",
    "%b %d, %Y",
    "%B %d, %Y",
    "%d %B %Y",
)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def iso(dt: Optional[datetime] = None) -> str:
    if dt is None:
        dt = now_utc()
    return as_utc(dt).replace(microsecond=0).isoformat()


def parse_date(s: Any) -> Optional[datetime]:
    if s is None:
        return None
    if isinstance(s, datetime):
        return as_utc(s)
    if isinstance(s, bool):  # bool 是 int 的子类，显式挡掉
        return None
    if isinstance(s, (int, float)):
        try:
            seconds = float(s)
        except (TypeError, ValueError):
            return None
        if abs(seconds) > 1e11:  # 毫秒时间戳
            seconds = seconds / 1000.0
        try:
            return datetime.fromtimestamp(seconds, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None

    text = str(s).strip()
    if not text:
        return None

    candidate = text
    if candidate[-1:] in ("Z", "z"):
        candidate = candidate[:-1] + "+00:00"
    candidate = _LONG_FRACTION_RE.sub(r".\1", candidate)
    try:
        return as_utc(datetime.fromisoformat(candidate))
    except (ValueError, TypeError):
        pass

    try:
        return as_utc(parsedate_to_datetime(text))
    except (TypeError, ValueError, IndexError):
        pass

    for fmt in _FALLBACK_DATE_FORMATS:
        try:
            return as_utc(datetime.strptime(text, fmt))
        except ValueError:
            continue

    logger.debug("parse_date 无法解析时间: %r", s)
    return None


def normalize_title(title: Any) -> str:
    text = unicodedata.normalize("NFKC", str(title or ""))
    text = text.casefold()
    text = _NON_SIG_CHARS_RE.sub("", text)
    return text[:TITLE_SIG_MAXLEN]


def item_signature(
    item_or_title: Union[Dict[str, Any], str],
    source: Optional[str] = None,
    version: Optional[str] = None,
) -> str:
    """release 条目以 source|version 为基串，防同一版本号在双仓被误合并/漏合并。"""
    if isinstance(item_or_title, dict):
        item = item_or_title
        title = item.get("title") or ""
        if source is None:
            source = item.get("source") or ""
        if version is None:
            metrics = item.get("metrics") or {}
            if isinstance(metrics, dict):
                version = metrics.get("version")
        tags = item.get("tags") or []
        try:
            is_release = any(str(t).casefold() == "release" for t in tags)
        except TypeError:
            is_release = False
    else:
        title = item_or_title or ""
        source = source or ""
        is_release = version is not None

    if is_release and version:
        base = "{0}|{1}".format(source or "", version)
    else:
        base = normalize_title(title)

    return hashlib.sha1(base.encode("utf-8")).hexdigest()[:SIG_HEX_LEN]


def compute_hotness(
    weight: Any,
    date_iso: Any,
    cross_refs: Any = 0,
    now: Optional[datetime] = None,
) -> float:
    now = now or now_utc()
    try:
        w = float(weight)
    except (TypeError, ValueError):
        w = 0.0
    try:
        refs = int(cross_refs or 0)
    except (TypeError, ValueError):
        refs = 0
    if refs < 0:
        refs = 0

    dt = parse_date(date_iso)
    if dt is None:
        age_days = 0.0
    else:
        age_days = max(0.0, (as_utc(now) - dt).total_seconds() / 86400.0)

    freshness = 0.5 ** (age_days / HALF_LIFE_DAYS)
    hotness = w * freshness * (1.0 + CROSS_BONUS * math.log1p(refs))
    return round(hotness, 4)


def _weight_of(item: Dict[str, Any]) -> float:
    try:
        return float(item.get("weight") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _source_list_of(item: Dict[str, Any]) -> List[str]:
    raw = item.get("source_list") or []
    names: List[str] = []
    if isinstance(raw, (list, tuple)):
        for name in raw:
            if name and name not in names:
                names.append(name)
    own = item.get("source")
    if own and own not in names:
        names.append(own)
    return names


def _merge_shallow(group_desc: Sequence[Dict[str, Any]], key: str) -> Dict[str, Any]:
    merged: Dict[str, Any] = {}
    for item in group_desc:
        value = item.get(key) or {}
        if not isinstance(value, dict):
            continue
        for k, v in value.items():
            merged.setdefault(k, v)
    return merged


def dedup_and_score(
    items: Iterable[Dict[str, Any]],
    now: Optional[datetime] = None,
) -> List[Dict[str, Any]]:
    now = as_utc(now or now_utc())
    groups: "Dict[str, List[Dict[str, Any]]]" = {}
    order: List[str] = []

    for item in items or []:
        if not isinstance(item, dict):
            logger.warning("dedup_and_score 跳过非 dict 条目: %r", type(item))
            continue
        sig = item.get("sig") or item_signature(item)
        if sig not in groups:
            groups[sig] = []
            order.append(sig)
        groups[sig].append(item)

    merged_items: List[Dict[str, Any]] = []
    for sig in order:
        group = groups[sig]
        group_desc = [g for _, g in sorted(
            enumerate(group), key=lambda pair: (-_weight_of(pair[1]), pair[0])
        )]
        best = group_desc[0]

        source_list: List[str] = []
        for member in group:
            for name in _source_list_of(member):
                if name not in source_list:
                    source_list.append(name)
        cross_refs = max(0, len(source_list) - 1)

        summary = ""
        for member in group:
            candidate = member.get("summary") or ""
            if len(candidate) > len(summary):
                summary = candidate

        earliest: Optional[datetime] = None
        for member in group:
            dt = parse_date(member.get("date"))
            if dt is not None and (earliest is None or dt < earliest):
                earliest = dt
        date_iso = iso(earliest) if earliest is not None else iso(now)

        tags: List[str] = []
        for member in group:
            for tag in member.get("tags") or []:
                if tag not in tags:
                    tags.append(tag)

        weight = max(_weight_of(member) for member in group)
        hotness = compute_hotness(weight, date_iso, cross_refs, now)

        badges: List[str] = []
        if cross_refs >= HEAVY_MIN_CROSS_REFS:
            badges.append("heavy")
        if weight >= FLASH_MIN_WEIGHT:
            dt = parse_date(date_iso)
            age_hours = 0.0 if dt is None else (now - dt).total_seconds() / 3600.0
            if age_hours <= FLASH_MAX_AGE_HOURS:
                badges.append("flash")

        stamp_src = next(
            (m for m in group_desc if m.get("sync_run_id") or m.get("fetched_at")),
            best,
        )

        merged_items.append({
            "sig": sig,
            "title": best.get("title") or "",
            "url": best.get("url") or "",
            "summary": summary,
            "source": best.get("source") or "",
            "source_kind": best.get("source_kind") or "",
            "backend": best.get("backend") or "",
            "weight": weight,
            "date": date_iso,
            "cross_refs": cross_refs,
            "source_list": source_list,
            "hotness": hotness,
            "badges": badges,
            "tags": tags,
            "metrics": _merge_shallow(group_desc, "metrics"),
            "extra": _merge_shallow(group_desc, "extra"),
            "sync_run_id": stamp_src.get("sync_run_id") or "",
            "fetched_at": stamp_src.get("fetched_at") or "",
        })

    merged_items.sort(key=lambda it: it.get("hotness") or 0.0, reverse=True)
    return merged_items


def is_older_than(date_value: Any, max_age_days: float, now: Optional[datetime] = None) -> bool:
    """时间不可解析时返回 False——不敢删。"""
    dt = parse_date(date_value)
    if dt is None:
        return False
    now = as_utc(now or now_utc())
    return (now - dt) > timedelta(days=float(max_age_days))
