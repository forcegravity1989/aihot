"""eyes/aihot.py —— 左眼 aihot：RSS 抓取「AIHOT · 卡兹克」精选/全量/分类 feed。

站点 ``aihot.virxact.com`` 是 Next.js SSR + 反爬 JS 挑战，但暴露干净的 RSS 出口，
走 RSS 即可绕过反爬。cfg 即 ``config/sources.yaml`` 的 ``aihot:`` 段（spec-v0.3 §1 新结构）::

    aihot:
      base_url: https://aihot.virxact.com
      source_name: "AIHOT · 卡兹克"
      feeds:          # 通用 feed：{path, kind, weight}
        - {path: /feed.xml,     kind: curated, weight: 0.85}
        - {path: /feed/all.xml, kind: all,     weight: 0.70}
      category_feeds: # 分类 feed slug（只填真实 200 的四个）
        slugs: [ai-models, ai-products, industry, paper]
        weight: 0.75
      category_map:   # 中文 category → slug/tags
        "AI 模型": {slug: models, tags: [models]}
        ...

解析要点（spec-v0.3 §0.1）：canonical ``url`` = description 里「阅读原文」锚点 href（抽不到才
回退 ``<link>``）；``summary`` = 首个 ``<p>`` 文本；``extra`` 存 aihot_id/aihot_url/category/
category_slug/feed_kind，并把描述内嵌 ``<img>`` 收进 ``extra.images``；aihot 内部按 url/guid 去重。

``fetch`` 是网络壳；``parse_feed`` 是纯函数（不出网），单测直喂 ``tests/fixtures/real/*.xml``。
"""

from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from html import unescape
from typing import Any, Dict, List, Optional
from urllib.parse import quote

from ..core import schema
from ..engine import http

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://aihot.virxact.com"
SOURCE_NAME = "AIHOT · 卡兹克"
DEFAULT_WEIGHT = 0.75

# fetch 缺省 feeds（cfg 未配时用）——对应 spec-v0.3 §0.1 表格。
DEFAULT_FEEDS: List[Dict[str, Any]] = [
    {"path": "/feed.xml", "kind": "curated", "weight": 0.85},
    {"path": "/feed/all.xml", "kind": "all", "weight": 0.70},
]
DEFAULT_CATEGORY_SLUGS = ["ai-models", "ai-products", "industry", "paper"]
DEFAULT_CATEGORY_WEIGHT = 0.75
CATEGORY_FEED_TMPL = "/feed/category/{slug}.xml"

# 中文 category 原值 → {slug, tags}。cfg 若提供 category_map 以其为准。
DEFAULT_CATEGORY_MAP: Dict[str, Dict[str, Any]] = {
    "AI 模型": {"slug": "models", "tags": ["models"]},
    "AI 产品": {"slug": "products", "tags": ["products"]},
    "论文": {"slug": "paper", "tags": ["paper", "research"]},
    "行业动态": {"slug": "industry", "tags": ["industry"]},
    "技巧观点": {"slug": "opinion", "tags": ["opinion"]},
}

_READ_ORIGINAL_RE = re.compile(
    r'<a\b[^>]*\bhref\s*=\s*["\']([^"\']+)["\'][^>]*>\s*阅读原文\s*</a>',
    re.IGNORECASE,
)
_FIRST_P_RE = re.compile(r"<p\b[^>]*>(.*?)</p>", re.IGNORECASE | re.DOTALL)
_IMG_SRC_RE = re.compile(
    r'<img\b[^>]*\bsrc\s*=\s*["\']([^"\']+)["\']', re.IGNORECASE
)
_TAG_RE = re.compile(r"<[^>]+>")


# =========================================================================
# XML / HTML 小工具
# =========================================================================
def _local_tag(el: ET.Element) -> str:
    tag = el.tag
    if isinstance(tag, str) and "}" in tag:
        return tag.split("}", 1)[1]
    return tag if isinstance(tag, str) else ""


def _text(el: Optional[ET.Element]) -> str:
    if el is None:
        return ""
    return (el.text or "").strip()


def _strip_tags(html: str) -> str:
    """去 HTML 标签 + 解实体 + 折叠空白。"""
    text = _TAG_RE.sub("", html or "")
    text = unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _extract_original_url(desc_html: str) -> str:
    """抽「阅读原文」锚点 href（真实原文 URL）；抽不到返回空串。"""
    if not desc_html:
        return ""
    match = _READ_ORIGINAL_RE.search(desc_html)
    return unescape(match.group(1)).strip() if match else ""


def _first_paragraph(desc_html: str) -> str:
    """description 首个 ``<p>`` 的纯文本摘要；无 ``<p>`` 时退化为整段去标签。"""
    if not desc_html:
        return ""
    match = _FIRST_P_RE.search(desc_html)
    if match:
        return _strip_tags(match.group(1))
    return _strip_tags(desc_html)


def _collect_images(desc_html: str) -> List[str]:
    """收集 description 内嵌 ``<img src>``（供深读用），保序去重。"""
    if not desc_html:
        return []
    out: List[str] = []
    for src in _IMG_SRC_RE.findall(desc_html):
        url = unescape(src).strip()
        if url and url not in out:
            out.append(url)
    return out


def _resolve_category_map(cfg: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    raw = cfg.get("category_map")
    if isinstance(raw, dict) and raw:
        mapping: Dict[str, Dict[str, Any]] = {}
        for key, val in raw.items():
            mapping[str(key)] = val if isinstance(val, dict) else {}
        return mapping
    return DEFAULT_CATEGORY_MAP


# =========================================================================
# 纯函数：解析单个 RSS feed
# =========================================================================
def parse_feed(
    xml_text: str,
    kind: str = "feed",
    weight: Any = DEFAULT_WEIGHT,
    cfg: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """解析一份 AIHOT RSS feed 文本为标准 item 列表；纯函数，不出网。

    ``kind`` 标记 feed 来源（curated/all/category:xxx），落入 ``extra.feed_kind``；
    ``weight`` 为该 feed 的权重。解析失败（畸形 XML/空文本）返回空列表，不抛异常。
    """
    cfg = cfg or {}
    category_map = _resolve_category_map(cfg)
    source_name = str(cfg.get("source_name") or SOURCE_NAME)
    try:
        weight_value = float(weight)
    except (TypeError, ValueError):
        weight_value = DEFAULT_WEIGHT

    if not xml_text or not str(xml_text).strip():
        return []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        logger.warning("aihot.parse_feed XML 解析失败(kind=%s): %s", kind, exc)
        return []

    items: List[Dict[str, Any]] = []
    for item_el in root.iter():
        if _local_tag(item_el) != "item":
            continue
        title = ""
        link = ""
        desc = ""
        category = ""
        guid = ""
        pubdate = ""
        for child in item_el:
            tag = _local_tag(child)
            if tag == "title" and not title:
                title = _text(child)
            elif tag == "link" and not link:
                link = _text(child)
            elif tag == "description" and not desc:
                desc = _text(child)
            elif tag == "category" and not category:
                category = _text(child)
            elif tag == "guid" and not guid:
                guid = _text(child)
            elif tag == "pubDate" and not pubdate:
                pubdate = _text(child)

        if not title and not link:
            continue
        canonical = _extract_original_url(desc) or link
        if not canonical:
            continue

        mapping = category_map.get(category) or {}
        slug = mapping.get("slug") or ""
        tags = [str(t) for t in (mapping.get("tags") or [])]

        extra: Dict[str, Any] = {
            "aihot_id": guid or "",
            "aihot_url": link or "",
            "category": category or "",
            "category_slug": slug,
            "feed_kind": kind,
        }
        images = _collect_images(desc)
        if images:
            extra["images"] = images

        items.append(schema.make_item(
            title=title,
            url=canonical,
            source=source_name,
            source_kind="aihot",
            backend="rss",
            weight=weight_value,
            date=pubdate or None,
            summary=_first_paragraph(desc),
            tags=tags,
            extra=extra,
        ))
    return items


def _dedup(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """aihot 内部去重：按 canonical url 或 aihot guid，先到者胜（高权重 feed 在前）。"""
    seen_urls = set()
    seen_guids = set()
    out: List[Dict[str, Any]] = []
    for item in items:
        url = item.get("url")
        guid = (item.get("extra") or {}).get("aihot_id")
        if url and url in seen_urls:
            continue
        if guid and guid in seen_guids:
            continue
        if url:
            seen_urls.add(url)
        if guid:
            seen_guids.add(guid)
        out.append(item)
    return out


def _feed_targets(cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    """把 cfg 展开为 [{path, kind, weight}] 抓取目标列表（通用 feed + 分类 feed）。"""
    targets: List[Dict[str, Any]] = []
    for feed in cfg.get("feeds") or DEFAULT_FEEDS:
        if not isinstance(feed, dict):
            continue
        path = feed.get("path")
        if not path:
            continue
        targets.append({
            "path": str(path),
            "kind": str(feed.get("kind") or "feed"),
            "weight": feed.get("weight", DEFAULT_WEIGHT),
        })

    cat = cfg.get("category_feeds") or {}
    if isinstance(cat, dict):
        slugs = cat.get("slugs") or []
        cat_weight = cat.get("weight", DEFAULT_CATEGORY_WEIGHT)
        for slug in slugs:
            slug = str(slug).strip()
            if not slug:
                continue
            targets.append({
                "path": CATEGORY_FEED_TMPL.format(slug=quote(slug)),
                "kind": "category:{0}".format(slug),
                "weight": cat_weight,
            })
    return targets


# =========================================================================
# 已弃用：旧 REST 解析壳（v0.2，无下游；仅为向后兼容保留，勿新用）
# =========================================================================
def _extract_list(payload: Any) -> List[Any]:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("data", "items", "list"):
            value = payload.get(key)
            if isinstance(value, list):
                return value
    return []


def _first(entry: Dict[str, Any], *keys: str) -> Optional[Any]:
    for key in keys:
        value = entry.get(key)
        if value not in (None, ""):
            return value
    return None


def parse_payload(
    payload: Any,
    cfg: Optional[Dict[str, Any]] = None,
    category: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """[DEPRECATED] v0.2 REST 响应解析壳，已被 RSS 的 ``parse_feed`` 取代，无下游调用。

    仅为向后兼容旧单测保留；``backend`` 仍为 ``rest``。新代码一律走 ``parse_feed``/``fetch``。
    """
    cfg = cfg or {}
    weight = float(cfg.get("weight", DEFAULT_WEIGHT))
    items: List[Dict[str, Any]] = []
    for entry in _extract_list(payload):
        if not isinstance(entry, dict):
            continue
        title = _first(entry, "title", "name")
        url = _first(entry, "url", "link")
        if not title or not url:
            continue
        summary = _first(entry, "summary", "desc", "description") or ""
        date = _first(entry, "date", "publish_time", "created_at")
        cat = category if category is not None else entry.get("category")
        extra: Dict[str, Any] = {"category": cat} if cat else {}
        items.append(schema.make_item(
            title=str(title),
            url=str(url),
            source=SOURCE_NAME,
            source_kind="aihot",
            backend="rest",
            weight=weight,
            date=date,
            summary=str(summary),
            tags=["aihot"],
            extra=extra,
        ))
    return items


# =========================================================================
# 网络壳
# =========================================================================
def fetch(cfg: Optional[Dict[str, Any]] = None, since: Any = None) -> List[Dict[str, Any]]:
    """逐 feed 拉取 AIHOT RSS 并解析、汇总、去重。

    任一 feed 拉取/解析失败记 warning 继续，不因单 feed 拖垮整眼（spec-v0.3 §1）。
    """
    cfg = cfg or {}
    base_url = str(cfg.get("base_url") or DEFAULT_BASE_URL).rstrip("/")

    collected: List[Dict[str, Any]] = []
    for target in _feed_targets(cfg):
        url = base_url + target["path"]
        try:
            resp = http.get(url)
            xml_text = getattr(resp, "text", "") or ""
        except Exception as exc:  # 单 feed 失败不外溢
            logger.warning("aihot feed 拉取失败，跳过 %s: %s", url, exc)
            continue
        try:
            collected.extend(parse_feed(xml_text, target["kind"], target["weight"], cfg))
        except Exception as exc:  # 解析异常同样兜底
            logger.warning("aihot feed 解析失败，跳过 %s: %s", url, exc)
            continue
    return _dedup(collected)
