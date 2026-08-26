"""pipeline/report.py —— HTML 简报渲染（spec §9.3）。

用自研 ``minitpl`` + ``templates/digest.html.jinja`` 渲染**单文件自包含**简报
（内联 CSS/JS，零外链资源），默认写到 ``$QLY_DATA_DIR/digest.html``。

产物必备（spec §9.3）：

1. 版面 = 全局热榜 + 各频道分区；每条含中文优先标题、来源徽章、时间、hotness、
   📈 重磅 / ⚡ 一手速报 标记，以及**可点击的原文 URL**（铁律 2）；
2. 三排序按钮 🔥 热度 / 🕒 时间 / 📈 得分，vanilla JS 原地重排；
3. Ctrl+K 命令面板，跨版面模糊过滤 + 回车跳转 + Esc 关闭；
4. 大牛头像：``builder-avatars/{handle}.png`` 存在则 base64 内嵌，否则首字母圆形占位；
5. 简体中文文案、浅色明快风格。

本模块只做「数据 → 视图模型 → HTML」的纯加工，不出网、不调 LLM。
"""

from __future__ import annotations

import base64
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from .. import __version__
from ..core import paths, utils
from . import channels as channels_mod
from . import minitpl

logger = logging.getLogger(__name__)

__all__ = [
    "TEMPLATE_NAME",
    "OUTPUT_NAME",
    "HOTLIST_LIMIT",
    "BADGE_LABELS",
    "build_context",
    "render_html",
]

TEMPLATE_NAME = "digest.html.jinja"
OUTPUT_NAME = "digest.html"
AVATAR_DIR = "builder-avatars"
HOTLIST_LIMIT = 50
PERSONALIZED_LIMIT = 30
SUMMARY_MAX_CHARS = 160
MAX_AVATAR_BYTES = 512 * 1024

#: personal_reasons 前缀 → 展示 chip 图标（spec-v0.3 §5 「命中兴趣的原因用 chip 展示」）
_REASON_ICONS = {
    "tag": "🏷",
    "source": "📰",
    "people": "👤",
    "mute": "🔕",
}

PAGE_TITLE = "千里眼 · AI 情报简报"
PAGE_SUBTITLE = "五眼并行采集 · 去重打分 · 频道分发"

#: badge → 页面展示文案（spec §1：heavy=📈 重磅，flash=⚡ 一手速报）
BADGE_LABELS = (("heavy", "📈 重磅"), ("flash", "⚡ 一手速报"))

_AVATAR_COLORS = (
    "#2f6df6", "#ff6a3d", "#0ea5a4", "#7c3aed",
    "#d946a0", "#0284c7", "#16a34a", "#b45309",
)


# =========================================================================
# 小工具
# =========================================================================
def _extra(item: Dict[str, Any]) -> Dict[str, Any]:
    extra = item.get("extra")
    return extra if isinstance(extra, dict) else {}


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _handle_of(item: Dict[str, Any]) -> str:
    """推断大牛 handle：``extra.handle`` 优先，其次形如 ``@karpathy`` 的 source。"""
    handle = str(_extra(item).get("handle") or "").strip()
    if not handle:
        source = str(item.get("source") or "").strip()
        if source.startswith("@"):
            handle = source[1:]
    return handle.lstrip("@").strip()


def _avatar_color(key: str) -> str:
    """由 handle/source 稳定映射到一个占位底色（不依赖进程内 hash 随机化）。"""
    seed = sum(ord(ch) for ch in (key or "?"))
    return _AVATAR_COLORS[seed % len(_AVATAR_COLORS)]


def _avatar_initial(item: Dict[str, Any]) -> str:
    name = _handle_of(item) or str(item.get("source") or "") or "?"
    for ch in name:
        if not ch.isspace():
            return ch.upper()
    return "?"


def _load_avatar(handle: str, avatar_dir: Path, cache: Dict[str, str]) -> str:
    """读 ``builder-avatars/{handle}.png`` 并内联为 data URI；不存在返回空串。"""
    if not handle:
        return ""
    if handle in cache:
        return cache[handle]
    data_uri = ""
    try:
        path = avatar_dir / "{0}.png".format(handle)
        if path.is_file() and path.stat().st_size <= MAX_AVATAR_BYTES:
            payload = base64.b64encode(path.read_bytes()).decode("ascii")
            data_uri = "data:image/png;base64,{0}".format(payload)
    except OSError as exc:
        logger.warning("读取头像失败 %s: %s", handle, exc)
    cache[handle] = data_uri
    return data_uri


def _humanize(dt: Optional[datetime], now: datetime) -> str:
    """相对时间中文文案：刚刚 / N 分钟前 / N 小时前 / N 天前 / 具体日期。"""
    if dt is None:
        return "时间未知"
    seconds = (now - dt).total_seconds()
    if seconds < 0:
        return "刚刚"
    if seconds < 60:
        return "刚刚"
    if seconds < 3600:
        return "{0} 分钟前".format(int(seconds // 60))
    if seconds < 86400:
        return "{0} 小时前".format(int(seconds // 3600))
    if seconds < 86400 * 30:
        return "{0} 天前".format(int(seconds // 86400))
    return dt.strftime("%Y-%m-%d")


def _summary_of(item: Dict[str, Any]) -> str:
    text = str(_extra(item).get("summary_zh") or item.get("summary") or "").strip()
    text = " ".join(text.split())
    if len(text) > SUMMARY_MAX_CHARS:
        text = text[:SUMMARY_MAX_CHARS].rstrip() + "…"
    return text


def _badges_of(item: Dict[str, Any]) -> List[Dict[str, str]]:
    owned = item.get("badges") or []
    return [{"kind": kind, "label": label} for kind, label in BADGE_LABELS if kind in owned]


def _personal_score(item: Dict[str, Any]) -> Optional[float]:
    """取 ``extra.personal_score``（未个性化过则返回 None，用于判断是否渲染「为你推荐」）。"""
    raw = _extra(item).get("personal_score")
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _reason_chips(reasons: Any) -> List[Dict[str, str]]:
    """把 ``extra.personal_reasons``（如 ``tag:models`` / ``source:anthropic``）渲染为 chip。"""
    chips: List[Dict[str, str]] = []
    for reason in reasons or []:
        text = str(reason or "").strip()
        if not text:
            continue
        prefix, _, rest = text.partition(":")
        icon = _REASON_ICONS.get(prefix, "•")
        label = rest.strip() or prefix
        # mute:tag:x 形态：rest 仍含冒号，取末段展示
        if ":" in label:
            label = label.split(":")[-1].strip() or label
        chips.append({"text": "{0} {1}".format(icon, label)})
    return chips


# =========================================================================
# 视图模型
# =========================================================================
def _view_item(
    item: Dict[str, Any],
    board_name: str,
    now: datetime,
    avatar_dir: Path,
    avatar_cache: Dict[str, str],
) -> Dict[str, Any]:
    sig = str(item.get("sig") or "")
    dt = utils.parse_date(item.get("date"))
    handle = _handle_of(item)
    sources = [str(s) for s in (item.get("source_list") or []) if s]
    if not sources and item.get("source"):
        sources = [str(item.get("source"))]

    return {
        "anchor": "item-{0}-{1}".format(board_name, sig or str(id(item))),
        "sig": sig,
        "display_title": channels_mod.display_title(item),
        "raw_title": str(item.get("title") or ""),
        "url": str(item.get("url") or ""),
        "summary": _summary_of(item),
        "source": str(item.get("source") or ""),
        "sources_text": " + ".join(sources),
        "date_text": _humanize(dt, now),
        "date_abs": utils.iso(dt) if dt is not None else "",
        "ts": str(int(dt.timestamp())) if dt is not None else "0",
        "hotness": "{0:.4f}".format(_float(item.get("hotness"))),
        "weight": "{0:.2f}".format(_float(item.get("weight"))),
        "badges": _badges_of(item),
        "tier": str(_extra(item).get("tier") or ""),
        "avatar_src": _load_avatar(handle, avatar_dir, avatar_cache),
        "avatar_color": _avatar_color(handle or str(item.get("source") or "")),
        "avatar_initial": _avatar_initial(item),
        "personal_score": "{0:.4f}".format(_personal_score(item) or 0.0),
        "personal_reasons": _reason_chips(_extra(item).get("personal_reasons")),
    }


def _persona_view(
    persona: Dict[str, Any],
    now: datetime,
    avatar_dir: Path,
    avatar_cache: Dict[str, str],
) -> Dict[str, Any]:
    """把一条 persona（``pipeline.persona.build_personas`` 产物）整理成卡片视图模型。"""
    handle = str(persona.get("handle") or "").strip().lstrip("@")
    name = str(persona.get("name") or handle or "?")
    avatar_src = (
        _load_avatar(handle, avatar_dir, avatar_cache)
        or _load_avatar(handle.casefold(), avatar_dir, avatar_cache)
    )
    initial = "?"
    for ch in (name or handle or "?"):
        if not ch.isspace():
            initial = ch.upper()
            break

    top_items = []
    for it in (persona.get("top_items") or [])[:3]:
        if not isinstance(it, dict):
            continue
        top_items.append({
            "title": str(it.get("title") or ""),
            "url": str(it.get("url") or ""),
            "engagement": str(it.get("engagement") or 0),
        })

    topics = [{"text": str(t)} for t in (persona.get("topics") or []) if str(t).strip()]

    last_dt = utils.parse_date(persona.get("last_active"))
    return {
        "handle": handle,
        "name": name,
        "bio": str(persona.get("bio") or ""),
        "avatar_src": avatar_src,
        "avatar_color": _avatar_color(handle or name),
        "avatar_initial": initial,
        "recent_focus": str(persona.get("recent_focus") or ""),
        "topics": topics,
        "top_items": top_items,
        "total_engagement": str(persona.get("total_engagement") or 0),
        "item_count": str(persona.get("item_count") or 0),
        "avg_engagement": str(persona.get("avg_engagement") or 0),
        "last_active_text": _humanize(last_dt, now) if last_dt is not None else "",
    }


def _json_blob(rows: Sequence[Dict[str, Any]]) -> str:
    """内联 ``<script type="application/json">`` 数据块（转义掉可能提前闭合脚本的字符）。"""
    text = json.dumps(rows, ensure_ascii=False, separators=(",", ":"))
    return text.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


def build_context(
    items: Sequence[Dict[str, Any]],
    channel_map: Optional[Dict[str, Sequence[Dict[str, Any]]]] = None,
    now: Optional[datetime] = None,
    personas: Optional[Sequence[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """把统一池 + 频道路由结果整理成模板上下文（纯函数，便于单测）。

    ``personas`` 省略/为空 → 不渲染「人物画像」版面（向后兼容）；池内任一条目带
    ``extra.personal_score`` → 追加「为你推荐」版面（按 personal_score 降序）。
    """
    now = utils.as_utc(now or utils.now_utc())
    pool = [it for it in (items or []) if isinstance(it, dict)]
    pool_sorted = sorted(pool, key=lambda it: _float(it.get("hotness")), reverse=True)

    if channel_map is None:
        channel_map = channels_mod.route(pool, channels_mod.load_channels())

    titles = {c.get("name"): c.get("title") or c.get("name") for c in channels_mod.load_channels()}

    try:
        avatar_dir = paths.resolve_data_dir() / AVATAR_DIR
    except Exception as exc:  # noqa: BLE001 - 渲染不因路径问题失败
        logger.warning("头像目录解析失败，全部使用首字母占位: %s", exc)
        avatar_dir = Path(AVATAR_DIR)
    avatar_cache: Dict[str, str] = {}

    boards: List[Dict[str, Any]] = []
    index_rows: List[Dict[str, Any]] = []

    def add_board(name: str, title: str, desc: str, board_items: Sequence[Dict[str, Any]]) -> None:
        views = [
            _view_item(it, name, now, avatar_dir, avatar_cache)
            for it in board_items
            if isinstance(it, dict)
        ]
        boards.append({
            "name": name,
            "title": title,
            "desc": desc,
            "count": len(views),
            "is_empty": not views,
            "items": views,
        })
        for view in views:
            index_rows.append({
                "id": view["anchor"],
                "t": view["display_title"],
                "s": "{0} {1} {2}".format(
                    view["display_title"], view["raw_title"], view["sources_text"]
                ).lower(),
                "u": view["url"],
                "b": title,
                "src": view["sources_text"],
                "h": view["hotness"],
            })

    add_board(
        "hotlist",
        "🔥 全局热榜",
        "全池 Top {0} · 按热度排序".format(HOTLIST_LIMIT),
        pool_sorted[:HOTLIST_LIMIT],
    )
    for name, channel_items in (channel_map or {}).items():
        add_board(
            str(name),
            str(titles.get(name) or name),
            "频道 {0}".format(name),
            list(channel_items or []),
        )

    # 「为你推荐」：池内存在 personal_score 才渲染（未个性化时保持向后兼容，不入 palette 索引）
    scored = [it for it in pool if _personal_score(it) is not None]
    personalized_views: List[Dict[str, Any]] = []
    if scored:
        scored.sort(key=lambda it: _personal_score(it) or 0.0, reverse=True)
        personalized_views = [
            _view_item(it, "personalized", now, avatar_dir, avatar_cache)
            for it in scored[:PERSONALIZED_LIMIT]
        ]

    # 「人物画像」：personas 省略/为空则不渲染
    persona_views: List[Dict[str, Any]] = []
    for persona in (personas or []):
        if isinstance(persona, dict):
            persona_views.append(_persona_view(persona, now, avatar_dir, avatar_cache))

    return {
        "version": __version__,
        "page_title": PAGE_TITLE,
        "subtitle": PAGE_SUBTITLE,
        "generated_at": now.strftime("%Y-%m-%d %H:%M UTC"),
        "total_items": len(pool),
        "board_count": len(boards),
        "heavy_count": sum(1 for it in pool if "heavy" in (it.get("badges") or [])),
        "flash_count": sum(1 for it in pool if "flash" in (it.get("badges") or [])),
        "boards": boards,
        "personalized": personalized_views,
        "personalized_count": len(personalized_views),
        "personas": persona_views,
        "personas_count": len(persona_views),
        "data_json": _json_blob(index_rows),
    }


# =========================================================================
# 渲染
# =========================================================================
def load_template() -> str:
    """读 ``templates/digest.html.jinja``；缺失时抛 ``FileNotFoundError``。"""
    path = paths.templates_dir() / TEMPLATE_NAME
    if not path.is_file():
        raise FileNotFoundError("简报模板缺失: {0}".format(path))
    return path.read_text(encoding="utf-8")


def render_html(
    items: Sequence[Dict[str, Any]],
    channel_map: Optional[Dict[str, Sequence[Dict[str, Any]]]] = None,
    personas: Optional[Sequence[Dict[str, Any]]] = None,
    out_path: Any = None,
) -> str:
    """渲染单文件自包含 HTML 简报并写盘，返回 HTML 文本。

    ``channel_map`` 为 ``None`` 时自行按 channels.yaml 路由（不写频道页）；
    ``personas`` 省略/为空则不渲染「人物画像」版面（向后兼容）；池内带
    ``extra.personal_score`` 时追加「为你推荐」版面；
    ``out_path`` 为 ``None`` 时写 ``$QLY_DATA_DIR/digest.html``，传入 ``False`` 表示只渲染不写盘。
    """
    context = build_context(items, channel_map, personas=personas)
    html_text = minitpl.render(load_template(), context)

    if out_path is not False:
        target = Path(str(out_path)) if out_path else paths.data_path(OUTPUT_NAME)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(html_text, encoding="utf-8")
            logger.info("简报已写出: %s（%d 字节 / %d 个版面）",
                        target, len(html_text), context["board_count"])
        except OSError as exc:
            logger.warning("简报写盘失败 %s: %s", target, exc)

    return html_text
