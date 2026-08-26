"""eyes/company.py —— 内眼 company：心声社区 + 稼先 两个内网信源，CDP 优先、代理兜底。

所有选择器/URL 收敛在本文件头部常量块——内网联调只改这里。降级链：CDP 抓取失败 →
``engine.cdp.fetch_via_proxy`` 兜底（走通用链接启发式，因为拿到的是原始 HTML 而非可查询 DOM）→
单信源两条路都失败则跳过该信源、记 warning；若两个信源全灭，最终把最后一次异常向上抛出，
由 ``cli.sync`` 统一记账（单眼故障不外溢）。不要求真实联调：本地/CI 环境下 playwright 多半缺失、
代理多半连不上，属预期内的“全灭抛异常”路径。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from ..core import schema
from ..engine import cdp, html_page

logger = logging.getLogger(__name__)

# ---- 模块头部常量块：内网联调（选择器/URL）只改这里 ------------------------
XINSHENG_LIST_URL = "https://xinsheng.company.internal/c/ai-platform"
XINSHENG_ITEM_SELECTOR = ".topic-list .topic-item"
XINSHENG_TITLE_SELECTOR = ".topic-title"
XINSHENG_LINK_SELECTOR = "a.topic-title-link"

JIAXIAN_LIST_URL = "https://jiaxian.company.internal/#/feed/ai"
JIAXIAN_ITEM_SELECTOR = ".feed-card"
JIAXIAN_TITLE_SELECTOR = ".feed-card__title"
JIAXIAN_LINK_SELECTOR = "a.feed-card__link"

CDP_NAV_TIMEOUT_MS = 15000
CDP_WAIT_TIMEOUT_MS = 15000
DEFAULT_WEIGHT = 0.85
TAGS = ("company", "internal")

INTERNAL_SOURCES: List[Dict[str, str]] = [
    {
        "name": "心声社区",
        "url": XINSHENG_LIST_URL,
        "item_selector": XINSHENG_ITEM_SELECTOR,
        "title_selector": XINSHENG_TITLE_SELECTOR,
        "link_selector": XINSHENG_LINK_SELECTOR,
    },
    {
        "name": "稼先",
        "url": JIAXIAN_LIST_URL,
        "item_selector": JIAXIAN_ITEM_SELECTOR,
        "title_selector": JIAXIAN_TITLE_SELECTOR,
        "link_selector": JIAXIAN_LINK_SELECTOR,
    },
]
# ---------------------------------------------------------------------------


def parse_payload(entries: Any, cfg: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """把已抓取的标准化条目（``{title, url, date?, summary?}``）转成标准 item；纯函数，不出网。"""
    cfg = cfg or {}
    source_name = cfg.get("source") or "内网"
    weight = float(cfg.get("weight", DEFAULT_WEIGHT))

    items: List[Dict[str, Any]] = []
    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        title = (entry.get("title") or "").strip()
        url = entry.get("url") or ""
        if not title or not url:
            continue
        items.append(schema.make_item(
            title=title,
            url=str(url),
            source=source_name,
            source_kind="company",
            backend="cdp",
            weight=weight,
            date=entry.get("date"),
            summary=entry.get("summary", ""),
            tags=list(TAGS),
        ))
    return items


def _scrape_via_cdp(source: Dict[str, str]) -> List[Dict[str, Any]]:
    """打开列表页、等待选择器出现，用选择器抽标题与链接。调用方负责异常处理与资源释放已在此完成。"""
    playwright_ctx = browser = None
    try:
        playwright_ctx, browser = cdp.connect()
        page = browser.new_page()
        page.goto(source["url"], timeout=CDP_NAV_TIMEOUT_MS)
        page.wait_for_selector(source["item_selector"], timeout=CDP_WAIT_TIMEOUT_MS)
        entries: List[Dict[str, Any]] = []
        for node in page.query_selector_all(source["item_selector"]):
            title_el = node.query_selector(source["title_selector"])
            link_el = node.query_selector(source["link_selector"])
            title = (title_el.inner_text() if title_el else "").strip()
            url = (link_el.get_attribute("href") if link_el else "") or ""
            if title and url:
                entries.append({"title": title, "url": url})
        return entries
    finally:
        try:
            if browser is not None:
                browser.close()
        finally:
            if playwright_ctx is not None:
                playwright_ctx.stop()


def _scrape_via_proxy(source: Dict[str, str]) -> List[Dict[str, Any]]:
    """CDP 不可用时的兜底：走内网代理拉原始 HTML，用通用链接启发式抽取（拿不到 DOM，无法用选择器）。"""
    payload = cdp.fetch_via_proxy(source["url"])
    html_text = ""
    if isinstance(payload, dict):
        html_text = str(payload.get("html") or payload.get("content") or "")
    elif isinstance(payload, str):
        html_text = payload
    if not html_text:
        raise cdp.CDPUnavailable("代理未返回可用内容: {0}".format(source["url"]))
    return html_page.extract_links(html_text, base_url=source["url"], limit=20)


def fetch(cfg: Optional[Dict[str, Any]] = None, since: Any = None) -> List[Dict[str, Any]]:
    """心声 + 稼先：CDP 优先、代理兜底；单信源双降级失败则跳过，两个信源全灭则抛出最后一次异常。"""
    cfg = cfg or {}
    weight = float(cfg.get("weight", DEFAULT_WEIGHT))

    items: List[Dict[str, Any]] = []
    last_error: Optional[Exception] = None
    any_ok = False

    for source in INTERNAL_SOURCES:
        try:
            entries = _scrape_via_cdp(source)
        except Exception as exc:  # noqa: BLE001 - CDP 失败走代理兜底
            logger.warning("company CDP 抓取失败 (%s): %s，尝试代理回退", source["name"], exc)
            try:
                entries = _scrape_via_proxy(source)
            except Exception as exc2:  # noqa: BLE001 - 双降级失败，跳过该信源
                logger.warning("company 代理回退也失败 (%s): %s", source["name"], exc2)
                last_error = exc2
                continue
        any_ok = True
        items.extend(parse_payload(entries, {"source": source["name"], "weight": weight}))

    if not any_ok and last_error is not None:
        raise last_error
    return items
