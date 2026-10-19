from __future__ import annotations

import logging
import os
from typing import Any, Optional, Tuple

logger = logging.getLogger(__name__)

DEFAULT_CDP_URL = "http://127.0.0.1:9333"
DEFAULT_PROXY_URL = "http://127.0.0.1:3456"
PROXY_FETCH_TIMEOUT = 20


class CDPUnavailable(Exception):
    pass


def connect(cdp_url: str = DEFAULT_CDP_URL) -> Tuple[Any, Any]:
    """调用方负责 browser.close() + playwright_ctx.stop()。"""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise CDPUnavailable(
            "缺少 playwright 依赖，请 `pip install qianliyan[browser]` 并 `playwright install`"
        ) from exc

    playwright_ctx = None
    try:
        playwright_ctx = sync_playwright().start()
        browser = playwright_ctx.chromium.connect_over_cdp(cdp_url)
        return playwright_ctx, browser
    except Exception as exc:  # noqa: BLE001 - playwright 异常族统一归一为 CDPUnavailable
        if playwright_ctx is not None:
            try:
                playwright_ctx.stop()
            except Exception:  # noqa: BLE001
                pass
        raise CDPUnavailable("连接 CDP 失败 ({0}): {1}".format(cdp_url, exc)) from exc


def fetch_via_proxy(url: str, timeout: int = PROXY_FETCH_TIMEOUT) -> Any:
    proxy_base = (
        os.environ.get("WEB_ACCESS_PROXY")
        or os.environ.get("QLY_CDP_PROXY")
        or DEFAULT_PROXY_URL
    )
    try:
        import requests
    except ImportError as exc:  # pragma: no cover - 依赖缺失属部署问题
        raise CDPUnavailable("缺少 requests 依赖") from exc

    try:
        resp = requests.post(
            proxy_base.rstrip("/") + "/fetch", json={"url": url}, timeout=timeout
        )
        resp.raise_for_status()
        try:
            return resp.json()
        except ValueError:
            return {"html": resp.text}
    except Exception as exc:  # noqa: BLE001
        raise CDPUnavailable("代理回退抓取失败 ({0}): {1}".format(url, exc)) from exc
