"""test_eyes.py —— 锁死 spec §7：各眼 parse_payload 喂 fixtures 出标准 item、白名单/mute 过滤。

fetch() 网络壳一律 monkeypatch 底层 engine 调用，绝不真实出网（离线思维 + conftest 物理层兜底）。
"""

from __future__ import annotations

import json

import pytest

from qianliyan.core import paths, schema
from qianliyan.eyes import EYES, aihot, builders, company, insights, local
from qianliyan.engine import http


def _load_fixture(fixtures_dir, name):
    return json.loads((fixtures_dir / name).read_text(encoding="utf-8"))


# =========================================================================
# EYES 注册表
# =========================================================================
def test_eyes_registry_has_five_eyes():
    assert set(EYES.keys()) == {"aihot", "builders", "company", "local", "insights"}
    for fn in EYES.values():
        assert callable(fn)


# =========================================================================
# 左眼 aihot
# =========================================================================
def test_aihot_parse_payload_maps_field_variants_and_drops_incomplete(fixtures_dir):
    payload = _load_fixture(fixtures_dir, "aihot_payload.json")
    items = aihot.parse_payload(payload, {"weight": 0.75}, category="llm")

    # 第 4 条缺 title/url，应被丢弃
    assert len(items) == 3
    for item in items:
        problems = schema.validate_item(item)
        assert problems == []
        assert item["source_kind"] == "aihot"
        assert item["backend"] == "rest"
        assert item["weight"] == 0.75
        assert item["extra"]["category"] == "llm"

    titles = {item["title"] for item in items}
    assert "GPT-6 rumored to ship with native tool use" in titles
    assert "Agent frameworks converge on MCP" in titles
    assert "New dev tool: instant repo indexing" in titles

    by_title = {item["title"]: item for item in items}
    assert by_title["Agent frameworks converge on MCP"]["summary"].startswith("Most major agent")
    assert by_title["New dev tool: instant repo indexing"]["url"].endswith("repo-indexing")


def test_aihot_parse_payload_default_weight():
    items = aihot.parse_payload({"data": [{"title": "t", "url": "https://x/1"}]}, {})
    assert items[0]["weight"] == aihot.DEFAULT_WEIGHT


def test_aihot_parse_payload_accepts_bare_list():
    items = aihot.parse_payload(
        [{"title": "t", "url": "https://x/1"}], {"weight": 0.5},
    )
    assert len(items) == 1
    assert items[0]["weight"] == 0.5


def test_aihot_fetch_pulls_feeds_and_dedups(monkeypatch, fixtures_dir):
    """v0.3 RSS：逐 feed 拉 xml、解析、汇总、内部去重（同一 feed 供两次应折叠）。"""
    curated = (fixtures_dir / "real" / "aihot_feed_curated.xml").read_text(encoding="utf-8")
    calls = []

    class _Resp:
        def __init__(self, text):
            self.text = text

    def fake_get(url, *args, **kwargs):
        calls.append(url)
        return _Resp(curated)

    monkeypatch.setattr(aihot.http, "get", fake_get)
    # 通用 feed 两条（都会返回同一份 curated）+ 一个分类 feed —— 期望三次请求、结果去重
    cfg = {
        "base_url": "https://aihot.virxact.com",
        "feeds": [
            {"path": "/feed.xml", "kind": "curated", "weight": 0.85},
            {"path": "/feed/all.xml", "kind": "all", "weight": 0.70},
        ],
        "category_feeds": {"slugs": ["paper"], "weight": 0.75},
    }
    items = aihot.fetch(cfg)

    assert len(calls) == 3
    assert calls[0].endswith("/feed.xml")
    assert calls[1].endswith("/feed/all.xml")
    assert calls[2].endswith("/feed/category/paper.xml")
    # 三次都返回同一份 35 条，去重后应恰为 35（按 canonical url/guid）
    assert len(items) == 35
    for item in items:
        assert item["source_kind"] == "aihot"
        assert item["backend"] == "rss"
        assert item["source"] == "AIHOT · 卡兹克"


def test_aihot_fetch_offline_returns_empty(monkeypatch):
    """离线时每个 feed 拉取都抛错并被吞掉，整眼返回空而非炸整眼。"""
    monkeypatch.setenv("QLY_OFFLINE", "1")
    assert aihot.fetch({"feeds": [{"path": "/feed.xml", "kind": "curated", "weight": 0.85}]}) == []


# =========================================================================
# 右眼 builders
# =========================================================================
def test_builders_parse_payload_whitelist_and_mute_with_injected_cfg(fixtures_dir):
    payload = _load_fixture(fixtures_dir, "builders_payload.json")
    cfg = {
        "default_weight": 0.8,
        "builders": [
            {"handle": "karpathy", "name": "Andrej Karpathy"},
            {"handle": "simonw", "name": "Simon Willison", "weight": 0.9},
            {"handle": "GaryMarcus", "name": "Gary Marcus"},
        ],
        "x_follows": [
            {"handle": "GaryMarcus", "mode": "highlights", "mute": True},
        ],
    }
    items = builders.parse_payload(payload, cfg)

    # some_random_person 不在白名单、GaryMarcus 被 mute，均应被剔除
    sources = {item["source"] for item in items}
    assert sources == {"@karpathy", "@simonw"}
    for item in items:
        problems = schema.validate_item(item)
        assert problems == []
        assert item["source_kind"] == "builders"
        assert item["backend"] == "raw_json"
        assert item["tags"] == ["x", "builders"]
        assert item["extra"] == {"platform": "x"}

    by_source = {item["source"]: item for item in items}
    assert by_source["@karpathy"]["weight"] == 0.8       # 用 default_weight
    assert by_source["@simonw"]["weight"] == 0.9          # builder 级覆盖
    assert len(by_source["@karpathy"]["title"]) <= 80


def test_builders_parse_payload_title_truncated_to_80_chars():
    long_text = "x" * 200
    payload = {"tweets": [{"handle": "karpathy", "text": long_text, "url": "https://x.com/1"}]}
    cfg = {"builders": [{"handle": "karpathy"}], "x_follows": []}
    items = builders.parse_payload(payload, cfg)
    assert len(items[0]["title"]) == 80
    assert items[0]["summary"] == long_text


def test_builders_parse_feed_x_uses_real_x_follows_config_by_default(fixtures_dir):
    """v0.3：不注入 x_follows 时回退读真实 config/x-follows.yaml；allowlist 空 → 全收。"""
    payload = _load_fixture(fixtures_dir, "real/builders_feed_x.json")
    cfg = paths.load_yaml_config("builders")            # allowlist: [] → 全收
    items = builders.parse_feed_x(payload, cfg)
    handles = {item["extra"]["handle"] for item in items}
    # feed 内 13 位 builder 全部收录（elonmusk 虽在 x-follows 被 mute，但不在本 feed，无副作用）
    assert "zarazhangrui" in handles
    assert "amasad" in handles
    assert len(handles) == 13
    assert len(items) == 29                             # feed-x.json stats.totalTweets


def test_builders_and_follows_config_new_structure():
    """v0.3 配置结构：builders.yaml 指向真实仓库、x-follows 用真实 13 handle + mute 示例。"""
    cfg = paths.load_yaml_config("builders")
    assert cfg.get("repo") == "zarazhangrui/follow-builders"
    assert cfg.get("branch") == "main"
    assert "feed-x.json" in (cfg.get("feeds") or [])
    assert float(cfg.get("default_weight")) == 0.8
    assert (cfg.get("allowlist") or []) == []           # 空 = 全收
    assert isinstance(cfg.get("weight_overrides"), dict)

    follows = paths.load_yaml_config("x-follows").get("follows") or []
    handles = {str(f.get("handle")).casefold() for f in follows}
    for real_handle in ("thsottiaux", "amasad", "rauchg", "garrytan", "zarazhangrui", "steipete"):
        assert real_handle in handles
    assert any(f.get("mute") for f in follows)          # 至少一个 mute 示例


def test_builders_fetch_missing_repo_returns_empty(caplog):
    with caplog.at_level("WARNING"):
        assert builders.fetch({"repo": None, "feeds": ["feed-x.json"]}) == []


def test_builders_fetch_builds_raw_url_and_parses(monkeypatch, fixtures_dir):
    payload = _load_fixture(fixtures_dir, "real/builders_feed_x.json")
    captured = []

    def fake_get_json(url):
        captured.append(url)
        return payload if url.endswith("feed-x.json") else {}

    monkeypatch.setattr(builders.http, "get_json", fake_get_json)
    cfg = {
        "repo": "zarazhangrui/follow-builders", "branch": "main",
        "feeds": ["feed-x.json"],
        "default_weight": 0.8,
        "x_follows": [],
    }
    items = builders.fetch(cfg)
    assert captured == [
        "https://raw.githubusercontent.com/zarazhangrui/follow-builders/main/feed-x.json"
    ]
    assert len(items) == 29
    assert all(it["source_kind"] == "builders" for it in items)


# =========================================================================
# 内眼 company
# =========================================================================
def test_company_parse_payload_builds_standard_items():
    entries = [
        {"title": "平台完成一次算力扩容", "url": "https://xinsheng.internal/thread/1", "summary": "扩容公告"},
        {"title": "", "url": "https://xinsheng.internal/thread/2"},  # 缺标题应丢弃
        {"title": "缺链接的条目", "url": ""},  # 缺链接应丢弃
    ]
    items = company.parse_payload(entries, {"source": "心声社区", "weight": 0.85})
    assert len(items) == 1
    item = items[0]
    assert schema.validate_item(item) == []
    assert item["source"] == "心声社区"
    assert item["source_kind"] == "company"
    assert item["backend"] == "cdp"
    assert item["weight"] == 0.85
    assert item["tags"] == ["company", "internal"]


def test_company_parse_payload_default_source_and_weight():
    items = company.parse_payload([{"title": "标题足够长", "url": "https://x/1"}])
    assert items[0]["source"] == "内网"
    assert items[0]["weight"] == company.DEFAULT_WEIGHT


def test_company_fetch_raises_when_all_sources_fail(monkeypatch):
    """无 playwright 且代理不可达（socket 被物理封锁）—— 预期两个信源全灭，最终向上抛异常。"""
    with pytest.raises(Exception):
        company.fetch({})


def test_company_internal_sources_constants_are_well_formed():
    assert len(company.INTERNAL_SOURCES) == 2
    names = {src["name"] for src in company.INTERNAL_SOURCES}
    assert names == {"心声社区", "稼先"}
    for src in company.INTERNAL_SOURCES:
        assert src["url"].startswith("https://")
        assert src["item_selector"]
        assert src["title_selector"]
        assert src["link_selector"]


# =========================================================================
# 远眼 local
# =========================================================================
def test_local_parse_payload_inherits_weight_tags_and_merges_entry_tags():
    entries = [
        {"title": "Claude Code v2.1.0", "url": "https://github.com/a/b/releases/tag/v2.1.0",
         "summary": "release notes", "date": "2026-08-20", "tags": ["release"],
         "metrics": {"version": "v2.1.0"}},
        {"title": "", "url": "https://x/2"},  # 缺标题应丢弃
    ]
    src_cfg = {"name": "Claude Code Releases", "url": "https://github.com/a/b", "type": "git",
               "weight": 0.97, "tags": ["official", "claude-code"]}
    items = local.parse_payload(entries, src_cfg)

    assert len(items) == 1
    item = items[0]
    assert schema.validate_item(item) == []
    assert item["source"] == "Claude Code Releases"
    assert item["source_kind"] == "local"
    assert item["backend"] == "git"
    assert item["weight"] == 0.97
    assert item["tags"] == ["official", "claude-code", "release"]
    assert item["metrics"] == {"version": "v2.1.0"}


def test_local_parse_payload_default_backend_via_detect_backend():
    entries = [{"title": "An article with a decent title", "url": "https://example.com/a"}]
    src_cfg = {"name": "Anthropic News", "url": "https://www.anthropic.com/news", "type": "html",
               "weight": 0.98, "tags": ["official"]}
    items = local.parse_payload(entries, src_cfg)
    assert items[0]["backend"] == "html"


def test_local_fetch_isolates_single_source_failures(monkeypatch):
    def fake_fetch_source(src_cfg):
        if src_cfg["name"] == "Broken Source":
            raise RuntimeError("boom")
        return [{"title": "A perfectly fine long enough title", "url": "https://example.com/ok"}]

    monkeypatch.setattr(local.remote_sync, "fetch_source", fake_fetch_source)
    cfg = {
        "sources": [
            {"name": "Broken Source", "url": "https://example.com/broken", "type": "html", "weight": 0.5},
            {"name": "Good Source", "url": "https://example.com/good", "type": "html", "weight": 0.6},
        ]
    }
    items = local.fetch(cfg)
    assert len(items) == 1
    assert items[0]["source"] == "Good Source"


def test_local_fetch_no_sources_returns_empty():
    assert local.fetch({}) == []
    assert local.fetch(None) == []


# =========================================================================
# 洞眼 insights
# =========================================================================
def test_insights_parse_payload_maps_fields_and_ranks(fixtures_dir):
    payload = _load_fixture(fixtures_dir, "insights_payload.json")
    items = insights.parse_payload(payload, {"weight": 0.6})

    # 第 4 条无 name，应被丢弃
    assert len(items) == 3
    for item in items:
        assert schema.validate_item(item) == []
        assert item["source_kind"] == "insights"
        assert item["backend"] == "raw_json"
        assert item["tags"] == ["plugins", "insights"]
        assert item["weight"] == 0.6

    by_title = {item["title"]: item for item in items}
    assert by_title["auto-commit-helper"]["metrics"]["rank"] == 1
    assert by_title["auto-commit-helper"]["metrics"]["installs"] == 4820
    assert by_title["context-compactor"]["metrics"]["rank"] == 2
    assert by_title["context-compactor"]["metrics"]["installs"] == 3190
    assert by_title["test-writer"]["metrics"]["rank"] == 3
    assert by_title["test-writer"]["metrics"]["installs"] == 2100


def test_insights_parse_payload_default_weight():
    items = insights.parse_payload([{"name": "p", "installs": 1}])
    assert items[0]["weight"] == insights.DEFAULT_WEIGHT


def test_insights_fetch_prefers_raw_when_env_set(monkeypatch, fixtures_dir):
    monkeypatch.setenv("QLY_INSIGHTS_PREFER_RAW", "1")
    payload = _load_fixture(fixtures_dir, "insights_payload.json")
    captured = {}

    def fake_get_json(url):
        captured["url"] = url
        return payload

    monkeypatch.setattr(insights.http, "get_json", fake_get_json)
    items = insights.fetch({"repo": "org/repo", "branch": "main", "data_paths": ["data/plugins.json"]})
    assert captured["url"] == "https://raw.githubusercontent.com/org/repo/main/data/plugins.json"
    assert len(items) == 3


def test_insights_fetch_offline_skips_clone_and_falls_back_to_raw_which_also_fails(monkeypatch):
    """离线模式下不应尝试真实 git 子进程；raw 回退同样因 QLY_OFFLINE 快速失败，最终返回空列表。"""
    monkeypatch.setenv("QLY_OFFLINE", "1")

    def _should_not_be_called(*args, **kwargs):
        raise AssertionError("离线模式下不应调用 subprocess.run 尝试真实 git 出网")

    monkeypatch.setattr(insights.subprocess, "run", _should_not_be_called)
    items = insights.fetch({"repo": "org/repo", "branch": "main", "data_paths": ["data/plugins.json"]})
    assert items == []


def test_insights_fetch_clone_success_reads_local_file(monkeypatch, tmp_data_dir, fixtures_dir):
    payload = _load_fixture(fixtures_dir, "insights_payload.json")
    repo_dir = tmp_data_dir / "repos" / "claude-code-insights"
    (repo_dir / "data").mkdir(parents=True, exist_ok=True)
    (repo_dir / "data" / "plugins.json").write_text(json.dumps(payload), encoding="utf-8")

    monkeypatch.setenv("QLY_OFFLINE", "0")  # 该测试模拟"clone 已就地成功"，不做真实网络调用

    class _FakeCompletedProcess:
        returncode = 0
        stderr = ""

    monkeypatch.setattr(insights.subprocess, "run", lambda *a, **kw: _FakeCompletedProcess())
    items = insights.fetch({"repo": "org/repo", "branch": "main", "data_paths": ["data/plugins.json"]})
    assert len(items) == 3


def test_insights_fetch_clone_failure_falls_back_to_raw(monkeypatch, tmp_data_dir, fixtures_dir):
    payload = _load_fixture(fixtures_dir, "insights_payload.json")

    class _FakeFailedProcess:
        returncode = 1
        stderr = "fatal: could not resolve host"

    monkeypatch.setenv("QLY_OFFLINE", "0")
    monkeypatch.setattr(insights.subprocess, "run", lambda *a, **kw: _FakeFailedProcess())
    monkeypatch.setattr(insights.http, "get_json", lambda url: payload)
    items = insights.fetch({"repo": "org/repo", "branch": "main", "data_paths": ["data/plugins.json"]})
    assert len(items) == 3
