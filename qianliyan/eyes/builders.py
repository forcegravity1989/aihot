"""eyes/builders.py —— 右眼 builders：真实仓库 ``zarazhangrui/follow-builders`` 的 X 动态。

cfg 即 ``config/builders.yaml`` 全量（spec-v0.3 §2 新结构）::

    repo: zarazhangrui/follow-builders
    branch: main
    feeds: [feed-x.json, feed-podcasts.json, feed-blogs.json]
    default_weight: 0.8
    allowlist: []          # 空 = 收录 feed 中出现的全部 builder
    weight_overrides: {}   # {handle: weight}

从 ``raw.githubusercontent.com/{repo}/{branch}/{feed}`` 逐个拉 JSON：``feed-x.json`` 是
``{generatedAt, lookbackHours, x:[{source, name, handle, bio, tweets:[...]}], stats}``，每条
tweet → 一个 item；``feed-podcasts.json``/``feed-blogs.json`` 为 ``{podcasts|blogs:[...]}``
（当前空数组，解析必须容忍）。过滤：``x-follows.yaml`` 的 mute 名单剔除；``allowlist`` 非空则
仅留白名单，否则默认全收。

``fetch`` 是网络壳；``parse_feed_x`` / ``parse_feed_generic`` 是纯函数（不出网），单测直喂
``tests/fixtures/real/builders_feed_x.json`` 等真实 fixture。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Set

from ..core import paths, schema
from ..engine import http

logger = logging.getLogger(__name__)

DEFAULT_WEIGHT = 0.8
TITLE_MAXLEN = 80
RAW_URL_TMPL = "https://raw.githubusercontent.com/{repo}/{branch}/{data_path}"
DEFAULT_FEEDS = ["feed-x.json", "feed-podcasts.json", "feed-blogs.json"]


# =========================================================================
# 公共小工具
# =========================================================================
def _first(entry: Dict[str, Any], *keys: str) -> Optional[Any]:
    for key in keys:
        value = entry.get(key)
        if value not in (None, ""):
            return value
    return None


def _norm_handle(handle: Any) -> str:
    """handle 归一：去 @、去空白、casefold（仅用于匹配，不用于展示）。"""
    return str(handle or "").strip().lstrip("@").casefold()


def _clean_handle(handle: Any) -> str:
    """展示用 handle：去 @ 与首尾空白，保留大小写。"""
    return str(handle or "").strip().lstrip("@")


def _as_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _muted_handles(cfg: Dict[str, Any]) -> Set[str]:
    """mute 名单：优先用 ``cfg["x_follows"]``（供测试注入），否则读 ``config/x-follows.yaml``。"""
    follows = cfg.get("x_follows")
    if follows is None:
        follows = (paths.load_yaml_config("x-follows") or {}).get("follows") or []
    muted: Set[str] = set()
    for follow in follows or []:
        if isinstance(follow, dict) and follow.get("mute"):
            handle = _norm_handle(follow.get("handle"))
            if handle:
                muted.add(handle)
    return muted


def _allowlist(cfg: Dict[str, Any]) -> Optional[Set[str]]:
    """allowlist：非空 → casefold 集合（仅收白名单）；空/缺失 → ``None``（全收）。"""
    raw = cfg.get("allowlist")
    if not raw:
        return None
    allowed = {_norm_handle(h) for h in raw if _norm_handle(h)}
    return allowed or None


def _weight_overrides(cfg: Dict[str, Any]) -> Dict[str, float]:
    raw = cfg.get("weight_overrides") or {}
    out: Dict[str, float] = {}
    if isinstance(raw, dict):
        for key, val in raw.items():
            handle = _norm_handle(key)
            if not handle:
                continue
            try:
                out[handle] = float(val)
            except (TypeError, ValueError):
                logger.warning("builders weight_overrides 非法权重 %r（handle=%s），忽略", val, handle)
    return out


def _single_line(text: str) -> str:
    """去换行折叠空白，取前 ``TITLE_MAXLEN`` 字符作标题。"""
    collapsed = " ".join(str(text or "").split())
    return collapsed[:TITLE_MAXLEN]


# =========================================================================
# 纯函数：解析 feed-x.json
# =========================================================================
def _tweet_to_item(
    tweet: Dict[str, Any],
    display_handle: str,
    name: str,
    bio: str,
    weight: float,
) -> Optional[Dict[str, Any]]:
    text = str(_first(tweet, "text", "content") or "")
    url = _first(tweet, "url", "link")
    if not url:
        return None
    likes = _as_int(tweet.get("likes"))
    retweets = _as_int(tweet.get("retweets"))
    replies = _as_int(tweet.get("replies"))
    metrics = {
        "likes": likes,
        "retweets": retweets,
        "replies": replies,
        "engagement": likes + retweets + replies,
    }
    extra = {
        "handle": display_handle,
        "name": name,
        "bio": bio,
        "platform": "x",
        "is_quote": bool(tweet.get("isQuote")),
    }
    date = _first(tweet, "createdAt", "created_at", "date")
    return schema.make_item(
        title=_single_line(text),
        url=str(url),
        source="@{0}".format(display_handle),
        source_kind="builders",
        backend="raw_json",
        weight=weight,
        date=date,
        summary=text,
        tags=["x", "builders"],
        metrics=metrics,
        extra=extra,
    )


def parse_feed_x(payload: Any, cfg: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """解析 ``feed-x.json`` 信封（``{x:[{handle, name, bio, tweets:[...]}]}``）为 item 列表。

    每条 tweet → 一个 item；allowlist（非空仅收白名单）与 mute 过滤在 builder 粒度生效。
    纯函数，不出网。
    """
    cfg = cfg or {}
    default_weight = float(cfg.get("default_weight", DEFAULT_WEIGHT))
    allowed = _allowlist(cfg)
    muted = _muted_handles(cfg)
    overrides = _weight_overrides(cfg)

    if isinstance(payload, dict):
        builders = payload.get("x")
    else:
        builders = payload
    if not isinstance(builders, list):
        return []

    items: List[Dict[str, Any]] = []
    for builder in builders:
        if not isinstance(builder, dict):
            continue
        handle_key = _norm_handle(builder.get("handle"))
        if not handle_key:
            continue
        if allowed is not None and handle_key not in allowed:
            continue
        if handle_key in muted:
            continue

        display_handle = _clean_handle(builder.get("handle")) or handle_key
        name = str(builder.get("name") or "")
        bio = str(builder.get("bio") or "")
        weight = overrides.get(handle_key, default_weight)

        for tweet in builder.get("tweets") or []:
            if not isinstance(tweet, dict):
                continue
            item = _tweet_to_item(tweet, display_handle, name, bio, weight)
            if item is not None:
                items.append(item)
    return items


def parse_feed_generic(
    payload: Any,
    kind: str,
    cfg: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """解析 podcasts/blogs 信封（``{podcasts|blogs:[...]}``）为 item 列表；空数组容忍返回 []。

    ``kind`` ∈ {"podcasts", "blogs"}；当前 fixture 为空，函数对空/缺失键一律容忍。
    纯函数，不出网。
    """
    cfg = cfg or {}
    default_weight = float(cfg.get("default_weight", DEFAULT_WEIGHT))

    entries: Any = []
    if isinstance(payload, dict):
        entries = payload.get(kind) or payload.get("items") or []
    elif isinstance(payload, list):
        entries = payload
    if not isinstance(entries, list):
        return []

    tag = "podcast" if str(kind).startswith("podcast") else "blog"
    items: List[Dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        title = _first(entry, "title", "name")
        url = _first(entry, "url", "link", "indexUrl")
        if not title or not url:
            continue
        summary = str(_first(entry, "summary", "description", "text") or "")
        date = _first(entry, "publishedAt", "createdAt", "date", "pubDate")
        items.append(schema.make_item(
            title=str(title),
            url=str(url),
            source=str(entry.get("name") or entry.get("source") or title),
            source_kind="builders",
            backend="raw_json",
            weight=default_weight,
            date=date,
            summary=summary,
            tags=[tag, "builders"],
            extra={"platform": tag},
        ))
    return items


# =========================================================================
# 已弃用：v0.2 扁平白名单解析壳（无下游；仅为向后兼容保留，勿新用）
# =========================================================================
def _extract_list(payload: Any) -> List[Any]:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("tweets", "items"):
            value = payload.get(key)
            if isinstance(value, list):
                return value
    return []


def _builder_index(cfg: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    index: Dict[str, Dict[str, Any]] = {}
    for builder in cfg.get("builders") or []:
        if not isinstance(builder, dict):
            continue
        handle = _norm_handle(builder.get("handle"))
        if handle:
            index[handle] = builder
    return index


def parse_payload(payload: Any, cfg: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """[DEPRECATED] v0.2 扁平快照解析壳（``{tweets:[{handle,text,url}]}`` + ``builders`` 白名单）。

    已被真实仓库的 ``parse_feed_x`` 取代，无下游调用；仅为向后兼容旧单测保留。
    """
    cfg = cfg or {}
    default_weight = float(cfg.get("default_weight", DEFAULT_WEIGHT))
    allowed = _builder_index(cfg)
    muted = _muted_handles(cfg)

    items: List[Dict[str, Any]] = []
    for entry in _extract_list(payload):
        if not isinstance(entry, dict):
            continue
        raw_handle = _first(entry, "handle", "user", "author")
        handle = _norm_handle(raw_handle)
        if not handle or handle not in allowed or handle in muted:
            continue
        builder = allowed[handle]
        text = str(_first(entry, "text", "content") or "")
        url = _first(entry, "url", "link")
        if not url:
            continue
        date = _first(entry, "created_at", "date")
        weight = float(builder.get("weight", default_weight))
        display_handle = builder.get("handle") or raw_handle
        items.append(schema.make_item(
            title=text[:TITLE_MAXLEN],
            url=str(url),
            source="@{0}".format(display_handle),
            source_kind="builders",
            backend="raw_json",
            weight=weight,
            date=date,
            summary=text,
            tags=["x", "builders"],
            extra={"platform": "x"},
        ))
    return items


# =========================================================================
# 网络壳
# =========================================================================
def _dispatch_feed(feed: str, payload: Any, cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    name = str(feed).lower()
    if "podcast" in name:
        return parse_feed_generic(payload, "podcasts", cfg)
    if "blog" in name:
        return parse_feed_generic(payload, "blogs", cfg)
    # 缺省及 feed-x.json 走 X 解析
    return parse_feed_x(payload, cfg)


def fetch(cfg: Optional[Dict[str, Any]] = None, since: Any = None) -> List[Dict[str, Any]]:
    """逐 feed 从 raw.githubusercontent 拉 ``zarazhangrui/follow-builders`` JSON 并解析。

    单 feed 拉取/解析失败记 warning 继续（podcasts/blogs 空数组自然产 0 条）。
    """
    cfg = cfg or {}
    repo = cfg.get("repo")
    if not repo:
        logger.warning("builders.yaml 缺少 repo，跳过抓取")
        return []
    branch = cfg.get("branch") or "main"
    feeds = cfg.get("feeds") or DEFAULT_FEEDS

    items: List[Dict[str, Any]] = []
    for feed in feeds:
        if not feed:
            continue
        url = RAW_URL_TMPL.format(repo=repo, branch=branch, data_path=feed)
        try:
            payload = http.get_json(url)
        except Exception as exc:  # 单 feed 失败不外溢
            logger.warning("builders feed 拉取失败，跳过 %s: %s", url, exc)
            continue
        try:
            items.extend(_dispatch_feed(str(feed), payload, cfg))
        except Exception as exc:
            logger.warning("builders feed 解析失败，跳过 %s: %s", url, exc)
            continue
    return items
