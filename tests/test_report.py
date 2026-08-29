"""test_report.py —— 锁死 spec §9.3：三排序按钮、Ctrl+K palette、badge emoji、条目 URL、单文件自包含。"""

from __future__ import annotations

import base64
import json
import re

import pytest

from qianliyan.core import schema, utils
from qianliyan.pipeline import channels, report

# 1×1 透明 PNG，用于验证头像 base64 内嵌
TINY_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


@pytest.fixture
def digest(tmp_data_dir, sample_items):
    """渲染一份真实简报（用 conftest 的 sample_items 走完整链路）。"""
    items = utils.dedup_and_score(sample_items)
    routed = channels.run_all(items)
    html = report.render_html(items, routed)
    return {"html": html, "items": items, "routed": routed, "data_dir": tmp_data_dir}


# =========================================================================
# 三排序按钮（spec §9.3.2）
# =========================================================================
def test_has_three_sort_buttons_with_required_labels(digest):
    html = digest["html"]
    for label in ("🔥 热度", "🕒 时间", "📈 得分"):
        assert label in html, "缺少排序按钮: {0}".format(label)
    for mode in ("hot", "time", "score"):
        assert 'data-sort="{0}"'.format(mode) in html
    assert len(re.findall(r'class="sort-btn"', html)) == 3


def test_sort_uses_vanilla_js_over_data_attributes(digest):
    html = digest["html"]
    assert "data-sort-root" in html
    for attr in ("data-hotness", "data-ts", "data-weight"):
        assert attr in html
    assert "appendChild" in html, "排序应为原地重排 DOM"
    assert "<script>" in html and "</script>" in html


# =========================================================================
# Ctrl+K Command Palette（spec §9.3.3）
# =========================================================================
def test_has_ctrl_k_command_palette(digest):
    html = digest["html"]
    assert 'id="qly-palette"' in html
    assert 'id="qly-palette-input"' in html
    assert "Ctrl" in html and "<kbd>K</kbd>" in html
    assert "Escape" in html, "Esc 关闭"
    assert "ctrlKey" in html and "metaKey" in html
    assert "Enter" in html, "回车跳转"
    assert "fuzzy" in html, "模糊过滤"


def test_palette_index_covers_every_rendered_item(digest):
    html = digest["html"]
    blob = re.search(
        r'<script type="application/json" id="qly-data">(.*?)</script>', html, re.S
    )
    assert blob is not None
    rows = json.loads(blob.group(1))
    assert rows, "palette 索引不能为空"
    for row in rows:
        assert row["u"], "铁律 2：索引每条都要有 URL"
        assert row["id"].startswith("item-")
        assert row["t"]
    # 索引条数 = 各版面条目数之和
    expected = len(digest["items"][:report.HOTLIST_LIMIT]) + sum(
        len(v) for v in digest["routed"].values()
    )
    assert len(rows) == expected


# =========================================================================
# badge / 版面 / URL（spec §9.3.1，铁律 2）
# =========================================================================
def test_badges_render_as_chinese_emoji_labels(digest):
    html = digest["html"]
    assert "📈 重磅" in html
    assert "⚡ 一手速报" in html
    assert 'class="badge badge-heavy"' in html
    assert 'class="badge badge-flash"' in html


def test_every_card_carries_a_clickable_url(digest):
    html = digest["html"]
    cards = re.findall(r"<li class=\"card\".*?</li>", html, re.S)
    assert cards
    for card in cards:
        assert re.search(r'<a href="https?://[^"]+"', card), "每条必须带可点 URL（铁律 2）"


def test_all_item_urls_appear_in_page(digest):
    html = digest["html"]
    for item in digest["items"]:
        assert item["url"] in html


def test_boards_cover_hotlist_and_every_channel(digest):
    html = digest["html"]
    assert 'id="board-hotlist"' in html
    assert "🔥 全局热榜" in html
    for name in digest["routed"]:
        assert 'id="board-{0}"'.format(name) in html
    # 热榜 + 9 个频道 = 10 个版面
    assert len(re.findall(r'<section class="board"', html)) == 1 + len(digest["routed"])


def test_meta_shows_sources_time_and_hotness(digest):
    html = digest["html"]
    assert "Anthropic News + Simon Willison" in html
    assert "🔥 1." in html or "🔥 0." in html
    assert "小时前" in html or "天前" in html


# =========================================================================
# 单文件自包含 + 中文文案（spec §9.3.5）
# =========================================================================
def test_page_is_self_contained_without_external_assets(digest):
    html = digest["html"]
    assert re.search(r"<link[^>]+rel=[\"']?stylesheet", html) is None
    assert re.search(r"<script[^>]+\ssrc=", html) is None
    assert re.search(r"<img[^>]+src=[\"']https?://", html) is None
    assert "@import" not in html
    assert re.search(r"url\(\s*['\"]?https?://", html) is None
    assert "<style>" in html, "CSS 必须内联"


def test_page_is_simplified_chinese(digest):
    html = digest["html"]
    assert 'lang="zh-CN"' in html
    for phrase in ("千里眼", "排序", "版面", "阅读原文", "关闭"):
        assert phrase in html


def test_no_unrendered_template_tags_remain(digest):
    html = digest["html"]
    assert "{{" not in html
    assert "{%" not in html


# =========================================================================
# 中文标题优先 / 转义
# =========================================================================
def test_chinese_title_wins_over_english(tmp_data_dir):
    item = schema.make_item(
        title="Anthropic ships Claude Opus 5", url="https://a.example/1",
        source="Anthropic News", source_kind="local", backend="html", weight=0.98,
        summary="English summary",
    )
    item["extra"].update({"title_zh": "Anthropic 发布 Claude Opus 5", "summary_zh": "中文摘要"})
    html = report.render_html([item], {})
    assert "Anthropic 发布 Claude Opus 5" in html
    assert "中文摘要" in html
    assert "English summary" not in html


def test_titles_are_html_escaped(tmp_data_dir):
    item = schema.make_item(
        title="<script>alert(1)</script>", url="https://a.example/1",
        source="X", source_kind="local", backend="html", weight=0.5,
    )
    html = report.render_html([item], {})
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html


# =========================================================================
# 头像（spec §9.3.4）
# =========================================================================
def _builder_item():
    return schema.make_item(
        title="karpathy: the best agent harness", url="https://x.com/karpathy/status/1",
        source="@karpathy", source_kind="builders", backend="raw_json", weight=0.8,
        tags=["x", "builders"], extra={"platform": "x"},
    )


def test_existing_avatar_is_inlined_as_base64(tmp_data_dir):
    avatar_dir = tmp_data_dir / "builder-avatars"
    avatar_dir.mkdir(parents=True, exist_ok=True)
    (avatar_dir / "karpathy.png").write_bytes(TINY_PNG)

    html = report.render_html([_builder_item()], {})
    assert "data:image/png;base64," in html
    assert base64.b64encode(TINY_PNG).decode("ascii") in html


def test_missing_avatar_falls_back_to_initial_circle(tmp_data_dir):
    html = report.render_html([_builder_item()], {})
    assert "data:image/png;base64," not in html
    assert 'class="avatar" style="background:' in html
    assert ">K</span>" in html


# =========================================================================
# 写盘
# =========================================================================
def test_render_html_writes_digest_to_data_dir(digest):
    written = digest["data_dir"] / "digest.html"
    assert written.is_file()
    assert written.read_text(encoding="utf-8") == digest["html"]


def test_out_path_can_be_overridden(tmp_data_dir, sample_items):
    target = tmp_data_dir / "archive" / "2026-08-25" / "digest.html"
    html = report.render_html(utils.dedup_and_score(sample_items), {}, out_path=target)
    assert target.read_text(encoding="utf-8") == html


def test_out_path_false_skips_writing(tmp_data_dir, sample_items):
    report.render_html(utils.dedup_and_score(sample_items), {}, out_path=False)
    assert not (tmp_data_dir / "digest.html").exists()


def test_channel_map_defaults_to_config_routing(tmp_data_dir, sample_items):
    html = report.render_html(utils.dedup_and_score(sample_items), None)
    assert 'id="board-company-internal"' in html


def test_empty_pool_still_renders_a_valid_page(tmp_data_dir):
    html = report.render_html([], {})
    assert "🔥 全局热榜" in html
    assert "本版面暂无条目。" in html
    assert "{{" not in html


def test_build_context_counts_badges(tmp_data_dir, sample_items):
    items = utils.dedup_and_score(sample_items)
    context = report.build_context(items, {})
    assert context["total_items"] == len(items)
    assert context["heavy_count"] == sum(1 for it in items if "heavy" in it["badges"])
    assert context["flash_count"] == sum(1 for it in items if "flash" in it["badges"])
    assert context["board_count"] == 1  # 只有热榜（channel_map 为空）


# =========================================================================
# 设计系统（v0.3 版面重构）
# =========================================================================
def test_digest_uses_the_single_design_system(digest):
    """简报页与日报页吃同一份 token 源，页面里不得再有第二套配色。"""
    html = digest["html"]
    assert "--theme-accent: #d97757" in html, "简报页没吃到设计系统"
    assert "prefers-color-scheme: dark" in html, "简报页缺暗色适配"
    # 历史类名沿用旧变量名，但它们必须是**别名**（值取自 token），不能自带色值
    assert "--ink-soft: var(--sidebar-bg)" in html
    assert "--brand: var(--theme-accent)" in html
    for legacy in ("#2f6df6", "#f0f2f8", "#232838"):
        assert legacy not in html, "简报页残留旧配色 {0}".format(legacy)


def test_avatar_palette_stays_in_step_with_the_accent_colors(digest):
    """头像占位色是唯一写死色值的地方——它得取自设计系统的语义色，不是随手挑的通用色。"""
    from qianliyan.pipeline import report

    assert "#2f6df6" not in report._AVATAR_COLORS
    # 四个语义色的深色档都应在色板里（明暗主题下都要压得住白色首字母）
    for accent in ("#af5233", "#4c73a5", "#579766", "#ce9042"):
        assert accent in report._AVATAR_COLORS


# =========================================================================
# 变更情报版面（王牌：叙事 ↔ 实证）
# =========================================================================
def _changelog_item(sig, subject, version, delta, **kw):
    from qianliyan.core import schema

    return schema.make_item(
        title=kw.get("title", "{0} {1} 提示词变更".format(subject, version)),
        url="https://github.com/x/commit/{0}".format(sig),
        source="Claude Code 系统提示词", source_kind="local", backend="git",
        weight=0.7, date="2026-08-25T04:00:00+00:00",
        extra={"format": "changelog", "subject": subject, "version": version,
               "token_delta": delta},
    )


def test_change_board_surfaces_bind_changes_cards():
    """``bind_changes`` 的版本变更卡此前算了就扔——只活在内存里，既不落盘也不渲染。

    「哪个版本的提示词涨了多少 token」是本产品自称的王牌之一，必须露出来。
    """
    from qianliyan.pipeline import report

    items = [
        _changelog_item("a1", "claude-code-prompts", "2.1.246", 69754),
        _changelog_item("a2", "claude-code-prompts", "2.1.100", -1200),
    ]
    ctx = report.build_context(items)
    assert ctx["change_count"] >= 1
    html = report.render_html(items, out_path=False)
    assert 'id="board-change"' in html
    assert "变更情报" in html
    assert "+69,754 tokens" in html, "token 增减要带千位分隔与正负号"
    assert "{{" not in html and "{%" not in html


def test_change_board_orders_anomalies_first():
    """实证核验按 矛盾 > 存疑 > 实证 排——反常的排前面，那才是值得看的。"""
    from qianliyan.core import schema
    from qianliyan.pipeline import report

    def narrative(sig, verdict):
        return schema.make_item(
            title="演讲 {0}".format(verdict), url="https://x/{0}".format(sig),
            source="Talk", source_kind="local", backend="rss", weight=0.8,
            date="2026-08-25T04:00:00+00:00",
            extra={"format": "talk",
                   "corroboration": {"verdict": verdict, "claim": "减少 80% 提示词", "evidence": "diff"}},
        )

    items = [narrative("c", "corroborated"), narrative("u", "unverified"), narrative("x", "contradicted")]
    rows = report._corroboration_views(items)
    assert [r["verdict"] for r in rows] == ["contradicted", "unverified", "corroborated"]


def test_change_board_hidden_when_there_is_nothing_to_show(digest):
    """没有 changelog 条目就不渲染这个版面（空版面比没有版面更糟）。"""
    assert 'id="board-change"' not in digest["html"]


def test_change_card_refs_is_a_count_not_a_list():
    """refs 是**关联条目的 sig 列表**不是数字，直接 format 会渲染出 "['abc'] 源"。"""
    from qianliyan.pipeline import report

    views = report._change_card_views([
        _changelog_item("a1", "claude-code-prompts", "2.1.246", 69754),
    ])
    for view in views:
        assert "[" not in view["refs_text"], "refs 渲染成了列表字面量"
