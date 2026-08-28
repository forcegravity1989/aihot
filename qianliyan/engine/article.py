"""engine/article.py —— 文章正文抽取（深读的原料来源）。

RSS/索引页抓到的 ``summary`` 常常是空的或只有一句导语（真实案例：``claude.com/blog``
的文章在索引页只有标题，摘要为空），**深读因此没有可读的原料**。本模块负责打开单篇
文章页、把正文段落抽出来，供 ``cli/daily_digest_all.py`` 的 ``--finalize`` 做精读增强。

纯 stdlib 实现（``html.parser`` + 正则），不引入 readability/bs4 之类依赖。抽取思路是
「块级文本 + 噪声过滤」而不是猜某个站点的 CSS 选择器——后者每换一个站点就得改一次：

* 先整段丢掉 ``script/style/nav/header/footer/aside/form`` 等非正文容器；
* 逐个收集 ``<p>/<h2>/<h3>/<li>`` 块，记录每块的链接文字占比；
* 用三条噪声判据过滤：链接占比过高（导航/推荐位）、无句末标点且很长（导航词堆叠成的
  一坨，如 "Meet ClaudeProductsClaude Code…"）、过短（版权、按钮文案）。

``extract_article`` 是纯函数（喂 HTML 文本），``fetch_article`` 是网络壳。
"""

from __future__ import annotations

import logging
import re
from html.parser import HTMLParser
from typing import Any, Dict, List, Optional

from . import http

logger = logging.getLogger(__name__)

#: 整段丢弃的非正文容器（含其全部子孙）
_SKIP_CONTAINERS = frozenset((
    "script", "style", "nav", "header", "footer", "aside", "form",
    "noscript", "svg", "select", "button", "iframe", "template",
))
#: 作为独立文本块收集的块级元素
_BLOCK_TAGS = frozenset(("p", "h1", "h2", "h3", "h4", "li", "blockquote"))
_HEADING_TAGS = frozenset(("h1", "h2", "h3", "h4"))

#: 句末标点（中英）——正文段落总会有，导航词堆叠没有
_SENTENCE_PUNCT_RE = re.compile(r"[.!?。！？；;]")
#: 裸 URL（分享条 "ShareCopy linkhttps://…" 这类块，URL 自带的点号会骗过句末标点判据）
_BARE_URL_RE = re.compile(r"https?://\S+")
#: 噪声判据阈值
MIN_BLOCK_LEN = 40          # 短于此的块当作按钮/版权/标签，不进正文
MAX_LINK_DENSITY = 0.5      # 链接文字占比高于此当作导航/推荐位
NAV_SOUP_MIN_LEN = 100      # 长度超过此且无句末标点 → 导航词堆叠
DEFAULT_MAX_PARAGRAPHS = 60
DEFAULT_TIMEOUT = 20
#: 正文页通常不大；给个上限防止个别站点塞进整站数据（复用 §19.4 的量级保护思路）
DEFAULT_MAX_BYTES = 3 * 1024 * 1024


class _ArticleTextExtractor(HTMLParser):
    """收集块级文本 + 每块的链接文字占比。容忍畸形 HTML。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: List[Dict[str, Any]] = []
        #: 跳过容器按**标签名栈**跟踪，不用单一深度计数——真实网页里
        #: 未闭合的 <nav>（站点模板常见）会让纯计数永远归不了零，从而把
        #: 正文整段吞掉。闭合时清掉该标签的全部层级，容忍这类畸形。
        self._skip_stack: List[str] = []
        self._block_stack: List[Dict[str, Any]] = []
        self._in_a = 0

    # -- 容器进出 --------------------------------------------------------
    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in _SKIP_CONTAINERS:
            self._skip_stack.append(tag)
            return
        if self._skip_stack:
            return
        if tag == "a":
            self._in_a += 1
            return
        if tag in _BLOCK_TAGS:
            # 块可嵌套（li 里套 p）；用栈保证文本记到最内层那个块上
            self._block_stack.append({"tag": tag, "chars": [], "link_chars": 0})

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_CONTAINERS:
            # 清掉该标签的所有层级（见 _skip_stack 注释）
            self._skip_stack = [t for t in self._skip_stack if t != tag]
            return
        if self._skip_stack:
            return
        if tag == "a":
            if self._in_a:
                self._in_a -= 1
            return
        if tag in _BLOCK_TAGS and self._block_stack:
            block = self._block_stack.pop()
            text = " ".join("".join(block["chars"]).split())
            if text:
                self.blocks.append({
                    "tag": block["tag"],
                    "text": text,
                    "link_chars": block["link_chars"],
                })

    def handle_data(self, data: str) -> None:
        if self._skip_stack or not self._block_stack:
            return
        current = self._block_stack[-1]
        current["chars"].append(data)
        if self._in_a:
            current["link_chars"] += len(data.strip())


def _is_noise(text: str, link_chars: int) -> bool:
    """噪声判据（见模块 docstring）；命中任一即丢弃。"""
    length = len(text)
    if length < MIN_BLOCK_LEN:
        return True
    if length and link_chars / float(length) > MAX_LINK_DENSITY:
        return True
    if length >= NAV_SOUP_MIN_LEN and not _SENTENCE_PUNCT_RE.search(text):
        return True
    # 分享条：去掉裸 URL 后几乎不剩内容（URL 自带的点号会骗过上面的句末标点判据）
    if _BARE_URL_RE.search(text):
        without_url = _BARE_URL_RE.sub("", text).strip()
        if len(without_url) < MIN_BLOCK_LEN:
            return True
    return False


def extract_article(
    html_text: str,
    max_paragraphs: int = DEFAULT_MAX_PARAGRAPHS,
) -> Dict[str, Any]:
    """从单篇文章页 HTML 抽正文；纯函数，不出网。

    返回 ``{"paragraphs": [str], "text": str, "lead": str, "char_count": int}``。
    ``lead`` 是首个正文段落（可直接当摘要用），``text`` 是段落用换行拼接的全文。
    解析失败/无正文一律返回空结构，不抛异常。
    """
    if not html_text or not str(html_text).strip():
        return {"paragraphs": [], "text": "", "lead": "", "char_count": 0}

    parser = _ArticleTextExtractor()
    try:
        parser.feed(str(html_text))
    except Exception as exc:  # noqa: BLE001 - 容忍畸形 HTML，不许拖垮调用方
        logger.warning("extract_article 解析失败: %s", exc)

    paragraphs: List[str] = []
    seen = set()
    for block in parser.blocks:
        text = block["text"]
        if block["tag"] in _HEADING_TAGS:
            # 标题块放宽长度限制（小标题往往很短），但仍要过链接密度
            if len(text) < 4 or block["link_chars"] / float(max(len(text), 1)) > MAX_LINK_DENSITY:
                continue
        elif _is_noise(text, block["link_chars"]):
            continue
        if text in seen:
            continue
        seen.add(text)
        paragraphs.append(text)
        if len(paragraphs) >= max_paragraphs:
            break

    lead = ""
    for text in paragraphs:
        if len(text) >= MIN_BLOCK_LEN:
            lead = text
            break

    full_text = "\n".join(paragraphs)
    return {
        "paragraphs": paragraphs,
        "text": full_text,
        "lead": lead,
        "char_count": len(full_text),
    }


def fetch_article(
    url: str,
    timeout: int = DEFAULT_TIMEOUT,
    max_paragraphs: int = DEFAULT_MAX_PARAGRAPHS,
) -> Dict[str, Any]:
    """GET 单篇文章页 + :func:`extract_article`（网络壳）。

    网络失败一律返回空结构并记 warning——深读拿不到正文时要能优雅退回摘要，
    不能让整轮 finalize 挂掉。
    """
    try:
        resp = http.get(url, timeout=timeout, max_bytes=DEFAULT_MAX_BYTES)
    except Exception as exc:  # noqa: BLE001 - 含 OfflineError；深读取正文属尽力而为
        logger.warning("fetch_article 抓取失败 (%s): %s", url, exc)
        return {"paragraphs": [], "text": "", "lead": "", "char_count": 0}
    return extract_article(getattr(resp, "text", "") or "", max_paragraphs=max_paragraphs)
