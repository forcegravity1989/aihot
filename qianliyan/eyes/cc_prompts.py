from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..core import schema
from ..engine import http

logger = logging.getLogger(__name__)

DEFAULT_REPO = "Piebald-AI/claude-code-system-prompts"
DEFAULT_BRANCH = "main"
DEFAULT_PATH = "CHANGELOG.md"
DEFAULT_WEIGHT = 0.9
SOURCE_NAME = "Claude Code 系统提示词"
SUBJECT = "claude-code-prompts"
RAW_URL_TMPL = "https://raw.githubusercontent.com/{repo}/{branch}/{path}"

# 版本块头：#### [2.1.245](commit_url) 或 # [2.1.242](...)
_VERSION_RE = re.compile(r"^#+\s*\[([0-9]+(?:\.[0-9]+)+)\]\(([^)]+)\)\s*$")
# token 增量：_+30,636 tokens_ / _-1,911 tokens_
_TOKEN_RE = re.compile(r"_([+-]?[\d,]+)\s+tokens_")
_BULLET_RE = re.compile(r"^\s*[-*]\s+(.*\S)\s*$")
_NEW_RE = re.compile(r"^\*\*NEW:\*\*\s*")
_REMOVED_RE = re.compile(r"^\*\*REMOVED:\*\*\s*")
# 以「空格 + em dash + 空格」分隔，desc 内部无空格的连字号不误切
_DASH_SPLIT_RE = re.compile(r"\s+—\s+")

SUMMARY_MAX_ENTRIES = 3
SUMMARY_MAX_LEN = 300


def _parse_token_delta(block_text: str) -> Optional[int]:
    match = _TOKEN_RE.search(block_text)
    if not match:
        return None
    raw = match.group(1).replace(",", "")
    try:
        return int(raw)
    except ValueError:
        return None


def _classify_entry(text: str) -> "tuple[str, str]":
    """kind ∈ new/removed/modified，rest 是去掉前缀后的正文。"""
    if _NEW_RE.match(text):
        return "new", _NEW_RE.sub("", text, count=1).strip()
    if _REMOVED_RE.match(text):
        return "removed", _REMOVED_RE.sub("", text, count=1).strip()
    return "modified", text.strip()


def _split_component(rest: str) -> "tuple[str, str, str]":
    """把 Component: Title — Desc 拆成 (component, title, desc)（尽力而为）。"""
    component = ""
    body = rest
    if ":" in rest:
        head, _, tail = rest.partition(":")
        component = head.strip()
        body = tail.strip()
    parts = _DASH_SPLIT_RE.split(body, maxsplit=1)
    title = parts[0].strip()
    desc = parts[1].strip() if len(parts) > 1 else ""
    return component, title, desc


def _parse_entry(text: str) -> Dict[str, str]:
    kind, rest = _classify_entry(text)
    component, title, desc = _split_component(rest)
    return {"kind": kind, "component": component, "title": title, "desc": desc}


def _iter_blocks(md_text: str) -> "List[tuple[str, str, List[str]]]":
    blocks: "List[tuple[str, str, List[str]]]" = []
    current: "Optional[tuple[str, str]]" = None
    buffer: List[str] = []
    for line in (md_text or "").splitlines():
        header = _VERSION_RE.match(line)
        if header:
            if current is not None:
                blocks.append((current[0], current[1], buffer))
            current = (header.group(1), header.group(2))
            buffer = []
        elif current is not None:
            buffer.append(line)
    if current is not None:
        blocks.append((current[0], current[1], buffer))
    return blocks


def _format_delta(token_delta: int) -> str:
    return "{0:+,}".format(int(token_delta))


def parse_changelog(md_text: str) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    for version, commit_url, lines in _iter_blocks(md_text):
        block_text = "\n".join(lines)
        token_delta = _parse_token_delta(block_text)

        entries: List[Dict[str, str]] = []
        for line in lines:
            bullet = _BULLET_RE.match(line)
            if bullet:
                entries.append(_parse_entry(bullet.group(1)))

        if not entries:
            continue

        delta = token_delta if token_delta is not None else 0
        title = "Claude Code {ver} 提示词变更 · {delta} tokens · {n} 项".format(
            ver=version, delta=_format_delta(delta), n=len(entries),
        )
        summary_bits = [e["title"] for e in entries[:SUMMARY_MAX_ENTRIES] if e["title"]]
        summary = "；".join(summary_bits)[:SUMMARY_MAX_LEN]

        items.append(schema.make_item(
            title=title,
            url=commit_url,
            source=SOURCE_NAME,
            source_kind="local",
            backend="git",
            weight=DEFAULT_WEIGHT,
            summary=summary,
            tags=["claude-code", "prompts", "changelog", "practices"],
            extra={
                "format": "changelog",
                "subject": SUBJECT,
                "version": version,
                "token_delta": delta,
                "changes": entries,
            },
        ))
    return items


def _load_text(cfg: Dict[str, Any]) -> str:
    local_path = cfg.get("local_path")
    if local_path:
        return Path(str(local_path)).read_text(encoding="utf-8")
    repo = cfg.get("repo") or DEFAULT_REPO
    branch = cfg.get("branch") or DEFAULT_BRANCH
    path = cfg.get("path") or DEFAULT_PATH
    url = RAW_URL_TMPL.format(repo=repo, branch=branch, path=path)
    return http.get_text(url, max_bytes=http.DEFAULT_MAX_BYTES)


def fetch(cfg: Optional[Dict[str, Any]] = None, since: Any = None) -> List[Dict[str, Any]]:
    cfg = cfg or {}
    text = _load_text(cfg)
    return parse_changelog(text)
