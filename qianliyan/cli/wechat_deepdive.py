"""cli/wechat_deepdive.py —— 公众号每天第二篇：一篇原文的**深度解读**（正文类型）。

贴图（N 秒读完）给广度，这一篇给深度：从当期头条 / 深读里挑一篇原料最厚的原文，编辑读全文写解读，
代码只做三件事——出写作简报、校验编辑交回的 JSON（关键数字必须逐字出自原文）、渲染成公众号正文 HTML
（行内样式，同首页 token）并出「深度解读」封面。

  python -m qianliyan.cli.wechat_deepdive --prompt [--sig SIG] [--date D]   打印写作简报
  python -m qianliyan.cli.wechat_deepdive --apply FILE [--date D]           校验并渲染

产物都在 ``archive/<日>/``：deepdive-prompt.md、deepdive.json、deepdive.html、deepdive.md、cover-deep.html/.png。
``qly-publish.sh deepdive-prompt / deepdive / wechat-deep`` 是给定时任务的固定入口。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ..core import paths, storage, utils
from ..pipeline import minitpl
from . import daily_digest_all as daily

PROMPT_NAME = "deepdive-prompt.md"
JSON_NAME = "deepdive.json"
HTML_NAME = "deepdive.html"
MD_NAME = "deepdive.md"
TEMPLATE = "deepdive.html.jinja"
COVER_HTML_NAME = "cover-deep.html"
COVER_PNG_NAME = "cover-deep.png"
COVER_KIND = "深度解读"

TITLE_MAX = 64          # 公众号文章标题上限
SUMMARY_MAX = 120       # 摘要上限
BODY_MIN, BODY_MAX = 1500, 6000   # 解读正文字数（导语 + 各节 + 局限 + 意味着什么）
SECTIONS_MIN, SECTIONS_MAX = 3, 6
SOURCE_MIN = 3000       # 原文少于这么多字的条目不配做深度解读（同深读准入门槛）


# =========================================================================
# 挑哪一篇
# =========================================================================
def _figures(entry: Dict[str, Any]) -> List[Dict[str, str]]:
    """可放进解读的图：原文信息图与示意图（og 封面、装饰图不要），带本地绝对路径给编辑亲眼看。"""
    out = []
    for fig in daily._figure_views(entry, daily.MEDIA_BASE_ARCHIVE):
        name = fig["src"][len(daily.MEDIA_BASE_ARCHIVE):]
        out.append(dict(fig, local=str(paths.data_path(daily.MEDIA_DIR, name))))
    return out


def candidates(items: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """头条 / 深读里原文 ≥ SOURCE_MIN 字的条目，按「有原文信息图 → 头条 → 原文更长」排。"""
    pool = [e for e in items if e.get("depth") in ("lead", "deep") and daily._source_chars(e) >= SOURCE_MIN]

    def key(e: Dict[str, Any]) -> Tuple[int, int, int]:
        has_fig = any(f["kind"] == "article" for f in _figures(e))
        return (int(has_fig), int(e.get("depth") == "lead"), daily._source_chars(e))

    return sorted(pool, key=key, reverse=True)


def _load(date_str: str) -> Dict[str, Any]:
    final = storage.read_json(daily._archive_path(date_str, daily.FINAL_NAME), default=None)
    if not isinstance(final, dict) or not final.get("items"):
        raise SystemExit("digest-final.json 不存在或为空：{0} 还没定稿".format(date_str))
    return final


# =========================================================================
# 写作简报
# =========================================================================
_GUIDE = """# 千里眼 · 深度解读写作简报（{date}）

贴图那篇给读者「今天发生了什么」，这一篇给「这一件事到底是怎么回事」。只解读下面这一篇原文。

## 写法
- 读者：做 AI 的工程师与产品人，懂行但没时间读英文长文。目标是读完这篇就不必再读原文，还知道该不该跟进。
- 只依据下面的原文全文：事实、人名、模型名、数字必须原文里有；原文没说的不要补，推断要写明「推断」。
- 结构：导语（一段，先给结论：这件事是什么、为什么值得花 5 分钟）→ {smin}～{smax} 节拆解（每节一个小标题 + 2～4 段，
  讲清机制 / 做法 / 结果，能配原文图就配，图下写「看这里：…」）→ 局限与存疑（原文没回答的、条件受限的、可能的偏差）
  → 对你意味着什么（给工程师 / 产品人的可操作判断）。
- 正文（导语 + 各节 + 局限 + 意味着什么）合计 {bmin}～{bmax} 字；中文书面语，短句，不用 emoji、不用斜体、不喊口号。
- 术语第一次出现给中文解释，保留英文原名（如「投机解码（speculative decoding）」）。
- numbers 列 3～8 个关键数字，每个都要和原文**逐字一致**（含单位写法，如 `840 tokens/s`、`1.8×`），代码会逐个核对，
  对不上就打回；正文里用到的其他数字也要出自原文。
- 标题 ≤{tmax} 字，带主体和结论（谁做了什么、结果如何），不要「重磅」「炸裂」；摘要 ≤{smax2} 字。

## 交回的 JSON（存成文件后运行 qly-publish.sh deepdive FILE {date}）
```json
{{
  "sig": "{sig}",
  "title": "标题",
  "summary": "摘要",
  "lede": "导语一段",
  "sections": [
    {{"heading": "小标题", "paras": ["段落", "段落"], "figure": 0, "figure_note": "看这里：……"}},
    {{"heading": "小标题", "paras": ["段落"], "figure": null, "figure_note": ""}}
  ],
  "numbers": ["原文里的关键数字", "…"],
  "caveats": ["局限 / 存疑，一条一句到两句"],
  "so_what": ["对读者意味着什么，一条一句到两句"]
}}
```
figure 填下面配图清单里的下标（不配图填 null）；同一张图只用一次。

## 这一篇
- sig：{sig}
- 标题：{title}
- 来源：{source} · {url}
- 密度档：{depth}
- 编辑提炼（一句话）：{takeaway}
- 为什么重要：{note}
- 逻辑链：{logic}
- 已核对的数字：{stats}

## 配图清单（先用 Read 打开本地路径亲眼看，再决定放哪一节）
{figures}

## 原文全文（{chars} 字{cut}）
{fulltext}
"""


def render_prompt(date_str: str, entry: Dict[str, Any], others: Sequence[Dict[str, Any]] = ()) -> str:
    extra = daily._extra(entry)
    logic = entry.get("logic") or {}
    chain = " → ".join(str(n) for n in (logic.get("chain") or []) if n) if isinstance(logic, dict) else ""
    stats = "；".join("{0} {1}".format(s.get("value", ""), s.get("label", "")).strip()
                     for s in entry.get("stats") or [] if isinstance(s, dict)) or "（无）"
    figs = _figures(entry)
    fig_lines = "\n".join("{0}. [{1}] {2}\n   本地：{3}{4}".format(
        i, "示意图" if f["synth"] else "原文图", f["caption"] or f["alt"], f["local"],
        "\n   编辑图注：" + str(entry.get("figure_note") or "") if i == 0 and entry.get("figure_note") else "")
        for i, f in enumerate(figs)) or "（这一篇没有可用配图，figure 一律填 null）"
    text = str(extra.get("fulltext") or extra.get("transcript") or "")
    body = _GUIDE.format(
        date=date_str, smin=SECTIONS_MIN, smax=SECTIONS_MAX, bmin=BODY_MIN, bmax=BODY_MAX, tmax=TITLE_MAX,
        smax2=SUMMARY_MAX, sig=entry["sig"], title=daily._display_title(entry), source=entry.get("source") or "",
        url=entry.get("url") or "", depth=entry.get("depth") or "", takeaway=entry.get("takeaway") or "（无）",
        note=daily._editor_note(entry) or "（无）", logic=chain or "（无）", stats=stats, figures=fig_lines,
        chars=len(text), cut="，采集时截断在 {0} 字".format(daily.FULLTEXT_MAX_CHARS)
        if len(text) >= daily.FULLTEXT_MAX_CHARS else "", fulltext=text)
    if others:
        body += "\n## 备选（想换一篇就用 qly-publish.sh deepdive-prompt {0} <sig>）\n".format(date_str)
        body += "\n".join("- {0} · {1} · 原文 {2} 字".format(e["sig"], daily._display_title(e), daily._source_chars(e))
                          for e in others)
        body += "\n"
    return body


def cmd_prompt(date_str: str, sig: Optional[str] = None) -> int:
    final = _load(date_str)
    pool = candidates(final["items"])
    if sig:
        entry = next((e for e in final["items"] if e.get("sig") == sig), None)
        if entry is None:
            print("sig {0} 不在 {1} 的定稿里".format(sig, date_str), file=sys.stderr)
            return 1
    elif pool:
        entry = pool[0]
    else:
        print("{0} 没有原文 ≥{1} 字的头条 / 深读，今天不出深度解读".format(date_str, SOURCE_MIN), file=sys.stderr)
        return 2
    text = render_prompt(date_str, entry, [e for e in pool if e is not entry])
    daily._write_text(daily._archive_path(date_str, PROMPT_NAME), text)
    print(text)
    return 0


# =========================================================================
# 校验 + 渲染
# =========================================================================
def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()


def validate(doc: Dict[str, Any], entry: Dict[str, Any]) -> Tuple[List[str], List[str]]:
    """返回 (错误, 提醒)。错误必须改；提醒是正文里出现、原文里找不到的数字，请编辑自查。"""
    errors: List[str] = []
    title = str(doc.get("title") or "").strip()
    summary = str(doc.get("summary") or "").strip()
    if not title or len(title) > TITLE_MAX:
        errors.append("title 要有且 ≤{0} 字（现在 {1}）".format(TITLE_MAX, len(title)))
    if not summary or len(summary) > SUMMARY_MAX:
        errors.append("summary 要有且 ≤{0} 字（现在 {1}）".format(SUMMARY_MAX, len(summary)))
    sections = doc.get("sections") if isinstance(doc.get("sections"), list) else []
    if not SECTIONS_MIN <= len(sections) <= SECTIONS_MAX:
        errors.append("sections 要 {0}～{1} 节（现在 {2}）".format(SECTIONS_MIN, SECTIONS_MAX, len(sections)))
    figs = _figures(entry)
    used = set()
    for i, sec in enumerate(sections, start=1):
        if not isinstance(sec, dict) or not str(sec.get("heading") or "").strip() or not sec.get("paras"):
            errors.append("第 {0} 节缺 heading 或 paras".format(i))
            continue
        fig = sec.get("figure")
        if fig is not None:
            if not isinstance(fig, int) or not 0 <= fig < len(figs):
                errors.append("第 {0} 节 figure={1} 不在配图清单里（0～{2}）".format(i, fig, len(figs) - 1))
            elif fig in used:
                errors.append("第 {0} 节重复用了图 {1}".format(i, fig))
            else:
                used.add(fig)
    for key in ("lede",):
        if not str(doc.get(key) or "").strip():
            errors.append("{0} 不能空".format(key))
    for key in ("caveats", "so_what"):
        if not [x for x in doc.get(key) or [] if str(x).strip()]:
            errors.append("{0} 至少一条".format(key))
    chars = len(_body_text(doc))
    if not BODY_MIN <= chars <= BODY_MAX:
        errors.append("正文 {0} 字，要 {1}～{2}".format(chars, BODY_MIN, BODY_MAX))
    # 和日报提炼同一套核对口径（_source_corpus：去标签、去千分位逗号、小写）
    # 原文图注也是原文（图上的数常在图注里写明），一并算进核对范围
    captions = " ".join(f["caption"] for f in figs).replace(",", "").lower()
    corpus = _norm(daily._source_corpus(entry) + " " + captions)
    numbers = [str(n).strip() for n in doc.get("numbers") or [] if str(n).strip()]
    if not numbers:
        errors.append("numbers 至少列 1 个关键数字")
    for n in numbers:
        if _norm(n.replace(",", "")) not in corpus:
            errors.append("关键数字「{0}」原文里找不到（要逐字一致）".format(n))
    warnings = sorted({tok for tok in daily._NUM_TOKEN.findall(_body_text(doc)) if tok.replace(",", "") not in corpus})
    return errors, warnings


def _body_text(doc: Dict[str, Any]) -> str:
    parts = [str(doc.get("lede") or "")]
    for sec in doc.get("sections") or []:
        if isinstance(sec, dict):
            parts += [str(p) for p in sec.get("paras") or []]
    parts += [str(x) for x in (doc.get("caveats") or []) + (doc.get("so_what") or [])]
    return "".join(parts)


def render_html(date_str: str, doc: Dict[str, Any], entry: Dict[str, Any]) -> str:
    figs = _figures(entry)
    sections = []
    for i, sec in enumerate(doc["sections"], start=1):
        fig = figs[sec["figure"]] if isinstance(sec.get("figure"), int) else None
        sections.append({"no": "{0:02d}".format(i), "heading": sec["heading"],
                         "paras": [str(p) for p in sec["paras"]], "fig": fig,
                         "fig_credit": "◔ 示意 · Codex 按原文生成" if fig and fig["synth"] else "图源：原文",
                         "note": str(sec.get("figure_note") or "")})
    weekday = daily._weekday_cn(utils.parse_date(date_str + "T00:00:00+00:00"))
    return minitpl.render(daily._load_template(TEMPLATE), {
        "title": doc["title"], "summary": doc["summary"], "lede": doc["lede"],
        "date_label": "{0} {1}".format(date_str, weekday), "source": entry.get("source") or "",
        "source_title": daily._display_title(entry), "url": entry.get("url") or "",
        "numbers": doc["numbers"], "sections": sections,
        "caveats": [str(x) for x in doc["caveats"]], "so_what": [str(x) for x in doc["so_what"]],
        "minutes": str(max(1, round(len(_body_text(doc)) / daily.READ_CJK_PER_MIN))),
    })


def render_md(doc: Dict[str, Any]) -> str:
    """只给发布脚本取标题 / 摘要 / 封面（正文发 HTML）。"""
    return "---\ntitle: {0}\nauthor: {1}\ndescription: {2}\ncoverImage: imgs/cover.png\n---\n\n{3}\n".format(
        doc["title"], daily.WECHAT_AUTHOR, doc["summary"], doc["lede"])


def cmd_apply(date_str: str, file: str) -> int:
    final = _load(date_str)
    try:
        doc = json.loads(open(file, encoding="utf-8").read())
    except (OSError, ValueError) as exc:
        print("读不了 {0}：{1}".format(file, exc), file=sys.stderr)
        return 1
    entry = next((e for e in final["items"] if e.get("sig") == doc.get("sig")), None)
    if entry is None:
        print("sig {0} 不在 {1} 的定稿里".format(doc.get("sig"), date_str), file=sys.stderr)
        return 1
    errors, warnings = validate(doc, entry)
    for w in warnings:
        print("提醒：正文数字「{0}」原文里没找到原样写法，请自查".format(w))
    if errors:
        print("深度解读没通过校验：", file=sys.stderr)
        for e in errors:
            print("- " + e, file=sys.stderr)
        return 1
    daily._write_text(daily._archive_path(date_str, JSON_NAME), json.dumps(doc, ensure_ascii=False, indent=2))
    daily._write_text(daily._archive_path(date_str, HTML_NAME), render_html(date_str, doc, entry))
    daily._write_text(daily._archive_path(date_str, MD_NAME), render_md(doc))
    cover_html = daily._archive_path(date_str, COVER_HTML_NAME)
    cover_png = daily._archive_path(date_str, COVER_PNG_NAME)
    daily._write_text(cover_html, daily.render_cover(date_str, [], kind=COVER_KIND, line=doc["title"]))
    has_cover = daily.export_cover_png(cover_html, cover_png)
    print("deepdive={0}".format(daily._archive_path(date_str, HTML_NAME)))
    print("title={0}".format(doc["title"]))
    print("chars={0}".format(len(_body_text(doc))))
    print("cover={0}".format("yes" if has_cover else "missing"))
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="公众号深度解读：写作简报 / 校验渲染")
    parser.add_argument("--prompt", action="store_true", help="打印写作简报")
    parser.add_argument("--sig", default=None, help="--prompt 时指定条目（缺省按原料自动挑）")
    parser.add_argument("--apply", metavar="FILE", default=None, help="校验编辑交回的 JSON 并渲染")
    parser.add_argument("--date", default=None, help="YYYY-MM-DD，缺省今天 (UTC)")
    args = parser.parse_args(argv)
    date_str = args.date or utils.now_utc().strftime("%Y-%m-%d")
    if args.apply:
        return cmd_apply(date_str, args.apply)
    if args.prompt:
        return cmd_prompt(date_str, args.sig)
    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
