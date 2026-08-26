"""test_channels.py —— 锁死 spec §8.2 / §9.2：各匹配字段、any_of、多频道归属、limit、写盘。"""

from __future__ import annotations

import json

import pytest

from qianliyan.core import schema, storage, utils
from qianliyan.pipeline import channels


def make(**kwargs):
    """构造一条最小可用 item，只覆盖用例关心的字段。"""
    base = dict(
        title="A title",
        url="https://example.com/a",
        source="Example",
        source_kind="local",
        backend="rss",
        weight=0.5,
    )
    extra = kwargs.pop("extra", None)
    base.update(kwargs)
    item = schema.make_item(**base)
    if extra:
        item["extra"].update(extra)
    return item


# =========================================================================
# 单字段匹配语义
# =========================================================================
def test_tags_any_is_case_insensitive():
    item = make(tags=["Official", "Models"])
    assert channels.match_item(item, {"tags_any": ["models"]}) is True
    assert channels.match_item(item, {"tags_any": ["MODELS", "无关"]}) is True
    assert channels.match_item(item, {"tags_any": ["plugins"]}) is False


def test_tags_all_requires_every_tag():
    item = make(tags=["official", "models"])
    assert channels.match_item(item, {"tags_all": ["official", "models"]}) is True
    assert channels.match_item(item, {"tags_all": ["official", "release"]}) is False


def test_sources_include_does_case_insensitive_substring_over_source_list():
    item = make(source="Anthropic News")
    item["source_list"] = ["Anthropic News", "Simon Willison"]
    assert channels.match_item(item, {"sources_include": ["anthropic"]}) is True
    assert channels.match_item(item, {"sources_include": ["WILLISON"]}) is True
    assert channels.match_item(item, {"sources_include": ["openai"]}) is False


def test_sources_include_falls_back_to_source_when_list_missing():
    item = make(source="arXiv cs.CL")
    item["source_list"] = []
    assert channels.match_item(item, {"sources_include": ["arxiv"]}) is True


def test_keywords_any_matches_title_and_summary():
    item = make(title="Shipping Claude Code 2.1", summary="包含 MCP 服务器支持")
    assert channels.match_item(item, {"keywords_any": ["claude code"]}) is True
    assert channels.match_item(item, {"keywords_any": ["mcp 服务器"]}) is True
    assert channels.match_item(item, {"keywords_any": ["gemini"]}) is False


def test_aihot_category_matches_extra_category():
    item = make(extra={"category": "LLM"})
    assert channels.match_item(item, {"aihot_category": ["llm"]}) is True
    assert channels.match_item(item, {"aihot_category": ["research"]}) is False
    assert channels.match_item(make(), {"aihot_category": ["llm"]}) is False


def test_platforms_matches_extra_platform():
    item = make(extra={"platform": "x"})
    assert channels.match_item(item, {"platforms": ["x", "twitter"]}) is True
    assert channels.match_item(item, {"platforms": ["weibo"]}) is False


def test_source_kinds_matches_item_source_kind():
    assert channels.match_item(make(source_kind="company"), {"source_kinds": ["company"]}) is True
    assert channels.match_item(make(source_kind="local"), {"source_kinds": ["company"]}) is False


def test_scalar_value_is_accepted_like_single_element_list():
    item = make(tags=["release"])
    assert channels.match_item(item, {"tags_any": "release"}) is True


# =========================================================================
# 块内 AND / any_of 的 OR
# =========================================================================
def test_fields_inside_one_block_are_anded():
    item = make(tags=["models"], source_kind="local")
    assert channels.match_item(item, {"tags_any": ["models"], "source_kinds": ["local"]}) is True
    assert channels.match_item(item, {"tags_any": ["models"], "source_kinds": ["company"]}) is False


def test_any_of_blocks_are_ored():
    match_cfg = {"any_of": [{"tags_any": ["claude-code"]}, {"keywords_any": ["claude code"]}]}
    assert channels.match_item(make(tags=["claude-code"]), match_cfg) is True
    assert channels.match_item(make(title="Claude Code ships"), match_cfg) is True
    assert channels.match_item(make(title="Gemini ships", tags=["models"]), match_cfg) is False


def test_any_of_is_anded_with_sibling_fields():
    match_cfg = {
        "source_kinds": ["local"],
        "any_of": [{"tags_any": ["models"]}, {"tags_any": ["release"]}],
    }
    assert channels.match_item(make(source_kind="local", tags=["release"]), match_cfg) is True
    assert channels.match_item(make(source_kind="company", tags=["release"]), match_cfg) is False
    assert channels.match_item(make(source_kind="local", tags=["kol"]), match_cfg) is False


def test_nested_any_of_is_supported():
    match_cfg = {"any_of": [{"any_of": [{"tags_any": ["a"]}, {"tags_any": ["b"]}]}]}
    assert channels.match_item(make(tags=["b"]), match_cfg) is True
    assert channels.match_item(make(tags=["c"]), match_cfg) is False


def test_empty_match_block_is_vacuously_true():
    assert channels.match_item(make(), {}) is True
    assert channels.match_item(make(), None) is True


def test_unknown_match_field_is_ignored_with_warning(caplog):
    with caplog.at_level("WARNING"):
        assert channels.match_item(make(tags=["x"]), {"tags_any": ["x"], "无此字段": [1]}) is True
    assert any("未知匹配字段" in rec.getMessage() for rec in caplog.records)


# =========================================================================
# route：多频道归属 / 排序 / limit
# =========================================================================
def _channel(name, match, limit=30, title=None):
    return {"name": name, "title": title or name, "limit": limit, "match": match}


def test_route_keeps_channel_order_and_allows_multi_membership():
    item = make(title="Claude Opus 5", tags=["models", "release"])
    item["hotness"] = 1.0
    routed = channels.route([item], [
        _channel("models", {"tags_any": ["models"]}),
        _channel("releases", {"tags_any": ["release"]}),
        _channel("plugins", {"tags_any": ["plugins"]}),
    ])
    assert list(routed.keys()) == ["models", "releases", "plugins"]
    assert routed["models"] == [item]
    assert routed["releases"] == [item]
    assert routed["plugins"] == []


def test_route_sorts_by_hotness_desc_and_truncates_to_limit():
    items = []
    for index in range(5):
        item = make(title="t{0}".format(index), tags=["models"])
        item["hotness"] = index / 10.0
        items.append(item)
    routed = channels.route(items, [_channel("models", {"tags_any": ["models"]}, limit=3)])
    assert [it["hotness"] for it in routed["models"]] == [0.4, 0.3, 0.2]


def test_route_skips_channel_without_match_rules(caplog):
    item = make(tags=["models"])
    with caplog.at_level("WARNING"):
        routed = channels.route([item], [_channel("broken", {})])
    assert routed["broken"] == []
    assert any("没有 match 规则" in rec.getMessage() for rec in caplog.records)


def test_route_uses_default_limit_when_unset():
    items = []
    for index in range(channels.DEFAULT_LIMIT + 5):
        item = make(title="t{0}".format(index), tags=["models"])
        item["hotness"] = float(index)
        items.append(item)
    routed = channels.route(items, [{"name": "models", "match": {"tags_any": ["models"]}}])
    assert len(routed["models"]) == channels.DEFAULT_LIMIT


# =========================================================================
# 真实 config/channels.yaml
# =========================================================================
REQUIRED_CHANNELS = (
    "claude-code", "models", "model-dev", "plugins",
    "company-internal", "x-watch", "builders-live",
)


def test_load_channels_provides_all_required_channels():
    loaded = channels.load_channels()
    names = [c["name"] for c in loaded]
    for required in REQUIRED_CHANNELS:
        assert required in names
    for channel in loaded:
        assert channel["title"], "频道必须有中文显示名: {0}".format(channel["name"])
        assert channel["limit"] > 0
        assert isinstance(channel["match"], dict) and channel["match"]


def test_real_config_routes_sample_items_sensibly(sample_items):
    items = utils.dedup_and_score(sample_items)
    routed = channels.route(items, channels.load_channels())

    def titles(name):
        return [it["title"] for it in routed[name]]

    assert any("Opus 5" in t for t in titles("models"))
    assert any("心声" in t for t in titles("company-internal"))
    assert any("karpathy" in t for t in titles("x-watch"))
    assert any("karpathy" in t for t in titles("builders-live"))
    assert any("v2.1.0" == t for t in titles("releases"))
    assert titles("claude-code"), "claude-code 频道不应为空"
    assert any(len(v) for v in routed.values())


# =========================================================================
# run_all 写盘
# =========================================================================
def test_run_all_writes_markdown_and_index(tmp_data_dir, sample_items):
    items = utils.dedup_and_score(sample_items)
    routed = channels.run_all(items)

    index = storage.read_json(tmp_data_dir / "channels.json")
    assert set(index.keys()) == set(routed.keys())
    for name, sigs in index.items():
        assert sigs == [it["sig"] for it in routed[name]]

    for name in routed:
        md = (tmp_data_dir / "channels" / "{0}.md".format(name)).read_text(encoding="utf-8")
        assert md.startswith("# ")
        assert "更新时间" in md
        for item in routed[name]:
            assert item["url"] in md, "铁律 2：每条必须带可点 URL"


def test_channel_md_line_format_carries_badges_sources_and_link():
    item = make(title="Anthropic ships Claude Opus 5", url="https://a.example/1")
    item["badges"] = ["heavy", "flash"]
    item["source_list"] = ["Anthropic News", "Simon Willison"]
    item["hotness"] = 1.4082
    line = channels.format_line(item)
    assert line.startswith("- 📈⚡ **Anthropic ships Claude Opus 5**")
    assert "（Anthropic News + Simon Willison）" in line
    assert "1.4082" in line
    assert line.endswith("— [链接](https://a.example/1)")


def test_channel_md_prefers_chinese_title():
    item = make(title="English title", extra={"title_zh": "中文标题"})
    assert channels.display_title(item) == "中文标题"
    assert "**中文标题**" in channels.format_line(item)


def test_empty_channel_still_gets_a_page(tmp_data_dir):
    routed = channels.run_all([])
    assert all(v == [] for v in routed.values())
    for name in routed:
        md = (tmp_data_dir / "channels" / "{0}.md".format(name)).read_text(encoding="utf-8")
        assert "_暂无条目_" in md


def test_channels_json_is_valid_json_file(tmp_data_dir, sample_items):
    channels.run_all(utils.dedup_and_score(sample_items))
    raw = (tmp_data_dir / "channels.json").read_text(encoding="utf-8")
    assert isinstance(json.loads(raw), dict)


@pytest.mark.parametrize("bad", [None, 123, "字符串", []])
def test_match_item_rejects_non_dict_items(bad):
    assert channels.match_item(bad, {"tags_any": ["x"]}) is False
