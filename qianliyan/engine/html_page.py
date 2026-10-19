from __future__ import annotations

import logging
import re
from html.parser import HTMLParser
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin, urlparse

from . import http

logger = logging.getLogger(__name__)

MIN_TITLE_LEN = 8
NAV_NOISE_MAX_LEN = 20
DEFAULT_LIMIT = 20
ARTICLE_FORMAT = "blog"

# 仅在链接文本较短时用于剔除，避免误伤含这些词的长标题
_NAV_NOISE = (
    "home", "login", "sign in", "sign up", "log in", "contact", "about",
    "privacy", "terms", "cookie", "menu", "search", "subscribe", "follow",
    "share", "more", "next", "prev", "previous", "read more",
    "首页", "登录", "登陆", "注册", "关于", "隐私", "条款", "菜单", "搜索",
    "订阅", "分享", "更多", "下一页", "上一页",
)


class _AnchorExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: List[Dict[str, str]] = []
        self._href: Optional[str] = None
        self._buf: List[str] = []
        self._depth = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag != "a":
            return
        href = dict(attrs).get("href")
        if not href:
            return
        self._href = href
        self._buf = []
        self._depth += 1

    def handle_data(self, data: str) -> None:
        if self._depth > 0:
            self._buf.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag != "a" or self._depth == 0:
            return
        self._depth -= 1
        text = " ".join("".join(self._buf).split())
        if self._href is not None:
            self.links.append({"href": self._href, "text": text})
        self._href = None
        self._buf = []


def extract_links(html_text: str, base_url: str = "", limit: int = DEFAULT_LIMIT) -> List[Dict[str, str]]:
    if not html_text:
        return []
    parser = _AnchorExtractor()
    try:
        parser.feed(html_text)
    except Exception as exc:  # noqa: BLE001 - 容忍畸形 HTML，不许拖垮调用方
        logger.warning("extract_links 解析失败: %s", exc)

    seen = set()
    results: List[Dict[str, str]] = []
    for link in parser.links:
        text = link["text"]
        if len(text) < MIN_TITLE_LEN:
            continue
        lowered = text.casefold()
        if len(text) < NAV_NOISE_MAX_LEN and any(noise in lowered for noise in _NAV_NOISE):
            continue
        href = link["href"]
        if href.startswith("javascript:") or href.startswith("#") or href.startswith("mailto:"):
            continue
        url = urljoin(base_url, href) if base_url else href
        if url in seen:
            continue
        seen.add(url)
        results.append({"title": text, "url": url})
        if len(results) >= limit:
            break
    return results


def fetch_links(url: str, limit: int = DEFAULT_LIMIT, timeout: int = 15) -> List[Dict[str, str]]:
    resp = http.get(url, timeout=timeout)
    return extract_links(resp.text, base_url=url, limit=limit)


_HEADING_RE = re.compile(r"h[1-6]$")

# 卡片式官网常把 <time> 与标题放进同一个 <a>：按原始文本节点只摘开头连续的日期，避免误伤标题里的日期
_DATE_SEGMENT_RE = re.compile(
    r"^(?:"
    r"\d{4}-\d{2}-\d{2}"                          # 2026-08-27
    r"|[A-Za-z]{3,9}\.?\s+\d{1,2},?\s+\d{4}"        # Aug 27, 2026 / August 27 2026
    r"|\d{1,2}\s+[A-Za-z]{3,9}\.?\s+\d{4}"          # 27 Aug 2026
    r")$"
)


def _split_leading_date(segments: List[str]) -> "tuple[str, str]":
    date = ""
    idx = 0
    for idx, seg in enumerate(segments):
        stripped = seg.strip()
        if not stripped:
            continue
        if _DATE_SEGMENT_RE.match(stripped):
            date = stripped
            idx += 1
            continue
        break
    else:
        idx = len(segments)
    title = " ".join("".join(segments[idx:]).split())
    return date, title


# 整卡可点击站点的锚点常只包一句通用 CTA（Read more），此时改用锚点前最近的标题/日期
_CTA_GENERIC_RE = re.compile(
    r"^(?:read more|learn more|continue reading|see more|view more|more|"
    r"阅读更多|查看更多|了解更多|查看详情|更多)$",
    re.IGNORECASE,
)


class _ArticleExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.articles: List[Dict[str, str]] = []
        self._in_a = False
        self._href: Optional[str] = None
        self._text_buf: List[str] = []
        self._heading_buf: List[str] = []
        self._heading_depth = 0
        # 记录自上一个 </a> 结束以来最近的标题/日期，供 CTA 锚点兜底
        self._global_heading_depth = 0
        self._global_heading_buf: List[str] = []
        self._pending_heading = ""
        self._pending_date = ""

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag == "a":
            href = dict(attrs).get("href")
            if href:
                self._in_a = True
                self._href = href
                self._text_buf = []
                self._heading_buf = []
                self._heading_depth = 0
            return
        if self._in_a and _HEADING_RE.match(tag):
            self._heading_depth += 1
        if _HEADING_RE.match(tag):
            self._global_heading_depth += 1
            self._global_heading_buf = []

    def handle_data(self, data: str) -> None:
        if self._in_a:
            self._text_buf.append(data)
            if self._heading_depth > 0:
                self._heading_buf.append(data)
        if self._global_heading_depth > 0:
            self._global_heading_buf.append(data)
        elif not self._in_a:
            stripped = data.strip()
            if stripped and _DATE_SEGMENT_RE.match(stripped):
                self._pending_date = stripped

    def handle_endtag(self, tag: str) -> None:
        if _HEADING_RE.match(tag) and self._global_heading_depth > 0:
            self._global_heading_depth -= 1
            if self._global_heading_depth == 0:
                text = " ".join("".join(self._global_heading_buf).split())
                if text:
                    self._pending_heading = text
                self._global_heading_buf = []
        if not self._in_a:
            return
        if _HEADING_RE.match(tag) and self._heading_depth > 0:
            self._heading_depth -= 1
            return
        if tag == "a":
            text_date, text = _split_leading_date(self._text_buf)
            heading_date, heading = _split_leading_date(self._heading_buf)
            title = heading or text
            date = heading_date or text_date
            if not title or _CTA_GENERIC_RE.match(title.strip()):
                if self._pending_heading:
                    title = self._pending_heading
                if not date:
                    date = self._pending_date
            if self._href is not None:
                self.articles.append({
                    "href": self._href,
                    "text": text,
                    "heading": title,
                    "date": date,
                })
            self._in_a = False
            self._href = None
            self._text_buf = []
            self._heading_buf = []
            self._heading_depth = 0
            self._pending_heading = ""
            self._pending_date = ""


def extract_articles(
    html_text: str,
    article_pattern: str,
    base_url: str = "",
    limit: int = DEFAULT_LIMIT,
) -> List[Dict[str, Any]]:
    if not html_text:
        return []
    try:
        pattern = re.compile(article_pattern)
    except re.error as exc:
        logger.warning("extract_articles 非法 article_pattern %r: %s", article_pattern, exc)
        return []

    parser = _ArticleExtractor()
    try:
        parser.feed(html_text)
    except Exception as exc:  # noqa: BLE001 - 容忍畸形 HTML，不许拖垮调用方
        logger.warning("extract_articles 解析失败: %s", exc)

    seen = set()
    results: List[Dict[str, Any]] = []
    for art in parser.articles:
        href = art["href"]
        if href.startswith("javascript:") or href.startswith("#") or href.startswith("mailto:"):
            continue
        url = urljoin(base_url, href) if base_url else href
        path = urlparse(url).path or href
        if not pattern.search(path):
            continue
        if url in seen:
            continue
        seen.add(url)
        title = (art["heading"] or art["text"]).strip()
        if not title:
            continue
        results.append({
            "title": title,
            "url": url,
            "summary": "",
            "date": art.get("date", ""),
            "extra": {"format": ARTICLE_FORMAT},
        })
        if len(results) >= limit:
            break
    return results


def fetch_articles(
    index_url: str,
    article_pattern: str,
    base_url: Optional[str] = None,
    limit: int = DEFAULT_LIMIT,
    timeout: int = 15,
) -> List[Dict[str, Any]]:
    resp = http.get(index_url, timeout=timeout)
    return extract_articles(
        getattr(resp, "text", "") or "",
        article_pattern,
        base_url=base_url if base_url is not None else index_url,
        limit=limit,
    )
