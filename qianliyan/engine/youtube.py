from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from datetime import timedelta
from html import unescape
from typing import Any, Dict, List, Optional

from ..core import schema, utils
from . import http

logger = logging.getLogger(__name__)

CHANNEL_FEED_TMPL = "https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
PLAYLIST_FEED_TMPL = "https://www.youtube.com/feeds/videos.xml?playlist_id={playlist_id}"
CHANNEL_VIDEOS_URL_TMPL = "https://www.youtube.com/channel/{channel_id}/videos"
HANDLE_VIDEOS_URL_TMPL = "https://www.youtube.com/@{handle}/videos"
PLAYLIST_PAGE_URL_TMPL = "https://www.youtube.com/playlist?list={playlist_id}"
DEFAULT_WEIGHT = 0.85
DEFAULT_FORMAT = "video"
# 跳过欧盟 cookie 同意页跳转
_CONSENT_COOKIE = {"name": "CONSENT", "value": "YES+1", "domain": ".youtube.com", "path": "/"}
_CDP_NAV_TIMEOUT_MS = 20000
_CDP_DATA_WAIT_MS = 15000

# 频道页与播放列表页外层结构不同，但视频卡片 schema 相同，递归一份脚本通吃
_EXTRACT_VIDEOS_SCRIPT = """
() => {
  function findVideos(node, out, seen) {
    if (!node || typeof node !== 'object') return;
    if (node.lockupViewModel && node.lockupViewModel.contentType === 'LOCKUP_CONTENT_TYPE_VIDEO') {
      const lv = node.lockupViewModel;
      const vid = lv.contentId;
      if (vid && !seen.has(vid)) {
        seen.add(vid);
        const m = lv.metadata && lv.metadata.lockupMetadataViewModel;
        const rows = (m && m.metadata && m.metadata.contentMetadataViewModel &&
                      m.metadata.contentMetadataViewModel.metadataRows) || [];
        const metaParts = rows.flatMap(r => (r.metadataParts || [])
          .map(p => p.text && p.text.content).filter(Boolean));
        out.push({ videoId: vid, title: (m && m.title && m.title.content) || '', meta: metaParts });
      }
    }
    for (const k in node) {
      if (Object.prototype.hasOwnProperty.call(node, k)) findVideos(node[k], out, seen);
    }
  }
  const out = [];
  try { findVideos(window.ytInitialData, out, new Set()); } catch (e) {}
  return out;
}
"""

# YouTube 会在数字前加「直播时间：」之类前缀，只搜不整串匹配
_REL_UNIT_CN = {
    "秒": "seconds", "分钟": "minutes", "小时": "hours",
    "天": "days", "周": "weeks", "个月": "months", "年": "years",
}
_REL_CN_RE = re.compile(r"(\d+)\s*(秒|分钟|小时|天|周|个月|年)\s*前")
_REL_EN_RE = re.compile(r"(\d+)\s*(second|minute|hour|day|week|month|year)s?\s*ago", re.IGNORECASE)
_JUST_NOW_RE = re.compile(r"刚刚|just now", re.IGNORECASE)
# 只喂给新鲜度衰减，近似即可
_UNIT_DAYS = {
    "seconds": 0, "minutes": 0, "hours": 0,
    "days": 1, "weeks": 7, "months": 30, "years": 365,
}


def _relative_to_date(text: str, now: Any = None) -> Optional[str]:
    if not text:
        return None
    now = now or utils.now_utc()
    if _JUST_NOW_RE.search(text):
        return utils.iso(now)
    match = _REL_CN_RE.search(text)
    if match:
        count = int(match.group(1))
        unit = _REL_UNIT_CN[match.group(2)]
    else:
        match = _REL_EN_RE.search(text)
        if not match:
            return None
        count = int(match.group(1))
        unit = match.group(2).lower() + "s"
    days = _UNIT_DAYS.get(unit, 0) * count
    if unit in ("seconds", "minutes", "hours"):
        return utils.iso(now)
    return utils.iso(now - timedelta(days=days))


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
    for child in root:
        if _local_tag(child) == "title":
            return _text(child)
    return ""


def _entry_link(entry_el: ET.Element) -> str:
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
            # media:group 包裹 thumbnail/description
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


def page_url(cfg: Optional[Dict[str, Any]] = None) -> str:
    """与 feed_url 不同，handle 可直接用（CDP 打开 /@handle/videos）。"""
    cfg = cfg or {}
    playlist_id = cfg.get("playlist_id")
    if playlist_id:
        return PLAYLIST_PAGE_URL_TMPL.format(playlist_id=str(playlist_id).strip())
    channel_id = cfg.get("channel_id")
    if channel_id:
        return CHANNEL_VIDEOS_URL_TMPL.format(channel_id=str(channel_id).strip())
    handle = cfg.get("handle")
    if handle:
        return HANDLE_VIDEOS_URL_TMPL.format(handle=str(handle).strip().lstrip("@"))
    url = cfg.get("url")
    if url:
        return str(url)
    raise ValueError("YouTube 源缺少 channel_id/playlist_id/handle/url")


def _items_from_cdp_videos(videos: List[Dict[str, Any]], cfg: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    cfg = cfg or {}
    source = str(cfg.get("name") or "YouTube").strip() or "YouTube"
    fmt = str(cfg.get("format") or DEFAULT_FORMAT)
    base_tags = [str(t) for t in (cfg.get("tags") or [])]
    try:
        weight = float(cfg.get("weight", DEFAULT_WEIGHT))
    except (TypeError, ValueError):
        weight = DEFAULT_WEIGHT

    now = utils.now_utc()
    items: List[Dict[str, Any]] = []
    for video in videos or []:
        if not isinstance(video, dict):
            continue
        video_id = str(video.get("videoId") or "").strip()
        title = str(video.get("title") or "").strip()
        if not video_id or not title:
            continue
        meta = [str(m) for m in (video.get("meta") or [])]
        date = None
        for part in meta:
            date = _relative_to_date(part, now=now)
            if date:
                break
        extra: Dict[str, Any] = {
            "platform": "youtube",
            "video_id": video_id,
            "thumbnail": "https://i.ytimg.com/vi/{0}/hqdefault.jpg".format(video_id),
            "channel": source,
            "format": fmt,
        }
        items.append(schema.make_item(
            title=title,
            url="https://www.youtube.com/watch?v={0}".format(video_id),
            source=source,
            source_kind="local",
            backend="rss",
            weight=weight,
            date=date,
            summary=" · ".join(m for m in meta if m and m != title),
            tags=list(base_tags),
            extra=extra,
        ))
    return items


def fetch_via_cdp(cfg: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """失败一律抛出交给 fetch 回退，不在这里吞成 []。"""
    url = page_url(cfg)
    from . import cdp

    playwright_ctx = browser = None
    try:
        playwright_ctx, browser = cdp.connect()
        page = browser.new_page()
        try:
            page.context.add_cookies([_CONSENT_COOKIE])
            page.goto(url, timeout=_CDP_NAV_TIMEOUT_MS)
            page.wait_for_function(
                "() => !!window.ytInitialData", timeout=_CDP_DATA_WAIT_MS
            )
            raw_videos = page.evaluate(_EXTRACT_VIDEOS_SCRIPT)
        finally:
            try:
                page.close()
            except Exception:  # noqa: BLE001
                pass
        items = _items_from_cdp_videos(raw_videos, cfg)
        if not items:
            raise RuntimeError("CDP 页面未抽到任何视频（{0}）".format(url))
        return items
    finally:
        try:
            if browser is not None:
                browser.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            if playwright_ctx is not None:
                playwright_ctx.stop()
        except Exception:  # noqa: BLE001
            pass


def fetch(cfg: Optional[Dict[str, Any]] = None, timeout: int = 15) -> List[Dict[str, Any]]:
    """CDP 优先；feeds/videos.xml 自 2026-08 起一律 404，仅作回退。"""
    try:
        return fetch_via_cdp(cfg)
    except Exception as exc:  # noqa: BLE001 - CDP 失败降级到 feed，不是致命错误
        logger.warning("YouTube CDP 抓取失败，回退 feed 端点: %s", exc)

    url = feed_url(cfg)
    resp = http.get(url, timeout=timeout)
    return parse(getattr(resp, "text", "") or "", cfg)
