"""eyes/plugins_official.py —— 变更情报源：官方插件市场版本 bump。

消费 ``anthropics/claude-plugins-official`` 的 ``.claude-plugin/marketplace.json``
（``{plugins:[{name, description, ...}]}``）与 ``.github/bump-tracking.json``
（``{"releases-only":[name, ...]}``）。产出**官方插件变更 item**——近期 bump 的插件优先。

铁律：解析纯函数 ``parse(marketplace, bump)`` 与网络壳 ``fetch(cfg)`` 分离；离线/失败绝不外溢。
item 用合法 ``source_kind="local"`` / ``backend="git"``（原始类型 ``changelog`` 放 ``extra.format``）。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..core import schema
from ..engine import http

logger = logging.getLogger(__name__)

DEFAULT_REPO = "anthropics/claude-plugins-official"
DEFAULT_BRANCH = "main"
DEFAULT_MARKETPLACE_PATH = ".claude-plugin/marketplace.json"
DEFAULT_BUMP_PATH = ".github/bump-tracking.json"
DEFAULT_WEIGHT = 0.85
SOURCE_NAME = "Claude 官方插件市场"
SUBJECT = "plugins-official"
FALLBACK_URL = "https://github.com/anthropics/claude-plugins-official"
RAW_URL_TMPL = "https://raw.githubusercontent.com/{repo}/{branch}/{path}"

SUMMARY_MAX_LEN = 300


def _plugins_by_name(marketplace: Any) -> Dict[str, Dict[str, Any]]:
    plugins = {}
    entries = (marketplace or {}).get("plugins") if isinstance(marketplace, dict) else None
    for entry in entries or []:
        if isinstance(entry, dict) and entry.get("name"):
            plugins[str(entry["name"])] = entry
    return plugins


def _bumped_names(bump: Any) -> List[str]:
    if not isinstance(bump, dict):
        return []
    names = bump.get("releases-only") or bump.get("releases_only") or []
    return [str(n) for n in names if n]


def _plugin_ref(plugin: Dict[str, Any]) -> str:
    """从 ``source`` 抽版本标识（``ref`` 优先，否则短 ``sha``）；无则空串。"""
    source = plugin.get("source")
    if isinstance(source, dict):
        ref = source.get("ref")
        if ref:
            return str(ref)
        sha = source.get("sha")
        if sha:
            return str(sha)[:7]
    return ""


def parse(marketplace: Any, bump: Any, cfg: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """解析 marketplace + bump → 每个近期 bump 的插件一条变更 item（纯函数，不出网）。"""
    cfg = cfg or {}
    weight = float(cfg.get("weight", DEFAULT_WEIGHT))
    plugins = _plugins_by_name(marketplace)

    items: List[Dict[str, Any]] = []
    for name in _bumped_names(bump):
        plugin = plugins.get(name) or {}
        description = str(plugin.get("description") or "")
        homepage = plugin.get("homepage") or FALLBACK_URL
        category = plugin.get("category") or ""
        ref = _plugin_ref(plugin)

        title = "官方插件 {name} 版本更新".format(name=name)
        if ref:
            title += " · {ref}".format(ref=ref)

        extra: Dict[str, Any] = {
            "format": "changelog",
            "subject": SUBJECT,
            "plugin": name,
            "change_kind": "bump",
        }
        if category:
            extra["category"] = category
        if ref:
            extra["version"] = ref

        items.append(schema.make_item(
            title=title,
            url=str(homepage),
            source=SOURCE_NAME,
            source_kind="local",
            backend="git",
            weight=weight,
            summary=description[:SUMMARY_MAX_LEN],
            tags=["plugins", "official", "changelog"],
            extra=extra,
        ))
    return items


def _load_json(cfg: Dict[str, Any], local_key: str, path_key: str, default_path: str) -> Any:
    """取一个 JSON 文件：``cfg[local_key]`` 优先（离线/mock 用），否则走 engine.http。"""
    local_path = cfg.get(local_key)
    if local_path:
        with Path(str(local_path)).open("r", encoding="utf-8") as fh:
            return json.load(fh)
    repo = cfg.get("repo") or DEFAULT_REPO
    branch = cfg.get("branch") or DEFAULT_BRANCH
    path = cfg.get(path_key) or default_path
    url = RAW_URL_TMPL.format(repo=repo, branch=branch, path=path)
    return http.get_json(url, max_bytes=http.DEFAULT_MAX_BYTES)


def fetch(cfg: Optional[Dict[str, Any]] = None, since: Any = None) -> List[Dict[str, Any]]:
    """网络壳：取 marketplace.json + bump-tracking.json → parse。异常向上抛，由 sync 记账。"""
    cfg = cfg or {}
    marketplace = _load_json(cfg, "marketplace_path", "marketplace", DEFAULT_MARKETPLACE_PATH)
    bump = _load_json(cfg, "bump_path", "bump", DEFAULT_BUMP_PATH)
    return parse(marketplace, bump, cfg)
