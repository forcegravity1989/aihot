from __future__ import annotations

import json
from pathlib import Path

from qianliyan.eyes import aihot, builders

REAL = Path(__file__).resolve().parent / "fixtures" / "real"


def _read(name: str) -> str:
    return (REAL / name).read_text(encoding="utf-8")


def _load(name: str):
    return json.loads(_read(name))


def test_aihot_parse_curated_canonical_url_is_original_href():
    items = aihot.parse_feed(_read("aihot_feed_curated.xml"), kind="curated", weight=0.85)
    assert len(items) == 35

    first = items[0]
    # canonical url 取「阅读原文」锚点 href（真实原文），而非 AIHOT 详情页
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
    assert first["source_kind"] == "aihot"
    assert first["backend"] == "rss"
    assert first["source"] == "AIHOT · 卡兹克"
    assert first["weight"] == 0.85
    assert "MetaRoCE" in first["summary"]
    assert "<p>" not in first["summary"] and "🔗" not in first["summary"]


def test_aihot_parse_category_mapping():
    items = aihot.parse_feed(_read("aihot_feed_curated.xml"), kind="curated")
    papers = [i for i in items if i["extra"]["category"] == "论文"]
    assert papers, "curated 应含论文条目"
    for it in papers:
        assert it["extra"]["category_slug"] == "paper"
        assert "paper" in it["tags"] and "research" in it["tags"]
    products = [i for i in items if i["extra"]["category"] == "AI 产品"]
    assert products
    assert all(i["extra"]["category_slug"] == "products" for i in products)
    assert all("products" in i["tags"] for i in products)


def test_aihot_parse_all_and_daily_feeds():
    all_items = aihot.parse_feed(_read("aihot_feed_all.xml"), kind="all", weight=0.70)
    assert len(all_items) == 50
    assert all(i["weight"] == 0.70 for i in all_items)
    assert all(i["extra"]["feed_kind"] == "all" for i in all_items)

    # daily 索引无「阅读原文」→ canonical 回退到 <link>，仍产出 item
    daily = aihot.parse_feed(_read("aihot_feed_daily.xml"), kind="daily")
    assert len(daily) == 30
    assert daily[0]["url"].startswith("https://aihot.virxact.com/daily/")
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
    urls = [i["url"] for i in items]
    assert len(urls) == len(set(urls))

    dup = items[:2] + [dict(items[0])] + [
        {"url": "https://other.example/x", "extra": {"aihot_id": items[1]["extra"]["aihot_id"]}}
    ]
    deduped = aihot._dedup(dup)
    assert len(deduped) == 2


def test_builders_parse_feed_x_counts_and_engagement():
    payload = _load("builders_feed_x.json")
    expected = sum(
        1 for b in payload["x"] for t in b.get("tweets", []) if t.get("url")
    )
    items = builders.parse_feed_x(payload)
    assert len(items) == expected
    assert expected >= 13

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
    assert "rauchg" in handles


def test_builders_parse_feed_x_allowlist_only():
    payload = _load("builders_feed_x.json")
    items = builders.parse_feed_x(payload, {"allowlist": ["rauchg"]})
    assert items
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


def _real_aihot_cfg():
    import yaml

    cfg_path = Path(__file__).resolve().parents[1] / "config" / "sources.yaml"
    return yaml.safe_load(cfg_path.read_text(encoding="utf-8"))["aihot"]


def test_aihot_october_feed_every_item_lands_in_a_category_with_the_shipped_config():
    """2026-10-10 抓的真实 feed：上游已把「技巧观点」拆成「教程」「观点」，不允许任何一条落成空类别。"""
    cfg = _real_aihot_cfg()
    items = aihot.parse_feed(_read("aihot_feed_all_2026-10.xml"), kind="all", weight=0.70, cfg=cfg)
    assert len(items) == 51

    categories = {i["extra"]["category"] for i in items}
    assert {"教程", "观点"} <= categories, "样本必须包含拆分后的两类，否则这条测试没在测新东西"
    # feed 里本来就没有 <category> 的条目（如 AIHOT 自己的换域名通知）允许为空；带分类的一条都不能落空
    empty = [i["title"] for i in items if i["extra"]["category"] and not i["extra"]["category_slug"]]
    assert empty == [], "带分类却落成空类别的条目：{0}".format(empty)
    assert sum(1 for i in items if i["extra"]["category"]) >= 45

    tutorials = [i for i in items if i["extra"]["category"] == "教程"]
    assert tutorials and all(i["extra"]["category_slug"] == "practices" for i in tutorials)
    assert all("tutorial" in i["tags"] and "practices" in i["tags"] for i in tutorials)
    opinions = [i for i in items if i["extra"]["category"] == "观点"]
    assert opinions and all(i["extra"]["category_slug"] == "opinion" for i in opinions)
    old = aihot.parse_feed(_read("aihot_feed_curated.xml"), kind="curated", cfg=cfg)
    assert all(i["extra"]["category_slug"] for i in old if i["extra"]["category"])


def test_aihot_reads_from_the_new_domain_everywhere():
    """旧域名 10 月 31 日后失效：配置、缺省值、健康检查三处都要指向 aihot.news。"""
    from qianliyan.cli import health_check

    cfg = _real_aihot_cfg()
    assert cfg["base_url"].rstrip("/") == "https://aihot.news"
    assert aihot.DEFAULT_BASE_URL == "https://aihot.news"
    targets = aihot._feed_targets(cfg)
    assert targets and all(t["path"].startswith("/") for t in targets)
    src = Path(health_check.__file__).read_text(encoding="utf-8")
    assert "aihot.virxact.com" not in src
    items = aihot.parse_feed(_read("aihot_feed_all_2026-10.xml"), kind="all", cfg=cfg)
    assert all(i["extra"]["aihot_url"].startswith("https://aihot.news/") for i in items)
