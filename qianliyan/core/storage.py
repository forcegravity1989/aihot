"""core/storage.py —— JSONL / JSON 的原子读写与原始池按眼增量合并。

原子写统一走「同目录 tmp 文件 + ``os.replace``」，保证读侧永远看不到半截文件。
``merge_pool_by_eyes`` 是增量抓取的核心语义：**没跑的眼数据不动，跑过的眼以本次快照为准**。
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Union

from . import utils

logger = logging.getLogger(__name__)

PathLike = Union[str, Path]


def _as_path(path: PathLike) -> Path:
    return path if isinstance(path, Path) else Path(str(path))


def _atomic_write_text(path: PathLike, text: str) -> None:
    """原子写文本：写同目录 tmp 文件后 ``os.replace`` 覆盖目标。"""
    target = _as_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=target.name + ".", suffix=".tmp", dir=str(target.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, str(target))
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def read_jsonl(path: PathLike) -> List[Dict[str, Any]]:
    """读 JSONL；文件不存在返回 ``[]``，坏行跳过并 warning。"""
    target = _as_path(path)
    if not target.is_file():
        logger.debug("JSONL 不存在，返回空列表: %s", target)
        return []

    rows: List[Dict[str, Any]] = []
    try:
        with target.open("r", encoding="utf-8") as fh:
            for lineno, line in enumerate(fh, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError as exc:
                    logger.warning("跳过坏行 %s:%d (%s)", target, lineno, exc)
                    continue
                if not isinstance(row, dict):
                    logger.warning("跳过非 object 行 %s:%d", target, lineno)
                    continue
                rows.append(row)
    except OSError as exc:
        logger.warning("读取 JSONL 失败 %s: %s", target, exc)
        return []
    return rows


def write_jsonl(path: PathLike, rows: Iterable[Dict[str, Any]]) -> None:
    """原子写 JSONL（UTF-8，不转义非 ASCII）。"""
    buffer: List[str] = []
    for row in rows or []:
        try:
            buffer.append(json.dumps(row, ensure_ascii=False, default=str))
        except (TypeError, ValueError) as exc:
            logger.warning("跳过无法序列化的条目 (%s): %r", exc, row)
    text = "".join(line + "\n" for line in buffer)
    _atomic_write_text(path, text)
    logger.debug("已写入 %d 行 → %s", len(buffer), path)


def read_json(path: PathLike, default: Any = None) -> Any:
    """读 JSON；文件缺失或损坏时返回 ``default``。"""
    target = _as_path(path)
    if not target.is_file():
        return default
    try:
        with target.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError) as exc:
        logger.warning("读取 JSON 失败 %s: %s", target, exc)
        return default


def write_json(path: PathLike, obj: Any) -> None:
    """原子写 JSON（缩进 2，不转义非 ASCII）。"""
    text = json.dumps(obj, ensure_ascii=False, indent=2, default=str) + "\n"
    _atomic_write_text(path, text)


def _coerce_max_age_days(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        days = float(value)
    except (TypeError, ValueError):
        logger.warning("max_age_days 非法，忽略池龄淘汰: %r", value)
        return None
    if days <= 0:
        return None
    return days


def merge_pool_by_eyes(
    old_raw: Sequence[Dict[str, Any]],
    new_items: Sequence[Dict[str, Any]],
    ran_kinds: Iterable[str],
    max_age_days: Any = None,
    now: Optional[datetime] = None,
) -> List[Dict[str, Any]]:
    """按 ``source_kind`` 增量合并原始池。

    1. 保留 ``old_raw`` 中 ``source_kind not in ran_kinds`` 的条目（没跑的眼数据不动）；
    2. 丢弃 ``old_raw`` 中 ``source_kind in ran_kinds`` 的条目，换成 ``new_items``；
    3. ``max_age_days`` 非空时按 ``date`` 淘汰过龄条目（时间不可解析的保留）；
    4. 返回合并池，供 ``utils.dedup_and_score`` 消费。
    """
    kinds = set(ran_kinds or [])
    merged: List[Dict[str, Any]] = []
    dropped = 0

    for row in old_raw or []:
        if not isinstance(row, dict):
            continue
        if row.get("source_kind") in kinds:
            dropped += 1
            continue
        merged.append(row)

    kept_old = len(merged)
    fresh = [row for row in (new_items or []) if isinstance(row, dict)]
    merged.extend(fresh)

    days = _coerce_max_age_days(max_age_days)
    if days is not None:
        now = now or utils.now_utc()
        kept = [row for row in merged if not utils.is_older_than(row.get("date"), days, now)]
        if len(kept) != len(merged):
            logger.info("池龄淘汰 %d 条（> %s 天）", len(merged) - len(kept), days)
        merged = kept

    logger.debug(
        "merge_pool_by_eyes: 保留 %d 条旧数据（丢弃 %d）+ 新增 %d 条 → 合并后 %d 条",
        kept_old, dropped, len(fresh), len(merged),
    )
    return merged
