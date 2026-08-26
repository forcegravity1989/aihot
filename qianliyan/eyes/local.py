"""eyes/local.py —— 远眼 local：聚合 ``config/sources.yaml`` 的 ``sources:`` 列表，多源互不影响。

cfg 即 ``sources.yaml`` 全量内容（本眼只用其中的 ``sources`` 键）。逐源调用
``engine.remote_sync.fetch_source``，item 继承源的 ``weight``/``tags``/``format``/``category``，
``source`` 取源的 ``name``。单源失败记 warning 继续（远眼内部的"多源不互殃"），不影响其余源。

``fetch_source`` 会按 backend 返回两种形态，本眼一律归一为合法 item（``source_kind="local"``）：

* **完整 item**（``youtube``/``youtube-playlist``/``github-trending``——含 ``sig``/``source_kind``、
  ``backend`` 已是合法值 rss/html）：就地补齐 ``source``/``weight``/``tags``/``extra.format``/
  ``extra.category``/``extra.source_category``，保留其合法 backend；
* **entry dict**（``scrape`` 返回 ``{title,url,summary,date,extra:{format}}``、``rss``/``git``/
  ``arxiv``/``sitemap`` 沿用旧形态）：用 :func:`core.schema.make_item` 造 item，注入源的
  ``weight``/``tags``/``format``/``category``，透传 ``entry['extra']``，backend 取**合法值**
  （``scrape``→``html``、``youtube``→``rss`` 等映射，见 :data:`_TYPE_TO_BACKEND`）。

**铁律**：``youtube``/``github-trending``/``scrape`` 这类非法 ``type`` 字符串绝不直接塞进
``backend``（会被 :func:`schema.validate_item` 拒）；原始 type 保留在 ``extra.source_type``。
``QLY_OFFLINE=1`` 下 ``fetch_source`` 内部不出网，本眼快速返回。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from ..core import schema
from ..engine import remote_sync

logger = logging.getLogger(__name__)

DEFAULT_WEIGHT = 0.7

#: 源 ``type`` → 合法 backend 的兜底映射（``detect_backend`` 直接采信 declared 时可能返回
#: 非法字符串，这里把它们收敛回 :data:`schema.BACKENDS` 内的合法值）。
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
    """为该源算一个**一定合法**的 backend：先走 ``detect_backend``，非法则按 type 兜底映射。"""
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
    """``fetch_source`` 是否已返回完整 item（youtube/github-trending 走此路）。"""
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
    """把源的 format/category/type 注入 ``extra``（源配置权威，见 spec-v0.3 §14）。"""
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
    """归一 ``fetch_source`` 返回的**完整 item**：补 source/weight/tags/extra，保留合法 backend。"""
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
    """把 ``engine.remote_sync.fetch_source()`` 的返回归一为标准 item；纯函数，不出网。

    同时兼容两种形态：完整 item（youtube/github-trending）与 entry dict（scrape/rss/…）。
    ``cfg`` 为该源在 ``sources.yaml`` 里的配置（含 ``name``/``url``/``type``/``weight``/
    ``tags``/``format``/``category``）。
    """
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
    """逐源调用 ``remote_sync.fetch_source``；单源失败记 warning 继续，不中断其余源。"""
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
