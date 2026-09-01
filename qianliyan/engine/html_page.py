"""engine/html_page.py —— 通用网页链接抽取（无 CSS 选择器，仅 ``<a>`` 标签启发式）。

用于没有 RSS/API 的普通官网页面（如 Anthropic News），以及内眼 CDP 失败后的代理兜底路径。
``extract_links`` 是纯函数（喂 HTML 文本），``fetch_links`` = ``http.get`` + ``extract_links``。
"""

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

# 常见导航/功能性噪声词（中英文），仅在链接文本较短时用于剔除，避免误伤含这些词的长标题
_NAV_NOISE = (
    "home", "login", "sign in", "sign up", "log in", "contact", "about",
    "privacy", "terms", "cookie", "menu", "search", "subscribe", "follow",
    "share", "more", "next", "prev", "previous", "read more",
    "首页", "登录", "登陆", "注册", "关于", "隐私", "条款", "菜单", "搜索",
    "订阅", "分享", "更多", "下一页", "上一页",
)


class _AnchorExtractor(HTMLParser):
    """只关心 ``<a href=...>text</a>``，容忍畸形 HTML（stdlib parser 本身就很宽容）。"""

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
    """从 HTML 文本抽 ``<a>`` 链接：标题启发式（长度 ≥8、剔除短导航噪声词）+ 相对链接补全 + 去重。"""
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
    """GET 页面并抽取链接。"""
    resp = http.get(url, timeout=timeout)
    return extract_links(resp.text, base_url=url, limit=limit)


# =========================================================================
# 文章索引抽取（spec-v0.3 §13.3）：无 RSS 的官网 blog（如 Anthropic）走此路
# =========================================================================
_HEADING_RE = re.compile(r"h[1-6]$")

#: 卡片式官网常把 <time>发布日期</time> 与标题放进同一个可点击 <a> 里（如 Anthropic
#: News），逐文本节点拼接后日期会粘在标题最前面。按「原始文本节点」（不是拼接后的整
#: 串）逐个匹配，命中就摘出来单独当日期，不进标题——一次只处理开头连续的日期节点，
#: 避免误伤标题正文里恰好出现的日期。
_DATE_SEGMENT_RE = re.compile(
    r"^(?:"
    r"\d{4}-\d{2}-\d{2}"                          # 2026-08-27
    r"|[A-Za-z]{3,9}\.?\s+\d{1,2},?\s+\d{4}"        # Aug 27, 2026 / August 27 2026
    r"|\d{1,2}\s+[A-Za-z]{3,9}\.?\s+\d{4}"          # 27 Aug 2026
    r")$"
)


def _split_leading_date(segments: List[str]) -> "tuple[str, str]":
    """按原始文本节点顺序剥掉开头连续的日期节点，返回 ``(date, 剩余标题)``。"""
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


#: "整卡可点击"站点常把真标题/日期放在锚点外的兄弟元素里，锚点本身只包一句
#: 通用 CTA（Webflow 一类模板的典型结构：``<h2>标题</h2><div>日期</div>`` 紧邻
#: ``<a><span class="sr-only">Read more</span></a>``）。锚点自身文本落进这份
#: 泛词表时，改用"自上一个 </a> 结束以来"最近出现过的标题/日期兜底。
_CTA_GENERIC_RE = re.compile(
    r"^(?:read more|learn more|continue reading|see more|view more|more|"
    r"阅读更多|查看更多|了解更多|查看详情|更多)$",
    re.IGNORECASE,
)


class _ArticleExtractor(HTMLParser):
    """抽 ``<a href>`` 及其标题：优先锚点内首个 ``<h1..h6>`` 文本，回退锚点全文；
    两者都先剥掉开头粘连的日期文本节点（见 ``_split_leading_date``）；锚点自身文本
    是无信息 CTA 泛词时，回退用锚点外、紧邻在前的标题/日期（见上方 ``_CTA_GENERIC_RE``）。
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.articles: List[Dict[str, str]] = []
        self._in_a = False
        self._href: Optional[str] = None
        self._text_buf: List[str] = []
        self._heading_buf: List[str] = []
        self._heading_depth = 0
        # 全局标题深度追踪（不分是否在 <a> 内），配合下面的"待定标题/日期"，
        # 记录自上一个 </a> 结束以来见过的最近一个标题/日期，见类 docstring。
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
    """从索引页 HTML 抽符合 ``article_pattern``（对链接 **path** 匹配）的文章链接；纯函数。

    每条产标准化 entry ``{title, url, summary, date, extra:{format:"blog"}}``：``title`` 优先取
    锚点内标题（``<h1..h6>``）、回退锚点全文；相对链接以 ``base_url`` 补全；按 url 去重。
    """
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
    """GET 索引页 + ``extract_articles``（网络壳）。``base_url`` 缺省用 ``index_url`` 补全相对链接。"""
    resp = http.get(index_url, timeout=timeout)
    return extract_articles(
        getattr(resp, "text", "") or "",
        article_pattern,
        base_url=base_url if base_url is not None else index_url,
        limit=limit,
    )
