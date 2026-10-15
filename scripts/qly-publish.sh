#!/bin/bash
# scripts/qly-publish.sh —— 日报「编辑 + 送达」的固定命令入口（给 Claude 定时任务调用）。
#
# 定时任务每次都是全新会话、无人值守：让它现场拼 python 片段既容易出错，也会被权限弹窗
# 卡住。这里把它要做的每一步收成一条固定命令，项目 .claude/settings.json 只需放行本脚本。
# 从任意工作目录调用都行（脚本自己 cd 到仓库）。
#
#   status  [DAY]        当天出刊状态：草案/选稿/编辑身份/定稿/各方向条数/快讯数（key=value 行）
#   prompt  [DAY]        打印选稿简报（候选 + 选稿标准 + picks JSON 格式）
#   apply   FILE [DAY]   把 picks JSON 写进草案（替换规则回退的选稿）并定稿渲染
#   brief-prompt [DAY]   打印写要点的简报（入选条目 + 原文）
#   apply-briefs FILE [DAY]  把要点 JSON 写进定稿并重新渲染
#   stage   OUT_DIR      把数据根的 daily.html 与它链接的详情页、原文配图整理进 OUT_DIR，供发布 artifact
#   wechat  OUT_DIR [DAY]  把公众号稿 wechat.md 与它的配图整理成发布包（article.md + imgs/），交 baoyu-post-to-wechat
#   figures [DAY]        列出每条的密度档与原文配图（下标 / 本地绝对路径 / kind / 图注），定主图用
#   depth   FILE [DAY]   把定档 JSON（{"depth":[{sig,depth,figure,figure_note,synth_image}]}）写进定稿并重新渲染
#   synth   OUT_DIR [DAY] [SIG,SIG]  给头条 / 深读里没有信息图的条目用 Codex 出示意图（每张 3~5 分钟），打印 image=<sig>\t<png>
#
# DAY 缺省为今天（UTC，与 qly-daily.sh 一致）。

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="$REPO_ROOT/.venv/bin/python"
cd "$REPO_ROOT" || exit 1

if [ -z "${QLY_DATA_DIR:-}" ]; then
    QLY_DATA_DIR="$("$PY" -c 'from qianliyan.core import paths; print(paths.resolve_data_dir())' 2>/dev/null)"
fi
export QLY_DATA_DIR

cmd="${1:-}"
shift || true

case "$cmd" in
    status)
        DAY="${1:-$(date -u +%Y-%m-%d)}"
        "$PY" - "$DAY" <<'PYEOF' 2>/dev/null
import json, sys
from qianliyan.core import paths, storage
day = sys.argv[1]
draft = storage.read_json(paths.data_path("archive", day, "digest-draft.json"), default=None)
final = storage.read_json(paths.data_path("archive", day, "digest-final.json"), default=None)
meta = storage.read_json(paths.data_path("sync_meta.json"), default={}) or {}
items = (draft or {}).get("items") or []
picked = sorted((e for e in items if e.get("selected")), key=lambda e: e.get("editor_rank") or 999)
print("day={0}".format(day))
print("last_sync={0}".format(meta.get("finished_at", "")))
print("aihot_ok={0}".format(((meta.get("eyes") or {}).get("aihot") or {}).get("ok")))
print("draft={0}".format("yes" if draft else "no"))
print("candidates={0}".format(len(items)))
print("selected={0}".format(len(picked)))
print("edited_by={0}".format((draft or {}).get("edited_by") or ("human" if picked else "")))
print("final={0}".format("yes" if final else "no"))
finals = (final or {}).get("items") or []
print("briefs={0}/{1}".format(sum(1 for e in finals if e.get("takeaway") or e.get("brief")), len(finals)))
# 提炼按密度档算（ADR D1）：每条都要一句结论，只有头条还要逻辑链
def _distilled(e):
    return bool(e.get("takeaway")) and (e.get("depth") != "lead" or bool(e.get("logic")))
print("distilled={0}/{1}".format(sum(1 for e in finals if _distilled(e)), len(finals)))
depths = {"lead": 0, "deep": 0, "glance": 0, "": 0}
for e in finals:
    depths[e.get("depth") if e.get("depth") in depths else ""] += 1
print("depths=lead:{0},deep:{1},glance:{2},none:{3}".format(depths["lead"], depths["deep"], depths["glance"], depths[""]))
def _has_info_fig(e):
    return any(isinstance(f, dict) and f.get("kind", "article") in ("article", "synth") and f.get("file")
               for f in e.get("figures") or [])
print("deep_without_figure={0}".format(",".join(e.get("sig", "") for e in finals
                                                if e.get("depth") in ("lead", "deep") and not _has_info_fig(e))))
print("figure_notes={0}/{1}".format(sum(1 for e in finals if e.get("depth") in ("lead", "deep") and e.get("figure_note")),
                                     depths["lead"] + depths["deep"]))
from qianliyan.pipeline import tracks
cfg = tracks.load()
counts = tracks.count(finals or picked, cfg)
print("tracks={0}".format(",".join("{0}:{1}".format(k, v) for k, v in counts.items())))
print("empty_tracks={0}".format(",".join(k for k, v in counts.items() if not v)))
quick = (final or {}).get("quick")
if quick is None:
    quick = [e for e in items if e.get("quick") and not e.get("selected")]
print("quick={0}".format(len(quick)))
if picked:
    head = picked[0]
    print("headline={0}".format(head.get("title_zh") or head.get("title") or ""))
PYEOF
        ;;
    prompt)
        DAY="${1:-$(date -u +%Y-%m-%d)}"
        "$PY" -m qianliyan.cli.daily_digest_all --write-prompt --date "$DAY" >/dev/null 2>&1 \
            || { echo "生成选稿简报失败（草案不存在？先跑 scripts/qly-daily.sh）" >&2; exit 1; }
        cat "$QLY_DATA_DIR/archive/$DAY/prompt.md"
        ;;
    apply)
        FILE="${1:?用法: qly-publish.sh apply FILE [DAY]}"
        DAY="${2:-$(date -u +%Y-%m-%d)}"
        out="$("$PY" -m qianliyan.cli.daily_digest_all --apply-picks "$FILE" --edited-by agent --date "$DAY" 2>&1)"
        rc=$?
        printf '%s\n' "$out" | grep -v -i 'warn'
        [ $rc -eq 0 ] || exit $rc
        "$PY" -m qianliyan.cli.daily_digest_all --finalize --html --date "$DAY" 2>&1 | grep -v -i 'warn'
        exit "${PIPESTATUS[0]}"
        ;;
    brief-prompt)
        DAY="${1:-$(date -u +%Y-%m-%d)}"
        "$PY" -m qianliyan.cli.daily_digest_all --write-brief-prompt --date "$DAY" >/dev/null 2>&1 \
            || { echo "生成要点简报失败（还没定稿？先 apply 或跑 scripts/qly-daily.sh）" >&2; exit 1; }
        cat "$QLY_DATA_DIR/archive/$DAY/brief-prompt.md"
        ;;
    apply-briefs)
        FILE="${1:?用法: qly-publish.sh apply-briefs FILE [DAY]}"
        DAY="${2:-$(date -u +%Y-%m-%d)}"
        "$PY" -m qianliyan.cli.daily_digest_all --apply-briefs "$FILE" --html --date "$DAY" 2>&1 | grep -v -i 'warn'
        exit "${PIPESTATUS[0]}"
        ;;
    stage)
        OUT="${1:?用法: qly-publish.sh stage OUT_DIR}"
        "$PY" - "$QLY_DATA_DIR" "$OUT" <<'PYEOF'
import re, shutil, sys
from pathlib import Path

data, out = Path(sys.argv[1]), Path(sys.argv[2])
src = data / "daily.html"
if not src.is_file():
    sys.exit("daily.html 不存在：今天还没出刊")
if out.exists():
    shutil.rmtree(out)
(out / "story").mkdir(parents=True)

# artifact 的内容安全策略只放行自己的文件：外站图片一律被拦成破图，发布前剥掉
EXTERNAL_IMG = re.compile(r"<img\b[^>]*\bsrc=\"https?://[^\"]*\"[^>]*>", re.I)
# 只发当天一期：往期归档链接在 artifact 里是死链，去掉链接、保留文字
ARCHIVE_LINK = re.compile(r"<a\b[^>]*href=\"archive/[^\"]*\"[^>]*>(.*?)</a>", re.S)

def clean(html):
    html = EXTERNAL_IMG.sub("", html)
    return ARCHIVE_LINK.sub(r"<span>\1</span>", html)

# 原文配图（issue #52）定稿时已下载到数据根 media/，页面里是相对路径（首页 media/x，详情页 ../media/x）；
# 一起复制进发布目录、随页上传，才不会被当成外站图拦掉
MEDIA_REF = re.compile(r"src=\"(?:\.\./)*media/([0-9a-f]{16}\.(?:png|jpg|gif|webp))\"")
media = set()

page = src.read_text(encoding="utf-8")
stories = sorted(set(re.findall(r"href=\"(story/[A-Za-z0-9_.-]+\.html)\"", page)))
for rel in stories:
    story = data / rel
    if story.is_file():
        html = clean(story.read_text(encoding="utf-8"))
        media.update(MEDIA_REF.findall(html))
        (out / rel).write_text(html, encoding="utf-8")
page = clean(page)
media.update(MEDIA_REF.findall(page))
(out / "index.html").write_text(page, encoding="utf-8")
# 详情页的「返回日报」是 ../daily.html
(out / "daily.html").write_text(page, encoding="utf-8")
copied = []
for name in sorted(media):
    if (data / "media" / name).is_file():
        (out / "media").mkdir(exist_ok=True)
        shutil.copyfile(data / "media" / name, out / "media" / name)
        copied.append("media/" + name)
# 速读图（第一部分的独立 PNG，页首「⤓ 速读图」链到它）
for extra in ("speed.png",):
    if (data / extra).is_file() and 'href="{0}"'.format(extra) in page:
        shutil.copyfile(data / extra, out / extra)
        copied.append(extra)
print("staged={0}".format(out))
print("files=daily.html " + " ".join(stories + copied))
PYEOF
        ;;
    wechat)
        # 公众号稿发布包：archive/<DAY>/wechat.md + 它引用的配图 + 速读图，图片改成包内相对路径 imgs/，
        # 交给 baoyu-post-to-wechat（API 模式会逐张上传正文图；封面放 imgs/cover.png）
        OUT="${1:?用法: qly-publish.sh wechat OUT_DIR [DAY]}"
        DAY="${2:-$(date -u +%Y-%m-%d)}"
        "$PY" - "$QLY_DATA_DIR" "$OUT" "$DAY" <<'PYEOF2'
import re, shutil, sys
from pathlib import Path

data, out, day = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
src = data / "archive" / day / "wechat.md"
if not src.is_file():
    sys.exit("wechat.md 不存在：先 --html 重出 {0}".format(day))
(out / "imgs").mkdir(parents=True, exist_ok=True)
text = src.read_text(encoding="utf-8")
copied = []

def local(match):
    alt, ref = match.group(1), match.group(2)
    if ref.startswith("http"):
        return match.group(0)
    path = (src.parent / ref).resolve()
    if not path.is_file():
        return ""  # 图丢了就不留破图
    shutil.copyfile(path, out / "imgs" / path.name)
    copied.append("imgs/" + path.name)
    return "![{0}](imgs/{1})".format(alt, path.name)

text = re.sub(r"!\[([^\]]*)\]\(([^)\s]+)\)", local, text)
(out / "article.md").write_text(text, encoding="utf-8")
print("article={0}".format(out / "article.md"))

# HTML 正文（和首页同一套样式）：<img src> 同样改成包内 imgs/；发布脚本读 article.html，
# 标题 / 摘要从同名的 article.md frontmatter 取
html_src = src.with_suffix(".html")
if html_src.is_file():
    def local_img(match):
        ref = match.group(2)
        if ref.startswith("http"):
            return match.group(0)
        path = (src.parent / ref).resolve()
        if not path.is_file():
            return ""
        shutil.copyfile(path, out / "imgs" / path.name)
        copied.append("imgs/" + path.name)
        return '{0}imgs/{1}{2}'.format(match.group(1), path.name, match.group(3))
    html = re.sub(r'(<img\b[^>]*\bsrc=")([^"]+)(")', local_img, html_src.read_text(encoding="utf-8"))
    (out / "article.html").write_text(html, encoding="utf-8")
    print("html={0}".format(out / "article.html"))
print("images={0}".format(len(copied)))
# 封面：放在 archive/<日>/cover.png（数据目录，重启不丢），打包时带上
if (src.parent / "cover.png").is_file():
    shutil.copyfile(src.parent / "cover.png", out / "imgs" / "cover.png")
print("cover={0}".format("yes" if (out / "imgs" / "cover.png").is_file() else "missing"))
PYEOF2
        ;;
    figures)
        DAY="${1:-$(date -u +%Y-%m-%d)}"
        "$PY" - "$QLY_DATA_DIR" "$DAY" <<'PYEOF2'
import json, sys
from pathlib import Path
data, day = Path(sys.argv[1]), sys.argv[2]
doc = json.loads((data / "archive" / day / "digest-final.json").read_text(encoding="utf-8"))
items = sorted(doc.get("items") or [], key=lambda e: e.get("editor_rank") or 999)
for e in items:
    title = e.get("title_zh") or (e.get("extra") or {}).get("title_zh") or e.get("title") or ""
    print("== {0}\tdepth={1}\tchars={2}\t{3}".format(e.get("sig"), e.get("depth") or "-",
          (e.get("extra") or {}).get("fulltext_chars") or 0, title[:60]))
    for i, f in enumerate(e.get("figures") or []):
        if not isinstance(f, dict):
            continue
        path = data / str(f.get("file") or "")
        print("   [{0}] kind={1}\t{2}\t{3}".format(f.get("n", i), f.get("kind") or "article",
              path if path.is_file() else "(missing)", (f.get("caption") or f.get("alt") or "")[:60]))
print("status=ok")
PYEOF2
        ;;
    depth)
        FILE="${1:?用法: qly-publish.sh depth FILE [DAY]}"
        DAY="${2:-$(date -u +%Y-%m-%d)}"
        out="$("$PY" -m qianliyan.cli.daily_digest_all --apply-depth "$FILE" --html --date "$DAY" 2>&1)"
        rc=$?
        printf '%s\n' "$out" | grep -v -i 'warn'
        exit $rc
        ;;
    synth)
        # 示意图：提示词 = 风格模板 + 日报提炼 + 原文全文；Codex 出图 → 铺象牙底 → 缩到 1280 宽。
        # 出不来就跳过那条（页面退回数字卡），不阻塞出刊
        OUT="${1:?用法: qly-publish.sh synth OUT_DIR [DAY] [SIG,SIG]}"
        DAY="${2:-$(date -u +%Y-%m-%d)}"
        SIGS="${3:-}"
        GEN="$HOME/.baoyu-skills/codex-gen-images.sh"
        FLAT="$HOME/.baoyu-skills/flatten-alpha.swift"
        [ -x "$GEN" ] || [ -f "$GEN" ] || { echo "status=skipped reason=no-codex-gen-images.sh"; exit 0; }
        mkdir -p "$OUT"
        "$PY" -m qianliyan.cli.daily_digest_all --synth-prompts "$OUT" --sigs "$SIGS" --date "$DAY" 2>/dev/null | grep -v -i warn
        specs=()
        for pf in "$OUT"/prompts/*.md; do
            [ -f "$pf" ] || continue
            sig="$(basename "$pf" .md)"
            [ -n "$SIGS" ] && [[ ",$SIGS," != *",$sig,"* ]] && continue
            specs+=("$sig.md=$sig.png")
        done
        [ ${#specs[@]} -eq 0 ] && { echo "status=ok images=0"; exit 0; }
        bash "$GEN" "$OUT" "${specs[@]}"
        n=0
        for spec in "${specs[@]}"; do
            png="$OUT/${spec##*=}"; sig="${spec%%.md=*}"
            [ -f "$png" ] || { echo "missing=$sig"; continue; }
            [ -f "$FLAT" ] && swift "$FLAT" FFFAF0 "$png" >/dev/null 2>&1
            sips -Z 1280 "$png" >/dev/null 2>&1
            printf 'image=%s\t%s\n' "$sig" "$png"
            n=$((n+1))
        done
        echo "status=ok images=$n"
        ;;
    *)
        sed -n '2,20p' "$0"
        exit 1
        ;;
esac
