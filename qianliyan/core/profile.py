from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Sequence

from . import paths, storage, utils

logger = logging.getLogger(__name__)

__all__ = [
    "HISTORY_NAME",
    "HISTORY_ACTIONS",
    "load_reader_profile",
    "personalize",
    "log_history",
    "read_history",
]

HISTORY_NAME = "history.jsonl"
FEEDBACK_NAME = "feedback.jsonl"
ITEMS_NAME = "items.jsonl"

# received = 浅读「看过标题即已接收」
HISTORY_ACTIONS = ("seen", "open", "deepread", "received")

_ACTION_WEIGHTS = {
    "deepread": 2.0,
    "open": 1.5,
    "received": 1.0,
    "seen": 0.5,
}
_FEEDBACK_WEIGHTS = {
    "up": 2.0,
    "down": -1.5,
    "hide": -2.0,
}
_DERIVE_HALF_LIFE_DAYS = 14.0
_DERIVE_STRENGTH = 0.5


def log_history(entries: Sequence[Dict[str, Any]]) -> None:
    rows = [e for e in (entries or []) if isinstance(e, dict)]
    normalized: List[Dict[str, Any]] = []
    for entry in rows:
        action = str(entry.get("action") or "").strip()
        if action not in HISTORY_ACTIONS:
            logger.warning("log_history 跳过非法 action=%r", action)
            continue
        normalized.append({
            "sig": str(entry.get("sig") or ""),
            "ts": entry.get("ts") or utils.iso(utils.now_utc()),
            "action": action,
            "title": str(entry.get("title") or ""),
            "url": str(entry.get("url") or ""),
        })
    if not normalized:
        return
    path = paths.data_path(HISTORY_NAME)
    existing = storage.read_jsonl(path)
    existing.extend(normalized)
    storage.write_jsonl(path, existing)
    logger.debug("history 追加 %d 条 → 累计 %d 条", len(normalized), len(existing))


def read_history(limit: Optional[int] = None) -> List[Dict[str, Any]]:
    rows = storage.read_jsonl(paths.data_path(HISTORY_NAME))
    if limit is None:
        return rows
    try:
        n = int(limit)
    except (TypeError, ValueError):
        return rows
    if n <= 0:
        return []
    return rows[-n:]


def _norm_handle(handle: Any) -> str:
    return str(handle or "").strip().lstrip("@").casefold()


def _num_map(raw: Any) -> Dict[str, float]:
    out: Dict[str, float] = {}
    if not isinstance(raw, dict):
        return out
    for key, value in raw.items():
        try:
            out[str(key)] = float(value)
        except (TypeError, ValueError):
            logger.debug("reader.yaml 乘数非数值，跳过: %r=%r", key, value)
    return out


def _people_map(raw: Any) -> Dict[str, float]:
    out: Dict[str, float] = {}
    for key, value in _num_map(raw).items():
        handle = _norm_handle(key)
        if handle:
            out[handle] = value
    return out


def _combine(explicit: Dict[str, float], derived: Dict[str, float]) -> Dict[str, float]:
    keys = set(explicit) | set(derived)
    return {
        k: round(explicit.get(k, 1.0) * derived.get(k, 1.0), 4)
        for k in keys
    }


def _sig_index() -> Dict[str, Dict[str, Any]]:
    index: Dict[str, Dict[str, Any]] = {}
    for item in storage.read_jsonl(paths.data_path(ITEMS_NAME)):
        sig = item.get("sig")
        if sig:
            index[str(sig)] = item
    return index


def _recency_weight(ts: Any, now: Any) -> float:
    dt = utils.parse_date(ts)
    if dt is None:
        return 1.0
    age_days = max(0.0, (utils.as_utc(now) - dt).total_seconds() / 86400.0)
    return 0.5 ** (age_days / _DERIVE_HALF_LIFE_DAYS)


def _accumulate(
    scores: Dict[str, Dict[str, float]],
    item: Dict[str, Any],
    weight: float,
) -> None:
    for tag in item.get("tags") or []:
        key = str(tag or "").strip().casefold()
        if key:
            scores["tags"][key] = scores["tags"].get(key, 0.0) + weight
    source = str(item.get("source") or "").strip()
    if source:
        scores["sources"][source] = scores["sources"].get(source, 0.0) + weight
    handle = _norm_handle((item.get("extra") or {}).get("handle"))
    if handle:
        scores["people"][handle] = scores["people"].get(handle, 0.0) + weight


def _to_multipliers(scores: Dict[str, float]) -> Dict[str, float]:
    """按各维度绝对值峰归一到 [-1,1]，映射为 1±STRENGTH。"""
    if not scores:
        return {}
    peak = max(abs(v) for v in scores.values()) or 1.0
    out: Dict[str, float] = {}
    for key, value in scores.items():
        norm = value / peak
        multiplier = 1.0 + _DERIVE_STRENGTH * norm
        out[key] = round(max(0.0, multiplier), 4)
    return out


def _derive_preferences(now: Any = None) -> Dict[str, Dict[str, float]]:
    now = now or utils.now_utc()
    index = _sig_index()
    scores: Dict[str, Dict[str, float]] = {"tags": {}, "sources": {}, "people": {}}
    if not index:
        return {"tags": {}, "sources": {}, "people": {}}

    for entry in storage.read_jsonl(paths.data_path(HISTORY_NAME)):
        item = index.get(str(entry.get("sig") or ""))
        if item is None:
            continue
        base = _ACTION_WEIGHTS.get(str(entry.get("action") or ""), 0.0)
        if base == 0.0:
            continue
        _accumulate(scores, item, base * _recency_weight(entry.get("ts"), now))

    for entry in storage.read_jsonl(paths.data_path(FEEDBACK_NAME)):
        item = index.get(str(entry.get("sig") or ""))
        if item is None:
            continue
        base = _FEEDBACK_WEIGHTS.get(str(entry.get("action") or ""), 0.0)
        if base == 0.0:
            continue
        _accumulate(scores, item, base * _recency_weight(entry.get("ts"), now))

    return {
        "tags": _to_multipliers(scores["tags"]),
        "sources": _to_multipliers(scores["sources"]),
        "people": _to_multipliers(scores["people"]),
    }


def load_reader_profile() -> Dict[str, Any]:
    cfg = paths.load_yaml_config("reader") or {}
    interests = cfg.get("interests") if isinstance(cfg.get("interests"), dict) else {}
    explicit_tags = _num_map(interests.get("tags"))
    explicit_sources = _num_map(interests.get("sources"))
    explicit_people = _people_map(interests.get("people"))

    mute_cfg = cfg.get("mute") if isinstance(cfg.get("mute"), dict) else {}
    mute_tags = sorted({
        str(t).strip().casefold() for t in (mute_cfg.get("tags") or []) if str(t).strip()
    })
    mute_people = sorted({
        _norm_handle(h) for h in (mute_cfg.get("people") or []) if _norm_handle(h)
    })

    derived = _derive_preferences()
    return {
        "tags": _combine(explicit_tags, derived["tags"]),
        "sources": _combine(explicit_sources, derived["sources"]),
        "people": _combine(explicit_people, derived["people"]),
        "mute": {"tags": mute_tags, "people": mute_people},
        "derived": derived,
    }


def personalize(
    items: Sequence[Dict[str, Any]],
    profile: Optional[Dict[str, Any]] = None,
) -> None:
    """personal_score = hotness × ∏命中乘数；mute 命中归零；不改 hotness 本身。"""
    if profile is None:
        profile = load_reader_profile()

    tags_map = profile.get("tags") or {}
    sources_map = profile.get("sources") or {}
    people_map = profile.get("people") or {}
    mute = profile.get("mute") or {}
    mute_tags = {str(t).strip().casefold() for t in (mute.get("tags") or [])}
    mute_people = {_norm_handle(h) for h in (mute.get("people") or [])}

    for item in items or []:
        if not isinstance(item, dict):
            continue
        try:
            hotness = float(item.get("hotness") or 0.0)
        except (TypeError, ValueError):
            hotness = 0.0

        item_tags = [str(t).strip().casefold() for t in (item.get("tags") or [])]
        handle = _norm_handle((item.get("extra") or {}).get("handle"))
        source = str(item.get("source") or "")

        reasons: List[str] = []
        muted = False

        hit_mute_tags = [t for t in item_tags if t in mute_tags]
        if hit_mute_tags:
            muted = True
            reasons.append("mute:tag:{0}".format(hit_mute_tags[0]))
        if handle and handle in mute_people:
            muted = True
            reasons.append("mute:people:{0}".format(handle))

        multiplier = 1.0
        for tag in item_tags:
            factor = tags_map.get(tag)
            if factor is not None and factor != 1.0:
                multiplier *= factor
                reasons.append("tag:{0}".format(tag))
        for key, factor in sources_map.items():
            if key and factor != 1.0 and key in source:
                multiplier *= factor
                reasons.append("source:{0}".format(key))
        if handle:
            factor = people_map.get(handle)
            if factor is not None and factor != 1.0:
                multiplier *= factor
                reasons.append("people:{0}".format(handle))

        score = 0.0 if muted else round(hotness * multiplier, 4)

        extra = item.get("extra")
        if not isinstance(extra, dict):
            extra = {}
            item["extra"] = extra
        extra["personal_score"] = score
        extra["personal_reasons"] = reasons
