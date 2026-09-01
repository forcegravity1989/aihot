#!/bin/bash
# scripts/qly-daily.sh —— 千里眼每日抓取的调度入口（给 launchd / cron 调用）。
#
# 做三件事：抓取（sync）→ 备好当日选稿草案（daily_digest_all --prepare）→ 打印摘要。
# 选稿与定稿**刻意不自动做**：那两步需要判断力，是编辑（人或 Agent）的活，
# 自动跑只会产出一份没人看过的日报。
#
# 退出码语义（决定 launchd 要不要报警）：
#   0  当日数据拿到了（主信源 aihot OK）
#   1  抓取失败 / 主信源挂了 / 全部眼失败 —— 需要人看一眼
#   2  上一轮还在跑（拿不到锁），本轮跳过 —— 不是错误，不该告警
#
# **不用 `sync --strict`**：内网 company 眼在没有 CDP 浏览器的机器上天天失败，
# --strict 会让每一轮都非零退出，告警很快就被当噪音忽略。这里改成按「主信源有没有
# 拿到数据」判定，才是「今天的日报有没有原料」这个真问题。

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="$REPO_ROOT/.venv/bin/python"

# launchd 给的环境极简，PATH 必须自己补全（git / curl 等子进程要用）
export PATH="/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"

# 数据目录：尊重外部已设的值，否则让 paths.py 的四级解析自己决定
if [ -z "${QLY_DATA_DIR:-}" ]; then
    QLY_DATA_DIR="$("$PY" -c 'from qianliyan.core import paths; print(paths.resolve_data_dir())' 2>/dev/null)"
fi
if [ -z "$QLY_DATA_DIR" ]; then
    echo "无法解析数据目录（QLY_DATA_DIR 未设且 paths 解析失败）" >&2
    exit 1
fi
export QLY_DATA_DIR

LOG_DIR="$QLY_DATA_DIR/logs"
LOG_FILE="$LOG_DIR/daily-$(date +%Y-%m-%d).log"
LOCK_DIR="$QLY_DATA_DIR/.qly-daily.lock"
LOG_KEEP_DAYS=30

mkdir -p "$LOG_DIR"

log() { printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" | tee -a "$LOG_FILE"; }

# ---- 互斥锁：mkdir 是原子操作，比 flock 可移植 -------------------------------
# 抓取一轮 80 秒左右，正常不会撞上；但机器休眠唤醒后 launchd 可能补跑，
# 叠加执行会让两个进程同时写 items.jsonl。
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
    holder="$(cat "$LOCK_DIR/pid" 2>/dev/null || echo '?')"
    # 锁残留（进程已死）就抢过来，否则老实退让
    if [ "$holder" != "?" ] && kill -0 "$holder" 2>/dev/null; then
        log "上一轮仍在运行（pid=$holder），本轮跳过"
        exit 2
    fi
    log "发现残留锁（pid=$holder 已不存在），接管"
    rm -rf "$LOCK_DIR" && mkdir "$LOCK_DIR" || { log "抢锁失败"; exit 1; }
fi
echo $$ > "$LOCK_DIR/pid"
trap 'rm -rf "$LOCK_DIR"' EXIT

# ---- 抓取 -------------------------------------------------------------------
log "=== 千里眼每日抓取开始 · 数据目录 $QLY_DATA_DIR ==="
started=$(date +%s)

"$PY" -m qianliyan.cli.sync >>"$LOG_FILE" 2>&1
sync_rc=$?

elapsed=$(( $(date +%s) - started ))
log "sync 退出码 $sync_rc，耗时 ${elapsed}s"

if [ $sync_rc -ne 0 ]; then
    log "❌ sync 进程失败，跳过后续步骤"
    exit 1
fi

# ---- 判定「今天到底拿到数据没有」---------------------------------------------
# 读 sync_meta.json 而不是看退出码：sync 对单眼失败是容忍的（设计如此），
# 但主信源 aihot 挂了 = 今天没有原料，必须让人知道。
verdict="$("$PY" - <<'PYEOF' 2>/dev/null
import json, sys
from qianliyan.core import paths, storage

meta = storage.read_json(paths.data_path("sync_meta.json"), default={}) or {}
eyes = meta.get("eyes") or {}
ok = [n for n, m in eyes.items() if (m or {}).get("ok")]
bad = [n for n, m in eyes.items() if not (m or {}).get("ok")]
totals = meta.get("totals") or {}

print("OK={0} FAIL={1} deduped={2}".format(
    ",".join(sorted(ok)) or "-", ",".join(sorted(bad)) or "-", totals.get("deduped", 0)))
# 主信源挂了、或一个眼都没成，才算今天真的失败
primary_down = not (eyes.get("aihot") or {}).get("ok")
sys.exit(1 if (primary_down or not ok) else 0)
PYEOF
)"
data_rc=$?
log "信源结果：$verdict"

# ---- 备好当日选稿草案（选稿/定稿留给编辑）------------------------------------
"$PY" -m qianliyan.cli.daily_digest_all --prepare >>"$LOG_FILE" 2>&1 \
    && log "选稿草案已备好，等编辑选条目并写 editor_note" \
    || log "⚠ 选稿草案生成失败（不影响已抓到的数据）"

# ---- 日志留存 ---------------------------------------------------------------
find "$LOG_DIR" -name 'daily-*.log' -type f -mtime +$LOG_KEEP_DAYS -delete 2>/dev/null

if [ $data_rc -ne 0 ]; then
    log "❌ 主信源未拿到数据，今天的日报没有原料"
    exit 1
fi
log "✅ 完成"
exit 0
