"""core/og_image.py —— 为头部条目补 Open Graph 封面图（尽力而为，永不影响主链路）。

只对 hotness 前 ``top_n`` 且尚无 ``extra.og_image`` 的条目发一次 GET，正则抽
``<meta property="og:image" content="...">``；结果（含失败标记 ``""``）写入
``cache/og_image.json`` 以免重抓。``offline=True`` 或 env ``QLY_OFFLINE=1`` 时直接返回。
**一切异常静默吞掉**（spec §4），这是全项目唯一豁免「异常必须 warning」的模块。
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any, Dict, List, Optional, Sequence

from . import paths, storage

try:  # requests 属核心依赖，缺失时本模块降级为 no-op 而非 ImportError
    import requests
except ImportError:  # pragma: no cover - 依赖缺失属部署问题
    requests = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

DEFAULT_TOP_N = 30
REQUEST_TIMEOUT = 8
BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
CACHE_RELPATH = ("cache", "og_image.json")

_OG_PROPERTY_FIRST = re.compile(
    r"""<meta[^>]+(?:property|name)\s*=\s*["']og:image(?::url)?["'][^>]*"""
    r"""content\s*=\s*["']([^"']+)["']""",
    re.IGNORECASE,
)
_OG_CONTENT_FIRST = re.compile(
    r"""<meta[^>]+content\s*=\s*["']([^"']+)["'][^>]*"""
    r"""(?:property|name)\s*=\s*["']og:image(?::url)?["']""",
    re.IGNORECASE,
)


def extract_og_image(html: str) -> str:
    """从 HTML 文本抽 og:image 地址；抽不到返回空串。"""
    if not html:
        return ""
    for pattern in (_OG_PROPERTY_FIRST, _OG_CONTENT_FIRST):
        match = pattern.search(html)
        if match:
            return (match.group(1) or "").strip()
    return ""


def _is_offline(offline: bool) -> bool:
    return bool(offline) or os.environ.get("QLY_OFFLINE") == "1"


def enrich(
    items: Sequence[Dict[str, Any]],
    top_n: int = DEFAULT_TOP_N,
    cache_path: Optional[Any] = None,
    offline: bool = False,
) -> None:
    """原地为 hotness 前 ``top_n`` 的条目补 ``extra.og_image``（失败即跳过）。"""
    if _is_offline(offline):
        logger.debug("离线模式，跳过 og_image 补全")
        return
    if requests is None or not items:
        return

    try:
        if cache_path is None:
            cache_path = paths.data_path(*CACHE_RELPATH)
        cache = storage.read_json(cache_path, {}) or {}
        if not isinstance(cache, dict):
            cache = {}

        ranked = sorted(
            [it for it in items if isinstance(it, dict)],
            key=lambda it: it.get("hotness") or 0.0,
            reverse=True,
        )[: max(0, int(top_n))]

        dirty = False
        for item in ranked:
            extra = item.get("extra")
            if not isinstance(extra, dict):
                extra = {}
                item["extra"] = extra
            if extra.get("og_image"):
                continue

            sig = item.get("sig") or ""
            if sig and sig in cache:
                cached = cache.get(sig) or ""
                if cached:
                    extra["og_image"] = cached
                continue

            url = item.get("url") or ""
            image = ""
            if url:
                image = _fetch_og_image(url)
            if image:
                extra["og_image"] = image
            if sig:
                cache[sig] = image  # 含失败标记 ""，避免重抓
                dirty = True

        if dirty:
            storage.write_json(cache_path, cache)
    except Exception:  # noqa: BLE001 - 封面图永远不许拖垮主链路
        logger.debug("og_image.enrich 静默失败", exc_info=True)


def _fetch_og_image(url: str) -> str:
    try:
        resp = requests.get(
            url,
            timeout=REQUEST_TIMEOUT,
            headers={"User-Agent": BROWSER_UA, "Accept": "text/html,*/*"},
        )
        if resp.status_code >= 400:
            return ""
        return extract_og_image(resp.text or "")
    except Exception:  # noqa: BLE001
        return ""
