from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from ..core import schema
from ..engine import remote_sync

logger = logging.getLogger(__name__)

DEFAULT_WEIGHT = 0.7

# detect_backend 采信 declared 时可能返回非法字符串，这里收敛回 schema.BACKENDS
_TYPE_TO_BACKEND = {
    "rss": "rss",
    "arxiv": "arxiv",
    "sitemap": "sitemap",
    "git": "git",
    "html": "html",
    "scrape": "html",
    "youtube": "rss",
    "youtube-playlist": "rss",
    "github-trending": "html",
}


def _legal_backend(cfg: Dict[str, Any]) -> str:
    declared = cfg.get("type")
    backend = remote_sync.detect_backend(cfg.get("url", ""), declared)
    if backend in schema.BACKENDS:
        return backend
    mapped = _TYPE_TO_BACKEND.get(str(backend or "").strip().casefold())
    if mapped:
        return mapped
    mapped = _TYPE_TO_BACKEND.get(str(declared or "").strip().casefold())
    return mapped or "html"


def _is_complete_item(entry: Dict[str, Any]) -> bool:
    return "sig" in entry or "source_kind" in entry


def _merge_tags(base_tags: List[str], extra_tags: Any) -> List[str]:
    tags = list(base_tags)
    for tag in extra_tags or []:
        if tag not in tags:
            tags.append(tag)
    return tags


def _apply_source_extra(
    extra: Dict[str, Any],
    src_format: Any,
    src_category: Any,
    src_type: Any,
    prefer_src_format: bool = True,
) -> Dict[str, Any]:
    if src_format:
        if prefer_src_format or not extra.get("format"):
            extra["format"] = str(src_format)
    if src_category:
        extra["category"] = str(src_category)
        extra["source_category"] = str(src_category)
    if src_type:
        extra["source_type"] = str(src_type)
    return extra


def _adopt_item(
    raw: Dict[str, Any],
    name: str,
    weight_declared: bool,
    weight: float,
    base_tags: List[str],
    src_format: Any,
    src_category: Any,
    src_type: Any,
) -> Optional[Dict[str, Any]]:
    if not (raw.get("title") and raw.get("url")):
        return None
    item = dict(raw)
    item["source_kind"] = "local"
    if name:
        item["source"] = str(name)
        item["source_list"] = [str(name)]
    if weight_declared:
        item["weight"] = weight
    item["tags"] = _merge_tags(base_tags, item.get("tags"))

    backend = item.get("backend")
    if backend not in schema.BACKENDS:
        item["backend"] = _TYPE_TO_BACKEND.get(str(src_type or "").strip().casefold(), "html")

    extra = item.get("extra")
    extra = dict(extra) if isinstance(extra, dict) else {}
    item["extra"] = _apply_source_extra(extra, src_format, src_category, src_type)
    return item


def parse_payload(entries: Any, cfg: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    cfg = cfg or {}
    name = cfg.get("name") or cfg.get("url") or "未知信源"
    weight_declared = "weight" in cfg
    try:
        weight = float(cfg.get("weight", DEFAULT_WEIGHT))
    except (TypeError, ValueError):
        weight = DEFAULT_WEIGHT
    base_tags = [str(t) for t in (cfg.get("tags") or [])]
    src_format = cfg.get("format")
    src_category = cfg.get("category")
    src_type = cfg.get("type")
    backend = _legal_backend(cfg)

    items: List[Dict[str, Any]] = []
    for entry in entries or []:
        if not isinstance(entry, dict):
            continue

        if _is_complete_item(entry):
            adopted = _adopt_item(
                entry, str(name), weight_declared, weight, base_tags,
                src_format, src_category, src_type,
            )
            if adopted is not None:
                items.append(adopted)
            continue

        title = entry.get("title") or ""
        url = entry.get("url") or ""
        if not title or not url:
            continue
        tags = _merge_tags(base_tags, entry.get("tags"))
        extra = entry.get("extra")
        extra = dict(extra) if isinstance(extra, dict) else {}
        _apply_source_extra(extra, src_format, src_category, src_type)
        items.append(schema.make_item(
            title=str(title),
            url=str(url),
            source=str(name),
            source_kind="local",
            backend=backend,
            weight=weight,
            date=entry.get("date"),
            summary=str(entry.get("summary") or ""),
            tags=tags,
            metrics=entry.get("metrics") or {},
            extra=extra,
        ))
    return items


def fetch(cfg: Optional[Dict[str, Any]] = None, since: Any = None) -> List[Dict[str, Any]]:
    sources = (cfg or {}).get("sources") or []
    items: List[Dict[str, Any]] = []
    for src in sources:
        if not isinstance(src, dict):
            continue
        try:
            entries = remote_sync.fetch_source(src)
        except Exception as exc:  # noqa: BLE001 - 远眼多源不互殃
            logger.warning("local 源抓取失败 (%s): %s", src.get("name") or src.get("url"), exc)
            continue
        try:
            items.extend(parse_payload(entries, src))
        except Exception as exc:  # noqa: BLE001 - 归一异常也不许拖垮其余源
            logger.warning("local 源归一失败 (%s): %s", src.get("name") or src.get("url"), exc)
    return items
