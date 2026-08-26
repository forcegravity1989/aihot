"""engine/gitfeed.py —— GitHub 仓库动态（releases/tags/commits），**不 clone**。

一律走 GitHub 自带的 ``.atom`` feed 端点，复用 ``rss.parse``。``releases()`` 额外为每条
从标题抽版本号写入 ``metrics.version`` 并加 ``"release"`` 标签（防同一版本号在双仓被误合并/漏合并，
呼应 ``core.utils.item_signature`` 的 release 特例）。
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List

from . import http, rss

logger = logging.getLogger(__name__)

RELEASES_URL_TMPL = "https://github.com/{owner_repo}/releases.atom"
TAGS_URL_TMPL = "https://github.com/{owner_repo}/tags.atom"
COMMITS_URL_TMPL = "https://github.com/{owner_repo}/commits/{branch}.atom"

_VERSION_RE = re.compile(r"v?\d+[\w.\-]*")


def extract_version(title: str) -> str:
    """从 release 标题里抽版本号（正则 ``v?\\d+[\\w.\\-]*``），抽不到返回空串。"""
    match = _VERSION_RE.search(title or "")
    return match.group(0) if match else ""


def releases(owner_repo: str, timeout: int = 15) -> List[Dict[str, Any]]:
    """拉取 ``{owner_repo}/releases.atom``；每条补 ``metrics.version`` 与 ``"release"`` tag。"""
    url = RELEASES_URL_TMPL.format(owner_repo=owner_repo)
    resp = http.get(url, timeout=timeout)
    entries = rss.parse(resp.text)
    out: List[Dict[str, Any]] = []
    for entry in entries:
        version = extract_version(entry.get("title") or "")
        row = dict(entry)
        row["tags"] = ["release"]
        row["metrics"] = {"version": version} if version else {}
        out.append(row)
    return out


def tags(owner_repo: str, timeout: int = 15) -> List[Dict[str, Any]]:
    """拉取 ``{owner_repo}/tags.atom``（原样返回 rss.parse 结果，不额外加工）。"""
    url = TAGS_URL_TMPL.format(owner_repo=owner_repo)
    resp = http.get(url, timeout=timeout)
    return rss.parse(resp.text)


def commits(owner_repo: str, branch: str = "main", timeout: int = 15) -> List[Dict[str, Any]]:
    """拉取 ``{owner_repo}/commits/{branch}.atom``。"""
    url = COMMITS_URL_TMPL.format(owner_repo=owner_repo, branch=branch)
    resp = http.get(url, timeout=timeout)
    return rss.parse(resp.text)
