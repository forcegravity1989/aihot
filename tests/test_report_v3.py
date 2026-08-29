"""test_report_v3.py —— spec-v0.3 §5：render_html 新增「为你推荐」+「人物画像」版面。

* personas 传入 → 渲染人物画像卡片网格（头像/名字/handle/bio/近期关注/topics/影响力数字）；
* 池内带 ``extra.personal_score`` → 渲染「为你推荐」版面并按 personal_score 降序、原因 chip；
* personas 省略 / 无 personal_score → 两个版面都不渲染（向后兼容，既有 test_report.py 仍绿）。
"""

from __future__ import annotations

import base64
import re

import pytest

from qianliyan.core import profile, schema, utils
from qianliyan.pipeline import report

TINY_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


def _scored(title, url, hotness, score, reasons, **kw):
    item = schema.make_item(
        title=title, url=url, source=kw.get("source", "Src"),
        source_kind=kw.get("source_kind", "local"), backend="rss",
        weight=kw.get("weight", 0.7), summary=kw.get("summary", "摘要"),
        tags=kw.get("tags", ["models"]),
    )
    item["hotness"] = hotness
    item["extra"]["personal_score"] = score
    item["extra"]["personal_reasons"] = reasons
    return item


def _persona(handle, name, total, **kw):
    return {
        "handle": handle,
        "name": name,
        "bio": kw.get("bio", "AI builder"),
        "avatar_path": kw.get("avatar_path"),
        "item_count": kw.get("item_count", 3),
        "total_engagement": total,
        "avg_engagement": kw.get("avg_engagement", 100.0),
        "last_active": utils.iso(utils.now_utc()),
        "top_items": kw.get("top_items", [{"title": "代表作", "url": "https://x/t", "engagement": 90, "date": ""}]),
        "topics": kw.get("topics", ["agent", "evals"]),
        "recent_focus": kw.get("recent_focus", "近期在关注 agent harness"),
    }


# =========================================================================
# 人物画像版面
# =========================================================================
def test_personas_board_renders_cards(tmp_data_dir):
    personas = [_persona("karpathy", "Andrej Karpathy", 1500), _persona("amasad", "Amjad Masad", 800)]
    html = report.render_html([], {}, personas=personas, out_path=False)

    assert 'id="board-personas"' in html
    assert "人物画像" in html
    assert "Andrej Karpathy" in html and "@karpathy" in html
    assert "Amjad Masad" in html and "@amasad" in html
    assert "近期在关注" in html
    assert "agent" in html and "evals" in html  # topics chips
    assert "1500" in html  # 影响力数字 total_engagement
    assert "代表作" in html  # top_items 链接文案
    assert "{{" not in html and "{%" not in html


def test_personas_omitted_means_no_personas_board(tmp_data_dir):
    """向后兼容：不传 personas 时不渲染人物版面。"""
    item = schema.make_item(title="x", url="https://a/1", source="S", source_kind="local", backend="rss", weight=0.5)
    html = report.render_html([item], {}, out_path=False)
    assert 'id="board-personas"' not in html
    assert "👥 人物画像" not in html  # 渲染标题带 emoji；CSS 注释里的裸文案不算


# =========================================================================
# 为你推荐版面（personal_score 降序 + 原因 chip）
# =========================================================================
def test_personalized_board_sorts_by_personal_score_desc(tmp_data_dir):
    low = _scored("低分条目", "https://a/low", hotness=0.9, score=0.2, reasons=[])
    high = _scored("高分条目", "https://a/high", hotness=0.1, score=0.95, reasons=["tag:models"])
    mid = _scored("中分条目", "https://a/mid", hotness=0.5, score=0.5, reasons=["source:anthropic"])

    html = report.render_html([low, high, mid], {}, out_path=False)
    assert 'id="board-personalized"' in html
    assert "为你推荐" in html

    # 「为你推荐」版面在页面靠前，且组内 high < mid < low 的出现顺序
    section = html.split('id="board-personalized"', 1)[1].split("</section>", 1)[0]
    pos_high = section.index("高分条目")
    pos_mid = section.index("中分条目")
    pos_low = section.index("低分条目")
    assert pos_high < pos_mid < pos_low, "为你推荐应按 personal_score 降序"


def test_no_personal_score_means_no_personalized_board(tmp_data_dir):
    item = schema.make_item(title="x", url="https://a/1", source="S", source_kind="local", backend="rss", weight=0.5)
    html = report.render_html([item], {}, out_path=False)
    assert 'id="board-personalized"' not in html
    assert "✨ 为你推荐" not in html  # 渲染标题带 emoji；CSS 注释里的裸文案不算


# =========================================================================
# 与 core.profile.personalize 串起来（端到端）
# =========================================================================
def test_personalize_then_render_end_to_end(tmp_data_dir):
    items = [
        schema.make_item(title="模型发布", url="https://a/m", source="Anthropic News",
                         source_kind="local", backend="rss", weight=0.9, tags=["models"]),
        schema.make_item(title="闲聊", url="https://a/c", source="Misc",
                         source_kind="local", backend="rss", weight=0.5, tags=["chat"]),
    ]
    for it in items:
        it["hotness"] = 0.5
    reader = {
        "tags": {"models": 1.5},
        "sources": {},
        "people": {},
        "mute": {"tags": [], "people": []},
        "derived": {},
    }
    profile.personalize(items, reader)
    # models 条目 personal_score 应更高
    scores = {it["title"]: it["extra"]["personal_score"] for it in items}
    assert scores["模型发布"] > scores["闲聊"]

    html = report.render_html(items, {}, out_path=False)
    section = html.split('id="board-personalized"', 1)[1].split("</section>", 1)[0]
    assert section.index("模型发布") < section.index("闲聊")


def test_render_html_still_writes_and_supports_out_path(tmp_data_dir):
    """新签名的 personas 是关键字参数，out_path 关键字仍可用（不破坏既有调用）。"""
    personas = [_persona("karpathy", "Andrej", 100)]
    target = tmp_data_dir / "custom.html"
    html = report.render_html([], {}, personas=personas, out_path=target)
    assert target.read_text(encoding="utf-8") == html
