"""test_sync_mock.py —— 锁死 spec §10.1 / intent.md S1 S4 S5：`run_sync` 离线端到端编排。

覆盖：

* ``--mock`` 端到端产出五件套（items.jsonl / hotlist.md / channels/*.md + channels.json /
  digest.html / sync_meta.json），heavy 徽标自然触发；
* ``--eye`` 子集增量：第二次只跑一只眼时，其余眼的原始池数据原样保留
  （``merge_pool_by_eyes`` 增量语义）；
* 单眼故障注入（monkeypatch ``sync.EYES``）：sync 不中断，失败眼 ``ok=False``、
  其余眼照常出数；
* ``--strict`` 让进程退出码非 0，不带 ``--strict`` 时同样的失败不影响退出码；
* ``--status`` 输出可读；
* ``--quick`` 跳过 agent 五连标注、``--no-html`` 跳过 digest.html；
* flash 徽标（24h 内 weight>=0.95）——mock fixture 是静态文件，日期在测试内动态改写
  为「当前时间 - 2 小时」后重新触发。
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from qianliyan.cli import sync
from qianliyan.core import schema, storage, utils
from qianliyan.eyes import EYES

FLASH_TARGET_TITLE = "Anthropic launches Claude Opus 6"
FLASH_TARGET_SOURCE = "Anthropic News"


# =========================================================================
# 端到端五件套
# =========================================================================
def test_run_sync_mock_end_to_end_produces_five_artifacts(tmp_data_dir):
    meta = sync.run_sync(mock=True)

    assert meta["run_id"]
    assert meta["started_at"] and meta["finished_at"]
    assert set(meta["eyes"].keys()) == set(EYES.keys())
    for name, info in meta["eyes"].items():
        assert info["ok"] is True, "mock 模式下所有眼都应成功: {0}".format(name)
        assert info["count"] > 0
        assert info["error"] is None

    totals = meta["totals"]
    assert totals["raw"] >= totals["deduped"] > 0
    assert totals["heavy"] >= 1, "mock fixture 内建 4 源同题条目应触发 heavy"

    # 五件套
    assert (tmp_data_dir / "items.jsonl").is_file()
    assert (tmp_data_dir / "hotlist.md").is_file()
    assert (tmp_data_dir / "channels.json").is_file()
    assert (tmp_data_dir / "digest.html").is_file()
    assert (tmp_data_dir / "sync_meta.json").is_file()

    channel_dir = tmp_data_dir / "channels"
    assert channel_dir.is_dir()
    md_files = list(channel_dir.glob("*.md"))
    assert md_files, "channels/*.md 应至少产出一个频道页"

    items = storage.read_jsonl(tmp_data_dir / "items.jsonl")
    assert len(items) == totals["deduped"]
    for item in items:
        assert item["url"], "每条 item 必带 URL（铁律 2）"
        assert item["sync_run_id"] == meta["run_id"]
        assert item["fetched_at"]

    index = storage.read_json(tmp_data_dir / "channels.json", default=None)
    assert isinstance(index, dict) and index

    hotlist_text = (tmp_data_dir / "hotlist.md").read_text(encoding="utf-8")
    assert "全局热榜" in hotlist_text

    digest_html = (tmp_data_dir / "digest.html").read_text(encoding="utf-8")
    assert len(digest_html) > 100  # 只需非空、可读，不重复锁死 test_report.py 已覆盖的具体壳结构


def test_run_sync_mock_via_main_cli(tmp_data_dir, capsys):
    rc = sync.main(["--mock"])
    assert rc == 0
    captured = capsys.readouterr()
    assert "sync 完成" in captured.out
    assert (tmp_data_dir / "sync_meta.json").is_file()


# =========================================================================
# --eye 子集增量
# =========================================================================
def test_eye_subset_incremental_merge(tmp_data_dir):
    full_meta = sync.run_sync(mock=True)
    assert set(full_meta["eyes"].keys()) == set(EYES.keys())

    raw_before = storage.read_jsonl(tmp_data_dir / "raw_pool.jsonl")
    kinds_before = {row.get("source_kind") for row in raw_before}
    assert kinds_before == set(EYES.keys())
    aihot_count_before = sum(1 for row in raw_before if row.get("source_kind") == "aihot")
    assert aihot_count_before > 0

    subset_meta = sync.run_sync(mock=True, eyes=["aihot"])
    assert set(subset_meta["eyes"].keys()) == {"aihot"}
    assert subset_meta["eyes"]["aihot"]["ok"] is True

    raw_after = storage.read_jsonl(tmp_data_dir / "raw_pool.jsonl")
    kinds_after = {row.get("source_kind") for row in raw_after}
    assert kinds_after == set(EYES.keys()), "未跑的眼的数据应原样保留（merge_pool_by_eyes 增量语义）"

    items_after = storage.read_jsonl(tmp_data_dir / "items.jsonl")
    kinds_in_items = {item.get("source_kind") for item in items_after}
    assert kinds_in_items == set(EYES.keys()), "增量 sync 后统一池仍应覆盖全部五种 source_kind"


# =========================================================================
# 单眼故障注入（S1：单眼故障，其余眼照常）
# =========================================================================
def _canned_fetch(name):
    def _fetch(cfg=None, since=None):
        return [
            schema.make_item(
                title="canned {0} item".format(name),
                url="https://example.com/canned/{0}".format(name),
                source="Canned {0}".format(name),
                source_kind=name,
                backend="rest",
                weight=0.7,
            )
        ]

    return _fetch


def _boom_fetch(cfg=None, since=None):
    raise RuntimeError("模拟单眼故障：boom")


def test_single_eye_failure_does_not_abort_sync(tmp_data_dir, monkeypatch):
    for name in EYES:
        if name == "insights":
            monkeypatch.setitem(sync.EYES, name, _boom_fetch)
        else:
            monkeypatch.setitem(sync.EYES, name, _canned_fetch(name))

    meta = sync.run_sync(mock=False)

    assert meta["eyes"]["insights"]["ok"] is False
    assert meta["eyes"]["insights"]["count"] == 0
    assert "boom" in (meta["eyes"]["insights"]["error"] or "")

    for name in EYES:
        if name == "insights":
            continue
        assert meta["eyes"][name]["ok"] is True
        assert meta["eyes"][name]["count"] == 1

    # sync 整体仍然完成（不中断），五件套照常产出
    assert (tmp_data_dir / "items.jsonl").is_file()
    assert (tmp_data_dir / "sync_meta.json").is_file()
    items = storage.read_jsonl(tmp_data_dir / "items.jsonl")
    kinds = {item.get("source_kind") for item in items}
    assert "insights" not in kinds
    assert kinds == set(EYES.keys()) - {"insights"}


def test_failed_eye_keeps_old_pool_data(tmp_data_dir, monkeypatch):
    """故障 ≠ 空结果：失败眼的旧池数据必须保留（spec §10.1.4）。"""
    for name in EYES:
        monkeypatch.setitem(sync.EYES, name, _canned_fetch(name))
    sync.run_sync(mock=False)
    items = storage.read_jsonl(tmp_data_dir / "items.jsonl")
    assert "insights" in {item.get("source_kind") for item in items}

    # 第二轮：insights 故障，其余照常 —— 第一轮的 insights 数据仍应在池中
    monkeypatch.setitem(sync.EYES, "insights", _boom_fetch)
    meta = sync.run_sync(mock=False)
    assert meta["eyes"]["insights"]["ok"] is False
    items = storage.read_jsonl(tmp_data_dir / "items.jsonl")
    kinds = {item.get("source_kind") for item in items}
    assert "insights" in kinds, "失败眼的旧数据被错误地抹掉了"


# =========================================================================
# --strict 退出码
# =========================================================================
def test_strict_flag_makes_exit_code_nonzero_on_failure(tmp_data_dir, monkeypatch):
    for name in EYES:
        if name == "aihot":
            monkeypatch.setitem(sync.EYES, name, _boom_fetch)
        else:
            monkeypatch.setitem(sync.EYES, name, _canned_fetch(name))

    rc_strict = sync.main(["--strict"])
    assert rc_strict != 0

    rc_default = sync.main([])
    assert rc_default == 0, "不带 --strict 时同样的单眼失败不应影响退出码"


# =========================================================================
# --status
# =========================================================================
def test_status_before_any_sync_is_readable_and_nonzero(tmp_data_dir, capsys):
    rc = sync.main(["--status"])
    assert rc == 1
    captured = capsys.readouterr()
    assert "sync_meta.json" in captured.out


def test_status_after_sync_prints_readable_summary(tmp_data_dir, capsys):
    meta = sync.run_sync(mock=True)
    capsys.readouterr()  # 清空 run_sync 阶段的输出（若有）

    rc = sync.main(["--status"])
    assert rc == 0
    captured = capsys.readouterr()
    assert meta["run_id"] in captured.out
    assert "眼状态" in captured.out
    for name in EYES:
        assert name in captured.out
    assert "totals" in captured.out


# =========================================================================
# --quick / --no-html
# =========================================================================
def test_quick_skips_agent_annotations_and_og_image(tmp_data_dir):
    sync.run_sync(mock=True, quick=True)
    items = storage.read_jsonl(tmp_data_dir / "items.jsonl")
    assert items
    for item in items:
        extra = item.get("extra") or {}
        assert "tier" not in extra
        assert "cluster_key" not in extra
        assert "headline_fit" not in extra
        assert "story_key" not in extra
    # channels/hotlist/digest 仍按 spec §10.1.7 正常产出（不受 --quick 影响）
    assert (tmp_data_dir / "digest.html").is_file()
    assert (tmp_data_dir / "hotlist.md").is_file()
    assert (tmp_data_dir / "channels.json").is_file()


def test_no_html_skips_digest_rendering(tmp_data_dir):
    sync.run_sync(mock=True, no_html=True)
    assert not (tmp_data_dir / "digest.html").exists()
    # 其余产出不受影响
    assert (tmp_data_dir / "items.jsonl").is_file()
    assert (tmp_data_dir / "hotlist.md").is_file()


# =========================================================================
# flash 徽标：静态 fixture 动态改写 date 后触发
# =========================================================================
def test_flash_badge_triggers_with_freshly_rewritten_date(tmp_data_dir, monkeypatch):
    original_rows = storage.read_jsonl(sync._mock_fixture_path())
    now = utils.now_utc()

    target_sig = None
    for row in original_rows:
        if row.get("title") == FLASH_TARGET_TITLE and row.get("source") == FLASH_TARGET_SOURCE:
            assert row.get("weight", 0) >= 0.95, "flash 测试条目权重必须 >= 0.95"
            target_sig = row.get("sig")
    assert target_sig, "fixture 中未找到用于 flash 测试的基准条目，请检查 mock_items.jsonl 是否改动"

    # dedup_and_score 的合并 date 取组内「最早」可解析日期——必须把同 sig（同题多源）
    # 的全部成员日期都改到 24h 内，只改最高权重那一条不够（早到的旧日期会拖累合并结果）。
    touched = 0
    for row in original_rows:
        if row.get("sig") == target_sig:
            row["date"] = utils.iso(now - timedelta(hours=2))
            touched += 1
    assert touched >= 4, "同题多源分组应至少 4 条（用于同时验证 heavy 也不受影响）"

    rewritten_path = tmp_data_dir.parent / "mock_items_flash_rewritten.jsonl"
    storage.write_jsonl(rewritten_path, original_rows)
    monkeypatch.setattr(sync, "_mock_fixture_path", lambda: rewritten_path)

    meta = sync.run_sync(mock=True)
    assert meta["totals"]["flash"] >= 1

    items = storage.read_jsonl(tmp_data_dir / "items.jsonl")
    flashed_titles = [item["title"] for item in items if "flash" in (item.get("badges") or [])]
    assert FLASH_TARGET_TITLE in flashed_titles
