#!/bin/bash
# scripts/qly-publish.sh —— 日报「编辑 + 送达」的固定命令入口（给 Claude 定时任务调用）。
#
# 定时任务每次都是全新会话、无人值守：让它现场拼 python 片段既容易出错，也会被权限弹窗
# 卡住。这里把它要做的每一步收成一条固定命令，项目 .claude/settings.json 只需放行本脚本。
# 从任意工作目录调用都行（脚本自己 cd 到仓库）。
#
#   status  [DAY]        当天出刊状态：草案/选稿/编辑身份/定稿（key=value 行）
#   prompt  [DAY]        打印选稿简报（候选 + 选稿标准 + picks JSON 格式）
#   apply   FILE [DAY]   把 picks JSON 写进草案（替换规则回退的选稿）并定稿渲染
#   stage   OUT_DIR      把数据根的 daily.html 与它链接的详情页整理进 OUT_DIR，供发布 artifact
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

page = src.read_text(encoding="utf-8")
stories = sorted(set(re.findall(r"href=\"(story/[A-Za-z0-9_.-]+\.html)\"", page)))
for rel in stories:
    story = data / rel
    if story.is_file():
        (out / rel).write_text(clean(story.read_text(encoding="utf-8")), encoding="utf-8")
page = clean(page)
(out / "index.html").write_text(page, encoding="utf-8")
# 详情页的「返回日报」是 ../daily.html
(out / "daily.html").write_text(page, encoding="utf-8")
print("staged={0}".format(out))
print("files=daily.html " + " ".join(stories))
PYEOF
        ;;
    *)
        sed -n '2,14p' "$0"
        exit 1
        ;;
esac
