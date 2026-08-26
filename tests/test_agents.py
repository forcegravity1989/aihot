"""test_agents.py —— 锁死 spec §5 回退公约 + §9.5 四个 Agent 的落值行为。

铁律：LLM 不可用、抛 ``LLMUnavailable``、抛任意异常、返回垃圾结构 —— 四个 agent
一律走规则回退，**绝不向上抛**，且字段必须落值。

注意：本机 shell 可能导出了真实的 ``ANTHROPIC_API_KEY``，因此每个用例都强制
``QLY_OFFLINE=1`` 并删掉 key（否则 is_available() 为真，conftest 的封网会把
出网变成 RuntimeError 而不是走回退）。
"""

from __future__ import annotations

import json

import pytest

from qianliyan.core import llm_client, schema, storage, utils
from qianliyan.pipeline import (
    auto_translate,
    headline_cluster_agent,
    headline_fit_agent,
    model_cluster_agent,
    routing,
)

AGENTS = (
    ("model_cluster", model_cluster_agent.annotate, "cluster_key"),
    ("headline_fit", headline_fit_agent.annotate, "headline_fit"),
    ("headline_cluster", headline_cluster_agent.annotate, "story_key"),
)


@pytest.fixture(autouse=True)
def force_offline_llm(monkeypatch):
    """默认关掉 LLM：离线开关 + 抹掉真实 key。"""
    monkeypatch.setenv("QLY_OFFLINE", "1")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)


@pytest.fixture
def items(sample_items):
    return utils.dedup_and_score(sample_items)


def _payload_of(prompt):
    """从 agent 生成的 prompt 里取回输入 JSON（复刻「输入：\\n<json>」约定）。"""
    marker = "输入：\n"
    return json.loads(prompt[prompt.index(marker) + len(marker):])


class FakeClient(object):
    """可用但受控的假客户端 —— 不出网，行为由构造参数决定。"""

    def __init__(self, reply_for=None, raise_on_batch=None, available=True):
        self.reply_for = reply_for
        self.raise_on_batch = raise_on_batch
        self.available = available
        self.prompts = []

    def is_available(self):
        if isinstance(self.available, Exception):
            raise self.available
        return self.available

    def batch_json(self, prompts, system=None, max_workers=4):
        self.prompts = list(prompts)
        if self.raise_on_batch is not None:
            raise self.raise_on_batch
        return [self.reply_for(p) for p in self.prompts]


def install(monkeypatch, client):
    monkeypatch.setattr(
        llm_client.LLMClient, "from_env", classmethod(lambda cls: client)
    )


# =========================================================================
# 通用回退公约（spec §5）
# =========================================================================
@pytest.mark.parametrize("name,annotate,field", AGENTS)
def test_agents_annotate_without_llm(tmp_data_dir, items, name, annotate, field):
    annotate(items)
    for item in items:
        assert field in item["extra"], "{0} 必须落值 extra.{1}".format(name, field)


@pytest.mark.parametrize("name,annotate,field", AGENTS)
def test_agents_survive_from_env_exploding(tmp_data_dir, items, name, annotate, field, monkeypatch):
    def boom(cls):
        raise RuntimeError("网关配置炸了")

    monkeypatch.setattr(llm_client.LLMClient, "from_env", classmethod(boom))
    annotate(items)  # 不许抛
    for item in items:
        assert field in item["extra"]


@pytest.mark.parametrize("name,annotate,field", AGENTS)
def test_agents_survive_is_available_exploding(tmp_data_dir, items, name, annotate, field, monkeypatch):
    install(monkeypatch, FakeClient(available=RuntimeError("探活失败")))
    annotate(items)
    for item in items:
        assert field in item["extra"]


@pytest.mark.parametrize("name,annotate,field", AGENTS)
def test_agents_survive_llm_unavailable_exception(tmp_data_dir, items, name, annotate, field, monkeypatch):
    install(monkeypatch, FakeClient(raise_on_batch=llm_client.LLMUnavailable("gateway down")))
    annotate(items)
    for item in items:
        assert field in item["extra"]


@pytest.mark.parametrize("name,annotate,field", AGENTS)
def test_agents_survive_garbage_llm_replies(tmp_data_dir, items, name, annotate, field, monkeypatch):
    garbage = [None, "不是 JSON 对象", {"unexpected": True}, [1, 2, 3], [{"i": 99}], {}]
    for reply in garbage:
        install(monkeypatch, FakeClient(reply_for=lambda _p, r=reply: r))
        annotate(items)
        for item in items:
            assert field in item["extra"]


@pytest.mark.parametrize("name,annotate,field", AGENTS)
def test_agents_tolerate_empty_and_dirty_input(tmp_data_dir, name, annotate, field):
    annotate([])
    annotate(None)
    annotate([None, "字符串", 42])  # 非 dict 一律跳过，不抛


# =========================================================================
# auto_translate（spec §9.5）
# =========================================================================
def test_translate_skips_items_that_are_already_chinese(tmp_data_dir, items):
    auto_translate.translate(items)
    chinese = [it for it in items if "心声" in it["title"]]
    assert chinese
    for item in chinese:
        assert "title_zh" not in item["extra"], "中文条目不该被翻译"


def test_translate_leaves_english_untouched_when_llm_unavailable(tmp_data_dir, items):
    auto_translate.translate(items)
    english = [it for it in items if "Opus 5" in it["title"]]
    assert english
    for item in english:
        assert "title_zh" not in item["extra"], "LLM 不可用时不译，渲染层落回英文"


@pytest.mark.parametrize("ratio_text,expected", [
    ("心声社区：公司大模型平台升级公告", True),
    ("Anthropic ships Claude Opus 5", False),
    ("Claude Opus 5 正式发布：中文说明", True),   # CJK 占比 0.45 > 0.3
    ("Claude Opus 5 发布", False),                # CJK 占比 0.14 ≤ 0.3，仍需翻译
    ("", False),
])
def test_cjk_ratio_threshold(ratio_text, expected):
    assert auto_translate.is_chinese(ratio_text) is expected


def test_cjk_ratio_is_computed_over_non_space_chars():
    assert auto_translate.cjk_ratio("中文") == 1.0
    assert auto_translate.cjk_ratio("abcd") == 0.0
    assert auto_translate.cjk_ratio("ab 中文") == pytest.approx(0.5)


def test_translate_uses_cache_and_skips_llm(tmp_data_dir, items, monkeypatch):
    target = [it for it in items if "Opus 5" in it["title"]][0]
    cache_path = tmp_data_dir / "translations.json"
    storage.write_json(cache_path, {
        target["sig"]: {"title_zh": "缓存中文标题", "summary_zh": "缓存中文摘要"}
    })

    client = FakeClient(reply_for=lambda _p: None)
    install(monkeypatch, client)
    auto_translate.translate(items, cache_path)

    assert target["extra"]["title_zh"] == "缓存中文标题"
    assert target["extra"]["summary_zh"] == "缓存中文摘要"
    # 命中缓存的那条不应再出现在送去翻译的 prompt 里
    for prompt in client.prompts:
        for row in _payload_of(prompt):
            assert row["title"] != target["title"]


def test_translate_writes_results_and_cache_when_llm_works(tmp_data_dir, items, monkeypatch):
    def reply(prompt):
        return [
            {"i": row["i"], "title_zh": "译:" + row["title"][:8], "summary_zh": "摘:"}
            for row in _payload_of(prompt)
        ]

    install(monkeypatch, FakeClient(reply_for=reply))
    auto_translate.translate(items)

    english = [it for it in items if not auto_translate.is_chinese(it["title"])]
    assert english
    for item in english:
        assert item["extra"]["title_zh"].startswith("译:")

    cache = storage.read_json(tmp_data_dir / "translations.json", default={})
    assert cache
    for item in english:
        assert item["sig"] in cache


def test_translate_never_raises_on_batch_failure(tmp_data_dir, items, monkeypatch):
    install(monkeypatch, FakeClient(raise_on_batch=llm_client.LLMUnavailable("boom")))
    auto_translate.translate(items)  # 不许抛
    assert all("title_zh" not in it["extra"] for it in items)


def test_translate_survives_unwritable_cache(tmp_data_dir, items, monkeypatch):
    install(monkeypatch, FakeClient(
        reply_for=lambda p: [{"i": r["i"], "title_zh": "译文"} for r in _payload_of(p)]
    ))

    def boom(*args, **kwargs):
        raise OSError("磁盘满了")

    monkeypatch.setattr(storage, "write_json", boom)
    auto_translate.translate(items)  # 不许抛
    assert any(it["extra"].get("title_zh") == "译文" for it in items)


# =========================================================================
# model_cluster_agent（spec §9.5）
# =========================================================================
@pytest.mark.parametrize("title,expected", [
    ("Anthropic ships Claude Opus 5", "claude"),
    ("OpenAI releases GPT-6", "gpt"),
    ("Gemini 3 Ultra benchmarks", "gemini"),
    ("Meta open-sources Llama 4", "llama"),
    ("通义千问 Qwen3 发布", "qwen"),
    ("DeepSeek-V4 技术报告", "deepseek"),
    ("Mistral Large 3", "mistral"),
    ("xAI ships Grok 5", "grok"),
    ("Kimi K2 上线", "kimi"),
    ("智谱 GLM-5 发布", "glm"),
    ("A plugin ranking without any model name", ""),
    ("", ""),
])
def test_fallback_cluster_key_matches_known_families(title, expected):
    assert model_cluster_agent.fallback_cluster_key({"title": title}) == expected


def test_fallback_takes_first_family_in_title():
    item = {"title": "Claude Opus 5 outperforms GPT-6 on SWE-bench"}
    assert model_cluster_agent.fallback_cluster_key(item) == "claude"
    item = {"title": "GPT-6 outperforms Claude Opus 5 on SWE-bench"}
    assert model_cluster_agent.fallback_cluster_key(item) == "gpt"


def test_cluster_agent_falls_back_per_item_without_llm(tmp_data_dir, items):
    model_cluster_agent.annotate(items)
    opus = [it for it in items if "Opus 5" in it["title"]][0]
    assert opus["extra"]["cluster_key"] == "claude"
    plugins = [it for it in items if "plugin" in it["title"].lower()][0]
    assert plugins["extra"]["cluster_key"] == ""


def test_cluster_agent_prefers_llm_answer_when_available(tmp_data_dir, items, monkeypatch):
    install(monkeypatch, FakeClient(
        reply_for=lambda p: [{"i": r["i"], "cluster_key": "LLM-Family"} for r in _payload_of(p)]
    ))
    model_cluster_agent.annotate(items)
    assert all(it["extra"]["cluster_key"] == "llm-family" for it in items)


def test_cluster_agent_keeps_regex_value_when_llm_returns_blank(tmp_data_dir, items, monkeypatch):
    install(monkeypatch, FakeClient(
        reply_for=lambda p: [{"i": r["i"], "cluster_key": ""} for r in _payload_of(p)]
    ))
    model_cluster_agent.annotate(items)
    opus = [it for it in items if "Opus 5" in it["title"]][0]
    assert opus["extra"]["cluster_key"] == "claude"


# =========================================================================
# headline_fit_agent（spec §9.5）
# =========================================================================
def test_headline_fit_fallback_is_hotness_normalised(tmp_data_dir, items):
    headline_fit_agent.annotate(items)
    max_hotness = max(it["hotness"] for it in items)
    for item in items:
        expected = round(min(1.0, item["hotness"] / max_hotness), 4)
        assert item["extra"]["headline_fit"] == pytest.approx(expected, abs=1e-4)
    assert max(it["extra"]["headline_fit"] for it in items) == pytest.approx(1.0)


def test_headline_fit_is_zero_when_pool_has_no_hotness(tmp_data_dir):
    rows = [
        schema.make_item(title="A", url="https://a/1", source="S", source_kind="local",
                         backend="rss", weight=0.5),
        schema.make_item(title="B", url="https://a/2", source="S", source_kind="local",
                         backend="rss", weight=0.5),
    ]
    headline_fit_agent.annotate(rows)
    assert all(it["extra"]["headline_fit"] == 0.0 for it in rows)


def test_headline_fit_clamps_llm_scores_into_unit_range(tmp_data_dir, items, monkeypatch):
    scores = [-5, 0.42, 7, "不是数字", None]
    install(monkeypatch, FakeClient(
        reply_for=lambda p: [
            {"i": r["i"], "headline_fit": scores[r["i"] % len(scores)]}
            for r in _payload_of(p)
        ]
    ))
    headline_fit_agent.annotate(items)
    for item in items:
        assert 0.0 <= item["extra"]["headline_fit"] <= 1.0


# =========================================================================
# headline_cluster_agent（spec §9.5）
# =========================================================================
def test_story_key_falls_back_to_sig(tmp_data_dir, items):
    headline_cluster_agent.annotate(items)
    for item in items:
        assert item["extra"]["story_key"] == item["sig"]
    keys = [it["extra"]["story_key"] for it in items]
    assert len(set(keys)) == len(keys), "回退时各自成题"


def test_story_key_groups_items_when_llm_available(tmp_data_dir, items, monkeypatch):
    install(monkeypatch, FakeClient(
        reply_for=lambda p: [{"i": r["i"], "story_key": "Opus-5-Launch"} for r in _payload_of(p)]
    ))
    headline_cluster_agent.annotate(items)
    assert set(it["extra"]["story_key"] for it in items) == {"opus-5-launch"}


def test_story_key_keeps_sig_when_llm_returns_blank(tmp_data_dir, items, monkeypatch):
    install(monkeypatch, FakeClient(
        reply_for=lambda p: [{"i": r["i"], "story_key": ""} for r in _payload_of(p)]
    ))
    headline_cluster_agent.annotate(items)
    for item in items:
        assert item["extra"]["story_key"] == item["sig"]


# =========================================================================
# routing —— 与三个 agent 同属 sync 的标注环节（spec §9.4，纯规则、不调 LLM）
# =========================================================================
def test_load_tiers_provides_five_levels_and_mapping():
    tiers, mapping = routing.load_tiers()
    assert set(tiers) >= {"official", "vendor", "dev", "kol", "community"}
    assert tiers["official"] > tiers["vendor"] > tiers["dev"] > tiers["kol"] > tiers["community"]
    assert mapping, "mapping 不应为空"


@pytest.mark.parametrize("source,expected", [
    ("Anthropic News", "official"),
    ("OpenAI News", "official"),
    ("HuggingFace Blog", "vendor"),
    ("Simon Willison", "kol"),
    ("Latent Space", "kol"),
    ("arXiv cs.CL", "community"),
    ("某个没配过的小站", "community"),
    ("", "community"),
])
def test_classify_source_does_substring_mapping(source, expected):
    assert routing.classify_source(source) == expected


def test_classify_source_prefers_the_most_specific_key():
    mapping = {"claude code": "official", "code": "community"}
    assert routing.classify_source("Claude Code Releases", mapping) == "official"


def test_routing_annotate_writes_tier_only_for_model_related_items(tmp_data_dir, items):
    routing.annotate(items)
    opus = [it for it in items if "Opus 5" in it["title"]][0]
    assert opus["extra"]["tier"] == "official"
    unrelated = [it for it in items if "plugin" in it["title"].lower()][0]
    assert "tier" not in unrelated["extra"]


def test_tier_weight_falls_back_to_community():
    tiers, _ = routing.load_tiers()
    assert routing.tier_weight("official", tiers) == tiers["official"]
    assert routing.tier_weight("不存在的档位", tiers) == tiers["community"]


# =========================================================================
# 真实 LLMClient 在离线环境下必须自认不可用（回退的前提）
# =========================================================================
def test_llm_client_reports_unavailable_when_offline(tmp_data_dir):
    assert llm_client.LLMClient.from_env().is_available() is False


def test_llm_client_reports_unavailable_without_key(monkeypatch):
    monkeypatch.delenv("QLY_OFFLINE", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert llm_client.LLMClient.from_env().is_available() is False
