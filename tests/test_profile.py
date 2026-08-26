"""test_profile.py —— 读者画像 + 历史记录（spec-v0.3 §4 / §9）。

覆盖：reader.yaml 加载、history/feedback 派生偏好、personalize 落 personal_score/reasons、
mute 归零、无配置退化为 hotness、history 追加读回与坏行容忍。全部离线。
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from qianliyan.core import profile, schema, storage, utils, paths


@pytest.fixture(autouse=True)
def force_offline(monkeypatch):
    monkeypatch.setenv("QLY_OFFLINE", "1")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)


def _item(title, *, tags=None, source="Some Source", hotness=1.0, handle=None,
          source_kind="local"):
    extra = {}
    if handle is not None:
        extra["handle"] = handle
    it = schema.make_item(
        title=title, url="https://example.com/{0}".format(abs(hash(title)) % 10 ** 8),
        source=source, source_kind=source_kind, backend="rss", weight=0.8,
        summary="", tags=list(tags or []), extra=extra,
    )
    it["hotness"] = hotness
    return it


# =========================================================================
# 历史记录：追加 / 读回 / 坏行容忍
# =========================================================================
def test_log_and_read_history_roundtrip(tmp_data_dir):
    profile.log_history([
        {"sig": "abc", "action": "open", "title": "T1", "url": "u1"},
        {"sig": "def", "action": "deepread", "title": "T2", "url": "u2"},
    ])
    rows = profile.read_history()
    assert len(rows) == 2
    assert rows[0]["sig"] == "abc" and rows[0]["action"] == "open"
    # ts 自动补齐
    assert rows[0]["ts"]
    assert utils.parse_date(rows[0]["ts"]) is not None


def test_log_history_appends_not_overwrites(tmp_data_dir):
    profile.log_history([{"sig": "a", "action": "seen"}])
    profile.log_history([{"sig": "b", "action": "received"}])
    rows = profile.read_history()
    assert [r["sig"] for r in rows] == ["a", "b"]


def test_log_history_skips_invalid_action(tmp_data_dir):
    profile.log_history([
        {"sig": "ok", "action": "seen"},
        {"sig": "bad", "action": "frobnicate"},
    ])
    rows = profile.read_history()
    assert [r["sig"] for r in rows] == ["ok"]


def test_read_history_limit(tmp_data_dir):
    profile.log_history([{"sig": str(i), "action": "seen"} for i in range(5)])
    assert [r["sig"] for r in profile.read_history(limit=2)] == ["3", "4"]
    assert profile.read_history(limit=0) == []
    assert len(profile.read_history()) == 5


def test_read_history_tolerates_bad_lines(tmp_data_dir):
    profile.log_history([{"sig": "good", "action": "open"}])
    path = paths.data_path(profile.HISTORY_NAME)
    with path.open("a", encoding="utf-8") as fh:
        fh.write("this is not json\n")
    rows = profile.read_history()
    assert [r["sig"] for r in rows] == ["good"]


# =========================================================================
# 读者画像加载（显式 reader.yaml）
# =========================================================================
def test_load_reader_profile_reads_explicit_interests(tmp_data_dir):
    prof = profile.load_reader_profile()
    # config/reader.yaml 的显式兴趣（无历史时 derived 为空 → 合并即显式值）
    assert prof["tags"].get("models") == 1.3
    assert prof["tags"].get("agent") == 1.4
    assert prof["tags"].get("claude-code") == 1.6
    assert set(prof.keys()) == {"tags", "sources", "people", "mute", "derived"}
    assert prof["derived"] == {"tags": {}, "sources": {}, "people": {}}


# =========================================================================
# personalize：命中加权 / 无命中等于 hotness
# =========================================================================
def test_personalize_boosts_matching_tag(tmp_data_dir):
    hot = _item("models item", tags=["models"], hotness=2.0)
    plain = _item("unrelated item", tags=["misc"], hotness=2.0)
    profile.personalize([hot, plain])

    assert hot["extra"]["personal_score"] == round(2.0 * 1.3, 4)
    assert "tag:models" in hot["extra"]["personal_reasons"]
    # 未命中兴趣 → 等于 hotness
    assert plain["extra"]["personal_score"] == 2.0
    assert plain["extra"]["personal_reasons"] == []


def test_personalize_degrades_to_hotness_with_empty_profile(tmp_data_dir):
    it = _item("anything", tags=["models", "agent"], hotness=1.5)
    profile.personalize([it], profile={})
    assert it["extra"]["personal_score"] == 1.5
    assert it["extra"]["personal_reasons"] == []


def test_personalize_does_not_touch_hotness(tmp_data_dir):
    it = _item("models item", tags=["models"], hotness=3.0)
    profile.personalize([it])
    assert it["hotness"] == 3.0  # hotness 不变


def test_personalize_mute_tag_zeroes_score(tmp_data_dir):
    it = _item("spam", tags=["spam", "models"], hotness=5.0)
    prof = {"tags": {"models": 1.3}, "sources": {}, "people": {},
            "mute": {"tags": ["spam"], "people": []}}
    profile.personalize([it], profile=prof)
    assert it["extra"]["personal_score"] == 0.0
    assert any(r.startswith("mute:tag") for r in it["extra"]["personal_reasons"])


def test_personalize_mute_person_zeroes_score(tmp_data_dir):
    it = _item("post", tags=["models"], hotness=5.0, handle="baduser",
               source_kind="builders")
    prof = {"tags": {}, "sources": {}, "people": {},
            "mute": {"tags": [], "people": ["@BadUser"]}}
    profile.personalize([it], profile=prof)
    assert it["extra"]["personal_score"] == 0.0
    assert any(r.startswith("mute:people") for r in it["extra"]["personal_reasons"])


def test_personalize_source_substring_and_people(tmp_data_dir):
    it = _item("post", tags=[], source="Anthropic News", hotness=1.0,
               handle="karpathy", source_kind="builders")
    prof = {"tags": {}, "sources": {"Anthropic": 1.5}, "people": {"karpathy": 2.0},
            "mute": {"tags": [], "people": []}}
    profile.personalize([it], profile=prof)
    assert it["extra"]["personal_score"] == round(1.0 * 1.5 * 2.0, 4)
    reasons = it["extra"]["personal_reasons"]
    assert "source:Anthropic" in reasons and "people:karpathy" in reasons


# =========================================================================
# 派生偏好（history + feedback → 乘数）
# =========================================================================
def test_derived_preferences_from_history(tmp_data_dir):
    agent_item = _item("deep agent piece", tags=["agent"], hotness=1.0)
    other_item = _item("random", tags=["misc"], hotness=1.0)
    storage.write_jsonl(paths.data_path("items.jsonl"), [agent_item, other_item])

    # 对 agent 条目 deepread → 该 tag 派生偏好上扬
    profile.log_history([{"sig": agent_item["sig"], "action": "deepread"}])

    prof = profile.load_reader_profile()
    assert "agent" in prof["derived"]["tags"]
    assert prof["derived"]["tags"]["agent"] > 1.0
    # 合并后 = 显式(1.4) × 派生(>1) → 强于纯显式
    assert prof["tags"]["agent"] > 1.4


def test_derived_feedback_down_reduces_multiplier(tmp_data_dir):
    it = _item("meh models news", tags=["models"], hotness=1.0)
    storage.write_jsonl(paths.data_path("items.jsonl"), [it])
    # feedback down → 负向信号
    storage.write_jsonl(
        paths.data_path("feedback.jsonl"),
        [{"sig": it["sig"], "action": "down", "note": "", "ts": utils.iso(utils.now_utc())}],
    )
    prof = profile.load_reader_profile()
    assert prof["derived"]["tags"]["models"] < 1.0
    # 合并后被拉低到显式 1.3 以下
    assert prof["tags"]["models"] < 1.3


def test_recency_decay_favors_recent(tmp_data_dir):
    recent = _item("recent agent", tags=["agent"], hotness=1.0)
    stale = _item("stale agent", tags=["research"], hotness=1.0)
    storage.write_jsonl(paths.data_path("items.jsonl"), [recent, stale])

    now = utils.now_utc()
    profile.log_history([
        {"sig": recent["sig"], "action": "open", "ts": utils.iso(now)},
        {"sig": stale["sig"], "action": "open",
         "ts": utils.iso(now - timedelta(days=60))},
    ])
    derived = profile.load_reader_profile()["derived"]["tags"]
    # 同为 open，但近的 agent 权重高于 60 天前的 research
    assert derived["agent"] >= derived["research"]
