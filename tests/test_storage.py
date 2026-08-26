"""test_storage.py —— 锁死 spec §4：JSONL/JSON 原子读写、坏行容忍、按眼增量合并。"""

from __future__ import annotations

import json
from datetime import timedelta

import pytest

from qianliyan.core import storage, utils


# =========================================================================
# JSONL
# =========================================================================
def test_jsonl_roundtrip_preserves_unicode_and_order(tmp_path):
    path = tmp_path / "items.jsonl"
    rows = [
        {"sig": "a1", "title": "心声社区公告", "tags": ["company"]},
        {"sig": "b2", "title": "Anthropic ships", "metrics": {"stars": 42}},
    ]
    storage.write_jsonl(path, rows)
    assert storage.read_jsonl(path) == rows
    # 不转义非 ASCII，便于人眼审计
    assert "心声社区公告" in path.read_text(encoding="utf-8")


def test_write_jsonl_is_atomic_and_leaves_no_tmp(tmp_path):
    path = tmp_path / "sub" / "items.jsonl"
    storage.write_jsonl(path, [{"a": 1}])
    assert path.is_file()
    assert [p.name for p in path.parent.iterdir()] == ["items.jsonl"]


def test_write_jsonl_overwrites_previous_content(tmp_path):
    path = tmp_path / "items.jsonl"
    storage.write_jsonl(path, [{"n": i} for i in range(5)])
    storage.write_jsonl(path, [{"n": 99}])
    assert storage.read_jsonl(path) == [{"n": 99}]


def test_write_jsonl_empty_creates_empty_file(tmp_path):
    path = tmp_path / "items.jsonl"
    storage.write_jsonl(path, [])
    assert path.is_file()
    assert path.read_text(encoding="utf-8") == ""
    assert storage.read_jsonl(path) == []


def test_read_jsonl_missing_file_returns_empty(tmp_path):
    assert storage.read_jsonl(tmp_path / "nope.jsonl") == []


def test_read_jsonl_tolerates_bad_lines(tmp_path, caplog):
    path = tmp_path / "items.jsonl"
    path.write_text(
        "\n".join([
            json.dumps({"sig": "ok1"}),
            "{ this is not json",
            "",
            "   ",
            json.dumps([1, 2, 3]),          # 合法 JSON 但不是 object
            json.dumps({"sig": "ok2"}),
        ]) + "\n",
        encoding="utf-8",
    )
    with caplog.at_level("WARNING"):
        rows = storage.read_jsonl(path)
    assert rows == [{"sig": "ok1"}, {"sig": "ok2"}]
    assert len(caplog.records) == 2  # 坏行 + 非 object 行各一条 warning


def test_read_jsonl_accepts_str_path(tmp_path):
    path = tmp_path / "items.jsonl"
    storage.write_jsonl(str(path), [{"a": 1}])
    assert storage.read_jsonl(str(path)) == [{"a": 1}]


# =========================================================================
# JSON
# =========================================================================
def test_json_roundtrip(tmp_path):
    path = tmp_path / "meta" / "sync_meta.json"
    payload = {"run_id": "20260825T120000Z-ab12", "totals": {"raw": 180, "deduped": 155}}
    storage.write_json(path, payload)
    assert storage.read_json(path) == payload
    assert [p.name for p in path.parent.iterdir()] == ["sync_meta.json"]


def test_read_json_missing_returns_default(tmp_path):
    assert storage.read_json(tmp_path / "nope.json") is None
    assert storage.read_json(tmp_path / "nope.json", {}) == {}


def test_read_json_broken_returns_default(tmp_path, caplog):
    path = tmp_path / "broken.json"
    path.write_text("{ nope", encoding="utf-8")
    with caplog.at_level("WARNING"):
        assert storage.read_json(path, {"fallback": True}) == {"fallback": True}
    assert caplog.records


# =========================================================================
# merge_pool_by_eyes
# =========================================================================
def _row(kind, name, age_days=0.0, now=None):
    now = now or utils.now_utc()
    return {
        "sig": "{0}-{1}".format(kind, name),
        "title": name,
        "source_kind": kind,
        "date": utils.iso(now - timedelta(days=age_days)),
    }


def test_merge_keeps_kinds_that_did_not_run():
    old = [_row("aihot", "old-aihot"), _row("company", "old-company")]
    new = [_row("aihot", "new-aihot")]
    merged = storage.merge_pool_by_eyes(old, new, ran_kinds=["aihot"])
    titles = [r["title"] for r in merged]
    assert titles == ["old-company", "new-aihot"]


def test_merge_replaces_all_entries_of_ran_kinds():
    old = [_row("local", "a"), _row("local", "b"), _row("local", "c")]
    new = [_row("local", "only-this")]
    merged = storage.merge_pool_by_eyes(old, new, ran_kinds={"local"})
    assert [r["title"] for r in merged] == ["only-this"]


def test_merge_with_multiple_ran_kinds():
    old = [_row("aihot", "a"), _row("builders", "b"), _row("insights", "i")]
    new = [_row("aihot", "a2"), _row("builders", "b2")]
    merged = storage.merge_pool_by_eyes(old, new, ran_kinds=["aihot", "builders"])
    assert [r["title"] for r in merged] == ["i", "a2", "b2"]


def test_merge_no_ran_kinds_keeps_everything():
    old = [_row("aihot", "a")]
    merged = storage.merge_pool_by_eyes(old, [], ran_kinds=[])
    assert [r["title"] for r in merged] == ["a"]


def test_merge_handles_empty_old_pool():
    new = [_row("local", "x")]
    assert storage.merge_pool_by_eyes([], new, ran_kinds=["local"]) == new
    assert storage.merge_pool_by_eyes(None, new, ran_kinds=["local"]) == new


def test_merge_prunes_by_pool_age():
    now = utils.now_utc()
    old = [
        _row("company", "fresh-company", age_days=1, now=now),
        _row("company", "stale-company", age_days=40, now=now),
    ]
    new = [
        _row("local", "fresh-local", age_days=0, now=now),
        _row("local", "stale-local", age_days=31, now=now),
    ]
    merged = storage.merge_pool_by_eyes(old, new, ran_kinds=["local"], max_age_days=30, now=now)
    assert [r["title"] for r in merged] == ["fresh-company", "fresh-local"]


def test_merge_keeps_unparseable_dates_when_pruning():
    now = utils.now_utc()
    row = _row("local", "no-date", now=now)
    row["date"] = ""
    merged = storage.merge_pool_by_eyes([], [row], ran_kinds=["local"], max_age_days=1, now=now)
    assert [r["title"] for r in merged] == ["no-date"]


@pytest.mark.parametrize("bad", [None, "", "abc", 0, -5])
def test_merge_ignores_invalid_max_age(bad):
    now = utils.now_utc()
    ancient = _row("local", "ancient", age_days=3650, now=now)
    merged = storage.merge_pool_by_eyes([], [ancient], ran_kinds=["local"], max_age_days=bad, now=now)
    assert [r["title"] for r in merged] == ["ancient"]


def test_merge_accepts_string_max_age_from_env():
    now = utils.now_utc()
    rows = [_row("local", "old", age_days=10, now=now), _row("local", "new", age_days=1, now=now)]
    merged = storage.merge_pool_by_eyes([], rows, ran_kinds=["local"], max_age_days="7", now=now)
    assert [r["title"] for r in merged] == ["new"]


def test_merge_result_feeds_dedup_and_score(tmp_data_dir):
    """增量池 → dedup 打分 → 落盘 → 读回，走一遍 Wave 1 的完整数据契约。"""
    now = utils.now_utc()
    old = [_row("company", "kept", now=now)]
    new = [_row("local", "fresh", now=now)]
    pool = storage.merge_pool_by_eyes(old, new, ran_kinds=["local"], now=now)

    for row in pool:
        row["weight"] = 0.8
        row["url"] = "https://example.com/" + row["title"]

    scored = utils.dedup_and_score(pool, now=now)
    path = tmp_data_dir / "items.jsonl"
    storage.write_jsonl(path, scored)

    read_back = storage.read_jsonl(path)
    assert len(read_back) == 2
    assert all(r["hotness"] > 0 for r in read_back)
    assert read_back == scored
