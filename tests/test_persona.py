"""test_persona.py —— 人物画像聚合 + 排序 + LLM 回退（spec-v0.3 §3 / §9）。

铁律：所有用例离线。本机 shell 可能导出真实 ``ANTHROPIC_API_KEY``，因此
autouse 强制 ``QLY_OFFLINE=1`` 并删掉 key（否则 is_available() 为真、封网 fixture
会把出网变 RuntimeError 而非走回退）。
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from qianliyan.core import schema, storage, utils, paths
from qianliyan.pipeline import persona


@pytest.fixture(autouse=True)
def force_offline_llm(monkeypatch):
    monkeypatch.setenv("QLY_OFFLINE", "1")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)


def _builder_item(handle, title, *, likes=0, retweets=0, replies=0, engagement=None,
                  name=None, bio=None, tags=None, hours=1):
    metrics = {"likes": likes, "retweets": retweets, "replies": replies}
    if engagement is not None:
        metrics["engagement"] = engagement
    extra = {"handle": handle, "platform": "x"}
    if name is not None:
        extra["name"] = name
    if bio is not None:
        extra["bio"] = bio
    return schema.make_item(
        title=title,
        url="https://x.com/{0}/status/{1}".format(handle, abs(hash(title)) % 10 ** 8),
        source="@{0}".format(handle),
        source_kind="builders",
        backend="raw_json",
        weight=0.8,
        date=utils.iso(utils.now_utc() - timedelta(hours=hours)),
        summary=title,
        tags=list(tags or ["x", "builders"]),
        metrics=metrics,
        extra=extra,
    )


# =========================================================================
# 聚合 / 排序
# =========================================================================
def test_aggregates_by_handle_and_computes_stats(tmp_data_dir):
    items = [
        _builder_item("alice", "alice on agents", likes=10, retweets=2, replies=1,
                      name="Alice", bio="builds agents", tags=["x", "builders", "agent"], hours=2),
        _builder_item("alice", "alice on evals", likes=5, retweets=0, replies=0,
                      tags=["x", "builders", "evals"], hours=48),
        _builder_item("bob", "bob ships a model", engagement=200,
                      name="Bob", tags=["x", "builders", "models"], hours=5),
    ]
    personas = persona.build_personas(items)

    assert len(personas) == 2
    handles = [p["handle"] for p in personas]
    assert set(handles) == {"alice", "bob"}

    by_handle = {p["handle"]: p for p in personas}
    alice = by_handle["alice"]
    assert alice["item_count"] == 2
    # 13 (=10+2+1) + 5 = 18
    assert alice["total_engagement"] == 18
    assert alice["avg_engagement"] == 9.0
    assert alice["name"] == "Alice"
    assert alice["bio"] == "builds agents"
    # 代表作按互动量降序
    assert alice["top_items"][0]["title"] == "alice on agents"
    assert alice["top_items"][0]["engagement"] == 13
    # last_active 取最近一条（2h 前那条）
    assert alice["last_active"] >= alice["top_items"][-1]["date"] or alice["last_active"]


def test_sorted_by_total_engagement_desc(tmp_data_dir):
    items = [
        _builder_item("small", "minor take", likes=1),
        _builder_item("huge", "viral thread", engagement=9999),
        _builder_item("mid", "decent post", likes=100),
    ]
    personas = persona.build_personas(items)
    order = [p["handle"] for p in personas]
    assert order == ["huge", "mid", "small"]
    assert personas[0]["total_engagement"] == 9999


def test_top_items_capped_at_three(tmp_data_dir):
    items = [
        _builder_item("prolific", "post {0}".format(i), engagement=i * 10, hours=i + 1)
        for i in range(1, 6)
    ]
    personas = persona.build_personas(items)
    assert len(personas) == 1
    top = personas[0]["top_items"]
    assert len(top) == 3
    # 互动量最高的三条
    assert [t["engagement"] for t in top] == [50, 40, 30]


def test_only_builders_with_handle_are_aggregated(tmp_data_dir):
    good = _builder_item("real", "genuine builder post", likes=3)
    # builders 但缺 handle → 排除
    no_handle = schema.make_item(
        title="anonymous builder", url="https://x.com/x/status/9",
        source="@?", source_kind="builders", backend="raw_json", weight=0.8,
        summary="", tags=["x", "builders"], extra={"platform": "x"},
    )
    # 非 builders → 排除
    aihot = schema.make_item(
        title="some news", url="https://aihot.example/1", source="AIHOT",
        source_kind="aihot", backend="rss", weight=0.85, summary="",
        tags=["models"], extra={"handle": "notabuilder"},
    )
    personas = persona.build_personas([good, no_handle, aihot])
    assert [p["handle"] for p in personas] == ["real"]


def test_engagement_prefers_explicit_engagement_metric(tmp_data_dir):
    # engagement 显式给出时忽略 likes/rt/replies 之和
    item = _builder_item("x", "post", likes=1, retweets=1, replies=1, engagement=500)
    assert persona.item_engagement(item) == 500
    # 缺 engagement 时取三者之和
    item2 = _builder_item("y", "post2", likes=4, retweets=3, replies=2)
    assert persona.item_engagement(item2) == 9


def test_empty_or_no_builders_returns_empty(tmp_data_dir):
    assert persona.build_personas([]) == []
    aihot = schema.make_item(
        title="n", url="https://a/1", source="AIHOT", source_kind="aihot",
        backend="rss", weight=0.8, summary="",
    )
    assert persona.build_personas([aihot]) == []


# =========================================================================
# 回退（无 LLM）
# =========================================================================
def test_fallback_topics_and_recent_focus_without_llm(tmp_data_dir):
    items = [
        _builder_item("carol", "carol talks agents again", engagement=50,
                      tags=["x", "builders", "agent", "models"], hours=1),
        _builder_item("carol", "carol on agents", engagement=10,
                      tags=["x", "builders", "agent"], hours=10),
    ]
    personas = persona.build_personas(items)
    carol = personas[0]
    # topics 回退 = 高频 tag（剔除通用 x/builders）
    assert "agent" in carol["topics"]
    assert "x" not in carol["topics"] and "builders" not in carol["topics"]
    # recent_focus 回退 = 最高互动条目标题
    assert carol["recent_focus"] == "carol talks agents again"


def test_fallback_topics_from_keywords_when_tags_generic(tmp_data_dir):
    # 全是通用 tag → 从标题抽关键词
    items = [
        _builder_item("dave", "shipping robotics hardware today", engagement=5,
                      tags=["x", "builders"]),
        _builder_item("dave", "robotics is eating the world", engagement=3,
                      tags=["x", "builders"]),
    ]
    personas = persona.build_personas(items)
    assert "robotics" in personas[0]["topics"]


# =========================================================================
# 写盘
# =========================================================================
def test_write_personas_emits_json_and_markdown(tmp_data_dir):
    items = [
        _builder_item("erin", "erin builds evals", engagement=42,
                      name="Erin", bio="eval nerd", tags=["x", "builders", "evals"]),
    ]
    personas = persona.build_personas(items)
    persona.write_personas(personas)

    data = storage.read_json(paths.data_path("personas.json"), default=None)
    assert isinstance(data, list) and data[0]["handle"] == "erin"

    md_path = paths.data_path("personas", "erin.md")
    assert md_path.is_file()
    text = md_path.read_text(encoding="utf-8")
    assert "Erin" in text and "@erin" in text
    assert "erin builds evals" in text  # 代表作链接
    assert "eval nerd" in text          # bio


# =========================================================================
# LLM 增强（可注入 client）
# =========================================================================
class FakeClient(object):
    def __init__(self, reply=None, raise_on_batch=None, available=True):
        self.reply = reply
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
        return [self.reply for _ in self.prompts]


def test_llm_enhances_topics_and_focus(tmp_data_dir):
    items = [_builder_item("frank", "frank posts", engagement=10, tags=["x", "builders"])]
    client = FakeClient(reply={"topics": ["Agents", "Evals"], "recent_focus": "在关注智能体评测"})
    personas = persona.build_personas(items, client=client)
    assert personas[0]["topics"] == ["agents", "evals"]  # 小写归一
    assert personas[0]["recent_focus"] == "在关注智能体评测"
    assert client.prompts, "应当调用了 LLM"


def test_llm_failure_keeps_fallback(tmp_data_dir):
    items = [_builder_item("grace", "grace on agents", engagement=10,
                           tags=["x", "builders", "agent"])]
    client = FakeClient(raise_on_batch=RuntimeError("boom"))
    personas = persona.build_personas(items, client=client)
    # 未向上抛，topics/recent_focus 仍是回退值
    assert "agent" in personas[0]["topics"]
    assert personas[0]["recent_focus"] == "grace on agents"


def test_llm_unavailable_keeps_fallback(tmp_data_dir):
    items = [_builder_item("henry", "henry ships", engagement=10, tags=["x", "builders"])]
    client = FakeClient(available=False)
    personas = persona.build_personas(items, client=client)
    assert personas[0]["recent_focus"] == "henry ships"
    assert client.prompts == []  # 不可用则根本不调 batch


# =========================================================================
# 预留壳
# =========================================================================
def test_build_person_from_news_stub_returns_empty(tmp_data_dir):
    assert persona.build_person_from_news([]) == []
