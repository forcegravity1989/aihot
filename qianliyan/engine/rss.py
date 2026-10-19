from __future__ import annotations

import html
import logging
import re
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional

from . import http

logger = logging.getLogger(__name__)


def _text(el: Optional[ET.Element]) -> str:
    if el is None:
        return ""
    return (el.text or "").strip()


def _local_tag(el: ET.Element) -> str:
    tag = el.tag
    if isinstance(tag, str) and "}" in tag:
        return tag.split("}", 1)[1]
    return tag if isinstance(tag, str) else ""


# Mintlify 等 changelog feed 的通病：<title>2026-08-26</title>
_DATE_ONLY_TITLE = re.compile(r"^\s*\d{4}[-/.]\d{1,2}[-/.]\d{1,2}\s*$")
_TAG_RE = re.compile(r"<[^>]+>")
_SENTENCE_END = re.compile(r"(?<=[.!?。！？])\s")
DERIVED_TITLE_MAX = 90


def _plain(markup: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(_TAG_RE.sub(" ", markup or ""))).strip()


def _derive_title(title: str, body: str) -> str:
    """标题只是日期时读者看不出发生了什么，用正文第一句补成「日期 · 第一句」。"""
    if not _DATE_ONLY_TITLE.match(title or ""):
        return title
    text = _plain(body)
    if not text:
        return title
    first = _SENTENCE_END.split(text, 1)[0].strip()
    if len(first) > DERIVED_TITLE_MAX:
        first = first[:DERIVED_TITLE_MAX].rstrip() + "…"
    return "{0} · {1}".format(title.strip(), first)


def _parse_rss2(root: ET.Element) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    for item_el in root.iter():
        if _local_tag(item_el) != "item":
            continue
        title = ""
        link = ""
        summary = ""
        content = ""
        date = ""
        for child in item_el:
            tag = _local_tag(child)
            if tag == "title" and not title:
                title = _text(child)
            elif tag == "link" and not link:
                link = _text(child) or (child.attrib.get("href") or "")
            elif tag in ("description", "summary") and not summary:
                summary = _text(child)
            elif tag == "encoded" and not content:  # content:encoded
                content = _text(child)
            elif tag in ("pubDate", "date") and not date:
                date = _text(child)
        if not summary and content:
            summary = _plain(content)
        title = _derive_title(title, content or summary)
        if title or link:
            items.append({"title": title, "url": link, "summary": summary, "date": date})
    return items


def _atom_link(entry_el: ET.Element) -> str:
    fallback = ""
    for child in entry_el:
        if _local_tag(child) != "link":
            continue
        href = child.attrib.get("href", "")
        if not href:
            continue
        rel = child.attrib.get("rel", "alternate")
        if rel == "alternate":
            return href
        if not fallback:
            fallback = href
    return fallback


def _parse_atom(root: ET.Element) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    for entry_el in root:
        if _local_tag(entry_el) != "entry":
            continue
        title = ""
        summary = ""
        published = ""
        updated = ""
        for child in entry_el:
            tag = _local_tag(child)
            if tag == "title" and not title:
                title = _text(child)
            elif tag in ("summary", "content") and not summary:
                summary = _text(child)
            elif tag == "published" and not published:
                published = _text(child)
            elif tag == "updated" and not updated:
                updated = _text(child)
        link = _atom_link(entry_el)
        date = published or updated
        if title or link:
            items.append({"title": title, "url": link, "summary": summary, "date": date})
    return items


def parse(xml_text: str) -> List[Dict[str, Any]]:
    if not xml_text or not str(xml_text).strip():
        return []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        logger.warning("rss.parse XML 解析失败: %s", exc)
        return []

    if _local_tag(root) == "feed":
        return _parse_atom(root)
    return _parse_rss2(root)


def fetch(url: str, timeout: int = 15) -> List[Dict[str, Any]]:
    resp = http.get(url, timeout=timeout)
    return parse(resp.text)
