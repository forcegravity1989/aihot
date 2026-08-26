"""engine/remote_sync.py —— 远眼调度：backend 探测 + 按 backend 分发抓取。

``detect_backend`` 与 ``parse_sitemap`` 是纯函数；``fetch_source`` 按 backend 把请求分发给
``rss`` / ``gitfeed`` / ``html_page``，统一产出 ``{title, url, summary, date}`` 形状的条目列表
（``git`` backend 的条目额外带 ``tags``/``metrics``，见 ``gitfeed.releases``）。
"""

from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from . import github_trending, gitfeed, html_page, http, rss, youtube

logger = logging.getLogger(__name__)

SITEMAP_LIMIT = 20
ARTICLE_LIMIT = 20
_GITHUB_REPO_RE = re.compile(r"^/([^/]+)/([^/]+?)(?:\.git)?/?$")


def detect_backend(url: str, declared: Optional[str] = None) -> str:
    """判定抓取 backend（spec §6）：``declared`` 优先，否则按 URL 结构启发式判定。

    顺序：declared > arxiv.org > 路径含 sitemap > 后缀 .xml/.atom 或路径含 feed|rss
    > github.com/{owner}/{repo} > 兜底 html。
    """
    if declared:
        return str(declared)

    parsed = urlparse(url or "")
    host = (parsed.netloc or "").casefold()
    path = (parsed.path or "").casefold()
    query = (parsed.query or "").casefold()

    if "arxiv.org" in host:
        return "arxiv"
    # 未显式声明 type 时的 youtube feed 启发式：youtube.com host 且带 channel/playlist
    if "youtube.com" in host and ("channel_id=" in query or "playlist_id=" in query):
        return "youtube"
    if "sitemap" in path:
        return "sitemap"
    if path.endswith(".xml") or path.endswith(".atom") or "feed" in path or "rss" in path:
        return "rss"
    if "github.com" in host and _GITHUB_REPO_RE.match(parsed.path or ""):
        return "git"
    return "html"


def _owner_repo_from_url(url: str) -> Optional[str]:
    parsed = urlparse(url or "")
    match = _GITHUB_REPO_RE.match(parsed.path or "")
    if not match:
        return None
    return "{0}/{1}".format(match.group(1), match.group(2))


def parse_sitemap(xml_text: str, limit: int = SITEMAP_LIMIT) -> List[Dict[str, Any]]:
    """从 sitemap XML 抽 ``<loc>`` 前 ``limit`` 条；无标题信息，title 与 url 同值。"""
    if not xml_text or not str(xml_text).strip():
        return []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        logger.warning("sitemap 解析失败: %s", exc)
        return []

    out: List[Dict[str, Any]] = []
    for el in root.iter():
        tag = el.tag
        if isinstance(tag, str) and "}" in tag:
            tag = tag.split("}", 1)[1]
        if tag != "loc":
            continue
        loc = (el.text or "").strip()
        if not loc:
            continue
        out.append({"title": loc, "url": loc, "summary": "", "date": ""})
        if len(out) >= limit:
            break
    return out


def fetch_source(src_cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    """按 backend 分发抓取单个远眼信源（``sources.yaml`` 的 ``sources[i]``）。"""
    cfg = src_cfg or {}
    url = cfg.get("url") or ""
    backend = detect_backend(url, cfg.get("type"))

    if backend in ("rss", "arxiv"):
        resp = http.get(url, timeout=15)
        return rss.parse(resp.text)
    if backend == "sitemap":
        resp = http.get(url, timeout=15)
        return parse_sitemap(resp.text)
    if backend == "git":
        owner_repo = _owner_repo_from_url(url)
        if not owner_repo:
            raise ValueError("无法从 URL 解析 owner/repo: {0}".format(url))
        return gitfeed.releases(owner_repo)
    if backend in ("youtube", "youtube-playlist"):
        feed = youtube.feed_url(cfg)
        resp = http.get(feed, timeout=15)
        return youtube.parse(getattr(resp, "text", "") or "", cfg)
    if backend == "github-trending":
        return github_trending.fetch(cfg.get("since", "daily"))
    if backend == "scrape":
        pattern = cfg.get("article_pattern") or ""
        return html_page.fetch_articles(url, pattern, base_url=url, limit=ARTICLE_LIMIT)
    # html 兜底
    return html_page.fetch_links(url, limit=20)
