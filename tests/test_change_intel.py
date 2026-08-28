"""test_change_intel.py —— 变更情报加工层（spec-v0.3 §19.2）离线单测。

覆盖：
  * extract_subject          —— 主题键抽取（模型/版本/特性词、changelog 用 extra.subject）；
  * bind_changes             —— 同主题/版本变更绑成版本变更卡；
  * cross_map_claims         —— 叙事↔实证 verdict 三态（corroborated/unverified/contradicted，
                                regex 回退，数字方向/量级判定）；
  * industry_evolution       —— 从 insights item 提业界资产演进；
  * 健壮性                    —— 空/脏输入绝不抛。

全程离线：cross_map 传 llm=None 纯走 regex 回退；conftest 物理层封网兜底。
"""

from __future__ import annotations

import pytest

from qianliyan.core import schema
from qianliyan.eyes import insights
from qianliyan.pipeline import change_intel as ci


def mk(fmt, title, summary="", **extra):
    e = {"format": fmt}
    e.update(extra)
    return schema.make_item(
        title=title, url="https://example.com/" + title.replace(" ", "-"),
        source="S", source_kind="local", backend="git", weight=0.8,
        summary=summary, extra=e,
    )


def _changelog(subject, version, token_delta):
    return mk("changelog", "changelog {0}".format(version),
              subject=subject, version=version, token_delta=token_delta)


# =========================================================================
# extract_subject
# =========================================================================
def test_extract_subject_changelog_uses_extra_subject():
    assert ci.extract_subject(_changelog("claude-code-prompts", "3.0.0", -1)) == "claude-code-prompts"
    assert ci.extract_subject(mk("changelog", "x", subject="plugins-official")) == "plugins-official"


def test_extract_subject_narrative_model_family():
    assert ci.extract_subject(mk("talk", "Fable 5 changes everything")) == "fable-5"
    assert ci.extract_subject(mk("video", "A look at Opus-5")) == "opus-5"
    assert ci.extract_subject(mk("talk", "Haiku is fast")) == "haiku"


def test_extract_subject_claude_code_version_and_topic():
    assert ci.extract_subject(mk("blog", "Notes on Claude Code 2.1.242")) == "claude-code-2.1.242"
    assert ci.extract_subject(mk("blog", "Claude Code is great")) == "claude-code"
    assert ci.extract_subject(mk("news", "All about the system prompt design")) == "prompts"


def test_extract_subject_none_when_nothing_matches():
    assert ci.extract_subject(mk("news", "generic industry roundup")) is None
    assert ci.extract_subject(None) is None
    assert ci.extract_subject({}) is None


# =========================================================================
# bind_changes
# =========================================================================
def test_bind_changes_groups_by_subject_and_version():
    items = [
        _changelog("claude-code-prompts", "3.0.0", -50000),
        _changelog("claude-code-prompts", "2.9.0", 1200),
        mk("changelog", "plugin azure bump", subject="plugins-official", plugin="azure"),
        mk("changelog", "plugin datadog bump", subject="plugins-official", plugin="datadog"),
        mk("blog", "unrelated narrative"),   # 非 changelog 不参与
    ]
    cards = ci.bind_changes(items)
    keys = {(c["subject"], c["version"]) for c in cards}
    assert ("claude-code-prompts", "3.0.0") in keys
    assert ("claude-code-prompts", "2.9.0") in keys
    assert ("plugins-official", None) in keys

    plugin_card = next(c for c in cards if c["subject"] == "plugins-official")
    assert plugin_card["count"] == 2
    prompt_card = next(c for c in cards if c["version"] == "3.0.0")
    assert prompt_card["token_delta"] == -50000


def test_bind_changes_robust_on_bad_input():
    assert ci.bind_changes(None) == []
    assert ci.bind_changes([None, 123, "x"]) == []


# =========================================================================
# cross_map_claims —— verdict 三态（regex 回退）
# =========================================================================
def test_cross_map_corroborated_prompt_reduction(monkeypatch):
    monkeypatch.setenv("QLY_OFFLINE", "1")
    evidence = _changelog("claude-code-prompts", "3.0.0", -50000)
    talk = mk("talk", "Fable 5 cut Claude Code system prompts by 80%",
              "they reduced prompt tokens massively")
    items = [evidence, talk]
    ci.cross_map_claims(items, llm=None)

    corr = talk["extra"]["corroboration"]
    assert corr["verdict"] == "corroborated"
    assert corr["badge"] == "verified"
    assert corr["evidence"]["token_delta_total"] == -50000
    assert corr["evidence"]["kind"] == "changelog"
    assert corr["claim"]
    # 实证 item 自身不写 corroboration
    assert "corroboration" not in evidence["extra"]


def test_cross_map_contradicted_direction_mismatch(monkeypatch):
    monkeypatch.setenv("QLY_OFFLINE", "1")
    evidence = _changelog("claude-code-prompts", "3.0.0", -50000)   # 实测大幅缩减
    talk = mk("talk", "The Claude Code system prompt grew 3x", "the prompt increased a lot")
    items = [evidence, talk]
    ci.cross_map_claims(items, llm=None)
    assert talk["extra"]["corroboration"]["verdict"] == "contradicted"
    assert "badge" not in talk["extra"]["corroboration"]


def test_cross_map_unverified_no_evidence(monkeypatch):
    monkeypatch.setenv("QLY_OFFLINE", "1")
    # 有数字断言但话题非提示词、且无同主题实证 → unverified
    talk = mk("talk", "Inference got 50% faster", "raw speed improvement only")
    items = [talk, _changelog("claude-code-prompts", "3.0.0", -50000)]
    ci.cross_map_claims(items, llm=None)
    corr = talk["extra"]["corroboration"]
    assert corr["verdict"] == "unverified"
    assert corr["evidence"] is None


def test_cross_map_prompt_topic_but_no_changelog_is_unverified(monkeypatch):
    monkeypatch.setenv("QLY_OFFLINE", "1")
    talk = mk("talk", "They cut the system prompt by 80%", "prompt tokens down a lot")
    items = [talk]   # 没有任何 changelog 实证
    ci.cross_map_claims(items, llm=None)
    assert talk["extra"]["corroboration"]["verdict"] == "unverified"


def test_cross_map_bare_token_mention_unrelated_to_claude_code_is_unverified(monkeypatch):
    """回归用例：文章只是恰好提到"token"/"prompt"这类通用词，但完全不是在说 Claude
    Code——不能因为主题键都退化成 None 就侥幸对上（真实 bug：ChatGPT 搜索份额报道
    提到 token 被错配到 claude-code-prompts 的 changelog，判成"已实证"）。
    """
    monkeypatch.setenv("QLY_OFFLINE", "1")
    evidence = _changelog("claude-code-prompts", "3.0.0", -50000)
    talk = mk(
        "news",
        "ChatGPT search now uses the site:operator at scale",
        "the share of queries with a token-like operator dropped 68% this month",
    )
    items = [evidence, talk]
    ci.cross_map_claims(items, llm=None)
    corr = talk["extra"]["corroboration"]
    assert corr["verdict"] == "unverified"
    assert corr["evidence"] is None


def test_extract_claim_is_prompt_topic_requires_claude_code_mention():
    item = mk("news", "Some model got 3x more tokens", "a generic industry report")
    claim = ci._extract_claim_regex(item)
    assert claim is not None
    assert claim["is_prompt_topic"] is False


def test_cross_map_skips_narrative_without_numeric_claim(monkeypatch):
    monkeypatch.setenv("QLY_OFFLINE", "1")
    talk = mk("talk", "A qualitative talk about prompts", "no numbers here")
    items = [talk, _changelog("claude-code-prompts", "3.0.0", -50000)]
    ci.cross_map_claims(items, llm=None)
    assert "corroboration" not in talk["extra"]


def test_cross_map_corroborated_growth_against_real_changelog(fixtures_dir, monkeypatch):
    """用真实 CHANGELOG（净 token 大幅为正）验证「提示词在增长」的叙事被证实。"""
    monkeypatch.setenv("QLY_OFFLINE", "1")
    from qianliyan.eyes import cc_prompts
    md = (fixtures_dir / "real" / "ccprompts_changelog.md").read_text(encoding="utf-8")
    evidence = cc_prompts.parse_changelog(md)
    talk = mk("talk", "Claude Code system prompts grew 40% this year",
              "the prompt keeps getting larger")
    items = list(evidence) + [talk]
    ci.cross_map_claims(items, llm=None)
    corr = talk["extra"]["corroboration"]
    assert corr["verdict"] == "corroborated"
    assert corr["evidence"]["token_delta_total"] > 0


def test_cross_map_robust_on_empty_and_bad(monkeypatch):
    monkeypatch.setenv("QLY_OFFLINE", "1")
    ci.cross_map_claims([], llm=None)                 # 不抛
    ci.cross_map_claims([None, 5, "x"], llm=None)     # 不抛
    ci.cross_map_claims(None, llm=None)               # 不抛


# =========================================================================
# industry_evolution
# =========================================================================
def test_industry_evolution_from_real_insights(fixtures_dir):
    md = (fixtures_dir / "real" / "insights_daily.md").read_text(encoding="utf-8")
    items = insights.parse_daily(md)
    evo = ci.industry_evolution(items)

    assert len(evo) == 30
    # 按 rank 升序
    ranks = [e["rank"] for e in evo]
    assert ranks == sorted(ranks)
    top = evo[0]
    assert top["name"] == "superpowers"
    assert top["rising"] is True                       # rank<=10
    assert top["stars"] == 277158
    # 榜尾 rank>10 不算 rising
    assert evo[-1]["rising"] is False


def test_industry_evolution_ignores_non_insights():
    items = [mk("talk", "a talk"), mk("blog", "a blog")]
    assert ci.industry_evolution(items) == []


def test_industry_evolution_robust_on_bad_input():
    assert ci.industry_evolution(None) == []
    assert ci.industry_evolution([None, 1]) == []
