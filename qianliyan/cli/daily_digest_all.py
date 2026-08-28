"""cli/daily_digest_all.py —— 日报编排（深读 / 浅读双视图，spec-v0.3 §6 / §16）。

流水线：``--prepare``（候选按 ``extra.personal_score`` 降序、缺失回退 hotness，取
全局 top 40 ∪ 各频道 top 5 写选稿草案）→ 人工/Agent 编辑 ``selected`` 字段 →
``--check``（校验草案）→ ``--write-prompt``（生成选稿提示词供 Agent 参考）→
``--finalize``（读入已选条目，做深读精读增强：LLM 可选、回退不阻塞，补
``distill`` 四段 + ``theses`` + ``images``）→ ``--html``（从同一份
``digest-final.json`` 渲染 **浅读 / 深读 / 带切换的合并页**）。

三种产物（spec-v0.3 §6）：

* **浅读** ``archive/<date>/glance.html``：按 ``extra.format`` 分组的极速标题流，
  一眼扫完即「已接收」，localStorage 记已读态并置灰；
* **深读** ``archive/<date>/deep.html``：每条一张精读卡，突出图片与论点，
  distill 四段 + 交叉验证展开；
* **合并页** ``archive/<date>/digest.html``：顶部「浅读 / 深读」切换（默认浅读），
  并复制一份到数据根 ``daily.html``。

V2 PNG 文生图路线已被 HTML 路线取代（intent.md 非目标），``build_v2_png_prompt``
只保留函数壳以兼容旧引用，不再有任何下游调用。
"""

from __future__ import annotations

import argparse
import logging
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ..core import llm_client, paths, storage, utils
from ..engine import article as article_engine
from ..engine import youtube_transcript
from ..pipeline import channels, minitpl

logger = logging.getLogger("qianliyan.cli.daily_digest_all")

DRAFT_NAME = "digest-draft.json"
FINAL_NAME = "digest-final.json"
PROMPT_NAME = "prompt.md"
MERGED_NAME = "digest.html"
GLANCE_NAME = "glance.html"
DEEP_NAME = "deep.html"
DAILY_ROOT_NAME = "daily.html"

GLANCE_TEMPLATE = "glance.html.jinja"
DEEP_TEMPLATE = "deep.html.jinja"

PAGE_TITLE = "千里眼 · 每日日报"

TOP_N_GLOBAL = 40
TOP_N_CHANNEL = 5

#: extra.format → 浅读/深读图标（spec-v0.3 §16）
FORMAT_ICONS = {
    "news": "📰", "blog": "📝", "video": "🎬", "talk": "🎤",
    "podcast": "🎧", "repo": "📦", "paper": "📄", "x": "🐦",
}
#: extra.format → 中文分组名
FORMAT_LABELS = {
    "news": "资讯", "blog": "博客", "video": "视频", "talk": "演讲",
    "podcast": "播客", "repo": "仓库", "paper": "论文", "x": "X 动态",
}
#: 分组呈现顺序（未出现的 format 追加在后）
FORMAT_ORDER = ("news", "blog", "paper", "talk", "video", "podcast", "repo", "x")

#: extra.corroboration.verdict（Wave H1 变更情报写入）→ 深读卡叙事↔实证徽章（spec-v0.3 §19.3）
CORROBORATION_LABELS = {
    "corroborated": "🔬 实证",
    "unverified": "🔬 存疑",
    "contradicted": "🔬 矛盾",
}

#: 选稿草案精简字段（保留渲染 + 选稿判断 + 深读增强所需的最小集合）
DRAFT_FIELDS = (
    "sig", "title", "url", "source", "source_kind", "backend", "source_list",
    "date", "hotness", "weight", "cross_refs", "tags", "badges", "summary",
    "metrics", "extra",
)

#: 编辑（Agent 或人）可直接写进草案条目的字段——``--finalize`` 一律**尊重已写入的值**，
#: 不用自动生成覆盖。这是本项目「Agent 在环」的落点：选稿、中文化、深读提炼这些需要
#: 判断力的活由编辑做，代码只负责取原料（正文/字幕）与渲染。
EDITOR_FIELDS = ("title_zh", "summary_zh", "editor_note", "distill")
#: 摘要短于此字符数就认为"深读没有原料"，去抓正文（索引页抓取常只有标题，摘要为空）
THIN_SUMMARY_CHARS = 200
#: 正文抓取上限，避免个别超长文把草案撑爆
FULLTEXT_MAX_CHARS = 12000

#: intent.md「设计哲学四条铁律」——与 cli.deliver 同源文案，独立维护避免跨 cli 模块耦合
IRON_LAWS = (
    "1. 以本地数据为准，不凭记忆——一切转述都必须来自落盘的数据底座。",
    "2. 每条必带 URL——可溯源是底线。",
    "3. 简体中文 + 人话——面向中文读者的最终呈现。",
    "4. 交叉验证是核心价值——多源报道自动加权（cross_refs → hotness 加成 → 重磅标记）。",
)

_SENT_SPLIT = re.compile(r"(?<=[。！？.!?])\s+|(?<=[。！？])")


def _today() -> str:
    return utils.now_utc().strftime("%Y-%m-%d")


def _archive_path(date_str: str, name: str):
    return paths.data_path("archive", date_str, name)


def _load_draft(date_str: str) -> Optional[Dict[str, Any]]:
    doc = storage.read_json(_archive_path(date_str, DRAFT_NAME), default=None)
    return doc if isinstance(doc, dict) else None


def _extra(entry: Dict[str, Any]) -> Dict[str, Any]:
    extra = entry.get("extra")
    return extra if isinstance(extra, dict) else {}


def _metrics(entry: Dict[str, Any]) -> Dict[str, Any]:
    metrics = entry.get("metrics")
    return metrics if isinstance(metrics, dict) else {}


def _slim_item(item: Dict[str, Any]) -> Dict[str, Any]:
    slim = {field: item.get(field) for field in DRAFT_FIELDS}
    slim["selected"] = False
    slim["editor_note"] = ""
    return slim


def _rank_key(item: Dict[str, Any]) -> float:
    """候选排序键：``extra.personal_score`` 优先，缺失回退 ``hotness``（spec-v0.3 §6）。"""
    ps = _extra(item).get("personal_score")
    if ps is not None:
        try:
            return float(ps)
        except (TypeError, ValueError):
            pass
    try:
        return float(item.get("hotness") or 0.0)
    except (TypeError, ValueError):
        return 0.0


# =========================================================================
# format 推断（spec-v0.3 §12：Wave E 落 extra.format，缺失则按 backend/source 兜底）
# =========================================================================
def infer_format(item: Dict[str, Any]) -> str:
    """推断条目呈现类型 ∈ FORMAT_ICONS：优先 ``extra.format``，否则按信号兜底归类。"""
    extra = _extra(item)
    fmt = str(extra.get("format") or "").strip().lower()
    if fmt in FORMAT_ICONS:
        return fmt

    tags = {str(t).strip().lower() for t in (item.get("tags") or [])}
    source_kind = str(item.get("source_kind") or "").lower()
    backend = str(item.get("backend") or "").lower()
    platform = str(extra.get("platform") or "").lower()

    if source_kind == "builders" or platform in ("x", "twitter") or (tags & {"x", "twitter"}):
        return "x"
    if backend == "arxiv" or (tags & {"arxiv", "paper", "research"}):
        return "paper"
    if "podcast" in tags:
        return "podcast"
    if tags & {"talk", "conference"}:
        return "talk"
    if platform == "youtube" or "video" in tags:
        return "video"
    if backend == "git" or (tags & {"repo", "trending", "github"}):
        return "repo"
    if tags & {"blog", "official"}:
        return "blog"
    return "news"


# =========================================================================
# 深读精读增强（distill + images）
# =========================================================================
def _first_sentences(summary: Any, n: int = 3) -> List[str]:
    """把摘要按中英文句末标点切分，返回前 N 句（回退 distill 的 ``kp`` 用）。"""
    text = str(summary or "").strip()
    if not text:
        return []
    parts = [p.strip() for p in _SENT_SPLIT.split(text) if p and p.strip()]
    if not parts:
        parts = [text]
    return parts[:n]


def _maybe_attach_fulltext(entry: Dict[str, Any]) -> bool:
    """摘要太薄时抓单篇正文存进 ``extra.fulltext``；抓到返回 True。

    索引页抓取（``type: scrape`` 的官网 blog）往往只拿到标题、摘要为空——**深读因此
    没有原料**。这里对选中的条目补一次单篇抓取。失败/离线静默回退，绝不阻塞。
    """
    summary = str(entry.get("summary") or "").strip()
    if len(summary) >= THIN_SUMMARY_CHARS:
        return False
    extra = _extra(entry)
    if str(extra.get("transcript") or "").strip():
        return False           # video/talk 已有字幕全文，不必再抓网页
    if str(extra.get("fulltext") or "").strip():
        return False           # 已抓过（草案里带着），不重复
    url = str(entry.get("url") or "").strip()
    if not url:
        return False

    try:
        result = article_engine.fetch_article(url)
    except Exception as exc:  # noqa: BLE001 - 取正文属尽力而为，绝不阻塞 finalize
        logger.warning("正文抓取失败 (%s): %s", url, exc)
        return False

    text = str(result.get("text") or "").strip()
    if not text:
        return False
    if not isinstance(entry.get("extra"), dict):
        entry["extra"] = {}
    entry["extra"]["fulltext"] = text[:FULLTEXT_MAX_CHARS]
    # 摘要空时顺手用首段补上，浅读列表才有一句话可看
    if not summary and result.get("lead"):
        entry["summary"] = str(result["lead"])
    return True


def _distill_source_text(entry: Dict[str, Any]) -> str:
    """深读 distill 的输入文本：字幕全文（video/talk）> 网页正文 > 摘要。"""
    extra = _extra(entry)
    for key in ("transcript", "fulltext"):
        value = str(extra.get(key) or "").strip()
        if value:
            return value
    return str(entry.get("summary") or "").strip()


def _distill_fallback(entry: Dict[str, Any]) -> Dict[str, Any]:
    """规则回退：``kp`` = 输入文本（字幕全文/摘要）前 3 句、``theses`` = []、其余留空（spec-v0.3 §6/§18.2）。"""
    return {
        "kp": _first_sentences(_distill_source_text(entry), 3),
        "chain": "",
        "pull": "",
        "limits": "",
        "theses": [],
    }


def _distill_llm(entry: Dict[str, Any], client: "llm_client.LLMClient") -> Optional[Dict[str, Any]]:
    """LLM 深读增强：补 kp/chain/pull/limits/theses；失败/非法返回 None（调用方保留回退）。"""
    fmt = infer_format(entry)
    has_transcript = bool(_extra(entry).get("transcript"))
    hint = "该条为演讲/视频，theses 侧重「讲了什么观点」。" if fmt in ("talk", "video") else ""
    source_label = "字幕全文" if has_transcript else "摘要"
    prompt = (
        "请基于下列 AI 资讯条目做深读精读增强，输出 JSON，字段：\n"
        "kp（关键要点，字符串数组，2-4 条）、chain（一句话脉络/因果）、\n"
        "pull（一句话影响，为什么值得关注）、limits（一句话局限或需注意之处）、\n"
        "theses（关键论点，字符串数组，1-4 条）。\n"
        + hint
        + "\n只输出 JSON，不要任何解释文字。\n\n"
        "标题：{0}\n{1}：{2}\nURL：{3}\n".format(
            entry.get("title") or "", source_label, _distill_source_text(entry), entry.get("url") or ""
        )
    )
    data = client.complete_json(prompt)
    if not isinstance(data, dict):
        return None
    kp = [str(x).strip() for x in (data.get("kp") or []) if str(x).strip()]
    theses = [str(x).strip() for x in (data.get("theses") or []) if str(x).strip()]
    return {
        "kp": kp or _first_sentences(_distill_source_text(entry), 3),
        "chain": str(data.get("chain") or ""),
        "pull": str(data.get("pull") or ""),
        "limits": str(data.get("limits") or ""),
        "theses": theses,
    }


def _collect_images(entry: Dict[str, Any]) -> List[str]:
    """合并 ``extra.og_image`` + ``extra.images``（aihot 描述内嵌图），保序去重。"""
    extra = _extra(entry)
    out: List[str] = []
    seen = set()

    def _add(value: Any) -> None:
        s = str(value or "").strip()
        if s and s not in seen:
            seen.add(s)
            out.append(s)

    _add(extra.get("og_image"))
    images = extra.get("images")
    if isinstance(images, (list, tuple)):
        for img in images:
            _add(img)
    return out


def _maybe_attach_transcript(record: Dict[str, Any]) -> bool:
    """对 ``extra.format ∈ {video, talk}`` 且有 ``extra.video_id`` 的条目抓字幕全文存
    ``extra.transcript``（spec-v0.3 §18.2）。失败/无字幕/离线一律静默回退，**绝不阻塞**。"""
    extra = record.get("extra")
    if not isinstance(extra, dict):
        return False
    if infer_format(record) not in ("video", "talk"):
        return False
    video_id = str(extra.get("video_id") or "").strip()
    if not video_id:
        return False
    try:
        text = youtube_transcript.get_transcript(video_id)
    except Exception as exc:  # noqa: BLE001 - get_transcript 本就不抛，这里再兜一层双保险
        logger.warning("字幕抓取异常，回退 media:description (video_id=%s): %s", video_id, exc)
        text = None
    if text and text.strip():
        extra["transcript"] = text.strip()
        return True
    return False


def _make_client() -> Tuple[Optional["llm_client.LLMClient"], bool]:
    """构造 LLM 客户端并判定可用性（任何异常按不可用处理，回退不阻塞）。"""
    try:
        client = llm_client.LLMClient.from_env()
        return client, bool(client.is_available())
    except Exception as exc:  # noqa: BLE001 - 可用性判定失败按不可用处理
        logger.warning("LLM 可用性判定异常，深读增强走回退: %s", exc)
        return None, False


# =========================================================================
# --prepare
# =========================================================================
def cmd_prepare(date_str: str) -> int:
    """候选按 personal_score（回退 hotness）取全局 top 40 ∪ 各频道 top 5，写选稿草案。"""
    items = storage.read_jsonl(paths.data_path("items.jsonl"))
    if not items:
        print("items.jsonl 为空或不存在，请先执行一次 `python -m qianliyan.cli.sync`。")
        return 1

    ranked = sorted(items, key=_rank_key, reverse=True)
    chosen: "Dict[str, Dict[str, Any]]" = {}
    for item in ranked[:TOP_N_GLOBAL]:
        sig = item.get("sig")
        if sig:
            chosen.setdefault(sig, item)

    channel_defs = channels.load_channels()
    routed = channels.route(items, channel_defs)
    for _, channel_items in routed.items():
        for item in channel_items[:TOP_N_CHANNEL]:
            sig = item.get("sig")
            if sig:
                chosen.setdefault(sig, item)

    ordered = sorted(chosen.values(), key=_rank_key, reverse=True)
    draft = {
        "date": date_str,
        "generated_at": utils.iso(utils.now_utc()),
        "items": [_slim_item(it) for it in ordered],
    }
    storage.write_json(_archive_path(date_str, DRAFT_NAME), draft)
    print(
        "draft 已写出（{0} 条，个性化 top {1} ∪ 各频道 top {2}）：{3}".format(
            len(draft["items"]), TOP_N_GLOBAL, TOP_N_CHANNEL, _archive_path(date_str, DRAFT_NAME)
        )
    )
    return 0


# =========================================================================
# --check
# =========================================================================
def cmd_check(date_str: str) -> int:
    """校验草案：文件存在、每条有 url；``selected`` 全 false 属 --prepare 后正常状态。"""
    draft = _load_draft(date_str)
    if draft is None:
        print("draft 不存在: {0}（请先执行 --prepare）".format(_archive_path(date_str, DRAFT_NAME)))
        return 1

    entries = draft.get("items") or []
    problems = [
        "第 {0} 条缺少 url（sig={1}）".format(idx, entry.get("sig"))
        for idx, entry in enumerate(entries)
        if not entry.get("url")
    ]
    if problems:
        print("draft 校验失败：")
        for problem in problems:
            print("  - {0}".format(problem))
        return 1

    selected = [entry for entry in entries if entry.get("selected")]
    print("draft 校验通过：共 {0} 条，已选 {1} 条。".format(len(entries), len(selected)))
    if not selected:
        print("提示：selected 全为 false，属 --prepare 后正常状态，待人工/Agent 选稿后再 --finalize。")
    return 0


# =========================================================================
# --write-prompt
# =========================================================================
def cmd_write_prompt(date_str: str) -> int:
    """生成 ``archive/<date>/prompt.md``：给 Agent 的选稿提示词。"""
    draft = _load_draft(date_str)
    if draft is None:
        print("draft 不存在: {0}（请先执行 --prepare）".format(_archive_path(date_str, DRAFT_NAME)))
        return 1

    entries = draft.get("items") or []
    lines: List[str] = [
        "# 千里眼日报选稿提示词 · {0}".format(date_str),
        "",
        "## 转述四条铁律（Agent 必须遵循）",
    ]
    lines.extend(IRON_LAWS)
    lines.append("")
    lines.append("## 待选稿目录（{0} 条）".format(len(entries)))
    for idx, entry in enumerate(entries, start=1):
        title = entry.get("title") or ""
        hotness = entry.get("hotness")
        sources = "+".join(entry.get("source_list") or [entry.get("source") or ""])
        lines.append(
            "{0}. [{1:.4f}] {2}（{3}） — {4}".format(idx, float(hotness or 0.0), title, sources, entry.get("url") or "")
        )
    lines.append("")
    lines.append("## 回写说明")
    lines.append("1. 打开 `archive/{0}/digest-draft.json`；".format(date_str))
    lines.append("2. 把入选条目的 `selected` 改为 `true`，可在 `editor_note` 写选稿理由；")
    lines.append(
        "3. 保存后执行 `python -m qianliyan.cli.daily_digest_all --date {0} --check` 校验；".format(date_str)
    )
    lines.append(
        "4. 校验通过后执行 "
        "`python -m qianliyan.cli.daily_digest_all --date {0} --finalize --html` 生成最终简报。".format(date_str)
    )
    lines.append("")

    path = _archive_path(date_str, PROMPT_NAME)
    try:
        path.write_text("\n".join(lines), encoding="utf-8")
    except OSError as exc:
        print("写 prompt.md 失败: {0}".format(exc))
        return 1
    print("prompt.md 已写出: {0}".format(path))
    return 0


# =========================================================================
# --finalize
# =========================================================================
def cmd_finalize(date_str: str, do_html: bool) -> int:
    """读 draft 的 selected 条目，做深读增强（LLM 可选、回退不阻塞），写 digest-final.json。"""
    draft = _load_draft(date_str)
    if draft is None:
        print("draft 不存在: {0}（请先执行 --prepare）".format(_archive_path(date_str, DRAFT_NAME)))
        return 1

    selected = [entry for entry in (draft.get("items") or []) if entry.get("selected")]
    if not selected:
        print("没有已选条目（selected 全为 false），无法 finalize；请先编辑草案或跑 --write-prompt。")
        return 1

    client, available = _make_client()

    finalized: List[Dict[str, Any]] = []
    distilled_count = 0
    transcript_count = 0
    fulltext_count = 0
    editor_distill_count = 0
    for entry in selected:
        record = dict(entry)
        # extra 浅拷贝，避免抓字幕/正文写 extra 时污染原草案条目
        if isinstance(record.get("extra"), dict):
            record["extra"] = dict(record["extra"])
        # video/talk 先抓字幕全文；其余摘要太薄的抓网页正文（失败/离线静默回退，不阻塞）
        if _maybe_attach_transcript(record):
            transcript_count += 1
        if _maybe_attach_fulltext(record):
            fulltext_count += 1

        # 编辑（Agent/人）已在草案里写好的深读，优先于任何自动生成
        editor_distill = entry.get("distill")
        if isinstance(editor_distill, dict) and any(editor_distill.get(k) for k in editor_distill):
            distill = editor_distill
            editor_distill_count += 1
        else:
            distill = _distill_fallback(record)
            if available and client is not None:
                try:
                    enhanced = _distill_llm(record, client)
                except Exception as exc:  # noqa: BLE001 - 深读增强失败该条回退，不影响其余
                    logger.warning("深读增强失败，回退 (sig=%s): %s", record.get("sig"), exc)
                    enhanced = None
                if enhanced:
                    distill = enhanced
                    distilled_count += 1
        record["distill"] = distill
        record["images"] = _collect_images(record)
        record["format"] = infer_format(record)
        finalized.append(record)

    final_doc = {
        "date": date_str,
        "generated_at": utils.iso(utils.now_utc()),
        "items": finalized,
    }
    storage.write_json(_archive_path(date_str, FINAL_NAME), final_doc)
    print(
        "finalize 完成：{0} 条精选条目（编辑深读 {1} 条，LLM 深读 {2} 条，"
        "字幕全文 {3} 条，网页正文 {4} 条，其余走回退）".format(
            len(finalized), editor_distill_count, distilled_count,
            transcript_count, fulltext_count,
        )
    )

    if do_html:
        return _render_daily_html(date_str, finalized)
    return 0


def cmd_html_only(date_str: str) -> int:
    """单独 ``--html``（未同时 --finalize）时，复用已有 ``digest-final.json``。"""
    final_doc = storage.read_json(_archive_path(date_str, FINAL_NAME), default=None)
    if not isinstance(final_doc, dict) or not final_doc.get("items"):
        print("digest-final.json 不存在或为空，请先执行 --finalize（可与 --html 一起）。")
        return 1
    return _render_daily_html(date_str, final_doc.get("items") or [])


# =========================================================================
# 视图模型
# =========================================================================
def _display_title(entry: Dict[str, Any]) -> str:
    """中文标题优先。编辑写在条目顶层的 ``title_zh`` 优先于自动翻译写进 ``extra`` 的。"""
    for candidate in (entry.get("title_zh"), _extra(entry).get("title_zh")):
        if candidate and str(candidate).strip():
            return str(candidate).strip()
    return str(entry.get("title") or "")


def _summary_text(entry: Dict[str, Any]) -> str:
    """中文摘要优先，同 :func:`_display_title` 的优先级。"""
    for candidate in (entry.get("summary_zh"), _extra(entry).get("summary_zh")):
        if candidate and str(candidate).strip():
            return str(candidate).strip()
    return str(entry.get("summary") or "").strip()


def _sources_text(entry: Dict[str, Any]) -> str:
    names = [str(s) for s in (entry.get("source_list") or []) if s]
    if not names and entry.get("source"):
        names = [str(entry.get("source"))]
    return " + ".join(names)


def _source_count(entry: Dict[str, Any]) -> int:
    names = [s for s in (entry.get("source_list") or []) if s]
    return len(names) if names else (1 if entry.get("source") else 0)


def _cross_badge(entry: Dict[str, Any]) -> str:
    """浅读交叉验证徽章：📈 重磅 / ⚡ 一手速报 / N 源。"""
    badges = entry.get("badges") or []
    parts: List[str] = []
    if "heavy" in badges:
        parts.append("📈")
    if "flash" in badges:
        parts.append("⚡")
    count = _source_count(entry)
    if count > 1:
        parts.append("{0}源".format(count))
    return " ".join(parts)


def _cross_parts(entry: Dict[str, Any]) -> List[str]:
    badges = entry.get("badges") or []
    parts: List[str] = []
    if "heavy" in badges:
        parts.append("📈 重磅")
    if "flash" in badges:
        parts.append("⚡ 一手速报")
    count = _source_count(entry)
    if count > 1:
        parts.append("{0} 源交叉".format(count))
    return parts


def _reltime(entry: Dict[str, Any], now) -> str:
    dt = utils.parse_date(entry.get("date"))
    if dt is None:
        return "时间未知"
    seconds = (now - dt).total_seconds()
    if seconds < 3600:
        return "刚刚" if seconds < 60 else "{0} 分钟前".format(int(seconds // 60))
    if seconds < 86400:
        return "{0} 小时前".format(int(seconds // 3600))
    if seconds < 86400 * 30:
        return "{0} 天前".format(int(seconds // 86400))
    return dt.strftime("%Y-%m-%d")


def _timeline_key(entry: Dict[str, Any]) -> float:
    """时间轴排序键：发布时间的 unix 秒；解析不出的排到最后（不是排到最前）。"""
    dt = utils.parse_date(entry.get("date"))
    if dt is None:
        return float("-inf")
    try:
        return dt.timestamp()
    except (OverflowError, OSError, ValueError):
        return float("-inf")


def _grouped_by_format(items: Sequence[Dict[str, Any]], now) -> List[Dict[str, Any]]:
    """按 format 分组（FORMAT_ORDER 优先），**组内按时间轴倒序**（新的在前）。

    浅读是"扫一遍就知道今天发生了什么"，时间顺序比热度顺序更符合这个用途——
    热度排序留给聚合页 digest.html。
    """
    buckets: "Dict[str, List[Dict[str, Any]]]" = {}
    items = sorted(items, key=_timeline_key, reverse=True)
    for entry in items:
        fmt = infer_format(entry)
        row = {
            "sig": str(entry.get("sig") or ""),
            "icon": FORMAT_ICONS.get(fmt, "•"),
            "title": _display_title(entry),
            "url": str(entry.get("url") or ""),
            "source": _sources_text(entry),
            "date_text": _reltime(entry, now),
            "cross": _cross_badge(entry),
        }
        buckets.setdefault(fmt, []).append(row)

    ordered_fmts = [f for f in FORMAT_ORDER if f in buckets]
    ordered_fmts += [f for f in buckets if f not in FORMAT_ORDER]
    groups: List[Dict[str, Any]] = []
    for fmt in ordered_fmts:
        groups.append({
            "format": fmt,
            "icon": FORMAT_ICONS.get(fmt, "•"),
            "label": FORMAT_LABELS.get(fmt, fmt),
            "count": len(buckets[fmt]),
            "items": buckets[fmt],
        })
    return groups


def _corroboration_view(extra: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """叙事↔实证映射视图（Wave H1 写入 ``extra.corroboration``）；缺字段/未知 verdict → None 不显示。"""
    corrob = extra.get("corroboration")
    if not isinstance(corrob, dict):
        return None
    verdict = str(corrob.get("verdict") or "").strip().lower()
    if verdict not in CORROBORATION_LABELS:
        return None
    return {
        "verdict": verdict,
        "label": CORROBORATION_LABELS[verdict],
        "claim": str(corrob.get("claim") or "").strip(),
        "evidence": str(corrob.get("evidence") or "").strip(),
    }


def _deep_card(entry: Dict[str, Any], now) -> Dict[str, Any]:
    fmt = infer_format(entry)
    extra = _extra(entry)
    metrics = _metrics(entry)
    distill = entry.get("distill") if isinstance(entry.get("distill"), dict) else {}
    images = [str(u) for u in (entry.get("images") or []) if str(u).strip()]

    kp = [str(x) for x in (distill.get("kp") or []) if str(x).strip()]
    theses = [str(x) for x in (distill.get("theses") or []) if str(x).strip()]
    is_video = fmt in ("video", "talk")
    has_transcript = bool(str(extra.get("transcript") or "").strip())

    return {
        "sig": str(entry.get("sig") or ""),
        "format": fmt,
        "icon": FORMAT_ICONS.get(fmt, "•"),
        "label": FORMAT_LABELS.get(fmt, fmt),
        "title": _display_title(entry),
        "url": str(entry.get("url") or ""),
        "summary": _summary_text(entry),
        "badges": _cross_parts(entry),
        "source_list": [{"name": str(s)} for s in (entry.get("source_list") or [entry.get("source") or ""]) if s],
        "source_count": _source_count(entry),
        "images": images,
        "has_images": bool(images),
        "theses": theses,
        "kp": kp,
        "chain": str(distill.get("chain") or ""),
        "pull": str(distill.get("pull") or ""),
        "limits": str(distill.get("limits") or ""),
        # format 专属
        "is_video": is_video,
        "has_transcript": has_transcript,
        "corroboration": _corroboration_view(extra),
        "is_repo": fmt == "repo",
        "is_paper": fmt == "paper",
        "thumbnail": str(extra.get("thumbnail") or "") if is_video else "",
        "stars": str(metrics.get("stars") or "") if fmt == "repo" else "",
        "stars_period": str(metrics.get("stars_period") or "") if fmt == "repo" else "",
        "language": str(extra.get("language") or "") if fmt == "repo" else "",
        "arxiv_url": str(entry.get("url") or "") if fmt == "paper" else "",
    }


def _glance_context(date_str: str, items: Sequence[Dict[str, Any]], embed: bool, now) -> Dict[str, Any]:
    groups = _grouped_by_format(items, now)
    return {
        "embed": embed,
        "page_title": PAGE_TITLE,
        "date": date_str,
        "total": len(items),
        "group_count": len(groups),
        "groups": groups,
    }


def _deep_context(date_str: str, items: Sequence[Dict[str, Any]], embed: bool, now) -> Dict[str, Any]:
    cards = [_deep_card(entry, now) for entry in items]
    return {
        "embed": embed,
        "page_title": PAGE_TITLE,
        "date": date_str,
        "total": len(items),
        "cards": cards,
    }


# =========================================================================
# 渲染
# =========================================================================
def _load_template(name: str) -> str:
    path = paths.templates_dir() / name
    if not path.is_file():
        raise FileNotFoundError("日报模板缺失: {0}".format(path))
    return path.read_text(encoding="utf-8")


#: 合并页外壳（minitpl 渲染；浅读 / 深读切换，默认浅读）。内联 JS 避免出现 ``{{`` / ``%}``。
MERGED_SHELL = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="generator" content="qianliyan daily">
<title>{{ page_title }}</title>
<style>
:root { --t-bg: #f5f7fb; --t-card: #ffffff; --t-ink: #171a21; --t-muted: #6b7280;
  --t-line: #e5e8f0; --t-brand: #2f6df6; }
@media (prefers-color-scheme: dark) {
  :root { --t-bg: #12151c; --t-card: #1b1f28; --t-ink: #e8ebf2; --t-muted: #9aa1ad;
    --t-line: #2a2f3a; --t-brand: #6ea0ff; }
}
html, body { margin: 0; padding: 0; }
body { background: var(--t-bg); color: var(--t-ink);
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC",
               "Hiragino Sans GB", "Microsoft YaHei", "Noto Sans SC", sans-serif; }
/* 浅读/深读切换是**纯 CSS**（隐藏 radio + :checked 兄弟选择器），不依赖 JavaScript：
   这份 HTML 会被邮件客户端、聊天工具的内嵌预览、文件面板等沙箱环境打开，那些环境
   常常不执行页面脚本——切换是本页最基本的导航，不能一被沙箱就点不动。 */
.qly-switch { position: absolute; opacity: 0; pointer-events: none; }
.daily-toggle { position: sticky; top: 0; z-index: 40; display: flex; gap: 8px;
  justify-content: center; padding: 12px; background: var(--t-card);
  border-bottom: 1px solid var(--t-line); }
.daily-toggle label { border: 1px solid var(--t-line); background: transparent;
  color: var(--t-muted); border-radius: 999px; padding: 6px 20px; font: inherit;
  font-size: 14px; cursor: pointer; user-select: none; -webkit-user-select: none; }
.daily-toggle label:hover { border-color: var(--t-brand); color: var(--t-brand); }
#qly-pick-glance:checked ~ .daily-toggle label[for="qly-pick-glance"],
#qly-pick-deep:checked ~ .daily-toggle label[for="qly-pick-deep"] {
  background: var(--t-brand); color: #fff; border-color: var(--t-brand); }
/* 键盘可达：radio 获得焦点时给对应标签一个可见焦点环 */
#qly-pick-glance:focus-visible ~ .daily-toggle label[for="qly-pick-glance"],
#qly-pick-deep:focus-visible ~ .daily-toggle label[for="qly-pick-deep"] {
  outline: 2px solid var(--t-brand); outline-offset: 2px; }
.qly-view { display: none; }
#qly-pick-glance:checked ~ #wrap-glance,
#qly-pick-deep:checked ~ #wrap-deep { display: block; }
</style>
</head>
<body>
<input class="qly-switch" type="radio" name="qly-view" id="qly-pick-glance" checked>
<input class="qly-switch" type="radio" name="qly-view" id="qly-pick-deep">
<div class="daily-toggle" aria-label="浅读 / 深读切换">
  <label for="qly-pick-glance">📑 浅读</label>
  <label for="qly-pick-deep">📖 深读</label>
</div>
<div class="qly-view" id="wrap-glance">{{ glance_body|safe }}</div>
<div class="qly-view" id="wrap-deep">{{ deep_body|safe }}</div>
</body>
</html>
"""


def render_glance(date_str: str, items: Sequence[Dict[str, Any]], embed: bool = False, now=None) -> str:
    now = utils.as_utc(now or utils.now_utc())
    return minitpl.render(_load_template(GLANCE_TEMPLATE), _glance_context(date_str, items, embed, now))


def render_deep(date_str: str, items: Sequence[Dict[str, Any]], embed: bool = False, now=None) -> str:
    now = utils.as_utc(now or utils.now_utc())
    return minitpl.render(_load_template(DEEP_TEMPLATE), _deep_context(date_str, items, embed, now))


def render_merged(date_str: str, items: Sequence[Dict[str, Any]], now=None) -> str:
    now = utils.as_utc(now or utils.now_utc())
    glance_frag = render_glance(date_str, items, embed=True, now=now)
    deep_frag = render_deep(date_str, items, embed=True, now=now)
    return minitpl.render(MERGED_SHELL, {
        "page_title": "{0} · {1}".format(PAGE_TITLE, date_str),
        "glance_body": glance_frag,
        "deep_body": deep_frag,
    })


def _write_text(path, text: str) -> bool:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return True
    except OSError as exc:
        logger.warning("写日报页失败 %s: %s", path, exc)
        return False


def _render_daily_html(date_str: str, items: Sequence[Dict[str, Any]]) -> int:
    """渲染浅读 / 深读 / 合并页三件套，合并页复制到数据根 ``daily.html``。"""
    now = utils.as_utc(utils.now_utc())
    try:
        glance_full = render_glance(date_str, items, embed=False, now=now)
        deep_full = render_deep(date_str, items, embed=False, now=now)
        merged = render_merged(date_str, items, now=now)
    except FileNotFoundError as exc:
        print("日报模板缺失，无法渲染 HTML: {0}".format(exc))
        return 1
    except Exception as exc:  # noqa: BLE001 - 渲染失败不许崩溃整条命令链
        logger.warning("日报 HTML 渲染意外失败: %s", exc)
        print("HTML 渲染失败: {0}".format(exc))
        return 1

    _write_text(_archive_path(date_str, GLANCE_NAME), glance_full)
    _write_text(_archive_path(date_str, DEEP_NAME), deep_full)
    merged_path = _archive_path(date_str, MERGED_NAME)
    _write_text(merged_path, merged)

    root_path = paths.data_path(DAILY_ROOT_NAME)
    _write_text(root_path, merged)

    print(
        "日报 HTML 已写出：浅读 {0} · 深读 {1} · 合并页 {2}（并复制为 {3}）".format(
            _archive_path(date_str, GLANCE_NAME),
            _archive_path(date_str, DEEP_NAME),
            merged_path,
            root_path,
        )
    )
    return 0


# =========================================================================
# V2 PNG 路线（deprecated）
# =========================================================================
def build_v2_png_prompt(draft: Dict[str, Any]) -> str:
    """V2 PNG 文生图路线的选稿提示词构造器。

    .. deprecated::
        日报主路线已改为 HTML（本文件的 ``--finalize --html`` 的深读/浅读双视图），
        本函数只保留函数壳以兼容旧引用，**不再有任何下游调用**。
    """
    lines = ["[deprecated] V2 PNG 文生图路线已废弃，仅保留函数壳以兼容旧引用。"]
    for entry in (draft or {}).get("items", []) or []:
        lines.append("- {0}".format(entry.get("title") or ""))
    return "\n".join(lines)


# =========================================================================
# CLI
# =========================================================================
def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m qianliyan.cli.daily_digest_all",
        description="千里眼日报编排（深读 / 浅读双视图）",
    )
    parser.add_argument("--prepare", action="store_true", help="按 personal_score 取候选写选稿草案")
    parser.add_argument("--check", action="store_true", help="校验选稿草案")
    parser.add_argument("--write-prompt", action="store_true", help="生成选稿提示词 prompt.md")
    parser.add_argument("--finalize", action="store_true", help="读入已选条目，深读增强写 digest-final.json")
    parser.add_argument("--html", action="store_true", help="渲染浅读/深读/合并页（通常与 --finalize 连用）")
    parser.add_argument("--date", default=None, help="YYYY-MM-DD，缺省今天 (UTC)")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = _build_arg_parser()
    args = parser.parse_args(argv)
    date_str = args.date or _today()

    if not any([args.prepare, args.check, args.write_prompt, args.finalize, args.html]):
        parser.print_help()
        return 1

    exit_code = 0
    if args.prepare:
        exit_code = exit_code or cmd_prepare(date_str)
    if args.check:
        exit_code = exit_code or cmd_check(date_str)
    if args.write_prompt:
        exit_code = exit_code or cmd_write_prompt(date_str)
    if args.finalize:
        exit_code = exit_code or cmd_finalize(date_str, args.html)
    elif args.html:
        exit_code = exit_code or cmd_html_only(date_str)

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
