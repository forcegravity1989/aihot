"""cli/data_prune.py —— 数据目录清理（spec §10.5）。

```
python -m qianliyan.cli.data_prune [--dry-run|--yes] [--keep-items-days N]
                                    [--clear-ephemeral] [--git-gc]
```

``--dry-run`` 默认开启（只打印将删除的内容），传 ``--yes`` 才真删。
``$QLY_DATA_DIR/repos/`` 是长期缓存（insights clone），**不属于**临时文件——
``--clear-ephemeral`` 只清 ``cache/`` 与 ``mirrors/``。
"""

from __future__ import annotations

import argparse
import logging
import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Sequence

from ..core import paths, utils

logger = logging.getLogger("qianliyan.cli.data_prune")

#: --clear-ephemeral 清理的目录（不含 repos/ —— 那是长期缓存）
EPHEMERAL_DIRS = ("cache", "mirrors")
DATE_ROOTS = ("items", "archive")


def _list_subdirs(base: Path) -> List[Path]:
    if not base.is_dir():
        return []
    return sorted(child for child in base.iterdir() if child.is_dir())


def _parse_dirname_date(name: str) -> Optional[datetime]:
    try:
        dt = datetime.strptime(name, "%Y-%m-%d")
    except ValueError:
        return None
    return dt.replace(tzinfo=timezone.utc)


def _find_stale_date_dirs(root_name: str, keep_days: float, now: datetime) -> List[Path]:
    base = paths.resolve_data_dir() / root_name
    stale: List[Path] = []
    for child in _list_subdirs(base):
        dt = _parse_dirname_date(child.name)
        if dt is None:
            continue
        age_days = (now - dt).total_seconds() / 86400.0
        if age_days > keep_days:
            stale.append(child)
    return stale


def _remove_dir(path: Path) -> None:
    try:
        shutil.rmtree(path)
    except OSError as exc:
        logger.warning("删除目录失败 %s: %s", path, exc)


# =========================================================================
# 主逻辑
# =========================================================================
def cmd_prune(
    keep_items_days: Optional[float],
    clear_ephemeral: bool,
    git_gc: bool,
    dry_run: bool,
) -> int:
    now = utils.now_utc()
    data_dir = paths.resolve_data_dir()
    verb = "将删除" if dry_run else "已删除"

    stale_dirs: List[Path] = []
    if keep_items_days is not None:
        for root_name in DATE_ROOTS:
            stale_dirs.extend(_find_stale_date_dirs(root_name, keep_items_days, now))

    do_ephemeral = clear_ephemeral or os.environ.get("QLY_PRUNE_EPHEMERAL") == "1"
    ephemeral_dirs: List[Path] = []
    if do_ephemeral:
        for name in EPHEMERAL_DIRS:
            candidate = data_dir / name
            if candidate.is_dir():
                ephemeral_dirs.append(candidate)

    if not stale_dirs and not ephemeral_dirs and not git_gc:
        print("没有可清理的内容（未指定 --keep-items-days / --clear-ephemeral / --git-gc）。")
        return 0

    for path in stale_dirs:
        print("{0}: {1}（超龄目录）".format(verb, path))
        if not dry_run:
            _remove_dir(path)

    for path in ephemeral_dirs:
        print("{0}: {1}（临时目录）".format(verb, path))
        if not dry_run:
            _remove_dir(path)

    if git_gc:
        if (data_dir / ".git").is_dir():
            if dry_run:
                print("将执行: git gc（{0}）".format(data_dir))
            else:
                try:
                    subprocess.run(
                        ["git", "gc"], cwd=str(data_dir),
                        capture_output=True, timeout=120, check=False,
                    )
                    print("已执行: git gc（{0}）".format(data_dir))
                except Exception as exc:  # noqa: BLE001 - git gc 失败不许崩溃 cli
                    print("git gc 失败: {0}".format(exc))
        else:
            print("data 目录不是 git 仓库，跳过 --git-gc: {0}".format(data_dir))

    if dry_run:
        print("（--dry-run 模式：以上内容尚未真正删除，加 --yes 才会执行）")
    return 0


# =========================================================================
# CLI
# =========================================================================
def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m qianliyan.cli.data_prune", description="千里眼数据目录清理",
    )
    parser.add_argument(
        "--dry-run", dest="dry_run", action="store_true", default=True,
        help="只打印将删除的内容（默认开启）",
    )
    parser.add_argument("--yes", dest="dry_run", action="store_false", help="真正执行删除")
    parser.add_argument(
        "--keep-items-days", type=float, default=None,
        help="清理 items/<date>/ 与 archive/<date>/ 中超过 N 天的目录",
    )
    parser.add_argument(
        "--clear-ephemeral", action="store_true",
        help="清 cache/、mirrors/（QLY_PRUNE_EPHEMERAL=1 等效，repos/ 不受影响）",
    )
    parser.add_argument("--git-gc", action="store_true", help="data_dir 为 git 仓库时执行 git gc")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = _build_arg_parser()
    args = parser.parse_args(argv)
    return cmd_prune(args.keep_items_days, args.clear_ephemeral, args.git_gc, args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
