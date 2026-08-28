"""test_article.py —— engine.article 正文抽取（深读原料）的离线测试。

fixture ``claude_blog_article.html`` 是真实的 claude.com/blog 文章页（"The AI-Native
SDLC playbook"），只剥掉了 script/style/svg 的**内容**（抽取器本来就跳过它们）以控制
体积，DOM 结构与真实页一致——抽取结果与完整真实页逐字相同（60 段 / 11026 字符）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from qianliyan.engine import article

REAL = Path(__file__).resolve().parent / "fixtures" / "real"


def _fixture() -> str:
    return (REAL / "claude_blog_article.html").read_text(encoding="utf-8")


# =========================================================================
# 真实文章页
# =========================================================================
def test_extract_real_article_body():
    result = article.extract_article(_fixture())
    assert len(result["paragraphs"]) >= 40, "真实长文应抽出足量段落，深读才有原料"
    assert result["char_count"] > 5000
    text = result["text"]
    # 正文实质内容在
    assert "Code is no longer the bottleneck" in text
    assert "software development lifecycle" in text
    # lead 可直接当摘要用
    assert result["lead"].startswith("How to transform your software development lifecycle")


def test_extract_real_article_filters_noise():
    """真实页里的三类噪声都要被滤掉（这正是索引页抓取只拿到标题时的典型污染源）。"""
    text = article.extract_article(_fixture())["text"]
    assert "Meet ClaudeProducts" not in text, "导航词堆叠（无句末标点的长串）"
    assert "ShareCopy" not in text, "分享条（裸 URL 块）"
    assert "Console login" not in text, "导航链接项"


def test_paragraphs_are_deduped_and_ordered():
    paragraphs = article.extract_article(_fixture())["paragraphs"]
    assert len(paragraphs) == len(set(paragraphs)), "重复块要去重"
    joined = "\n".join(paragraphs)
    assert joined.index("Code is no longer the bottleneck") < joined.index("Plays"), "保持原文顺序"


# =========================================================================
# 噪声判据（逐条锁死）
# =========================================================================
def test_drops_nav_soup_without_sentence_punctuation():
    html = (
        "<body><div>MeetClaudeProductsClaudeCodeCoworkFeaturesSkillsModelsPlatformOverview"
        "PricingDeveloperDocsConsoleLoginEcosystemMarketplaceConnectorsPluginsSolutions</div>"
        "<p>This is a real body paragraph with proper sentence punctuation, long enough to keep.</p>"
        "</body>"
    )
    paragraphs = article.extract_article(html)["paragraphs"]
    assert paragraphs == ["This is a real body paragraph with proper sentence punctuation, long enough to keep."]


def test_drops_high_link_density_block():
    html = (
        '<body><p><a href="/a">Documentation</a> <a href="/b">Pricing</a> '
        '<a href="/c">Careers</a> <a href="/d">Security</a> <a href="/e">Status page</a></p>'
        "<p>Real prose that happens to be long enough to survive the minimum length rule.</p>"
        "</body>"
    )
    paragraphs = article.extract_article(html)["paragraphs"]
    assert len(paragraphs) == 1
    assert paragraphs[0].startswith("Real prose")


def test_drops_share_bar_that_is_mostly_bare_url():
    html = (
        "<body><p>ShareCopy linkhttps://claude.com/blog/the-ai-native-sdlc-playbook</p>"
        "<p>An actual paragraph of article prose, comfortably past the length threshold.</p>"
        "</body>"
    )
    paragraphs = article.extract_article(html)["paragraphs"]
    assert len(paragraphs) == 1
    assert paragraphs[0].startswith("An actual paragraph")


def test_skips_script_style_and_footer_containers():
    html = (
        "<body>"
        "<script>var junk = 'this must not appear in the body text at all';</script>"
        "<style>.cls { content: 'neither should this ever show up here'; }</style>"
        "<p>The only paragraph that should survive this particular document.</p>"
        "<footer><p>Copyright 2026 Example Corp. All rights reserved worldwide.</p></footer>"
        "</body>"
    )
    paragraphs = article.extract_article(html)["paragraphs"]
    assert paragraphs == ["The only paragraph that should survive this particular document."]


def test_unclosed_nav_does_not_swallow_the_rest_of_the_page():
    """真实站点模板常有未闭合的 <nav>；纯深度计数会把正文整段吞掉（回归用例）。"""
    html = (
        "<body>"
        "<nav><nav><a href='/x'>Nav link item</a>"          # 两层 nav，只闭合一次
        "</nav>"
        "<p>Body text that appears after the malformed navigation block and must survive.</p>"
        "</body>"
    )
    paragraphs = article.extract_article(html)["paragraphs"]
    assert any(p.startswith("Body text that appears after") for p in paragraphs)


def test_headings_are_kept_even_when_short():
    html = (
        "<body><h2>Plays</h2>"
        "<p>A paragraph long enough to pass the minimum body length requirement here.</p></body>"
    )
    paragraphs = article.extract_article(html)["paragraphs"]
    assert "Plays" in paragraphs


# =========================================================================
# 边界与网络壳
# =========================================================================
@pytest.mark.parametrize("bad", ["", "   ", None])
def test_empty_input_returns_empty_structure(bad):
    result = article.extract_article(bad)
    assert result == {"paragraphs": [], "text": "", "lead": "", "char_count": 0}


def test_malformed_html_does_not_raise():
    result = article.extract_article("<body><p>unclosed paragraph<div><span>")
    assert isinstance(result["paragraphs"], list)


def test_fetch_article_returns_empty_structure_on_network_failure(monkeypatch):
    """抓不到正文时深读要能优雅退回摘要，不能让整轮 finalize 挂掉。"""
    def _boom(url, timeout=20, max_bytes=None):
        raise RuntimeError("network down (simulated)")

    monkeypatch.setattr(article.http, "get", _boom)
    result = article.fetch_article("https://example.com/post")
    assert result["paragraphs"] == []
    assert result["char_count"] == 0


def test_fetch_article_extracts_from_response(monkeypatch):
    class _Resp:
        text = "<body><p>Fetched body prose that is definitely long enough to survive.</p></body>"

    monkeypatch.setattr(article.http, "get", lambda url, timeout=20, max_bytes=None: _Resp())
    result = article.fetch_article("https://example.com/post")
    assert result["lead"].startswith("Fetched body prose")
