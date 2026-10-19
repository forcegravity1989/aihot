from __future__ import annotations

import logging
from collections import OrderedDict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

from ..core import paths, storage, utils

logger = logging.getLogger(__name__)

__all__ = [
    "CONFIG_NAME",
    "DEFAULT_LIMIT",
    "DEFAULT_MAX_PER_SOURCE",
    "diversify",
    "load_source_groups",
    "source_key",
    "BADGE_EMOJI",
    "load_channels",
    "match_item",
    "route",
    "run_all",
    "format_line",
    "render_channel_md",
]

CONFIG_NAME = "channels.yaml"
DEFAULT_LIMIT = 30

# 权重高又新鲜的源会整段吃掉名额（实测 7/15 个频道的 top10 被同一源占满）
DEFAULT_MAX_PER_SOURCE = 3

BADGE_EMOJI = OrderedDict((("heavy", "📈"), ("flash", "⚡")))

_MATCH_FIELDS = (
    "tags_any",
    "tags_all",
    "sources_include",
    "keywords_any",
    "aihot_category",
    "categories",
    "formats",
    "platforms",
    "source_kinds",
    "any_of",
)


def _as_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return [str(v) for v in value if v is not None and str(v) != ""]
    if isinstance(value, str):
        return [value] if value else []
    return [str(value)]


def _fold_all(values: Iterable[Any]) -> List[str]:
    return [str(v).casefold() for v in values]


def _coerce_cap(value: Any, fallback: int) -> int:
    """显式 0 / 负数 / null 表示不限（返回 0）。"""
    if value is None:
        return fallback
    try:
        cap = int(value)
    except (TypeError, ValueError):
        return fallback
    return cap if cap > 0 else 0


def _source_key(item: Dict[str, Any], groups: Optional[Dict[str, str]] = None) -> str:
    """取 source 而非 source_list：多源交叉条目正是想留下的；groups 按组织归并，命中多条取最长 key。"""
    source = str(item.get("source") or "").strip()
    folded = source.casefold()
    if groups:
        hit = ""
        for needle, group in groups.items():
            if needle in folded and len(needle) > len(hit):
                hit, matched = needle, group
        if hit:
            return "group:{0}".format(matched).casefold()
    return folded


def load_source_groups() -> Dict[str, str]:
    raw = paths.load_yaml_config(CONFIG_NAME)
    defaults = raw.get("defaults") if isinstance(raw.get("defaults"), dict) else {}
    spec = (defaults or {}).get("source_groups")
    flat: Dict[str, str] = {}
    if isinstance(spec, dict):
        for group, needles in spec.items():
            for needle in (needles or []):
                text = str(needle).strip().casefold()
                if text:
                    flat[text] = str(group)
    return flat


def source_key(item: Dict[str, Any], groups: Optional[Dict[str, str]] = None) -> str:
    """日报规则选稿也按这把尺子数席位。"""
    return _source_key(item, groups)


def diversify(items: List[Dict[str, Any]], cap: int, limit: int,
               groups: Optional[Dict[str, str]] = None) -> List[Dict[str, Any]]:
    """软上限：有的频道天生只有一两个源，硬砍会让它变空；items 须已按热度降序。"""
    if cap <= 0:
        return items[:limit]

    picked: List[Dict[str, Any]] = []
    held: List[Dict[str, Any]] = []
    seen: Dict[str, int] = {}
    for item in items:
        key = _source_key(item, groups)
        if seen.get(key, 0) < cap:
            picked.append(item)
            seen[key] = seen.get(key, 0) + 1
        else:
            held.append(item)
    if len(picked) < limit:
        picked.extend(held[: limit - len(picked)])
    return picked[:limit]


def _coerce_limit(value: Any, fallback: int = DEFAULT_LIMIT) -> int:
    try:
        limit = int(value)
    except (TypeError, ValueError):
        return fallback
    return limit if limit > 0 else fallback


def load_channels() -> List[Dict[str, Any]]:
    raw = paths.load_yaml_config(CONFIG_NAME)
    defaults = raw.get("defaults") if isinstance(raw.get("defaults"), dict) else {}
    default_limit = _coerce_limit((defaults or {}).get("limit"), DEFAULT_LIMIT)
    default_cap = _coerce_cap((defaults or {}).get("max_per_source"), DEFAULT_MAX_PER_SOURCE)

    entries = raw.get("channels")
    pairs: List[Any] = []
    if isinstance(entries, list):
        pairs = list(entries)
    elif isinstance(entries, dict):
        pairs = [dict(cfg or {}, name=name) for name, cfg in entries.items()]
    elif entries is not None:
        logger.warning("channels.yaml 的 channels 节点应为 list 或 mapping，实际 %s", type(entries))

    channels: List[Dict[str, Any]] = []
    for entry in pairs:
        if not isinstance(entry, dict):
            logger.warning("跳过非法频道配置: %r", entry)
            continue
        name = str(entry.get("name") or "").strip()
        if not name:
            logger.warning("跳过缺少 name 的频道配置: %r", entry)
            continue
        match_cfg = entry.get("match")
        if match_cfg is not None and not isinstance(match_cfg, dict):
            logger.warning("频道 %s 的 match 应为 mapping，已忽略", name)
            match_cfg = None
        channels.append({
            "name": name,
            "title": str(entry.get("title") or name),
            "limit": _coerce_limit(entry.get("limit"), default_limit),
            "max_per_source": _coerce_cap(entry.get("max_per_source"), default_cap),
            "match": match_cfg or {},
        })
    return channels


def _item_text(item: Dict[str, Any]) -> str:
    return "{0}\n{1}".format(item.get("title") or "", item.get("summary") or "").casefold()


def _extra(item: Dict[str, Any]) -> Dict[str, Any]:
    extra = item.get("extra")
    return extra if isinstance(extra, dict) else {}


def match_item(item: Dict[str, Any], match_cfg: Optional[Dict[str, Any]]) -> bool:
    if not isinstance(item, dict):
        return False
    if not isinstance(match_cfg, dict) or not match_cfg:
        return True  # route() 另行拦截空 match 频道

    for key in match_cfg:
        if key not in _MATCH_FIELDS:
            logger.warning("channels.yaml 出现未知匹配字段 %r（已忽略）", key)

    tags = _fold_all(_as_list(item.get("tags")))

    wanted = _fold_all(_as_list(match_cfg.get("tags_any")))
    if wanted and not any(tag in tags for tag in wanted):
        return False

    wanted = _fold_all(_as_list(match_cfg.get("tags_all")))
    if wanted and not all(tag in tags for tag in wanted):
        return False

    wanted = _fold_all(_as_list(match_cfg.get("sources_include")))
    if wanted:
        sources = _fold_all(_as_list(item.get("source_list")) or _as_list(item.get("source")))
        if not any(needle in name for needle in wanted for name in sources):
            return False

    wanted = _fold_all(_as_list(match_cfg.get("keywords_any")))
    if wanted:
        text = _item_text(item)
        if not any(keyword in text for keyword in wanted):
            return False

    extra = _extra(item)

    wanted = _fold_all(_as_list(match_cfg.get("aihot_category")))
    if wanted and str(extra.get("category") or "").casefold() not in wanted:
        return False

    wanted = set(_fold_all(_as_list(match_cfg.get("categories"))))
    if wanted:
        cats = {
            str(extra.get("category") or "").casefold(),
            str(extra.get("source_category") or "").casefold(),
        }
        cats.discard("")
        if not (cats & wanted):
            return False

    wanted = _fold_all(_as_list(match_cfg.get("formats")))
    if wanted and str(extra.get("format") or "").casefold() not in wanted:
        return False

    wanted = _fold_all(_as_list(match_cfg.get("platforms")))
    if wanted and str(extra.get("platform") or "").casefold() not in wanted:
        return False

    wanted = _fold_all(_as_list(match_cfg.get("source_kinds")))
    if wanted and str(item.get("source_kind") or "").casefold() not in wanted:
        return False

    if "any_of" in match_cfg:
        blocks = match_cfg.get("any_of") or []
        if not isinstance(blocks, (list, tuple)):
            logger.warning("any_of 应为块列表，已忽略: %r", blocks)
        elif not any(
            match_item(item, block) for block in blocks if isinstance(block, dict)
        ):
            return False

    return True


def _hotness(item: Dict[str, Any]) -> float:
    try:
        return float(item.get("hotness") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def route(
    items: Sequence[Dict[str, Any]],
    channels: Optional[Sequence[Dict[str, Any]]] = None,
) -> "OrderedDict[str, List[Dict[str, Any]]]":
    if channels is None:
        channels = load_channels()

    result: "OrderedDict[str, List[Dict[str, Any]]]" = OrderedDict()
    pool = [it for it in (items or []) if isinstance(it, dict)]
    groups = load_source_groups()

    for channel in channels or []:
        if not isinstance(channel, dict):
            continue
        name = str(channel.get("name") or "").strip()
        if not name:
            continue
        match_cfg = channel.get("match") or {}
        if not match_cfg:
            logger.warning("频道 %s 没有 match 规则，已跳过（避免吞掉全部条目）", name)
            result[name] = []
            continue

        matched = [it for it in pool if match_item(it, match_cfg)]
        matched.sort(key=_hotness, reverse=True)
        result[name] = diversify(
            matched,
            _coerce_cap(channel.get("max_per_source"), DEFAULT_MAX_PER_SOURCE),
            _coerce_limit(channel.get("limit")),
            groups,
        )

    return result


def display_title(item: Dict[str, Any]) -> str:
    zh = _extra(item).get("title_zh")
    if zh and str(zh).strip():
        return str(zh).strip()
    return str(item.get("title") or "")


def badge_prefix(item: Dict[str, Any]) -> str:
    marks = [BADGE_EMOJI[b] for b in BADGE_EMOJI if b in (item.get("badges") or [])]
    return "".join(marks)


def format_line(item: Dict[str, Any]) -> str:
    prefix = badge_prefix(item)
    sources = " + ".join(_as_list(item.get("source_list")) or _as_list(item.get("source")))
    return "- {0}**{1}**（{2}） {3} — [链接]({4})".format(
        prefix + " " if prefix else "",
        display_title(item),
        sources,
        "{0:.4f}".format(_hotness(item)),
        item.get("url") or "",
    )


def render_channel_md(title: str, items: Sequence[Dict[str, Any]], now_iso: str) -> str:
    lines = ["# {0}".format(title), "", "> 更新时间：{0}　条目数：{1}".format(now_iso, len(items)), ""]
    if not items:
        lines.append("_暂无条目_")
    else:
        lines.extend(format_line(item) for item in items)
    lines.append("")
    return "\n".join(lines)


def _write_text(path: Path, text: str) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    except OSError as exc:
        logger.warning("写入频道页失败 %s: %s", path, exc)


def run_all(
    items: Sequence[Dict[str, Any]],
    channels: Optional[Sequence[Dict[str, Any]]] = None,
) -> "OrderedDict[str, List[Dict[str, Any]]]":
    if channels is None:
        channels = load_channels()
    routed = route(items, channels)
    titles = {c.get("name"): c.get("title") or c.get("name") for c in channels or []}
    now_iso = utils.iso(utils.now_utc())

    index: "OrderedDict[str, List[str]]" = OrderedDict()
    for name, channel_items in routed.items():
        _write_text(
            paths.data_path("channels", "{0}.md".format(name)),
            render_channel_md(str(titles.get(name) or name), channel_items, now_iso),
        )
        index[name] = [str(it.get("sig") or "") for it in channel_items]

    storage.write_json(paths.data_path("channels.json"), index)
    logger.info("频道路由完成：%d 个频道 / 共 %d 条归属", len(routed), sum(len(v) for v in routed.values()))
    return routed
