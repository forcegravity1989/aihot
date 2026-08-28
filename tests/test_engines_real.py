"""test_engines_real.py —— spec-v0.3 §17：engine 新后端对真实 fixtures 的解析（全离线）。

覆盖：youtube.parse/feed_url（youtube_anthropic.xml）、github_trending.parse/fetch
（github_trending.html）、html_page.extract_articles/fetch_articles（anthropic_news.html）、
remote_sync.detect_backend 新分支 + fetch_source 对新 backend 的分发。真实网络一律
monkeypatch 掉，物理层 socket 封锁（conftest）兜底。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from qianliyan.core import utils
from qianliyan.engine import github_trending, html_page, http, remote_sync, youtube

REAL = Path(__file__).resolve().parent / "fixtures" / "real"


def _read(name: str) -> str:
    return (REAL / name).read_text(encoding="utf-8")


class _FakeResponse:
    def __init__(self, text="", status_code=200):
        self.text = text
        self.status_code = status_code


# =========================================================================
# engine.youtube
# =========================================================================
def test_youtube_feed_url_channel_and_playlist():
    assert youtube.feed_url({"channel_id": "UCabc"}) == (
        "https://www.youtube.com/feeds/videos.xml?channel_id=UCabc"
    )
    assert youtube.feed_url({"playlist_id": "PLxyz"}) == (
        "https://www.youtube.com/feeds/videos.xml?playlist_id=PLxyz"
    )
    # playlist 优先于 channel
    assert "playlist_id=PL1" in youtube.feed_url({"channel_id": "UC1", "playlist_id": "PL1"})


def test_youtube_feed_url_handle_requires_online_resolution():
    with pytest.raises(ValueError):
        youtube.feed_url({"handle": "@anthropic-ai"})
    with pytest.raises(ValueError):
        youtube.feed_url({})


def test_youtube_parse_real_feed_video_fields():
    cfg = {"name": "Anthropic YouTube", "weight": 0.9, "format": "video",
           "tags": ["official", "anthropic", "video"]}
    items = youtube.parse(_read("youtube_anthropic.xml"), cfg)
    assert len(items) == 15

    first = items[0]
    assert first["title"] == "How Icelanders are thinking about AI"
    assert first["url"] == "https://www.youtube.com/watch?v=iF5IWjOWcA4"
    assert first["extra"]["video_id"] == "iF5IWjOWcA4"
    assert first["extra"]["thumbnail"].endswith("/iF5IWjOWcA4/hqdefault.jpg")
    assert first["extra"]["platform"] == "youtube"
    assert first["extra"]["channel"] == "Anthropic"
    assert first["extra"]["format"] == "video"
    # media:description 进 summary
    assert "Iceland" in first["summary"]
    # published 落 date（ISO 归一后仍保留年月日）
    assert first["date"].startswith("2026-08-10")
    # item 标准字段
    assert first["source"] == "Anthropic YouTube"
    assert first["source_kind"] == "local"
    assert first["backend"] == "rss"
    assert first["weight"] == 0.9
    assert "video" in first["tags"]


def test_youtube_parse_format_and_source_fallback_from_cfg():
    # format 取源配置（会议 playlist → talk）
    talks = youtube.parse(_read("youtube_anthropic.xml"), {"format": "talk"})
    assert all(i["extra"]["format"] == "talk" for i in talks)
    # 无 name 时回退频道名
    assert talks[0]["source"] == "Anthropic"


def test_youtube_parse_empty_and_malformed_return_empty():
    assert youtube.parse("", {}) == []
    assert youtube.parse("<feed><entry><title>oops", {}) == []


def test_youtube_fetch_is_feed_url_get_parse(monkeypatch):
    monkeypatch.setattr(http, "get", lambda url, timeout=15: _FakeResponse(text=_read("youtube_anthropic.xml")))
    items = youtube.fetch({"channel_id": "UCrDwWp7EBBv4NwvScIpBDOA", "format": "video"})
    assert len(items) == 15


# =========================================================================
# engine.github_trending
# =========================================================================
def test_github_trending_parse_real_html():
    items = github_trending.parse(_read("github_trending.html"), since="daily")
    assert len(items) >= 10

    first = items[0]
    assert first["title"] == "freestylefly/awesome-gpt-image-2"
    assert first["url"] == "https://github.com/freestylefly/awesome-gpt-image-2"
    assert first["metrics"]["stars"] == 16752
    assert first["metrics"]["forks"] == 1746
    assert first["metrics"]["stars_period"] == 1698
    assert first["extra"]["format"] == "repo"
    assert first["extra"]["language"] == "JavaScript"
    assert first["extra"]["since"] == "daily"
    assert "github" in first["tags"] and "trending" in first["tags"]
    assert "javascript" in first["tags"]
    assert first["source"] == "GitHub Trending"
    assert first["source_kind"] == "local"
    assert first["backend"] == "html"


def test_github_trending_all_rows_have_owner_repo_url():
    items = github_trending.parse(_read("github_trending.html"), since="weekly")
    for it in items:
        assert it["url"].startswith("https://github.com/")
        assert it["title"].count("/") == 1  # owner/repo
        assert it["extra"]["since"] == "weekly"


def test_github_trending_parse_empty_returns_empty():
    assert github_trending.parse("", "daily") == []
    assert github_trending.parse("<html><body>no rows</body></html>", "daily") == []


def test_github_trending_fetch_is_get_plus_parse(monkeypatch):
    captured = {}

    def fake_get(url, timeout=15):
        captured["url"] = url
        return _FakeResponse(text=_read("github_trending.html"))

    monkeypatch.setattr(http, "get", fake_get)
    items = github_trending.fetch("daily")
    assert len(items) >= 10
    assert "since=daily" in captured["url"]


# =========================================================================
# engine.html_page.extract_articles / fetch_articles
# =========================================================================
def test_extract_articles_anthropic_news():
    articles = html_page.extract_articles(
        _read("anthropic_news.html"),
        r"^/news/[a-z0-9-]+$",
        base_url="https://www.anthropic.com/news",
    )
    assert len(articles) >= 10
    urls = [a["url"] for a in articles]
    # 每条 path 匹配 pattern、绝对化、去重
    assert len(urls) == len(set(urls))
    assert "https://www.anthropic.com/news/claude-opus-5" in urls
    for a in articles:
        assert a["url"].startswith("https://www.anthropic.com/news/")
        assert a["extra"]["format"] == "blog"
        assert a["title"]
    # 标题优先取锚点内标题
    opus = next(a for a in articles if a["url"].endswith("/news/claude-opus-5"))
    assert opus["title"] == "Introducing Claude Opus 5"
    # 卡片式列表项：<time>日期</time> 与标题同锚点，日期要被摘成 date 字段，
    # 不能粘在标题最前面（bug：真实抓取里 hotness 因此集体误判成"现在"）
    open_weights = next(a for a in articles if a["url"].endswith("/news/position-open-weights-models"))
    assert open_weights["date"] == "Jul 27, 2026"
    assert not open_weights["title"].startswith("Jul")
    assert utils.parse_date(open_weights["date"]) is not None


def test_extract_articles_strips_leading_date_segment_from_title():
    """卡片把 <time> 与标题塞进同一个 <a> 时，日期节点要被摘出去，不进标题（回归用例）。"""
    html = (
        '<a href="/news/real-post">'
        '<time>Jul 27, 2026</time><span>Announcements</span> '
        '<h2>A real headline that mentions Jul in passing</h2>'
        "</a>"
    )
    articles = html_page.extract_articles(html, r"^/news/[a-z0-9-]+$", base_url="https://x.com/news")
    assert len(articles) == 1
    assert articles[0]["date"] == "Jul 27, 2026"
    assert articles[0]["title"] == "A real headline that mentions Jul in passing"


def test_extract_articles_falls_back_to_sibling_heading_for_cta_only_anchor():
    """整卡可点击站点（Webflow 常见模式）：真标题/日期是锚点外的兄弟元素，锚点自身
    只有一句"Read more" CTA——要回退用锚点之前最近出现的标题/日期（回归用例，
    对应真实 claude.com/blog 的抓取质量问题）。
    """
    html = (
        '<div class="card">'
        '<h2>Claude in Chrome is generally available</h2>'
        '<div>August 26, 2026</div>'
        '<div class="clickable_wrap">'
        '<a href="/blog/claude-in-chrome-generally-available"><span class="sr-only">Read more</span></a>'
        "</div></div>"
        '<div class="card">'
        '<h2>A second real post title</h2>'
        '<div>April 10, 2026</div>'
        '<a href="/blog/a-second-real-post"><span>Learn more</span></a>'
        "</div>"
    )
    articles = html_page.extract_articles(html, r"^/blog/[a-z0-9-]+$", base_url="https://claude.com/blog")
    assert len(articles) == 2
    first = next(a for a in articles if a["url"].endswith("claude-in-chrome-generally-available"))
    assert first["title"] == "Claude in Chrome is generally available"
    assert first["date"] == "August 26, 2026"
    assert utils.parse_date(first["date"]) is not None
    second = next(a for a in articles if a["url"].endswith("a-second-real-post"))
    assert second["title"] == "A second real post title"
    assert second["date"] == "April 10, 2026"


def test_extract_articles_pattern_excludes_non_matching():
    html = (
        '<a href="/news/real-post"><h2>A real news post title</h2></a>'
        '<a href="/about/company">About us page link that is long</a>'
        '<a href="/news/">Index root should not match slug pattern</a>'
    )
    articles = html_page.extract_articles(html, r"^/news/[a-z0-9-]+$", base_url="https://x.com/news")
    urls = [a["url"] for a in articles]
    assert urls == ["https://x.com/news/real-post"]


def test_extract_articles_empty_input():
    assert html_page.extract_articles("", r"^/news/.+$") == []


def test_fetch_articles_is_get_plus_extract(monkeypatch):
    captured = {}

    def fake_get(url, timeout=15):
        captured["url"] = url
        return _FakeResponse(text=_read("anthropic_news.html"))

    monkeypatch.setattr(http, "get", fake_get)
    articles = html_page.fetch_articles(
        "https://www.anthropic.com/news", r"^/news/[a-z0-9-]+$"
    )
    assert captured["url"] == "https://www.anthropic.com/news"
    assert any(a["url"].endswith("/news/claude-opus-5") for a in articles)


# =========================================================================
# engine.remote_sync —— detect_backend 新分支 + fetch_source 分发
# =========================================================================
@pytest.mark.parametrize("url,declared,expected", [
    ("", "youtube", "youtube"),
    ("", "youtube-playlist", "youtube-playlist"),
    ("", "github-trending", "github-trending"),
    ("https://www.anthropic.com/news", "scrape", "scrape"),
    # 未声明 type 时的 youtube host 启发式
    ("https://www.youtube.com/feeds/videos.xml?channel_id=UCabc", None, "youtube"),
    ("https://www.youtube.com/feeds/videos.xml?playlist_id=PLabc", None, "youtube"),
])
def test_detect_backend_new_branches(url, declared, expected):
    assert remote_sync.detect_backend(url, declared) == expected


def test_detect_backend_existing_branches_unchanged():
    # 回归：既有分支不受影响
    assert remote_sync.detect_backend("https://example.com/feed.xml", None) == "rss"
    assert remote_sync.detect_backend("https://github.com/anthropics/claude-code", None) == "git"
    assert remote_sync.detect_backend("http://export.arxiv.org/api/query?x=1", None) == "arxiv"
    assert remote_sync.detect_backend("https://example.com/news", None) == "html"


def test_fetch_source_dispatches_youtube(monkeypatch):
    captured = {}

    def fake_get(url, timeout=15):
        captured["url"] = url
        return _FakeResponse(text=_read("youtube_anthropic.xml"))

    monkeypatch.setattr(remote_sync.http, "get", fake_get)
    items = remote_sync.fetch_source({
        "name": "Anthropic YouTube", "type": "youtube",
        "channel_id": "UCrDwWp7EBBv4NwvScIpBDOA", "weight": 0.9, "format": "video",
        "tags": ["official", "video", "talks"],
    })
    assert len(items) == 15
    assert "channel_id=UCrDwWp7EBBv4NwvScIpBDOA" in captured["url"]
    assert items[0]["extra"]["video_id"] == "iF5IWjOWcA4"


def test_fetch_source_dispatches_github_trending(monkeypatch):
    monkeypatch.setattr(remote_sync.http, "get", lambda url, timeout=15: _FakeResponse(text=_read("github_trending.html")))
    items = remote_sync.fetch_source({
        "name": "GitHub Trending (Daily)", "type": "github-trending", "since": "daily",
    })
    assert len(items) >= 10
    assert items[0]["extra"]["format"] == "repo"


def test_fetch_source_dispatches_scrape(monkeypatch):
    monkeypatch.setattr(remote_sync.http, "get", lambda url, timeout=15: _FakeResponse(text=_read("anthropic_news.html")))
    entries = remote_sync.fetch_source({
        "name": "Anthropic News", "type": "scrape",
        "url": "https://www.anthropic.com/news", "article_pattern": "^/news/[a-z0-9-]+$",
    })
    assert len(entries) >= 10
    assert all(e["url"].startswith("https://www.anthropic.com/news/") for e in entries)
    assert entries[0]["extra"]["format"] == "blog"
