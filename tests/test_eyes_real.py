"""test_eyes_real.py —— 补 Wave A 未竟：eyes/aihot 与 eyes/builders 对真实 fixtures 的解析。

全离线，直喂 ``tests/fixtures/real/`` 的真实样本（spec-v0.3 §0/§17）：
  - aihot.parse_feed 对 aihot_feed_{curated,all,daily}.xml：canonical url = 「阅读原文」原文
    href、中文 category → slug/tags 映射、内嵌 <img> 收集、aihot 内部按 url/guid 去重。
  - builders.parse_feed_x 对 builders_feed_x.json：每 tweet 一 item、engagement 计算、
    mute/allowlist 过滤、空 podcasts/blogs 容忍。
"""

from __future__ import annotations

import json
from pathlib import Path

from qianliyan.eyes import aihot, builders

REAL = Path(__file__).resolve().parent / "fixtures" / "real"


def _read(name: str) -> str:
    return (REAL / name).read_text(encoding="utf-8")


def _load(name: str):
    return json.loads(_read(name))


# =========================================================================
# eyes.aihot.parse_feed —— 三个真实 aihot feed
# =========================================================================
def test_aihot_parse_curated_canonical_url_is_original_href():
    items = aihot.parse_feed(_read("aihot_feed_curated.xml"), kind="curated", weight=0.85)
    assert len(items) == 35

    first = items[0]
    # canonical url = 「阅读原文」锚点 href（真实原文），而非 AIHOT 详情页
    assert first["url"] == (
        "https://engineering.fb.com/2026/08/24/networking-traffic/"
        "metaroce-rdma-transport-ai-ethernet"
    )
    assert first["extra"]["aihot_url"] == (
        "https://aihot.virxact.com/items/cmt7nq1d02bs1ro7373u88po4"
    )
    assert first["url"] != first["extra"]["aihot_url"]
    assert first["extra"]["aihot_id"] == "cmt7nq1d02bs1ro7373u88po4"
    assert first["extra"]["feed_kind"] == "curated"
    # 标准 item 字段
    assert first["source_kind"] == "aihot"
    assert first["backend"] == "rss"
    assert first["source"] == "AIHOT · 卡兹克"
    assert first["weight"] == 0.85
    # summary = 首个 <p> 纯文本
    assert "MetaRoCE" in first["summary"]
    assert "<p>" not in first["summary"] and "🔗" not in first["summary"]


def test_aihot_parse_category_mapping():
    items = aihot.parse_feed(_read("aihot_feed_curated.xml"), kind="curated")
    # 论文类：slug=paper、tags 含 paper/research
    papers = [i for i in items if i["extra"]["category"] == "论文"]
    assert papers, "curated 应含论文条目"
    for it in papers:
        assert it["extra"]["category_slug"] == "paper"
        assert "paper" in it["tags"] and "research" in it["tags"]
    # AI 产品 → products
    products = [i for i in items if i["extra"]["category"] == "AI 产品"]
    assert products
    assert all(i["extra"]["category_slug"] == "products" for i in products)
    assert all("products" in i["tags"] for i in products)


def test_aihot_parse_all_and_daily_feeds():
    all_items = aihot.parse_feed(_read("aihot_feed_all.xml"), kind="all", weight=0.70)
    assert len(all_items) == 50
    assert all(i["weight"] == 0.70 for i in all_items)
    assert all(i["extra"]["feed_kind"] == "all" for i in all_items)

    # daily 索引：无「阅读原文」→ canonical 回退到 <link>（AIHOT daily 页），仍产出 item
    daily = aihot.parse_feed(_read("aihot_feed_daily.xml"), kind="daily")
    assert len(daily) == 30
    assert daily[0]["url"].startswith("https://aihot.virxact.com/daily/")
    # daily 无 category → slug 为空
    assert daily[0]["extra"]["category_slug"] == ""


def test_aihot_parse_collects_inline_images():
    # 真实 fixtures 无内嵌图，构造最小 RSS 覆盖 <img> 收集分支
    xml = """<?xml version="1.0"?><rss version="2.0"><channel><item>
      <title><![CDATA[Item with image]]></title>
      <link>https://aihot.virxact.com/items/xyz</link>
      <description><![CDATA[<p>Body text.</p>
      <img src="https://cdn.example.com/a.png"/><img src="https://cdn.example.com/b.png"/>
      <p>🔗 <a href="https://origin.example.com/post">阅读原文</a></p>]]></description>
      <category>AI 模型</category>
      <guid>xyz</guid>
      <pubDate>Mon, 24 Aug 2026 18:02:29 GMT</pubDate>
    </item></channel></rss>"""
    items = aihot.parse_feed(xml, kind="curated")
    assert len(items) == 1
    it = items[0]
    assert it["url"] == "https://origin.example.com/post"
    assert it["extra"]["images"] == [
        "https://cdn.example.com/a.png",
        "https://cdn.example.com/b.png",
    ]
    assert it["extra"]["category_slug"] == "models"


def test_aihot_dedup_collapses_duplicate_url_and_guid():
    items = aihot.parse_feed(_read("aihot_feed_curated.xml"), kind="curated")
    # 真实解析结果本身 canonical url 唯一
    urls = [i["url"] for i in items]
    assert len(urls) == len(set(urls))

    # 直接验证内部去重：重复 url 与重复 guid 均被折叠，先到者胜
    dup = items[:2] + [dict(items[0])] + [
        {"url": "https://other.example/x", "extra": {"aihot_id": items[1]["extra"]["aihot_id"]}}
    ]
    deduped = aihot._dedup(dup)
    assert len(deduped) == 2


# =========================================================================
# eyes.builders.parse_feed_x —— 真实 follow-builders feed-x.json
# =========================================================================
def test_builders_parse_feed_x_counts_and_engagement():
    payload = _load("builders_feed_x.json")
    expected = sum(
        1 for b in payload["x"] for t in b.get("tweets", []) if t.get("url")
    )
    items = builders.parse_feed_x(payload)
    assert len(items) == expected
    assert expected >= 13  # 13 位 builder，多数有 tweet

    # 首条：thsottiaux 的第一条 tweet，engagement = likes+retweets+replies
    first = items[0]
    assert first["source"] == "@thsottiaux"
    assert first["source_kind"] == "builders"
    assert first["backend"] == "raw_json"
    assert first["extra"]["handle"] == "thsottiaux"
    assert first["extra"]["platform"] == "x"
    m = first["metrics"]
    assert m["engagement"] == m["likes"] + m["retweets"] + m["replies"]
    assert m["likes"] == 1869 and m["retweets"] == 77 and m["replies"] == 278
    assert m["engagement"] == 2224
    assert "x" in first["tags"] and "builders" in first["tags"]


def test_builders_parse_feed_x_mute_filter():
    payload = _load("builders_feed_x.json")
    cfg = {"x_follows": [{"handle": "amasad", "mute": True}]}
    items = builders.parse_feed_x(payload, cfg)
    handles = {i["extra"]["handle"].casefold() for i in items}
    assert "amasad" not in handles
    # 其余 builder 仍在
    assert "rauchg" in handles


def test_builders_parse_feed_x_allowlist_only():
    payload = _load("builders_feed_x.json")
    items = builders.parse_feed_x(payload, {"allowlist": ["rauchg"]})
    assert items  # rauchg 至少有一条
    assert all(i["extra"]["handle"].casefold() == "rauchg" for i in items)


def test_builders_parse_feed_x_weight_override():
    payload = _load("builders_feed_x.json")
    items = builders.parse_feed_x(payload, {"default_weight": 0.8, "weight_overrides": {"rauchg": 0.5}})
    rauchg = [i for i in items if i["extra"]["handle"].casefold() == "rauchg"]
    assert rauchg and all(i["weight"] == 0.5 for i in rauchg)
    others = [i for i in items if i["extra"]["handle"].casefold() != "rauchg"]
    assert all(i["weight"] == 0.8 for i in others)


def test_builders_empty_podcasts_and_blogs_tolerated():
    assert builders.parse_feed_generic(_load("builders_feed_podcasts.json"), "podcasts") == []
    assert builders.parse_feed_generic(_load("builders_feed_blogs.json"), "blogs") == []
