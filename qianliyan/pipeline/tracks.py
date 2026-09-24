"""日报的「方向」（``config/tracks.yaml``，issue #49）。

日报按方向分栏：模型发布所有人必看，其余（Agent 实践 / 训练 / 算子与推理 / 研究 / 产品与行业）
由读者选关注。一条条目属于哪个方向，编辑选稿时标（``entry["track"]``）；编辑没标的按
tracks.yaml 的规则判定——规则只是兜底，判错的代价是它出现在隔壁一栏。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Sequence

from ..core import paths
from . import channels

logger = logging.getLogger(__name__)

CONFIG_NAME = "tracks.yaml"

#: tracks.yaml 缺失或读坏时的最小可用配置——日报总得能分出「模型发布」和「其它」
_FALLBACK_CONFIG = {
    "classify_order": ["models", "industry"],
    "fallback": "industry",
    "tracks": [
        {"id": "models", "title": "模型发布", "everyone": True,
         "match": {"categories": ["models"]}},
        {"id": "industry", "title": "其它", "default": True},
    ],
}


def load() -> Dict[str, Any]:
    """读 tracks.yaml，归一为 ``{"tracks": [...], "order": [...], "fallback": id}``。"""
    try:
        raw = paths.load_yaml_config(CONFIG_NAME)
    except Exception as exc:  # noqa: BLE001 - 配置读坏不该让日报出不来
        logger.warning("读 %s 失败，用内置最小配置: %s", CONFIG_NAME, exc)
        raw = {}
    if not isinstance(raw, dict) or not isinstance(raw.get("tracks"), list) or not raw.get("tracks"):
        raw = _FALLBACK_CONFIG

    tracks: List[Dict[str, Any]] = []
    for row in raw.get("tracks") or []:
        if not isinstance(row, dict) or not str(row.get("id") or "").strip():
            logger.warning("跳过非法方向配置: %r", row)
            continue
        match_cfg = row.get("match")
        exclude_cfg = row.get("exclude")
        tracks.append({
            "id": str(row["id"]).strip(),
            "title": str(row.get("title") or row["id"]),
            "blurb": str(row.get("blurb") or ""),
            "everyone": bool(row.get("everyone")),
            "default": bool(row.get("default")),
            "match": match_cfg if isinstance(match_cfg, dict) else None,
            "exclude": exclude_cfg if isinstance(exclude_cfg, dict) and exclude_cfg else None,
        })
    ids = [t["id"] for t in tracks]
    order = [str(x) for x in (raw.get("classify_order") or []) if str(x) in ids]
    order += [i for i in ids if i not in order]
    fallback = str(raw.get("fallback") or "")
    if fallback not in ids:
        fallback = ids[-1]
    return {"tracks": tracks, "order": order, "fallback": fallback}


def ids(cfg: Dict[str, Any]) -> List[str]:
    return [t["id"] for t in cfg["tracks"]]


def by_id(cfg: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {t["id"]: t for t in cfg["tracks"]}


def rule_track(item: Dict[str, Any], cfg: Dict[str, Any]) -> str:
    """按规则判定方向：classify_order 里第一个 match 命中的；都不中给 fallback。"""
    table = by_id(cfg)
    for tid in cfg["order"]:
        row = table[tid]
        if row["exclude"] and channels.match_item(item, row["exclude"]):
            continue
        if row["match"] and channels.match_item(item, row["match"]):
            return tid
    return cfg["fallback"]


def track_of(item: Dict[str, Any], cfg: Dict[str, Any]) -> str:
    """编辑标过（且是合法 id）就听编辑的，否则走规则。"""
    tid = str(item.get("track") or "").strip()
    if tid in by_id(cfg):
        return tid
    return rule_track(item, cfg)


def everyone_ids(cfg: Dict[str, Any]) -> List[str]:
    return [t["id"] for t in cfg["tracks"] if t["everyone"]]


def default_ids(cfg: Dict[str, Any]) -> List[str]:
    return [t["id"] for t in cfg["tracks"] if t["default"] and not t["everyone"]]


def describe(cfg: Dict[str, Any]) -> str:
    """给选稿/提炼简报用的方向说明，一行一个。"""
    lines = []
    for t in cfg["tracks"]:
        tail = "（所有人必看）" if t["everyone"] else ""
        lines.append("- {0}：{1}{2}{3}".format(
            t["id"], t["title"], "——" + t["blurb"] if t["blurb"] else "", tail))
    return "\n".join(lines)


def count(items: Sequence[Dict[str, Any]], cfg: Dict[str, Any]) -> Dict[str, int]:
    out = {tid: 0 for tid in ids(cfg)}
    for item in items:
        tid = track_of(item, cfg)
        out[tid] = out.get(tid, 0) + 1
    return out


def title_of(tid: Optional[str], cfg: Dict[str, Any]) -> str:
    row = by_id(cfg).get(str(tid or ""))
    return row["title"] if row else ""
