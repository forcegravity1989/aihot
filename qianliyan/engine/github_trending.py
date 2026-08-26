"""engine/github_trending.py —— GitHub Trending 抓取（spec-v0.3 §13.2）。

GitHub Trending 无 API/RSS，只有 HTML。页面结构为一串 ``<article class="Box-row">``，每个
article 对应一个仓库。``fetch`` 是网络壳；``parse`` 是纯函数（不出网），单测直喂
``tests/fixtures/real/github_trending.html``，用正则从每个 Box-row 抽 owner/repo、描述、
star 总数、语言、fork 数、以及「X stars today/this week」增量，产出 ``schema.make_item`` item：
``metrics = {stars, stars_period, forks}``、``extra = {format:"repo", language, since}``。
trending 页无发布时间，``date`` 取当前 UTC。
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

from ..core import schema, utils
from . import http

logger = logging.getLogger(__name__)

TRENDING_URL_TMPL = "https://github.com/trending?since={since}"
SOURCE_NAME = "GitHub Trending"
DEFAULT_WEIGHT = 0.65

_ROW_RE = re.compile(r'<article\b[^>]*class="[^"]*\bBox-row\b[^"]*"[^>]*>(.*?)</article>', re.DOTALL)
_H2_RE = re.compile(r"<h2\b[^>]*>(.*?)</h2>", re.DOTALL | re.IGNORECASE)
_HREF_RE = re.compile(r'href="(/[^"?#]+)"')
_DESC_RE = re.compile(
    r'<p\b[^>]*class="[^"]*color-fg-muted[^"]*"[^>]*>(.*?)</p>', re.DOTALL | re.IGNORECASE
)
_LANG_RE = re.compile(
    r'<span\b[^>]*itemprop="programmingLanguage"[^>]*>(.*?)</span>', re.DOTALL | re.IGNORECASE
)
_STARGAZERS_RE = re.compile(
    r'href="/[^"]+/stargazers"[^>]*>(.*?)</a>', re.DOTALL | re.IGNORECASE
)
_FORKS_RE = re.compile(r'href="/[^"]+/forks"[^>]*>(.*?)</a>', re.DOTALL | re.IGNORECASE)
_PERIOD_RE = re.compile(
    r'([\d,]+)\s*stars?\s*(?:today|this\s+week|this\s+month)', re.IGNORECASE
)
_TAG_RE = re.compile(r"<[^>]+>")


def _strip_tags(html: str) -> str:
    text = _TAG_RE.sub(" ", html or "")
    return re.sub(r"\s+", " ", text).strip()


def _to_int(text: str) -> int:
    digits = re.sub(r"[^\d]", "", text or "")
    try:
        return int(digits) if digits else 0
    except ValueError:
        return 0


def _owner_repo(block: str) -> str:
    """从 Box-row 的 ``<h2>`` 内首个仓库锚点 href 抽 ``owner/repo``（跳过 Star 按钮的 /login 链接）。"""
    h2 = _H2_RE.search(block)
    if not h2:
        return ""
    href_match = _HREF_RE.search(h2.group(1))
    if not href_match:
        return ""
    parts = [p for p in href_match.group(1).strip("/").split("/") if p]
    if len(parts) != 2:
        return ""
    return "{0}/{1}".format(parts[0], parts[1])


def parse(html_text: str, since: str = "daily") -> List[Dict[str, Any]]:
    """解析 GitHub Trending HTML 为 item 列表；纯函数，不出网。空/无 Box-row 返回 []。"""
    if not html_text or not str(html_text).strip():
        return []
    since = str(since or "daily")
    now = utils.iso(utils.now_utc())

    items: List[Dict[str, Any]] = []
    for block in _ROW_RE.findall(html_text):
        owner_repo = _owner_repo(block)
        if not owner_repo:
            continue
        url = "https://github.com/{0}".format(owner_repo)

        desc_match = _DESC_RE.search(block)
        summary = _strip_tags(desc_match.group(1)) if desc_match else ""

        lang_match = _LANG_RE.search(block)
        language = _strip_tags(lang_match.group(1)) if lang_match else ""

        star_match = _STARGAZERS_RE.search(block)
        stars = _to_int(_strip_tags(star_match.group(1))) if star_match else 0

        fork_match = _FORKS_RE.search(block)
        forks = _to_int(_strip_tags(fork_match.group(1))) if fork_match else 0

        period_match = _PERIOD_RE.search(_strip_tags(block))
        stars_period = _to_int(period_match.group(1)) if period_match else 0

        tags = ["github", "trending"]
        if language:
            lang_tag = language.casefold()
            if lang_tag not in tags:
                tags.append(lang_tag)

        items.append(schema.make_item(
            title=owner_repo,
            url=url,
            source=SOURCE_NAME,
            source_kind="local",
            backend="html",
            weight=DEFAULT_WEIGHT,
            date=now,
            summary=summary,
            tags=tags,
            metrics={"stars": stars, "stars_period": stars_period, "forks": forks},
            extra={"format": "repo", "language": language, "since": since},
        ))
    return items


def fetch(since: str = "daily", spoken_lang: Optional[str] = None, timeout: int = 15) -> List[Dict[str, Any]]:
    """GET ``github.com/trending?since=..`` + parse（网络壳）。``spoken_lang`` 预留，暂不拼参。"""
    url = TRENDING_URL_TMPL.format(since=str(since or "daily"))
    if spoken_lang:
        url = "{0}&spoken_language_code={1}".format(url, spoken_lang)
    resp = http.get(url, timeout=timeout)
    return parse(getattr(resp, "text", "") or "", since)
