#!/bin/bash
# 退出码：0 日报已出；1 需要人看一眼；2 上一轮还在跑、本轮跳过（不该告警）

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="$REPO_ROOT/.venv/bin/python"
# qianliyan 包没装进 venv，python -m 靠当前目录找包
cd "$REPO_ROOT" || exit 1

# launchd 的 PATH 极简；~/.local/bin 是 claude CLI 的默认安装位置
export PATH="$HOME/.local/bin:/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"

# 本机私有环境（不进仓库），放 `claude setup-token` 的长期凭据：launchd 不读 shell profile
QLY_ENV_FILE="${QLY_ENV_FILE:-$HOME/.config/qianliyan/env}"
# shellcheck disable=SC1090
[ -r "$QLY_ENV_FILE" ] && . "$QLY_ENV_FILE"

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

# 休眠唤醒后 launchd 可能补跑，叠加执行会同时写 items.jsonl；mkdir 是原子操作，比 flock 可移植
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
    holder="$(cat "$LOCK_DIR/pid" 2>/dev/null || echo '?')"
    if [ "$holder" != "?" ] && kill -0 "$holder" 2>/dev/null; then
        log "上一轮仍在运行（pid=$holder），本轮跳过"
        exit 2
    fi
    log "发现残留锁（pid=$holder 已不存在），接管"
    rm -rf "$LOCK_DIR" && mkdir "$LOCK_DIR" || { log "抢锁失败"; exit 1; }
fi
echo $$ > "$LOCK_DIR/pid"
trap 'rm -rf "$LOCK_DIR"' EXIT

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

# 不用 sync --strict：company 眼在没有 CDP 的机器上天天失败，告警会沦为噪音；
# 改看 sync_meta.json 里主信源 aihot 有没有拿到数据
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
primary_down = not (eyes.get("aihot") or {}).get("ok")
sys.exit(1 if (primary_down or not ok) else 0)
PYEOF
)"
data_rc=$?
log "信源结果：$verdict"

# 日期显式给出：三步必须落在同一天的草案上
DAY="$(date -u +%Y-%m-%d)"
published=0
if "$PY" -m qianliyan.cli.daily_digest_all --prepare --date "$DAY" >>"$LOG_FILE" 2>&1; then
    "$PY" -m qianliyan.cli.daily_digest_all --auto-edit --date "$DAY" >>"$LOG_FILE" 2>&1 \
        || log "⚠ 自动选稿失败"
    if "$PY" -m qianliyan.cli.daily_digest_all --finalize --auto-brief --html --date "$DAY" >>"$LOG_FILE" 2>&1; then
        published=1
        log "日报已出：$DAY（$(grep -o '自动选稿完成.*\|草案里已有编辑选稿.*' "$LOG_FILE" | tail -1)）"
    else
        log "❌ 定稿渲染失败，今天的日报没出来"
    fi
else
    log "❌ 选稿草案生成失败，今天的日报没出来"
fi

find "$LOG_DIR" -name 'daily-*.log' -type f -mtime +$LOG_KEEP_DAYS -delete 2>/dev/null

if [ $data_rc -ne 0 ]; then
    log "❌ 主信源未拿到数据，今天的日报没有原料"
    exit 1
fi
if [ $published -ne 1 ]; then
    exit 1
fi
log "✅ 完成"
exit 0
