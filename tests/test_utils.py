"""test_utils.py —— 锁死 spec §1/§2：item 构造校验、签名归一化、合并语义、热度与 badge。"""

from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone

import pytest

from qianliyan.core import schema, utils


# =========================================================================
# 时间工具
# =========================================================================
def test_iso_is_utc_second_precision():
    dt = datetime(2026, 8, 25, 3, 0, 0, 123456, tzinfo=timezone.utc)
    assert utils.iso(dt) == "2026-08-25T03:00:00+00:00"


def test_iso_converts_naive_and_other_zones():
    naive = datetime(2026, 8, 25, 3, 0, 0)
    assert utils.iso(naive) == "2026-08-25T03:00:00+00:00"
    other = datetime(2026, 8, 25, 11, 0, 0, tzinfo=timezone(timedelta(hours=8)))
    assert utils.iso(other) == "2026-08-25T03:00:00+00:00"


@pytest.mark.parametrize(
    "raw",
    [
        "2026-08-25T03:00:00+00:00",
        "2026-08-25T03:00:00Z",
        "2026-08-25T11:00:00+08:00",
        "Tue, 25 Aug 2026 03:00:00 +0000",
        "2026-08-25 03:00:00",
    ],
)
def test_parse_date_accepts_iso_rfc822_and_plain(raw):
    parsed = utils.parse_date(raw)
    assert parsed is not None
    assert utils.iso(parsed) == "2026-08-25T03:00:00+00:00"


def test_parse_date_accepts_date_only():
    assert utils.iso(utils.parse_date("2026-08-25")) == "2026-08-25T00:00:00+00:00"


@pytest.mark.parametrize(
    "raw",
    ["August 25, 2026", "Aug 25, 2026", "25 August 2026"],
)
def test_parse_date_accepts_full_and_abbreviated_month_names(raw):
    """官网卡片式抓取（html_page.extract_articles）常见的日期格式（回归用例）。"""
    assert utils.iso(utils.parse_date(raw)) == "2026-08-25T00:00:00+00:00"


@pytest.mark.parametrize("raw", [None, "", "   ", "not a date", "昨天", [], True])
def test_parse_date_returns_none_on_failure(raw):
    assert utils.parse_date(raw) is None


# =========================================================================
# §2.1 item_signature
# =========================================================================
def test_signature_ignores_case_and_punctuation():
    assert utils.item_signature("Hello, World! — AI 模型") == utils.item_signature(
        "hello   world ai模型"
    )


def test_signature_nfkc_normalizes_fullwidth():
    assert utils.item_signature("ＡＩ模型发布") == utils.item_signature("AI模型发布")


def test_signature_truncates_to_first_50_chars():
    assert utils.normalize_title("a" * 80) == "a" * 50
    assert utils.item_signature("a" * 60) == utils.item_signature("a" * 50)
    # 第 50 位之内的差异必须体现出来
    assert utils.item_signature("a" * 49 + "b") != utils.item_signature("a" * 50)
    # 第 51 位之后的差异被截断掉
    assert utils.item_signature("a" * 50 + "b") == utils.item_signature("a" * 50 + "c")


def test_signature_is_16_hex_chars():
    sig = utils.item_signature("some title")
    assert len(sig) == 16
    assert all(ch in "0123456789abcdef" for ch in sig)


def test_signature_release_uses_source_and_version():
    base = {"tags": ["release"], "metrics": {"version": "v2.1.0"}}
    a = dict(base, title="v2.1.0", source="Claude Code Releases")
    b = dict(base, title="完全不同的发版标题", source="Claude Code Releases")
    c = dict(base, title="v2.1.0", source="Claude Code Mirror")
    # 同源同版本 → 无论标题如何都是一条
    assert utils.item_signature(a) == utils.item_signature(b)
    # 同版本不同源 → 不得误合并
    assert utils.item_signature(a) != utils.item_signature(c)


def test_signature_release_needs_both_tag_and_version():
    no_tag = {"title": "v2.1.0", "source": "Repo", "tags": [], "metrics": {"version": "v2.1.0"}}
    no_version = {"title": "v2.1.0", "source": "Repo", "tags": ["release"], "metrics": {}}
    plain = utils.item_signature("v2.1.0")
    assert utils.item_signature(no_tag) == plain
    assert utils.item_signature(no_version) == plain


def test_signature_accepts_plain_title_with_explicit_version():
    assert utils.item_signature("v1.0", source="Repo", version="v1.0") == utils.item_signature(
        {"title": "whatever", "source": "Repo", "tags": ["release"], "metrics": {"version": "v1.0"}}
    )


# =========================================================================
# §2.3 compute_hotness
# =========================================================================
def test_hotness_half_life_is_seven_days():
    now = utils.now_utc()
    seven_days_ago = utils.iso(now - timedelta(days=7))
    assert utils.compute_hotness(1.0, seven_days_ago, 0, now) == pytest.approx(0.5, abs=1e-3)


def test_hotness_fresher_is_hotter():
    now = utils.now_utc()
    fresh = utils.compute_hotness(0.9, utils.iso(now - timedelta(hours=1)), 0, now)
    stale = utils.compute_hotness(0.9, utils.iso(now - timedelta(days=7)), 0, now)
    older = utils.compute_hotness(0.9, utils.iso(now - timedelta(days=30)), 0, now)
    assert fresh > stale > older


def test_hotness_more_cross_refs_is_hotter():
    now = utils.now_utc()
    date = utils.iso(now - timedelta(hours=6))
    zero = utils.compute_hotness(0.9, date, 0, now)
    one = utils.compute_hotness(0.9, date, 1, now)
    three = utils.compute_hotness(0.9, date, 3, now)
    assert three > one > zero
    # 加成公式：weight * freshness * (1 + 0.35 * ln(1+refs))
    assert one == pytest.approx(zero * (1 + utils.CROSS_BONUS * 0.6931), rel=1e-3)


def test_hotness_unparseable_date_counts_as_zero_age():
    now = utils.now_utc()
    assert utils.compute_hotness(0.7, "不是时间", 0, now) == pytest.approx(0.7)
    assert utils.compute_hotness(0.7, "", 0, now) == pytest.approx(0.7)


def test_hotness_future_date_clamped_to_zero_age():
    now = utils.now_utc()
    future = utils.iso(now + timedelta(days=3))
    assert utils.compute_hotness(0.8, future, 0, now) == pytest.approx(0.8)


def test_hotness_rounds_to_four_decimals():
    now = utils.now_utc()
    value = utils.compute_hotness(0.777777, utils.iso(now - timedelta(days=1)), 2, now)
    assert value == round(value, 4)


# =========================================================================
# §2.2 dedup_and_score
# =========================================================================
def _mk(title, source, weight, date, summary="", tags=None, metrics=None, extra=None, **kw):
    item = {
        "title": title,
        "url": "https://example.com/" + source.replace(" ", "-").lower(),
        "summary": summary,
        "source": source,
        "source_kind": kw.get("source_kind", "local"),
        "backend": kw.get("backend", "rss"),
        "weight": weight,
        "date": date,
        "tags": list(tags or []),
        "metrics": dict(metrics or {}),
        "extra": dict(extra or {}),
    }
    item["sig"] = utils.item_signature(item)
    return item


def test_dedup_merges_group_with_full_semantics():
    now = utils.now_utc()
    early = utils.iso(now - timedelta(days=2))
    late = utils.iso(now - timedelta(hours=1))

    low = _mk(
        "Big model launch", "Low Source", 0.5, early,
        summary="a much longer summary written by the low weight source",
        tags=["b", "shared"], metrics={"stars": 1, "rank": 9},
        extra={"platform": "web", "lang": "en"},
        source_kind="aihot", backend="rest",
    )
    high = _mk(
        "BIG MODEL LAUNCH!", "High Source", 0.9, late,
        summary="short",
        tags=["a", "shared"], metrics={"stars": 42},
        extra={"platform": "rss"},
        source_kind="local", backend="html",
    )

    merged = utils.dedup_and_score([low, high], now=now)
    assert len(merged) == 1
    item = merged[0]

    # title/url/source_kind/backend 随 weight 最高者
    assert item["title"] == "BIG MODEL LAUNCH!"
    assert item["url"] == high["url"]
    assert item["source_kind"] == "local"
    assert item["backend"] == "html"
    # summary 取最长
    assert item["summary"] == low["summary"]
    # date 取最早
    assert item["date"] == early
    # weight 取最大
    assert item["weight"] == 0.9
    # source_list 按组内出现顺序去重，cross_refs = len-1
    assert item["source_list"] == ["Low Source", "High Source"]
    assert item["cross_refs"] == 1
    # tags 并集保序去重
    assert item["tags"] == ["b", "shared", "a"]
    # metrics/extra 浅合并，weight 高者胜出
    assert item["metrics"] == {"stars": 42, "rank": 9}
    assert item["extra"] == {"platform": "rss", "lang": "en"}
    assert item["hotness"] == utils.compute_hotness(0.9, early, 1, now)


def test_dedup_title_ties_take_first_arrival():
    now = utils.now_utc()
    date = utils.iso(now)
    first = _mk("Tie Breaker", "First", 0.8, date)
    second = _mk("tie breaker", "Second", 0.8, date)
    merged = utils.dedup_and_score([first, second], now=now)
    assert merged[0]["title"] == "Tie Breaker"
    assert merged[0]["source"] == "First"


def test_dedup_all_dates_unparseable_falls_back_to_now():
    now = utils.now_utc()
    merged = utils.dedup_and_score([_mk("No date here", "S", 0.5, "")], now=now)
    assert merged[0]["date"] == utils.iso(now)


def test_dedup_sorted_by_hotness_desc():
    now = utils.now_utc()
    date = utils.iso(now)
    items = [
        _mk("Cold one", "A", 0.2, date),
        _mk("Hot one", "B", 0.95, date),
        _mk("Mid one", "C", 0.6, date),
    ]
    merged = utils.dedup_and_score(items, now=now)
    assert [it["title"] for it in merged] == ["Hot one", "Mid one", "Cold one"]
    hotness = [it["hotness"] for it in merged]
    assert hotness == sorted(hotness, reverse=True)


def test_dedup_keeps_source_list_from_previous_merge():
    now = utils.now_utc()
    date = utils.iso(now)
    already = _mk("Carried over", "A", 0.7, date)
    already["source_list"] = ["A", "B"]
    fresh = _mk("carried over", "C", 0.6, date)
    merged = utils.dedup_and_score([already, fresh], now=now)
    assert merged[0]["source_list"] == ["A", "B", "C"]
    assert merged[0]["cross_refs"] == 2


def test_dedup_does_not_mutate_inputs():
    now = utils.now_utc()
    item = _mk("Untouched", "A", 0.7, utils.iso(now))
    snapshot = copy.deepcopy(item)
    utils.dedup_and_score([item], now=now)
    assert item == snapshot


def test_dedup_empty_input():
    assert utils.dedup_and_score([]) == []


# =========================================================================
# badge 阈值边界（重磅校准 v0.2）
# =========================================================================
def _group(n_sources, weight, age_hours, now):
    date = utils.iso(now - timedelta(hours=age_hours))
    return [
        _mk("Badge boundary story", "Source {0}".format(i), weight, date)
        for i in range(n_sources)
    ]


def test_badge_heavy_threshold_is_three_cross_refs():
    now = utils.now_utc()
    three_sources = utils.dedup_and_score(_group(3, 0.8, 1, now), now=now)[0]
    four_sources = utils.dedup_and_score(_group(4, 0.8, 1, now), now=now)[0]
    assert three_sources["cross_refs"] == 2
    assert "heavy" not in three_sources["badges"]
    assert four_sources["cross_refs"] == 3
    assert "heavy" in four_sources["badges"]


def test_badge_flash_needs_weight_095_and_within_24h():
    now = utils.now_utc()
    hit = utils.dedup_and_score(_group(1, 0.95, 23.9, now), now=now)[0]
    too_old = utils.dedup_and_score(_group(1, 0.95, 24.1, now), now=now)[0]
    too_light = utils.dedup_and_score(_group(1, 0.94, 1, now), now=now)[0]
    assert hit["badges"] == ["flash"]
    assert too_old["badges"] == []
    assert too_light["badges"] == []


def test_badge_both_can_apply(sample_items):
    now = utils.now_utc()
    merged = utils.dedup_and_score(sample_items, now=now)
    top = next(it for it in merged if it["title"] == "Anthropic ships Claude Opus 5")
    assert top["cross_refs"] == 3
    assert set(top["badges"]) == {"heavy", "flash"}
    assert top["source_list"] == [
        "Anthropic News", "Simon Willison", "Latent Space", "HuggingFace Blog",
    ]


def test_sample_items_release_pair_not_merged(sample_items):
    merged = utils.dedup_and_score(sample_items)
    releases = [it for it in merged if "release" in it["tags"]]
    assert len(releases) == 2
    assert {it["source"] for it in releases} == {"Claude Code Releases", "Claude Code Mirror"}


def test_sample_items_dedup_count(sample_items):
    # 10 条原始条目，其中 4 条同题 → 合并后 7 条
    assert len(sample_items) == 10
    assert len(utils.dedup_and_score(sample_items)) == 7


# =========================================================================
# §1 item schema
# =========================================================================
def test_make_item_fills_every_contract_field():
    item = schema.make_item(
        title="Anthropic ships Claude Opus 5",
        url="https://www.anthropic.com/news/claude-opus-5",
        source="Anthropic News",
        source_kind="local",
        backend="html",
        weight=0.98,
    )
    for field in schema.REQUIRED_FIELDS:
        assert item.get(field) not in (None, "")
    assert item["sig"] == utils.item_signature(item)
    assert item["cross_refs"] == 0
    assert item["source_list"] == ["Anthropic News"]
    assert item["hotness"] == 0.0
    assert item["badges"] == []
    assert item["sync_run_id"] == ""
    assert utils.parse_date(item["fetched_at"]) is not None
    assert item["summary"] == "" and item["tags"] == []
    assert item["metrics"] == {} and item["extra"] == {}


def test_make_item_defaults_date_to_now_and_normalizes_input():
    now = utils.now_utc()
    default_date = schema.make_item(
        title="t", url="u", source="s", source_kind="local", backend="rss", weight=0.5
    )["date"]
    assert abs((utils.parse_date(default_date) - now).total_seconds()) < 5

    normalized = schema.make_item(
        title="t", url="u", source="s", source_kind="local", backend="rss", weight=0.5,
        date="Tue, 25 Aug 2026 03:00:00 +0000",
    )["date"]
    assert normalized == "2026-08-25T03:00:00+00:00"


def test_make_item_release_signature_uses_source_and_version():
    item = schema.make_item(
        title="v2.1.0", url="u", source="Claude Code Releases", source_kind="local",
        backend="git", weight=0.97, tags=["release"], metrics={"version": "v2.1.0"},
    )
    assert item["sig"] == utils.item_signature(
        "ignored", source="Claude Code Releases", version="v2.1.0"
    )


def test_validate_item_accepts_well_formed_items(sample_items):
    good = [it for it in sample_items if it["date"]]
    assert len(good) == 9
    for item in good:
        assert schema.validate_item(item) == []


def test_validate_item_flags_missing_required_fields():
    problems = schema.validate_item({"title": "no url", "url": ""})
    assert any("url" in p for p in problems)
    assert any("sig" in p for p in problems)


@pytest.mark.parametrize(
    "patch, needle",
    [
        ({"weight": 1.5}, "weight"),
        ({"weight": "high"}, "weight"),
        ({"source_kind": "telepathy"}, "source_kind"),
        ({"backend": "carrier-pigeon"}, "backend"),
        ({"date": "昨天"}, "date"),
        ({"tags": "not-a-list"}, "tags"),
        ({"metrics": []}, "metrics"),
        ({"cross_refs": "1"}, "cross_refs"),
        ({"badges": ["sparkly"]}, "badge"),
    ],
)
def test_validate_item_flags_bad_values(patch, needle):
    item = schema.make_item(
        title="t", url="https://example.com", source="s", source_kind="local",
        backend="rss", weight=0.5,
    )
    item.update(patch)
    problems = schema.validate_item(item)
    assert any(needle in p for p in problems), problems
