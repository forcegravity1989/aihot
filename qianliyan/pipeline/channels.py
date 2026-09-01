"""pipeline/channels.py —— 频道路由（spec §8.2 + §9.2）。

职责：把统一池按 ``config/channels.yaml`` 的规则分发到各频道，并写出人读页
``channels/<name>.md`` 与机器可读索引 ``channels.json``。

匹配语义（spec §8.2）——一个频道一个 ``match`` 块，**块内字段之间是 AND**：

======================  ====================================================
字段                     语义
======================  ====================================================
``tags_any``            item.tags 命中任一（大小写不敏感）
``tags_all``            item.tags 命中全部
``sources_include``     对 ``source_list`` 任一元素做大小写不敏感**子串**匹配
``keywords_any``        ``title + summary`` casefold 后的子串匹配
``aihot_category``      ``extra.category`` 命中任一
``categories``          ``extra.category`` / ``extra.source_category`` 命中任一（源类目，spec-v0.3 §15）
``formats``             ``extra.format`` 命中任一（news/blog/video/talk/podcast/repo/paper/x，spec-v0.3 §15）
``platforms``           ``extra.platform`` 命中任一
``source_kinds``        ``item.source_kind`` 命中任一
``any_of``              子块列表，任一子块整体命中即算命中（OR 的唯一入口）
======================  ====================================================

空 ``match`` 块按「零条件 AND」处理即恒真；但 ``route()`` 会跳过没有 match 配置的
频道并记 warning——避免配置疏漏导致某频道吞掉全部条目。
"""

from __future__ import annotations

import logging
from collections import OrderedDict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

from ..core import paths, storage, utils

logger = logging.getLogger(__name__)

__all__ = [
    "CONFIG_NAME",
    "DEFAULT_LIMIT",
    "DEFAULT_MAX_PER_SOURCE",
    "diversify",
    "load_source_groups",
    "BADGE_EMOJI",
    "load_channels",
    "match_item",
    "route",
    "run_all",
    "format_line",
    "render_channel_md",
]

CONFIG_NAME = "channels.yaml"
DEFAULT_LIMIT = 30

#: 同一信源在单个频道内最多占多少席（``defaults.max_per_source`` 可改，频道级可覆盖，
#: 0 / null = 不限）。
#:
#: 为什么需要它：频道内按 hotness 降序截断 limit，而权重高又新鲜的源会整段吃掉名额。
#: 实测 15 个非空频道里有 7 个的 top10 被**同一个源**占满 9~10 席——practices 全是
#: Anthropic Engineering、talks 全是 OpenAI YouTube、trending 全是 GitHub Trending
#: Daily、change-intel 全是系统提示词。名字叫「模型发布」的频道 top5 全是 Anthropic，
#: 看不到 OpenAI/Google/Qwen。新加的信源更是连一席都排不进去——抓回来也看不见。
DEFAULT_MAX_PER_SOURCE = 3

#: badge → 渲染符号（spec §1：heavy=📈 重磅，flash=⚡ 一手速报）
BADGE_EMOJI = OrderedDict((("heavy", "📈"), ("flash", "⚡")))

_MATCH_FIELDS = (
    "tags_any",
    "tags_all",
    "sources_include",
    "keywords_any",
    "aihot_category",
    "categories",
    "formats",
    "platforms",
    "source_kinds",
    "any_of",
)


# =========================================================================
# 配置加载
# =========================================================================
def _as_list(value: Any) -> List[str]:
    """把标量/列表统一成字符串列表（None 与空值被剔除）。"""
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return [str(v) for v in value if v is not None and str(v) != ""]
    if isinstance(value, str):
        return [value] if value else []
    return [str(value)]


def _fold_all(values: Iterable[Any]) -> List[str]:
    return [str(v).casefold() for v in values]


def _coerce_cap(value: Any, fallback: int) -> int:
    """同源席位上限：缺省取 fallback；显式 0 / 负数 / null 表示**不限**（返回 0）。"""
    if value is None:
        return fallback
    try:
        cap = int(value)
    except (TypeError, ValueError):
        return fallback
    return cap if cap > 0 else 0


def _source_key(item: Dict[str, Any], groups: Optional[Dict[str, str]] = None) -> str:
    """判定「同源」用的键。

    取 ``source`` 而不是 ``source_list``——多源交叉的条目正是我们想留下的，不该因为
    它的某个来源已占满席位就被挤掉。

    ``groups`` 给的是**组织级归并**：Anthropic News / Anthropic Engineering / Claude Blog
    在 ``source`` 上是三个源，实际是同一家。只按源计数的话，「模型发布」频道 top5 仍然
    全是 Anthropic，看不到 OpenAI / Google / Qwen——那正是这个频道存在的意义。
    匹配规则同 model-sources.yaml：子串、大小写不敏感、**命中多条时取最长 key**
    （更具体的规则胜出）。
    """
    source = str(item.get("source") or "").strip()
    folded = source.casefold()
    if groups:
        hit = ""
        for needle, group in groups.items():
            if needle in folded and len(needle) > len(hit):
                hit, matched = needle, group
        if hit:
            return "group:{0}".format(matched).casefold()
    return folded


def load_source_groups() -> Dict[str, str]:
    """读 ``channels.yaml`` 的 ``defaults.source_groups``（组名 → 子串列表），
    返回展平后的「子串 → 组名」表（子串一律 casefold）。"""
    raw = paths.load_yaml_config(CONFIG_NAME)
    defaults = raw.get("defaults") if isinstance(raw.get("defaults"), dict) else {}
    spec = (defaults or {}).get("source_groups")
    flat: Dict[str, str] = {}
    if isinstance(spec, dict):
        for group, needles in spec.items():
            for needle in (needles or []):
                text = str(needle).strip().casefold()
                if text:
                    flat[text] = str(group)
    return flat


def diversify(items: List[Dict[str, Any]], cap: int, limit: int,
               groups: Optional[Dict[str, str]] = None) -> List[Dict[str, Any]]:
    """**软上限**：同源最多占 cap 席；铺不满 limit 时按热度回填被压下的条目。

    软而不是硬，是因为有些频道天生就只有一两个源（trending 只有 GitHub Trending
    daily/weekly，change-intel 只有系统提示词 + 插件市场）。硬砍会让这些频道直接变空——
    多样性上限的目的是「有得选时别让一家独占」，不是「宁缺毋滥」。

    入参 ``items`` 须已按热度降序。返回顺序：先是铺开的一轮（仍按热度），再接回填的。
    """
    if cap <= 0:
        return items[:limit]

    picked: List[Dict[str, Any]] = []
    held: List[Dict[str, Any]] = []
    seen: Dict[str, int] = {}
    for item in items:
        key = _source_key(item, groups)
        if seen.get(key, 0) < cap:
            picked.append(item)
            seen[key] = seen.get(key, 0) + 1
        else:
            held.append(item)
    if len(picked) < limit:
        picked.extend(held[: limit - len(picked)])
    return picked[:limit]


def _coerce_limit(value: Any, fallback: int = DEFAULT_LIMIT) -> int:
    try:
        limit = int(value)
    except (TypeError, ValueError):
        return fallback
    return limit if limit > 0 else fallback


def load_channels() -> List[Dict[str, Any]]:
    """读 ``config/channels.yaml``，归一为 ``[{name, title, limit, match}, …]``。

    兼容两种写法：``channels:`` 为列表（元素含 ``name``）或为 ``name -> cfg`` 映射。
    """
    raw = paths.load_yaml_config(CONFIG_NAME)
    defaults = raw.get("defaults") if isinstance(raw.get("defaults"), dict) else {}
    default_limit = _coerce_limit((defaults or {}).get("limit"), DEFAULT_LIMIT)
    default_cap = _coerce_cap((defaults or {}).get("max_per_source"), DEFAULT_MAX_PER_SOURCE)

    entries = raw.get("channels")
    pairs: List[Any] = []
    if isinstance(entries, list):
        pairs = list(entries)
    elif isinstance(entries, dict):
        pairs = [dict(cfg or {}, name=name) for name, cfg in entries.items()]
    elif entries is not None:
        logger.warning("channels.yaml 的 channels 节点应为 list 或 mapping，实际 %s", type(entries))

    channels: List[Dict[str, Any]] = []
    for entry in pairs:
        if not isinstance(entry, dict):
            logger.warning("跳过非法频道配置: %r", entry)
            continue
        name = str(entry.get("name") or "").strip()
        if not name:
            logger.warning("跳过缺少 name 的频道配置: %r", entry)
            continue
        match_cfg = entry.get("match")
        if match_cfg is not None and not isinstance(match_cfg, dict):
            logger.warning("频道 %s 的 match 应为 mapping，已忽略", name)
            match_cfg = None
        channels.append({
            "name": name,
            "title": str(entry.get("title") or name),
            "limit": _coerce_limit(entry.get("limit"), default_limit),
            "max_per_source": _coerce_cap(entry.get("max_per_source"), default_cap),
            "match": match_cfg or {},
        })
    return channels


# =========================================================================
# §8.2 匹配
# =========================================================================
def _item_text(item: Dict[str, Any]) -> str:
    return "{0}\n{1}".format(item.get("title") or "", item.get("summary") or "").casefold()


def _extra(item: Dict[str, Any]) -> Dict[str, Any]:
    extra = item.get("extra")
    return extra if isinstance(extra, dict) else {}


def match_item(item: Dict[str, Any], match_cfg: Optional[Dict[str, Any]]) -> bool:
    """判定一条 item 是否命中 match 块（块内字段 AND，``any_of`` 内部 OR）。"""
    if not isinstance(item, dict):
        return False
    if not isinstance(match_cfg, dict) or not match_cfg:
        return True  # 零条件 AND —— 恒真；route() 另行拦截空 match 频道

    for key in match_cfg:
        if key not in _MATCH_FIELDS:
            logger.warning("channels.yaml 出现未知匹配字段 %r（已忽略）", key)

    tags = _fold_all(_as_list(item.get("tags")))

    wanted = _fold_all(_as_list(match_cfg.get("tags_any")))
    if wanted and not any(tag in tags for tag in wanted):
        return False

    wanted = _fold_all(_as_list(match_cfg.get("tags_all")))
    if wanted and not all(tag in tags for tag in wanted):
        return False

    wanted = _fold_all(_as_list(match_cfg.get("sources_include")))
    if wanted:
        sources = _fold_all(_as_list(item.get("source_list")) or _as_list(item.get("source")))
        if not any(needle in name for needle in wanted for name in sources):
            return False

    wanted = _fold_all(_as_list(match_cfg.get("keywords_any")))
    if wanted:
        text = _item_text(item)
        if not any(keyword in text for keyword in wanted):
            return False

    extra = _extra(item)

    wanted = _fold_all(_as_list(match_cfg.get("aihot_category")))
    if wanted and str(extra.get("category") or "").casefold() not in wanted:
        return False

    # categories：源类目匹配，兼看 extra.category 与 extra.source_category（spec-v0.3 §15）
    wanted = set(_fold_all(_as_list(match_cfg.get("categories"))))
    if wanted:
        cats = {
            str(extra.get("category") or "").casefold(),
            str(extra.get("source_category") or "").casefold(),
        }
        cats.discard("")
        if not (cats & wanted):
            return False

    # formats：extra.format 单值命中任一（spec-v0.3 §12/§15）
    wanted = _fold_all(_as_list(match_cfg.get("formats")))
    if wanted and str(extra.get("format") or "").casefold() not in wanted:
        return False

    wanted = _fold_all(_as_list(match_cfg.get("platforms")))
    if wanted and str(extra.get("platform") or "").casefold() not in wanted:
        return False

    wanted = _fold_all(_as_list(match_cfg.get("source_kinds")))
    if wanted and str(item.get("source_kind") or "").casefold() not in wanted:
        return False

    if "any_of" in match_cfg:
        blocks = match_cfg.get("any_of") or []
        if not isinstance(blocks, (list, tuple)):
            logger.warning("any_of 应为块列表，已忽略: %r", blocks)
        elif not any(
            match_item(item, block) for block in blocks if isinstance(block, dict)
        ):
            return False

    return True


# =========================================================================
# §9.2 路由
# =========================================================================
def _hotness(item: Dict[str, Any]) -> float:
    try:
        return float(item.get("hotness") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def route(
    items: Sequence[Dict[str, Any]],
    channels: Optional[Sequence[Dict[str, Any]]] = None,
) -> "OrderedDict[str, List[Dict[str, Any]]]":
    """把 items 路由到各频道：一条 item 可入多频道；频道内按 hotness 降序并截断 limit。

    返回 ``OrderedDict``，键序与 channels.yaml 中的频道顺序一致（含空频道）。
    """
    if channels is None:
        channels = load_channels()

    result: "OrderedDict[str, List[Dict[str, Any]]]" = OrderedDict()
    pool = [it for it in (items or []) if isinstance(it, dict)]
    groups = load_source_groups()

    for channel in channels or []:
        if not isinstance(channel, dict):
            continue
        name = str(channel.get("name") or "").strip()
        if not name:
            continue
        match_cfg = channel.get("match") or {}
        if not match_cfg:
            logger.warning("频道 %s 没有 match 规则，已跳过（避免吞掉全部条目）", name)
            result[name] = []
            continue

        matched = [it for it in pool if match_item(it, match_cfg)]
        matched.sort(key=_hotness, reverse=True)
        result[name] = diversify(
            matched,
            _coerce_cap(channel.get("max_per_source"), DEFAULT_MAX_PER_SOURCE),
            _coerce_limit(channel.get("limit")),
            groups,
        )

    return result


# =========================================================================
# §9.2 写盘
# =========================================================================
def display_title(item: Dict[str, Any]) -> str:
    """中文标题优先：``extra.title_zh`` > ``title``。"""
    zh = _extra(item).get("title_zh")
    if zh and str(zh).strip():
        return str(zh).strip()
    return str(item.get("title") or "")


def badge_prefix(item: Dict[str, Any]) -> str:
    """把 badges 译为 emoji 前缀（无 badge 返回空串）。"""
    marks = [BADGE_EMOJI[b] for b in BADGE_EMOJI if b in (item.get("badges") or [])]
    return "".join(marks)


def format_line(item: Dict[str, Any]) -> str:
    """单条 markdown 行：``- 📈⚡ **标题**（源A + 源B） 0.9812 — [链接](url)``。"""
    prefix = badge_prefix(item)
    sources = " + ".join(_as_list(item.get("source_list")) or _as_list(item.get("source")))
    return "- {0}**{1}**（{2}） {3} — [链接]({4})".format(
        prefix + " " if prefix else "",
        display_title(item),
        sources,
        "{0:.4f}".format(_hotness(item)),
        item.get("url") or "",
    )


def render_channel_md(title: str, items: Sequence[Dict[str, Any]], now_iso: str) -> str:
    """渲染频道人读页（spec §9.2 格式）。"""
    lines = ["# {0}".format(title), "", "> 更新时间：{0}　条目数：{1}".format(now_iso, len(items)), ""]
    if not items:
        lines.append("_暂无条目_")
    else:
        lines.extend(format_line(item) for item in items)
    lines.append("")
    return "\n".join(lines)


def _write_text(path: Path, text: str) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    except OSError as exc:
        logger.warning("写入频道页失败 %s: %s", path, exc)


def run_all(
    items: Sequence[Dict[str, Any]],
    channels: Optional[Sequence[Dict[str, Any]]] = None,
) -> "OrderedDict[str, List[Dict[str, Any]]]":
    """路由 + 写盘：``channels/<name>.md`` 与 ``channels.json``；返回路由结果供 report 复用。"""
    if channels is None:
        channels = load_channels()
    routed = route(items, channels)
    titles = {c.get("name"): c.get("title") or c.get("name") for c in channels or []}
    now_iso = utils.iso(utils.now_utc())

    index: "OrderedDict[str, List[str]]" = OrderedDict()
    for name, channel_items in routed.items():
        _write_text(
            paths.data_path("channels", "{0}.md".format(name)),
            render_channel_md(str(titles.get(name) or name), channel_items, now_iso),
        )
        index[name] = [str(it.get("sig") or "") for it in channel_items]

    storage.write_json(paths.data_path("channels.json"), index)
    logger.info("频道路由完成：%d 个频道 / 共 %d 条归属", len(routed), sum(len(v) for v in routed.values()))
    return routed
