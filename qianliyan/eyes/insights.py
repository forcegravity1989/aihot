"""eyes/insights.py —— 洞眼 insights：``claude-code-insights`` 仓库插件排行（业界资产）。

v0.3 改指真实仓 ``zhoux77899/claude-code-insights``，消费 ``plugins/plugins-daily-insight.md``
（7KB Markdown 榜单表，**严禁整取 31MB/75MB 的 history/repos.json**）。按 data_path 后缀分派：
``.md`` → :func:`parse_daily`（Markdown 表格）；其余（``.json``）→ :func:`parse_payload`
（向后兼容旧 JSON 排行）。

``QLY_INSIGHTS_PREFER_RAW=1`` 时直接走 raw.githubusercontent 拉取 cfg 指定的 data 文件；否则
``git clone``/``pull`` 到 ``$QLY_DATA_DIR/repos/claude-code-insights`` 本地读取（clone 失败自动
回退 raw）。离线模式下（``QLY_OFFLINE=1``）直接跳过 clone（避免子进程真实出网），走 raw 分支
自然触发 ``engine.http.OfflineError``。
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
from typing import Any, Dict, List, Optional

from ..core import paths, schema
from ..engine import http

logger = logging.getLogger(__name__)

DEFAULT_REPO = "zhoux77899/claude-code-insights"
DEFAULT_BRANCH = "main"
DEFAULT_DATA_PATHS = ["plugins/plugins-daily-insight.md"]
DEFAULT_WEIGHT = 0.6
DEFAULT_SOURCE_NAME = "Claude Code Insights"
DAILY_SOURCE_NAME = "Claude Code Insights"
RAW_URL_TMPL = "https://raw.githubusercontent.com/{repo}/{branch}/{path}"
REPO_DIR_PARTS = ("repos", "claude-code-insights")
GIT_TIMEOUT = 30

#: daily-insight.md 表格行：``| 3 | [repo](url) | ⭐206576 | 🍴21092 | 2026-04-20 | desc |``
_DAILY_ROW_RE = re.compile(
    r"^\|\s*(\d+)\s*\|\s*\[([^\]]+)\]\(([^)]+)\)\s*\|"
    r"\s*[^\d|]*([\d,]+)\s*\|\s*[^\d|]*([\d,]+)\s*\|\s*([^|]*)\|\s*([^|]*)\|\s*$"
)


def _extract_list(payload: Any) -> List[Any]:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("data", "items", "list", "plugins"):
            value = payload.get(key)
            if isinstance(value, list):
                return value
    return []


def parse_payload(payload: Any, cfg: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """解析插件排行 JSON（尽力而为：list 内 dict 找 ``name``、``installs|count|downloads``、``description``）。

    排名（原始顺序，1 起）存 ``metrics.rank``（若条目自带 ``rank`` 则优先用它）；纯函数，不出网。
    """
    cfg = cfg or {}
    weight = float(cfg.get("weight", DEFAULT_WEIGHT))
    source_name = cfg.get("source") or DEFAULT_SOURCE_NAME
    repo = cfg.get("repo") or DEFAULT_REPO
    fallback_url = "https://github.com/{0}".format(repo)

    items: List[Dict[str, Any]] = []
    for idx, entry in enumerate(_extract_list(payload), start=1):
        if not isinstance(entry, dict):
            continue
        name = entry.get("name") or entry.get("title")
        if not name:
            continue
        installs = entry.get("installs")
        if installs is None:
            installs = entry.get("count")
        if installs is None:
            installs = entry.get("downloads")
        description = entry.get("description") or entry.get("summary") or ""
        url = entry.get("url") or entry.get("link") or fallback_url
        rank = entry.get("rank") or idx

        metrics: Dict[str, Any] = {"rank": rank}
        if installs is not None:
            metrics["installs"] = installs

        items.append(schema.make_item(
            title=str(name),
            url=str(url),
            source=source_name,
            source_kind="insights",
            backend="raw_json",
            weight=weight,
            date=entry.get("date"),
            summary=str(description),
            tags=["plugins", "insights"],
            metrics=metrics,
        ))
    return items


def _parse_int(raw: Any) -> Optional[int]:
    try:
        return int(str(raw).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def parse_daily(md_text: str, cfg: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """解析 ``plugins-daily-insight.md`` 的 Top 榜 Markdown 表格 → 每行一条 item（纯函数）。

    行形如 ``| # | [repo](url) | ⭐stars | 🍴forks | date | desc |``；表头/分隔行自动跳过。
    """
    cfg = cfg or {}
    weight = float(cfg.get("weight", DEFAULT_WEIGHT))
    source_name = cfg.get("source") or DAILY_SOURCE_NAME

    items: List[Dict[str, Any]] = []
    seen = set()
    for line in (md_text or "").splitlines():
        match = _DAILY_ROW_RE.match(line)
        if not match:
            continue
        rank = _parse_int(match.group(1))
        name = match.group(2).strip()
        url = match.group(3).strip()
        stars = _parse_int(match.group(4))
        forks = _parse_int(match.group(5))
        date = match.group(6).strip()
        desc = match.group(7).strip()
        if not name or not url:
            continue
        if url in seen:
            continue
        seen.add(url)

        metrics: Dict[str, Any] = {}
        if stars is not None:
            metrics["stars"] = stars
        if forks is not None:
            metrics["forks"] = forks
        if rank is not None:
            metrics["rank"] = rank

        extra: Dict[str, Any] = {"format": "repo", "subject": "plugin-ecosystem"}
        if rank is not None:
            extra["rank"] = rank

        items.append(schema.make_item(
            title=name,
            url=url,
            source=source_name,
            source_kind="insights",
            backend="git",
            weight=weight,
            date=date or None,
            summary=desc,
            tags=["plugins", "skills", "insights", "trending"],
            metrics=metrics,
            extra=extra,
        ))
    return items


def _raw_url(repo: str, branch: str, data_path: str) -> str:
    return RAW_URL_TMPL.format(repo=repo, branch=branch, path=data_path)


def _is_markdown(data_path: str) -> bool:
    return str(data_path).lower().endswith((".md", ".markdown"))


def _fetch_raw(repo: str, branch: str, data_path: str) -> Any:
    """取 raw 内容：``.md`` 走 get_text（带量级保护），否则 get_json。"""
    url = _raw_url(repo, branch, data_path)
    if _is_markdown(data_path):
        return http.get_text(url, max_bytes=http.DEFAULT_MAX_BYTES)
    return http.get_json(url)


def _clone_or_pull(repo: str, branch: str):
    repo_dir = paths.data_path(*REPO_DIR_PARTS)
    if (repo_dir / ".git").is_dir():
        cmd = ["git", "-C", str(repo_dir), "pull", "--ff-only"]
    else:
        cmd = [
            "git", "clone", "--depth", "1", "--branch", branch,
            "https://github.com/{0}.git".format(repo), str(repo_dir),
        ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=GIT_TIMEOUT)
    if result.returncode != 0:
        raise RuntimeError("git 操作失败: {0}".format((result.stderr or "").strip()[:300]))
    return repo_dir


def _fetch_via_clone(repo: str, branch: str, data_path: str) -> Any:
    if os.environ.get("QLY_OFFLINE") == "1":
        # 离线模式下不许尝试真实 git 子进程出网（子进程网络不受 engine.http 的 OfflineError 网关约束）
        raise http.OfflineError("QLY_OFFLINE=1，跳过 git clone")
    repo_dir = _clone_or_pull(repo, branch)
    file_path = repo_dir / data_path
    if not file_path.is_file():
        raise FileNotFoundError(str(file_path))
    if _is_markdown(data_path):
        return file_path.read_text(encoding="utf-8")
    with file_path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _parse_data(data_path: str, payload: Any, cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    """按 data_path 后缀分派解析器：``.md`` → parse_daily，其余 → parse_payload。"""
    if _is_markdown(data_path):
        return parse_daily(payload, cfg)
    return parse_payload(payload, cfg)


def fetch(cfg: Optional[Dict[str, Any]] = None, since: Any = None) -> List[Dict[str, Any]]:
    """逐 data_path 取插件排行数据；clone 失败自动回退 raw；单 data_path 彻底失败则跳过、记 warning。"""
    cfg = cfg or {}
    repo = cfg.get("repo") or DEFAULT_REPO
    branch = cfg.get("branch") or DEFAULT_BRANCH
    data_paths = cfg.get("data_paths") or DEFAULT_DATA_PATHS
    prefer_raw = os.environ.get("QLY_INSIGHTS_PREFER_RAW") == "1"

    items: List[Dict[str, Any]] = []
    for data_path in data_paths:
        if prefer_raw:
            try:
                payload = _fetch_raw(repo, branch, data_path)
            except Exception as exc:  # noqa: BLE001
                logger.warning("insights raw 抓取失败 (%s): %s", data_path, exc)
                continue
        else:
            try:
                payload = _fetch_via_clone(repo, branch, data_path)
            except Exception as exc:  # noqa: BLE001
                logger.warning("insights clone 失败，回退 raw (%s): %s", data_path, exc)
                try:
                    payload = _fetch_raw(repo, branch, data_path)
                except Exception as exc2:  # noqa: BLE001
                    logger.warning("insights raw 回退也失败 (%s): %s", data_path, exc2)
                    continue
        items.extend(_parse_data(data_path, payload, cfg))
    return items
