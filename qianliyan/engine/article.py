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

**正文配图**（issue #52）：同一趟解析顺带收集正文里的 ``<img>``（src / srcset / alt / 所在
``<figure>`` 的图注），``pick_images`` 挑出有信息量的那几张——实验曲线、架构图、流程图，
而不是 logo、作者头像、导航图标。原文自带的图往往比我们自己画的任何东西都更说明问题。
"""

from __future__ import annotations

import html as html_lib
import logging
import re
from html.parser import HTMLParser
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin

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
        #: 正文配图（跳过容器里的——导航 logo、页脚图标——根本不会进来）
        self.images: List[Dict[str, Any]] = []
        self.meta: Dict[str, str] = {}
        self._figures: List[Dict[str, Any]] = []
        self._in_caption = 0
        self._main_depth = 0

    # -- 容器进出 --------------------------------------------------------
    def handle_starttag(self, tag: str, attrs) -> None:
        if tag == "meta":
            self._meta(dict(attrs))
            return
        if tag in _SKIP_CONTAINERS:
            self._skip_stack.append(tag)
            return
        if self._skip_stack:
            # 交互图表的静态版放在 <noscript> 里（Epoch 的主图就是这样）——我们的页面不跑
            # 原站的 JS，要的恰恰是这张降级图；noscript 里的文字仍然不收
            if tag == "img" and all(t == "noscript" for t in self._skip_stack):
                self._image(dict(attrs))
            return
        if tag in ("article", "main"):
            self._main_depth += 1
        elif tag == "figure":
            self._figures.append({"images": [], "caption": []})
        elif tag == "figcaption":
            self._in_caption += 1
        elif tag == "img":
            self._image(dict(attrs))
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
        if tag in ("article", "main") and self._main_depth:
            self._main_depth -= 1
        elif tag == "figure" and self._figures:
            figure = self._figures.pop()
            caption = " ".join("".join(figure["caption"]).split())
            for image in figure["images"]:
                image["caption"] = caption
        elif tag == "figcaption" and self._in_caption:
            self._in_caption -= 1
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
        if self._skip_stack:
            return
        if self._in_caption and self._figures:
            self._figures[-1]["caption"].append(data)
        if not self._block_stack:
            return
        current = self._block_stack[-1]
        current["chars"].append(data)
        if self._in_a:
            current["link_chars"] += len(data.strip())


    def _meta(self, attrs: Dict[str, Any]) -> None:
        key = str(attrs.get("property") or attrs.get("name") or "").strip().casefold()
        value = str(attrs.get("content") or "").strip()
        if key in ("og:image", "og:site_name", "twitter:image") and value:
            self.meta.setdefault(key, value)

    def _image(self, attrs: Dict[str, Any]) -> None:
        image = {
            "src": str(attrs.get("src") or attrs.get("data-src") or "").strip(),
            "srcset": str(attrs.get("srcset") or attrs.get("data-srcset") or "").strip(),
            "alt": " ".join(str(attrs.get("alt") or "").split()),
            "width": _px(attrs.get("width")),
            "height": _px(attrs.get("height")),
            "in_figure": bool(self._figures),
            "in_main": bool(self._main_depth),
            "caption": "",
        }
        self.images.append(image)
        if self._figures:
            self._figures[-1]["images"].append(image)


def _px(value: Any) -> Optional[int]:
    """``width="300"`` / ``"16px"`` → 300 / 16；百分比、空值 → None（不知道就不据此过滤）。"""
    match = re.match(r"\s*(\d+)(?:px)?\s*$", str(value or ""))
    return int(match.group(1)) if match else None


# -------------------------------------------------------------------------
# 正文配图挑选
# -------------------------------------------------------------------------
#: src / alt 命中即当装饰图：站点 logo、作者头像（含 X 的 profile_images、Ars 的「Photo of 作者」）、
#: 图标、表情、追踪像素、加载动画、推荐位缩略图（Ars 的「Listing image for … Most Read」）、Substack 的 40px 头像裁切
_IMAGE_NOISE_RE = re.compile(
    r"logo|avatar|icon|favicon|sprite|badge|emoji|headshot|gravatar|spacer|pixel|tracking|profile_images|"
    r"placeholder|loader|loading|listing image|most read|^photo of |/nav/|/team/|/authors?/|w_40|h_40|\b40x40\b",
    re.I,
)
#: 声明尺寸小于此的不要（图标、按钮）；没声明尺寸的不据此过滤
MIN_IMAGE_SIDE = 200
#: srcset 里挑最接近这个宽度的那一档：够清楚，又不至于下一张 2560px 的原图
TARGET_IMAGE_WIDTH = 1200
MAX_PICKED_IMAGES = 6


def _best_src(image: Dict[str, Any], base_url: str) -> str:
    best, best_gap = "", None
    # 候选之间用「逗号 + 空白」分隔：Substack 的图片地址本身就带逗号（w_424,c_limit,f_auto），按裸逗号切会切碎地址
    for part in re.split(r",\s+", image.get("srcset", "").strip()):
        bits = part.strip().split()
        if len(bits) != 2 or not bits[1].endswith("w"):
            continue
        try:
            width = int(bits[1][:-1])
        except ValueError:
            continue
        gap = abs(width - TARGET_IMAGE_WIDTH) + (1000 if width > 1600 else 0)
        if best_gap is None or gap < best_gap:
            best, best_gap = bits[0], gap
    src = best or image.get("src") or ""
    src = html_lib.unescape(src.strip())
    if not src or src.startswith(("data:", "blob:")):
        return ""
    return urljoin(base_url, src) if base_url else src


def pick_images(
    images: List[Dict[str, Any]],
    base_url: str = "",
    site_name: str = "",
    limit: int = MAX_PICKED_IMAGES,
) -> List[Dict[str, str]]:
    """从正文图里挑有信息量的：去装饰图、去小图、去 SVG；页面有 ``<article>``/``<main>`` 就只要里面的。

    正文容器外的图是刊物 logo、订阅框插图（Latent Space 顶部两张「Latent.Space」）；容器里
    不在 ``<figure>`` 里的图照收——Substack 把推文截图直接放在正文段落间，那往往是全文最有料的一张。
    返回 ``[{"src", "alt", "caption"}]``，保持原文顺序、按 src 去重。
    """
    site = site_name.strip().casefold()
    passing: List[Dict[str, Any]] = []
    for image in images:
        src = _best_src(image, base_url)
        if not src or not src.startswith(("http://", "https://")):
            continue
        path = src.split("?", 1)[0].casefold()
        # SVG 多是图标；GIF 多是加载动画、表情——正文里的图表截图几乎不会是这两种
        if path.endswith((".svg", ".gif")) or "format=svg" in src.casefold():
            continue
        alt = image.get("alt") or ""
        if _IMAGE_NOISE_RE.search(src) or _IMAGE_NOISE_RE.search(alt):
            continue
        if site and alt.casefold() == site:
            continue
        sides = [v for v in (image.get("width"), image.get("height")) if v is not None]
        if sides and min(sides) < MIN_IMAGE_SIDE:
            continue
        passing.append(dict(image, src=src))
    if any(image["in_main"] for image in passing):
        passing = [image for image in passing if image["in_main"]]
    out: List[Dict[str, str]] = []
    seen = set()
    for image in passing:
        if image["src"] in seen:
            continue
        seen.add(image["src"])
        out.append({"src": image["src"], "alt": image.get("alt") or "", "caption": image.get("caption") or ""})
        if len(out) >= limit:
            break
    return out


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


def _empty() -> Dict[str, Any]:
    return {"paragraphs": [], "text": "", "lead": "", "char_count": 0, "images": [], "og_image": ""}


def extract_article(
    html_text: str,
    max_paragraphs: int = DEFAULT_MAX_PARAGRAPHS,
    base_url: str = "",
) -> Dict[str, Any]:
    """从单篇文章页 HTML 抽正文与正文配图；纯函数，不出网。

    返回 ``{"paragraphs": [str], "text": str, "lead": str, "char_count": int,
    "images": [{"src", "alt", "caption"}], "og_image": str}``。
    ``lead`` 是首个正文段落（可直接当摘要用），``text`` 是段落用换行拼接的全文；
    ``images`` 已经过 :func:`pick_images` 挑选，相对地址按 ``base_url`` 补全。
    解析失败/无正文一律返回空结构，不抛异常。
    """
    if not html_text or not str(html_text).strip():
        return _empty()

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
    og_image = parser.meta.get("og:image") or parser.meta.get("twitter:image") or ""
    return {
        "paragraphs": paragraphs,
        "text": full_text,
        "lead": lead,
        "char_count": len(full_text),
        "images": pick_images(parser.images, base_url, parser.meta.get("og:site_name", "")),
        "og_image": urljoin(base_url, html_lib.unescape(og_image)) if og_image and base_url else og_image,
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
        return _empty()
    base = str(getattr(resp, "url", "") or url)
    return extract_article(_decoded_text(resp), max_paragraphs=max_paragraphs, base_url=base)


def _decoded_text(resp: Any) -> str:
    """响应头没声明 charset 时 requests 按 ISO-8859-1 解，UTF-8 页面就成了「â」乱码
    （epoch.ai 就是这样）。没声明时先按 UTF-8 解，解不开再退回 requests 的猜测。"""
    headers = getattr(resp, "headers", None) or {}
    declared = "charset=" in str(headers.get("content-type", "")).lower()
    content = getattr(resp, "content", None)
    if not declared and isinstance(content, bytes):
        try:
            return content.decode("utf-8")
        except UnicodeDecodeError:
            pass
    return getattr(resp, "text", "") or ""
