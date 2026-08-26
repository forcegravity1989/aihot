"""test_eyes_change.py —— 变更情报数据层（Wave H1，spec-v0.3 §19）离线单测。

对 tests/fixtures/real 的真实 fixtures 喂纯解析函数：
  * cc_prompts.parse_changelog       —— CHANGELOG.md 版本块 → 变更 item；
  * plugins_official.parse           —— marketplace.json + bump-tracking.json → 官方插件变更 item；
  * insights.parse_daily             —— plugins-daily-insight.md 表格 → 业界资产 item；
  * engine.http 的 max_bytes / get_text —— §19.4 量级保护（流式截断），既有调用不受影响。

全程离线：网络壳只在 monkeypatch 掉底层后测；conftest 物理层封网兜底。
"""

from __future__ import annotations

import json

import pytest

from qianliyan.core import schema
from qianliyan.eyes import CHANGE_EYES, EYES, cc_prompts, insights, plugins_official
from qianliyan.engine import http


def _real(fixtures_dir, name):
    return (fixtures_dir / "real" / name).read_text(encoding="utf-8")


# =========================================================================
# 注册表：核心五眼不变；变更源单列 CHANGE_EYES
# =========================================================================
def test_core_eyes_registry_stays_five():
    assert set(EYES.keys()) == {"aihot", "builders", "company", "local", "insights"}


def test_change_eyes_registry_has_cc_prompts_and_plugins_official():
    assert set(CHANGE_EYES.keys()) == {"cc_prompts", "plugins_official"}
    for fn in CHANGE_EYES.values():
        assert callable(fn)


# =========================================================================
# cc_prompts.parse_changelog
# =========================================================================
def test_cc_prompts_parse_changelog_real(fixtures_dir):
    md = _real(fixtures_dir, "ccprompts_changelog.md")
    items = cc_prompts.parse_changelog(md)

    # fixture 里 16 个带变更的版本块（4 个「No changes」块产 0 条）
    assert len(items) == 16
    for item in items:
        assert schema.validate_item(item) == []
        assert item["source_kind"] == "local"
        assert item["backend"] == "git"
        assert item["source"] == cc_prompts.SOURCE_NAME
        extra = item["extra"]
        assert extra["format"] == "changelog"
        assert extra["subject"] == "claude-code-prompts"
        assert extra["version"]
        assert isinstance(extra["token_delta"], int)
        assert isinstance(extra["changes"], list) and extra["changes"]
        assert set(["claude-code", "prompts", "changelog", "practices"]).issubset(set(item["tags"]))


def test_cc_prompts_parse_changelog_token_delta_and_title(fixtures_dir):
    items = cc_prompts.parse_changelog(_real(fixtures_dir, "ccprompts_changelog.md"))
    by_ver = {it["extra"]["version"]: it for it in items}

    assert by_ver["2.1.242"]["extra"]["token_delta"] == 30636
    assert "+30,636 tokens" in by_ver["2.1.242"]["title"]
    assert "27 项" in by_ver["2.1.242"]["title"]
    assert by_ver["2.1.242"]["url"].endswith("/commit/e28b8de")
    # 负增量（token 缩减）
    assert by_ver["2.1.240"]["extra"]["token_delta"] == -1911
    assert "-1,911 tokens" in by_ver["2.1.240"]["title"]


def test_cc_prompts_no_changes_blocks_produce_zero(fixtures_dir):
    items = cc_prompts.parse_changelog(_real(fixtures_dir, "ccprompts_changelog.md"))
    versions = {it["extra"]["version"] for it in items}
    for empty in ("2.1.245", "2.1.243", "2.1.231", "2.1.226"):
        assert empty not in versions


def test_cc_prompts_change_entry_classification(fixtures_dir):
    items = cc_prompts.parse_changelog(_real(fixtures_dir, "ccprompts_changelog.md"))
    by_ver = {it["extra"]["version"]: it for it in items}

    first = by_ver["2.1.242"]["extra"]["changes"][0]
    assert first["kind"] == "new"
    assert first["component"] == "System Prompt"
    assert first["title"] == "Project timeline user message provenance"
    assert first["desc"].startswith("Treats server-verified")

    kinds = {c["kind"] for it in items for c in it["extra"]["changes"]}
    assert {"new", "removed", "modified"}.issubset(kinds)


def test_cc_prompts_fetch_local_path_offline(fixtures_dir, monkeypatch):
    monkeypatch.setenv("QLY_OFFLINE", "1")
    path = fixtures_dir / "real" / "ccprompts_changelog.md"
    items = cc_prompts.fetch({"local_path": str(path)})
    assert len(items) == 16


def test_cc_prompts_fetch_offline_without_local_raises(monkeypatch):
    monkeypatch.setenv("QLY_OFFLINE", "1")
    with pytest.raises(http.OfflineError):
        cc_prompts.fetch({})


# =========================================================================
# plugins_official.parse
# =========================================================================
def test_plugins_official_parse_real(fixtures_dir):
    marketplace = json.loads(_real(fixtures_dir, "plugins_official_marketplace.json"))
    bump = json.loads(_real(fixtures_dir, "plugins_official_bump.json"))
    items = plugins_official.parse(marketplace, bump)

    # 每个近期 bump 的插件一条（fixture bump-tracking 有 11 个 releases-only）
    assert len(items) == len(bump["releases-only"]) == 11
    for item in items:
        assert schema.validate_item(item) == []
        assert item["source_kind"] == "local"
        assert item["backend"] == "git"
        extra = item["extra"]
        assert extra["format"] == "changelog"
        assert extra["subject"] == "plugins-official"
        assert extra["plugin"]
        assert set(["plugins", "official", "changelog"]).issubset(set(item["tags"]))

    by_plugin = {it["extra"]["plugin"]: it for it in items}
    azure = by_plugin["azure"]
    assert azure["extra"]["category"] == "deployment"
    assert azure["extra"]["version"] == "ea76537"           # url-type source 用短 sha
    assert azure["summary"].startswith("Transform Claude into an Azure expert")
    assert "azure" in azure["title"]


def test_plugins_official_parse_handles_empty_and_missing():
    assert plugins_official.parse({}, {}) == []
    assert plugins_official.parse({"plugins": []}, {"releases-only": ["ghost"]}) != []  # 未匹配也产 item
    items = plugins_official.parse({"plugins": []}, {"releases-only": ["ghost"]})
    assert items[0]["extra"]["plugin"] == "ghost"
    assert items[0]["summary"] == ""


def test_plugins_official_fetch_local_paths(fixtures_dir, monkeypatch):
    monkeypatch.setenv("QLY_OFFLINE", "1")
    items = plugins_official.fetch({
        "marketplace_path": str(fixtures_dir / "real" / "plugins_official_marketplace.json"),
        "bump_path": str(fixtures_dir / "real" / "plugins_official_bump.json"),
    })
    assert len(items) == 11


# =========================================================================
# insights.parse_daily（改指真实仓的 Markdown 榜单）
# =========================================================================
def test_insights_parse_daily_real(fixtures_dir):
    items = insights.parse_daily(_real(fixtures_dir, "insights_daily.md"))

    assert len(items) == 30            # Top 30 表格
    for item in items:
        assert schema.validate_item(item) == []
        assert item["source_kind"] == "insights"
        assert item["backend"] == "git"
        assert item["extra"]["format"] == "repo"
        assert item["extra"]["subject"] == "plugin-ecosystem"
        assert set(["plugins", "skills", "insights", "trending"]).issubset(set(item["tags"]))

    first = items[0]
    assert first["title"] == "superpowers"
    assert first["url"] == "https://github.com/obra/superpowers"
    assert first["metrics"] == {"stars": 277158, "forks": 24798, "rank": 1}
    assert first["extra"]["rank"] == 1


def test_insights_parse_payload_still_works_backward_compat(fixtures_dir):
    """旧 JSON 排行解析壳保留（test_eyes.py 仍依赖）。"""
    payload = json.loads((fixtures_dir / "insights_payload.json").read_text(encoding="utf-8"))
    items = insights.parse_payload(payload, {"weight": 0.6})
    assert items
    assert all(it["backend"] == "raw_json" for it in items)


def test_insights_fetch_dispatches_markdown_to_parse_daily(fixtures_dir, monkeypatch):
    monkeypatch.setenv("QLY_INSIGHTS_PREFER_RAW", "1")
    md = _real(fixtures_dir, "insights_daily.md")
    captured = {}

    def fake_get_text(url, **kwargs):
        captured["url"] = url
        return md

    monkeypatch.setattr(insights.http, "get_text", fake_get_text)
    items = insights.fetch({
        "repo": "zhoux77899/claude-code-insights", "branch": "main",
        "data_paths": ["plugins/plugins-daily-insight.md"],
    })
    assert captured["url"].endswith("plugins/plugins-daily-insight.md")
    assert len(items) == 30
    assert all(it["source_kind"] == "insights" for it in items)


# =========================================================================
# engine.http：max_bytes 流式截断（§19.4 量级保护）
# =========================================================================
class _FakeStream:
    def __init__(self, chunks, json_data=None):
        self._chunks = chunks
        self._json_data = json_data
        self._content = b""
        self.closed = False

    def iter_content(self, chunk_size=65536):
        for chunk in self._chunks:
            yield chunk

    def close(self):
        self.closed = True

    @property
    def text(self):
        return self._content.decode("utf-8", "replace")

    def json(self):
        return json.loads(self._content.decode("utf-8"))


def test_http_get_default_max_bytes_is_none_no_stream(monkeypatch):
    """默认 max_bytes=None：既有行为不变，底层不传 stream（锁死向后兼容）。"""
    def fake_get(url, timeout=None, headers=None):     # 不接受 stream —— 传了会 TypeError
        return _FakeStream([b"ok"])

    monkeypatch.setattr(http.requests, "get", fake_get)
    resp = http.get("https://example.com/x")
    assert isinstance(resp, _FakeStream)


def test_http_get_with_max_bytes_streams_and_truncates(monkeypatch, caplog):
    def fake_get(url, timeout=None, headers=None, stream=None):
        assert stream is True
        return _FakeStream([b"aaaa", b"bbbb", b"cccc"])   # 12 字节

    monkeypatch.setattr(http.requests, "get", fake_get)
    with caplog.at_level("WARNING"):
        resp = http.get("https://example.com/big", max_bytes=10)
    assert resp.text == "aaaabbbbcc"                       # 截断到 10 字节
    assert resp.closed is True
    assert any("截断" in rec.getMessage() for rec in caplog.records)


def test_http_get_json_with_max_bytes(monkeypatch):
    body = json.dumps({"a": 1, "b": 2}).encode("utf-8")

    def fake_get(url, timeout=None, headers=None, stream=None):
        return _FakeStream([body])

    monkeypatch.setattr(http.requests, "get", fake_get)
    assert http.get_json("https://example.com/x", max_bytes=http.DEFAULT_MAX_BYTES) == {"a": 1, "b": 2}


def test_http_get_text_uses_default_cap(monkeypatch):
    def fake_get(url, timeout=None, headers=None, stream=None):
        return _FakeStream([b"hello ", b"world"])

    monkeypatch.setattr(http.requests, "get", fake_get)
    assert http.get_text("https://example.com/x") == "hello world"
