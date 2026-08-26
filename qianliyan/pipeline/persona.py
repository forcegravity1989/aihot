"""pipeline/persona.py —— 人物画像（builders 大牛聚合，spec-v0.3 §3）。

把统一池里 ``source_kind=="builders"`` 且带 ``extra.handle`` 的条目按人聚合成
persona 卡片：影响力数字、代表作、主题、近期关注点。产物写
``$QLY_DATA_DIR/personas.json`` 与 ``personas/<handle>.md``。

**回退公约（spec §5）**：``topics`` / ``recent_focus`` 优先用 LLM 聚类/概括，
LLM 不可用或任何异常一律走规则回退（topics=高频 tag/关键词，
recent_focus=最高互动条目的标题），**绝不向上抛**。
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Sequence

from ..core import llm_client, paths, storage, utils

logger = logging.getLogger(__name__)

__all__ = [
    "build_personas",
    "write_personas",
    "build_person_from_news",
]

# 聚合只认这一类条目
BUILDERS_KIND = "builders"
# topics 取前 N 个主题
TOPICS_MAX = 5
# top_items 取前 N 条代表作
TOP_ITEMS_MAX = 3
# topics 回退时排除的「无信息量」标签
_GENERIC_TAGS = {"x", "builders", "twitter", "tweet"}
# 关键词回退时的停用词（英文常见词 + 少量社媒噪声）
_STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "for", "to", "of", "in", "on", "at",
    "is", "are", "was", "were", "be", "been", "with", "that", "this", "it",
    "you", "your", "we", "our", "i", "my", "me", "he", "she", "they", "them",
    "as", "by", "from", "up", "out", "if", "so", "no", "not", "just", "can",
    "will", "new", "how", "why", "what", "when", "who", "about", "via", "rt",
    "http", "https", "com", "amp",
}
_WORD_RE = re.compile(r"[a-z0-9]+")

SYSTEM_PROMPT = (
    "你是科技情报编辑，为一位 AI 领域的 builder 归纳画像。"
    "根据其近期动态，给出 3~5 个英文小写主题标签（topics），"
    "以及一句不超过 40 字的中文「近期在关注……」概括（recent_focus）。只输出 JSON。"
)


# =========================================================================
# 工具
# =========================================================================
def _norm_handle(handle: Any) -> str:
    return str(handle or "").strip().lstrip("@").casefold()


def _extra(item: Dict[str, Any]) -> Dict[str, Any]:
    extra = item.get("extra")
    return extra if isinstance(extra, dict) else {}


def _metrics(item: Dict[str, Any]) -> Dict[str, Any]:
    metrics = item.get("metrics")
    return metrics if isinstance(metrics, dict) else {}


def _as_int(value: Any) -> Optional[int]:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def item_engagement(item: Dict[str, Any]) -> int:
    """单条 item 的互动量：``metrics.engagement`` 优先，否则 likes+retweets+replies。"""
    metrics = _metrics(item)
    direct = _as_int(metrics.get("engagement"))
    if direct is not None:
        return max(0, direct)
    total = 0
    seen = False
    for key in ("likes", "retweets", "rt", "replies"):
        val = _as_int(metrics.get(key))
        if val is not None:
            total += val
            seen = True
    return max(0, total) if seen else 0


def _builders_with_handle(items: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        if item.get("source_kind") != BUILDERS_KIND:
            continue
        if not _norm_handle(_extra(item).get("handle")):
            continue
        rows.append(item)
    return rows


def _fallback_topics(items: Sequence[Dict[str, Any]]) -> List[str]:
    """回退主题：先数 tag 词频（剔除通用标签），不足再从标题抽高频关键词。"""
    counts: Dict[str, int] = {}
    order: List[str] = []
    for item in items:
        for tag in item.get("tags") or []:
            key = str(tag or "").strip().casefold()
            if not key or key in _GENERIC_TAGS:
                continue
            if key not in counts:
                order.append(key)
            counts[key] = counts.get(key, 0) + 1

    topics = sorted(order, key=lambda k: (-counts[k], order.index(k)))[:TOPICS_MAX]
    if topics:
        return topics

    # tag 不够用 → 从标题里抽关键词
    kw_counts: Dict[str, int] = {}
    kw_order: List[str] = []
    for item in items:
        for word in _WORD_RE.findall(str(item.get("title") or "").casefold()):
            if len(word) < 3 or word in _STOPWORDS or word in _GENERIC_TAGS:
                continue
            if word not in kw_counts:
                kw_order.append(word)
            kw_counts[word] = kw_counts.get(word, 0) + 1
    return sorted(kw_order, key=lambda k: (-kw_counts[k], kw_order.index(k)))[:TOPICS_MAX]


def _avatar_path(handle: str) -> Optional[str]:
    """``builder-avatars/<handle>.png`` 若存在则返回相对路径，否则 None。"""
    rel = "builder-avatars/{0}.png".format(handle)
    try:
        if paths.data_path("builder-avatars", "{0}.png".format(handle)).is_file():
            return rel
    except Exception as exc:  # noqa: BLE001 - 头像探测失败不该拖垮画像
        logger.debug("头像探测失败 handle=%s: %s", handle, exc)
    return None


# =========================================================================
# 聚合
# =========================================================================
def build_personas(
    items: Sequence[Dict[str, Any]],
    cfg: Optional[Dict[str, Any]] = None,
    client: Optional[Any] = None,
) -> List[Dict[str, Any]]:
    """把 builders 条目按 ``extra.handle`` 聚合成 persona 列表（total_engagement 降序）。

    ``cfg`` 预留（当前未用到具体字段）；``client`` 可注入 LLM 客户端，缺省从
    ``LLMClient.from_env()`` 取。LLM 只增强 ``topics`` / ``recent_focus``，不可用即回退。
    """
    rows = _builders_with_handle(items)
    if not rows:
        return []

    # 按 handle 分组（保留首见的展示形态）
    groups: Dict[str, List[Dict[str, Any]]] = {}
    display: Dict[str, str] = {}
    order: List[str] = []
    for item in rows:
        raw_handle = str(_extra(item).get("handle") or "").strip().lstrip("@")
        key = raw_handle.casefold()
        if key not in groups:
            groups[key] = []
            display[key] = raw_handle or key
            order.append(key)
        groups[key].append(item)

    personas: List[Dict[str, Any]] = []
    for key in order:
        personas.append(_build_one(display[key], groups[key]))

    # LLM 增强（可选、必有回退，绝不向上抛）
    _maybe_enhance(personas, groups, client)

    personas.sort(key=lambda p: p.get("total_engagement") or 0, reverse=True)
    return personas


def _build_one(handle: str, group: List[Dict[str, Any]]) -> Dict[str, Any]:
    # 按互动量降序，代表作 / 展示信息都取自高互动条目
    ranked = sorted(
        enumerate(group),
        key=lambda pair: (-item_engagement(pair[1]), pair[0]),
    )
    ranked_items = [g for _, g in ranked]
    best = ranked_items[0]

    name = ""
    bio = ""
    for item in ranked_items:
        extra = _extra(item)
        if not name and extra.get("name"):
            name = str(extra.get("name"))
        if not bio and extra.get("bio"):
            bio = str(extra.get("bio"))
        if name and bio:
            break

    total_engagement = sum(item_engagement(it) for it in group)
    item_count = len(group)
    avg_engagement = round(total_engagement / float(item_count), 2) if item_count else 0.0

    last_active = ""
    latest = None
    for item in group:
        dt = utils.parse_date(item.get("date"))
        if dt is not None and (latest is None or dt > latest):
            latest = dt
    if latest is not None:
        last_active = utils.iso(latest)

    top_items = [
        {
            "title": str(it.get("title") or ""),
            "url": str(it.get("url") or ""),
            "engagement": item_engagement(it),
            "date": str(it.get("date") or ""),
        }
        for it in ranked_items[:TOP_ITEMS_MAX]
    ]

    return {
        "handle": handle,
        "name": name or handle,
        "bio": bio,
        "avatar_path": _avatar_path(handle.casefold()) or _avatar_path(handle),
        "item_count": item_count,
        "total_engagement": total_engagement,
        "avg_engagement": avg_engagement,
        "last_active": last_active,
        "top_items": top_items,
        "topics": _fallback_topics(group),
        "recent_focus": str(best.get("title") or ""),
    }


# =========================================================================
# LLM 增强
# =========================================================================
def _maybe_enhance(
    personas: List[Dict[str, Any]],
    groups: Dict[str, List[Dict[str, Any]]],
    client: Optional[Any],
) -> None:
    if not personas:
        return
    if client is None:
        try:
            client = llm_client.LLMClient.from_env()
        except Exception as exc:  # noqa: BLE001
            logger.debug("LLMClient.from_env 失败，人物画像走回退: %s", exc)
            return
    try:
        available = client.is_available()
    except Exception as exc:  # noqa: BLE001
        logger.debug("LLM 可用性判定异常，人物画像走回退: %s", exc)
        return
    if not available:
        logger.debug("persona: LLM 不可用，topics/recent_focus 全部回退")
        return

    prompts = [_build_prompt(p, groups.get(p["handle"].casefold(), [])) for p in personas]
    try:
        replies = client.batch_json(prompts, system=SYSTEM_PROMPT)
    except Exception as exc:  # noqa: BLE001 - 回退公约：绝不向上抛
        logger.warning("人物画像 LLM 调用失败，沿用回退: %s", exc)
        return

    for persona, reply in zip(personas, replies or []):
        if not isinstance(reply, dict):
            continue
        topics = reply.get("topics")
        if isinstance(topics, list):
            cleaned = [str(t).strip().casefold() for t in topics if str(t).strip()]
            if cleaned:
                persona["topics"] = cleaned[:TOPICS_MAX]
        focus = reply.get("recent_focus")
        if isinstance(focus, str) and focus.strip():
            persona["recent_focus"] = focus.strip()


def _build_prompt(persona: Dict[str, Any], group: Sequence[Dict[str, Any]]) -> str:
    import json

    titles = [str(it.get("title") or "") for it in group][:12]
    payload = {
        "handle": persona.get("handle"),
        "name": persona.get("name"),
        "bio": persona.get("bio"),
        "recent_titles": titles,
    }
    return (
        "为下面这位 builder 归纳 topics（3~5 个英文小写主题标签）与一句中文 recent_focus。\n"
        '输出 JSON，形如 {"topics": ["agent","evals"], "recent_focus": "近期在关注……"}。\n\n'
        "输入：\n" + json.dumps(payload, ensure_ascii=False)
    )


# =========================================================================
# 写盘
# =========================================================================
def write_personas(personas: Sequence[Dict[str, Any]]) -> None:
    """写 ``personas.json``（全量）+ ``personas/<handle>.md``（逐人卡片）。"""
    rows = [p for p in (personas or []) if isinstance(p, dict)]
    storage.write_json(paths.data_path("personas.json"), rows)
    for persona in rows:
        handle = str(persona.get("handle") or "").strip()
        if not handle:
            continue
        try:
            path = paths.data_path("personas", "{0}.md".format(handle))
            path.write_text(_render_md(persona), encoding="utf-8")
        except OSError as exc:
            logger.warning("写 persona md 失败 handle=%s: %s", handle, exc)


def _render_md(persona: Dict[str, Any]) -> str:
    lines: List[str] = []
    name = persona.get("name") or persona.get("handle") or ""
    lines.append("# {0} (@{1})".format(name, persona.get("handle") or ""))
    lines.append("")
    if persona.get("bio"):
        lines.append("> {0}".format(persona.get("bio")))
        lines.append("")
    lines.append(
        "- 动态数：{0} ｜ 总互动：{1} ｜ 均互动：{2} ｜ 最近活跃：{3}".format(
            persona.get("item_count", 0),
            persona.get("total_engagement", 0),
            persona.get("avg_engagement", 0),
            persona.get("last_active") or "—",
        )
    )
    topics = persona.get("topics") or []
    if topics:
        lines.append("- 主题：{0}".format(" / ".join(str(t) for t in topics)))
    lines.append("- 近期在关注：{0}".format(persona.get("recent_focus") or "—"))
    lines.append("")
    top_items = persona.get("top_items") or []
    if top_items:
        lines.append("## 代表作")
        for it in top_items:
            lines.append(
                "- [{0}]({1}) — 互动 {2}".format(
                    it.get("title") or "", it.get("url") or "", it.get("engagement", 0)
                )
            )
        lines.append("")
    return "\n".join(lines)


# =========================================================================
# 预留接口壳
# =========================================================================
def build_person_from_news(
    items: Sequence[Dict[str, Any]],
    cfg: Optional[Dict[str, Any]] = None,
    client: Optional[Any] = None,
) -> List[Dict[str, Any]]:
    """从新闻/资讯条目里抽取「新闻人物」画像的接口壳。

    v0.3 未实装（仅 builders 类人物画像落地）；保留签名以便后续从 aihot/local
    条目里做实体识别与人物聚合。当前恒返回空列表。
    """
    logger.debug("build_person_from_news 尚未实装（v0.3 仅 builders 人物画像）")
    return []
