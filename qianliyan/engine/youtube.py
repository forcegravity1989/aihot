"""engine/youtube.py —— YouTube 频道/播放列表 Atom feed 解析（spec-v0.3 §13.1）。

YouTube 的 ``feeds/videos.xml`` 是标准 Atom，但每个 ``<entry>`` 里塞了 YouTube/Media RSS
命名空间的私有元素（``yt:videoId``、``media:thumbnail``、``media:description``）。``engine.rss``
虽兼容 Atom 基本字段，却拿不到这些私有元素，故本模块自行按 **local-name** 解析（不依赖具体
命名空间前缀）。

``feed_url(cfg)`` 由 ``channel_id`` / ``playlist_id`` 拼频道/播放列表 feed URL；``handle``（@name）
需先在线解析 channel_id，离线测试不触发。``parse(xml_text, cfg)`` 是纯函数（不出网），单测直喂
``tests/fixtures/real/youtube_anthropic.xml``，产出 ``schema.make_item`` 标准 item：
``url`` = 视频链接、``summary`` = media:description、``extra`` 存 platform/video_id/thumbnail/
channel/format，``extra.format`` 取源配置（channel→video，会议 playlist→talk），tags 来自源。
"""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from html import unescape
from typing import Any, Dict, List, Optional

from ..core import schema
from . import http

logger = logging.getLogger(__name__)

CHANNEL_FEED_TMPL = "https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
PLAYLIST_FEED_TMPL = "https://www.youtube.com/feeds/videos.xml?playlist_id={playlist_id}"
DEFAULT_WEIGHT = 0.85
DEFAULT_FORMAT = "video"


def _local_tag(el: ET.Element) -> str:
    tag = el.tag
    if isinstance(tag, str) and "}" in tag:
        return tag.split("}", 1)[1]
    return tag if isinstance(tag, str) else ""


def _text(el: Optional[ET.Element]) -> str:
    if el is None:
        return ""
    return unescape((el.text or "").strip())


def feed_url(cfg: Optional[Dict[str, Any]] = None) -> str:
    """由源配置拼 YouTube feed URL。

    优先级：``playlist_id`` > ``channel_id`` > 已是完整 URL 的 ``url``。``handle``（@name）
    无法离线解析，抛 ``ValueError`` 提示上层先在线解析 channel_id（离线测试不触发本路径）。
    """
    cfg = cfg or {}
    playlist_id = cfg.get("playlist_id")
    if playlist_id:
        return PLAYLIST_FEED_TMPL.format(playlist_id=str(playlist_id).strip())
    channel_id = cfg.get("channel_id")
    if channel_id:
        return CHANNEL_FEED_TMPL.format(channel_id=str(channel_id).strip())
    url = cfg.get("url")
    if url:
        return str(url)
    handle = cfg.get("handle")
    if handle:
        raise ValueError(
            "YouTube handle {0!r} 需先在线解析 channel_id 才能拼 feed_url".format(handle)
        )
    raise ValueError("YouTube 源缺少 channel_id/playlist_id/url")


def _feed_channel_title(root: ET.Element) -> str:
    """feed 级频道名：root 的直接子 ``<title>``（早于任何 entry 出现）。"""
    for child in root:
        if _local_tag(child) == "title":
            return _text(child)
    return ""


def _entry_link(entry_el: ET.Element) -> str:
    """entry 的观看链接：优先 ``<link rel="alternate" href>``，否则第一个带 href 的 link。"""
    fallback = ""
    for child in entry_el:
        if _local_tag(child) != "link":
            continue
        href = child.attrib.get("href", "")
        if not href:
            continue
        if child.attrib.get("rel", "alternate") == "alternate":
            return href
        if not fallback:
            fallback = href
    return fallback


def parse(xml_text: str, cfg: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """解析 YouTube Atom feed 文本为标准 item 列表；纯函数，不出网。

    解析失败（畸形 XML/空文本）返回空列表，不抛异常。``cfg`` 提供 name/weight/tags/format。
    """
    cfg = cfg or {}
    source = str(cfg.get("name") or "").strip()
    fmt = str(cfg.get("format") or DEFAULT_FORMAT)
    base_tags = [str(t) for t in (cfg.get("tags") or [])]
    try:
        weight = float(cfg.get("weight", DEFAULT_WEIGHT))
    except (TypeError, ValueError):
        weight = DEFAULT_WEIGHT

    if not xml_text or not str(xml_text).strip():
        return []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        logger.warning("youtube.parse XML 解析失败: %s", exc)
        return []

    channel = _feed_channel_title(root)
    if not source:
        source = channel or "YouTube"

    items: List[Dict[str, Any]] = []
    for entry_el in root:
        if _local_tag(entry_el) != "entry":
            continue
        title = ""
        video_id = ""
        thumbnail = ""
        description = ""
        published = ""
        updated = ""
        for child in entry_el:
            tag = _local_tag(child)
            if tag == "title" and not title:
                title = _text(child)
            elif tag == "videoId" and not video_id:
                video_id = _text(child)
            elif tag == "published" and not published:
                published = _text(child)
            elif tag == "updated" and not updated:
                updated = _text(child)
            # media:group 包裹 thumbnail/description，递归其子孙拿 local-name
            for sub in child.iter():
                sub_tag = _local_tag(sub)
                if sub_tag == "thumbnail" and not thumbnail:
                    thumbnail = (sub.attrib.get("url") or "").strip()
                elif sub_tag == "description" and not description:
                    description = _text(sub)

        link = _entry_link(entry_el)
        if not (title or link or video_id):
            continue
        url = link or ("https://www.youtube.com/watch?v={0}".format(video_id) if video_id else "")
        if not url:
            continue

        extra: Dict[str, Any] = {
            "platform": "youtube",
            "video_id": video_id,
            "thumbnail": thumbnail,
            "channel": channel,
            "format": fmt,
        }
        items.append(schema.make_item(
            title=title or video_id,
            url=url,
            source=source,
            source_kind="local",
            backend="rss",
            weight=weight,
            date=(published or updated) or None,
            summary=description,
            tags=list(base_tags),
            extra=extra,
        ))
    return items


def fetch(cfg: Optional[Dict[str, Any]] = None, timeout: int = 15) -> List[Dict[str, Any]]:
    """GET feed_url(cfg) + parse（网络壳）。"""
    url = feed_url(cfg)
    resp = http.get(url, timeout=timeout)
    return parse(getattr(resp, "text", "") or "", cfg)
