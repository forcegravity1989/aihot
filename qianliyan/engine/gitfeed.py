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
    match = _VERSION_RE.search(title or "")
    return match.group(0) if match else ""


def releases(owner_repo: str, timeout: int = 15) -> List[Dict[str, Any]]:
    """每条补 metrics.version 与 release tag，配合 item_signature 的 release 特例。"""
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
    url = TAGS_URL_TMPL.format(owner_repo=owner_repo)
    resp = http.get(url, timeout=timeout)
    return rss.parse(resp.text)


def commits(owner_repo: str, branch: str = "main", timeout: int = 15) -> List[Dict[str, Any]]:
    url = COMMITS_URL_TMPL.format(owner_repo=owner_repo, branch=branch)
    resp = http.get(url, timeout=timeout)
    return rss.parse(resp.text)
