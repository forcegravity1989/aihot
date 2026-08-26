"""test_channels_v3.py —— spec-v0.3 §15：match 新增 formats / categories 键（含 any_of）。

只测新匹配语义，既有 test_channels.py 的语义保持不变（那份仍应全绿）。
"""

from __future__ import annotations

from qianliyan.core import schema
from qianliyan.pipeline import channels


def make(**kwargs):
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
# formats：对 extra.format 单值命中任一
# =========================================================================
def test_formats_matches_extra_format():
    item = make(extra={"format": "video"})
    assert channels.match_item(item, {"formats": ["video", "talk"]}) is True
    assert channels.match_item(item, {"formats": ["podcast"]}) is False


def test_formats_is_case_insensitive():
    item = make(extra={"format": "Talk"})
    assert channels.match_item(item, {"formats": ["talk"]}) is True


def test_formats_absent_never_matches():
    assert channels.match_item(make(), {"formats": ["repo"]}) is False


def test_formats_scalar_value_accepted():
    item = make(extra={"format": "repo"})
    assert channels.match_item(item, {"formats": "repo"}) is True


# =========================================================================
# categories：对 extra.category / extra.source_category 命中任一
# =========================================================================
def test_categories_matches_extra_category():
    item = make(extra={"category": "talks"})
    assert channels.match_item(item, {"categories": ["talks", "podcasts"]}) is True
    assert channels.match_item(item, {"categories": ["papers"]}) is False


def test_categories_matches_source_category():
    item = make(extra={"source_category": "trending"})
    assert channels.match_item(item, {"categories": ["trending"]}) is True


def test_categories_is_case_insensitive():
    item = make(extra={"category": "Papers"})
    assert channels.match_item(item, {"categories": ["papers"]}) is True


def test_categories_absent_never_matches():
    assert channels.match_item(make(), {"categories": ["talks"]}) is False


# =========================================================================
# 块内 AND / any_of 的 OR（新键与既有键混用）
# =========================================================================
def test_formats_anded_with_sibling_fields():
    item = make(tags=["talks"], extra={"format": "video"})
    assert channels.match_item(item, {"formats": ["video"], "tags_any": ["talks"]}) is True
    assert channels.match_item(item, {"formats": ["video"], "tags_any": ["models"]}) is False


def test_any_of_over_formats_and_categories():
    match_cfg = {"any_of": [{"formats": ["talk", "video"]}, {"categories": ["talks"]}]}
    assert channels.match_item(make(extra={"format": "video"}), match_cfg) is True
    assert channels.match_item(make(extra={"source_category": "talks"}), match_cfg) is True
    assert channels.match_item(make(extra={"format": "podcast"}), match_cfg) is False


def test_talks_channel_shape_matches_video_or_talk():
    """spec-v0.3 §15 talks 频道：formats_any[video, talk]。"""
    talks = {"formats": ["video", "talk"]}
    assert channels.match_item(make(extra={"format": "talk"}), talks) is True
    assert channels.match_item(make(extra={"format": "video"}), talks) is True
    assert channels.match_item(make(extra={"format": "blog"}), talks) is False


def test_new_keys_do_not_warn_as_unknown(caplog):
    with caplog.at_level("WARNING"):
        channels.match_item(make(extra={"format": "repo"}), {"formats": ["repo"], "categories": ["trending"]})
    assert not any("未知匹配字段" in rec.getMessage() for rec in caplog.records)
