"""core/utils.py —— 时间工具与三大核心算法（签名 / 合并打分 / 热度）。

职责：提供与信源语义无关的纯计算能力——
  * 时间：``now_utc`` / ``iso`` / ``parse_date``（容忍 ISO、RFC822、``YYYY-MM-DD``）；
  * 去重签名：``item_signature``（含 release 特例）；
  * 合并打分：``dedup_and_score``（按 sig 分组合并 + badge 标注）；
  * 热度：``compute_hotness``（7 天半衰期 + 交叉引用加成）。

边界：本模块不做 IO、不出网、不 import 其他分层，算法语义即 spec §2 的实现合同。
"""

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

# ---- 算法常量（spec §2）-------------------------------------------------
CROSS_BONUS = 0.35
HALF_LIFE_DAYS = 7.0
TITLE_SIG_MAXLEN = 50
SIG_HEX_LEN = 16
HEAVY_MIN_CROSS_REFS = 3
FLASH_MIN_WEIGHT = 0.95
FLASH_MAX_AGE_HOURS = 24.0

# 只保留数字、小写字母与 CJK 统一表意文字（casefold 之后再做剔除）
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
)


# =========================================================================
# 时间工具
# =========================================================================
def now_utc() -> datetime:
    """当前时间（带 UTC 时区的 aware datetime）。"""
    return datetime.now(timezone.utc)


def as_utc(dt: datetime) -> datetime:
    """把 datetime 归一为 UTC aware；naive 视作 UTC。"""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def iso(dt: Optional[datetime] = None) -> str:
    """输出 ISO 8601 UTC 字符串，形如 ``2026-08-25T03:00:00+00:00``（秒级精度）。"""
    if dt is None:
        dt = now_utc()
    return as_utc(dt).replace(microsecond=0).isoformat()


def parse_date(s: Any) -> Optional[datetime]:
    """尽力解析时间：ISO 8601 / RFC822 / ``YYYY-MM-DD`` / epoch 秒或毫秒。

    解析成功返回 UTC aware datetime；失败返回 ``None``（调用方自行兜底为 now）。
    """
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


# =========================================================================
# §2.1 去重签名
# =========================================================================
def normalize_title(title: Any) -> str:
    """标题归一化：NFKC → casefold → 剔除非字母数字非 CJK → 取前 50 字符。"""
    text = unicodedata.normalize("NFKC", str(title or ""))
    text = text.casefold()
    text = _NON_SIG_CHARS_RE.sub("", text)
    return text[:TITLE_SIG_MAXLEN]


def item_signature(
    item_or_title: Union[Dict[str, Any], str],
    source: Optional[str] = None,
    version: Optional[str] = None,
) -> str:
    """计算去重签名。

    * **release 特例**：item 的 ``tags`` 含 ``"release"`` 且 ``metrics.version`` 非空时，
      基串取 ``f"{source}|{version}"``——防同一版本号在双仓被误合并/漏合并。
      传入字符串标题时，显式给出 ``version`` 即视为 release 情形。
    * 一般情形：基串取 ``normalize_title(title)``。

    返回 sha1 十六进制摘要的前 16 位。
    """
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


# =========================================================================
# §2.3 热度
# =========================================================================
def compute_hotness(
    weight: Any,
    date_iso: Any,
    cross_refs: Any = 0,
    now: Optional[datetime] = None,
) -> float:
    """热度 = weight × 新鲜度(7 天半衰期) × (1 + 0.35·ln(1+cross_refs))，保留 4 位小数。

    ``date_iso`` 解析失败按 0 天龄处理（视作最新）。
    """
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


# =========================================================================
# §2.2 合并打分
# =========================================================================
def _weight_of(item: Dict[str, Any]) -> float:
    try:
        return float(item.get("weight") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _source_list_of(item: Dict[str, Any]) -> List[str]:
    """单条 item 的 source_list——至少含自身 source。"""
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
    """浅合并 dict 字段：按 weight 降序遍历，先到者（weight 高者）的键胜出。"""
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
    """按 ``sig`` 分组合并同一事件，产出打分后的统一池（hotness 降序）。

    合并语义见 spec §2.2：title/url 取 weight 最高者、summary 取最长、date 取最早、
    weight 取最大、tags 取并集、metrics/extra 浅合并（weight 高者胜）、
    ``cross_refs = len(source_list) - 1``，并按阈值打 ``heavy`` / ``flash`` 徽标。
    """
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
        # weight 降序、并列保持组内先到顺序
        group_desc = [g for _, g in sorted(
            enumerate(group), key=lambda pair: (-_weight_of(pair[1]), pair[0])
        )]
        best = group_desc[0]

        # source_list：按组内出现顺序去重累积
        source_list: List[str] = []
        for member in group:
            for name in _source_list_of(member):
                if name not in source_list:
                    source_list.append(name)
        cross_refs = max(0, len(source_list) - 1)

        # summary：最长非空
        summary = ""
        for member in group:
            candidate = member.get("summary") or ""
            if len(candidate) > len(summary):
                summary = candidate

        # date：最早可解析；全不可解析则用 now
        earliest: Optional[datetime] = None
        for member in group:
            dt = parse_date(member.get("date"))
            if dt is not None and (earliest is None or dt < earliest):
                earliest = dt
        date_iso = iso(earliest) if earliest is not None else iso(now)

        # tags：并集（保序去重）
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
    """判定条目是否超龄（时间不可解析时返回 False——不敢删）。"""
    dt = parse_date(date_value)
    if dt is None:
        return False
    now = as_utc(now or now_utc())
    return (now - dt) > timedelta(days=float(max_age_days))
