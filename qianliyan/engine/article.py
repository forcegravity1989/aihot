from __future__ import annotations

import html as html_lib
import logging
import re
from html.parser import HTMLParser
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin

from . import http

logger = logging.getLogger(__name__)

_SKIP_CONTAINERS = frozenset((
    "script", "style", "nav", "header", "footer", "aside", "form",
    "noscript", "svg", "select", "button", "iframe", "template",
))
_BLOCK_TAGS = frozenset(("p", "h1", "h2", "h3", "h4", "li", "blockquote"))
_HEADING_TAGS = frozenset(("h1", "h2", "h3", "h4"))

# 正文段落总会有句末标点，导航词堆叠没有
_SENTENCE_PUNCT_RE = re.compile(r"[.!?。！？；;]")
# 分享条那类块：URL 自带的点号会骗过句末标点判据
_BARE_URL_RE = re.compile(r"https?://\S+")
MIN_BLOCK_LEN = 40
MAX_LINK_DENSITY = 0.5
NAV_SOUP_MIN_LEN = 100  # 超过此长度且无句末标点 → 导航词堆叠
DEFAULT_MAX_PARAGRAPHS = 60
DEFAULT_TIMEOUT = 20
# 防个别站点塞进整站数据
DEFAULT_MAX_BYTES = 3 * 1024 * 1024


class _ArticleTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: List[Dict[str, Any]] = []
        # 按标签名栈而非深度计数：未闭合的 <nav> 会让计数归不了零，吞掉整段正文
        self._skip_stack: List[str] = []
        self._block_stack: List[Dict[str, Any]] = []
        self._in_a = 0
        self.images: List[Dict[str, Any]] = []
        self.meta: Dict[str, str] = {}
        self._figures: List[Dict[str, Any]] = []
        self._in_caption = 0
        self._main_depth = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag == "meta":
            self._meta(dict(attrs))
            return
        if tag in _SKIP_CONTAINERS:
            self._skip_stack.append(tag)
            return
        if self._skip_stack:
            # 交互图表的静态降级图放在 <noscript> 里（如 Epoch），要的正是它；noscript 里的文字仍不收
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
            # 块可嵌套（li 里套 p），用栈把文本记到最内层
            self._block_stack.append({"tag": tag, "chars": [], "link_chars": 0})

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_CONTAINERS:
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
    match = re.match(r"\s*(\d+)(?:px)?\s*$", str(value or ""))
    return int(match.group(1)) if match else None


# src / alt 命中即当装饰图：logo、作者头像、图标、表情、追踪像素、加载动画、推荐位缩略图
_IMAGE_NOISE_RE = re.compile(
    r"logo|avatar|icon|favicon|sprite|badge|emoji|headshot|gravatar|spacer|pixel|tracking|profile_images|"
    r"placeholder|loader|loading|listing image|most read|^photo of |/nav/|/team/|/authors?/|w_40|h_40|\b40x40\b",
    re.I,
)
# 没声明尺寸的不据此过滤
MIN_IMAGE_SIDE = 200
# 够清楚，又不至于下一张 2560px 的原图
TARGET_IMAGE_WIDTH = 1200
MAX_PICKED_IMAGES = 6


def _best_src(image: Dict[str, Any], base_url: str) -> str:
    best, best_gap = "", None
    # Substack 的图片地址本身带逗号（w_424,c_limit），按裸逗号切会切碎地址
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
    """页面有 <article>/<main> 就只要里面的：容器外是刊物 logo、订阅框插图。"""
    site = site_name.strip().casefold()
    passing: List[Dict[str, Any]] = []
    for image in images:
        src = _best_src(image, base_url)
        if not src or not src.startswith(("http://", "https://")):
            continue
        path = src.split("?", 1)[0].casefold()
        # SVG 多是图标；GIF 多是加载动画、表情
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
    length = len(text)
    if length < MIN_BLOCK_LEN:
        return True
    if length and link_chars / float(length) > MAX_LINK_DENSITY:
        return True
    if length >= NAV_SOUP_MIN_LEN and not _SENTENCE_PUNCT_RE.search(text):
        return True
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
            # 小标题往往很短，放宽长度限制但仍要过链接密度
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
    """网络失败返回空结构：深读要能退回摘要，不能挂掉整轮 finalize。"""
    try:
        resp = http.get(url, timeout=timeout, max_bytes=DEFAULT_MAX_BYTES)
    except Exception as exc:  # noqa: BLE001 - 含 OfflineError；深读取正文属尽力而为
        logger.warning("fetch_article 抓取失败 (%s): %s", url, exc)
        return _empty()
    base = str(getattr(resp, "url", "") or url)
    return extract_article(_decoded_text(resp), max_paragraphs=max_paragraphs, base_url=base)


def _decoded_text(resp: Any) -> str:
    """响应没声明 charset 时 requests 按 ISO-8859-1 解，UTF-8 页面会乱码（如 epoch.ai）。"""
    headers = getattr(resp, "headers", None) or {}
    declared = "charset=" in str(headers.get("content-type", "")).lower()
    content = getattr(resp, "content", None)
    if not declared and isinstance(content, bytes):
        try:
            return content.decode("utf-8")
        except UnicodeDecodeError:
            pass
    return getattr(resp, "text", "") or ""
