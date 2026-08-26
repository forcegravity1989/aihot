"""test_engines.py —— 锁死 spec §6：rss/atom/sitemap 解析、detect_backend 全分支、gitfeed 版本抽取。

全部离线：真实网络调用一律通过 monkeypatch 替换掉 ``engine.http`` 的底层函数，
物理层 socket 封锁（conftest 的 ``_no_real_network``）作为兜底保险。
"""

from __future__ import annotations

import json

import pytest

from qianliyan.engine import cdp, gitfeed, html_page, http, remote_sync, rss


# =========================================================================
# engine.http
# =========================================================================
def test_http_get_raises_offline_error_when_offline(monkeypatch):
    monkeypatch.setenv("QLY_OFFLINE", "1")
    with pytest.raises(http.OfflineError):
        http.get("https://example.com/anything")


def test_http_get_json_raises_offline_error_when_offline(monkeypatch):
    monkeypatch.setenv("QLY_OFFLINE", "1")
    with pytest.raises(http.OfflineError):
        http.get_json("https://example.com/anything")


def test_http_offline_error_is_runtime_error():
    assert issubclass(http.OfflineError, RuntimeError)


class _FakeResponse:
    def __init__(self, text="", json_data=None, status_code=200):
        self.text = text
        self._json_data = json_data
        self.status_code = status_code

    def json(self):
        if self._json_data is None:
            raise ValueError("no json")
        return self._json_data


def test_http_get_uses_browser_ua_and_explicit_timeout(monkeypatch):
    captured = {}

    def fake_get(url, timeout=None, headers=None):
        captured["url"] = url
        captured["timeout"] = timeout
        captured["headers"] = headers
        return _FakeResponse(text="ok")

    monkeypatch.setattr(http.requests, "get", fake_get)
    resp = http.get("https://example.com/x", timeout=9)
    assert resp.text == "ok"
    assert captured["timeout"] == 9
    assert "Chrome/126" in captured["headers"]["User-Agent"]
    assert captured["headers"]["User-Agent"].startswith("Mozilla/5.0")


def test_http_get_json_parses_response(monkeypatch):
    monkeypatch.setattr(
        http.requests, "get",
        lambda url, timeout=None, headers=None: _FakeResponse(json_data={"a": 1}),
    )
    assert http.get_json("https://example.com/x") == {"a": 1}


# =========================================================================
# engine.rss —— RSS2 / Atom
# =========================================================================
def test_rss_parse_rss2_sample(fixtures_dir):
    xml_text = (fixtures_dir / "rss_sample.xml").read_text(encoding="utf-8")
    items = rss.parse(xml_text)
    assert len(items) == 2
    assert items[0] == {
        "title": "Model X reaches new benchmark high",
        "url": "https://example.com/blog/model-x-benchmark",
        "summary": "Model X posts a new SOTA score on the reasoning benchmark.",
        "date": "Mon, 24 Aug 2026 09:00:00 GMT",
    }
    assert items[1]["title"] == "Weekly roundup: agents, tools, and evals"


def test_rss_parse_atom_sample(fixtures_dir):
    xml_text = (fixtures_dir / "atom_sample.xml").read_text(encoding="utf-8")
    items = rss.parse(xml_text)
    assert len(items) == 2

    first = items[0]
    assert first["title"] == "Introducing Foundation Model v3"
    assert first["url"] == "https://example.com/blog/foundation-v3"
    # published 优先于 updated
    assert first["date"] == "2026-08-22T10:00:00Z"
    assert "longer context window" in first["summary"]

    second = items[1]
    # 无 published，回退 updated
    assert second["date"] == "2026-08-21T08:00:00Z"
    # 无 summary，回退 content
    assert "scaling laws" in second["summary"]


def test_rss_parse_malformed_xml_returns_empty(caplog):
    with caplog.at_level("WARNING"):
        assert rss.parse("<rss><channel><item><title>oops") == []


def test_rss_parse_empty_text_returns_empty():
    assert rss.parse("") == []
    assert rss.parse(None) == []


def test_rss_fetch_is_get_plus_parse(monkeypatch, fixtures_dir):
    xml_text = (fixtures_dir / "rss_sample.xml").read_text(encoding="utf-8")
    monkeypatch.setattr(http, "get", lambda url, timeout=15: _FakeResponse(text=xml_text))
    items = rss.fetch("https://example.com/feed.xml")
    assert len(items) == 2


# =========================================================================
# engine.gitfeed —— 版本抽取 + releases()
# =========================================================================
@pytest.mark.parametrize("title,expected", [
    ("v2.1.0", "v2.1.0"),
    ("Release 10.3.2-beta", "10.3.2-beta"),
    ("qianliyan v0.2.0 — five eyes", "v0.2.0"),
    ("no version here", ""),
    ("", ""),
])
def test_gitfeed_extract_version(title, expected):
    assert gitfeed.extract_version(title) == expected


def test_gitfeed_releases_adds_version_and_release_tag(monkeypatch):
    atom = """<?xml version="1.0"?>
    <feed xmlns="http://www.w3.org/2005/Atom">
      <entry>
        <title>v2.1.0</title>
        <link href="https://github.com/anthropics/claude-code/releases/tag/v2.1.0" rel="alternate"/>
        <content>Release notes for 2.1.0</content>
        <updated>2026-08-24T00:00:00Z</updated>
      </entry>
    </feed>"""
    monkeypatch.setattr(gitfeed.http, "get", lambda url, timeout=15: _FakeResponse(text=atom))
    out = gitfeed.releases("anthropics/claude-code")
    assert len(out) == 1
    assert out[0]["tags"] == ["release"]
    assert out[0]["metrics"] == {"version": "v2.1.0"}
    assert out[0]["url"].endswith("v2.1.0")


def test_gitfeed_releases_no_version_leaves_metrics_empty(monkeypatch):
    atom = """<?xml version="1.0"?>
    <feed xmlns="http://www.w3.org/2005/Atom">
      <entry>
        <title>General announcement</title>
        <link href="https://github.com/o/r/releases/tag/misc" rel="alternate"/>
        <updated>2026-08-24T00:00:00Z</updated>
      </entry>
    </feed>"""
    monkeypatch.setattr(gitfeed.http, "get", lambda url, timeout=15: _FakeResponse(text=atom))
    out = gitfeed.releases("o/r")
    assert out[0]["metrics"] == {}
    assert out[0]["tags"] == ["release"]


def test_gitfeed_tags_and_commits_reuse_rss_parse(monkeypatch, fixtures_dir):
    xml_text = (fixtures_dir / "rss_sample.xml").read_text(encoding="utf-8")
    monkeypatch.setattr(gitfeed.http, "get", lambda url, timeout=15: _FakeResponse(text=xml_text))
    assert len(gitfeed.tags("o/r")) == 2
    assert len(gitfeed.commits("o/r", branch="main")) == 2


def test_gitfeed_url_templates():
    assert gitfeed.RELEASES_URL_TMPL.format(owner_repo="o/r") == "https://github.com/o/r/releases.atom"
    assert gitfeed.TAGS_URL_TMPL.format(owner_repo="o/r") == "https://github.com/o/r/tags.atom"
    assert (
        gitfeed.COMMITS_URL_TMPL.format(owner_repo="o/r", branch="main")
        == "https://github.com/o/r/commits/main.atom"
    )


# =========================================================================
# engine.html_page —— 通用链接抽取
# =========================================================================
_SAMPLE_HTML = """
<html><body>
<nav><a href="/">Home</a> <a href="/login">Sign In</a></nav>
<main>
<a href="/news/model-launch">Anthropic announces a brand new frontier model today</a>
<a href="https://external.example.com/deep-dive">A very deep technical dive into the training run</a>
<a href="/short">Short</a>
<a href="javascript:void(0)">Click here to expand</a>
<a href="#top">Back to top of this very long page anchor</a>
</main>
</body></html>
"""


def test_extract_links_filters_short_and_nav_noise_and_resolves_relative():
    links = html_page.extract_links(_SAMPLE_HTML, base_url="https://example.com/section/")
    urls = {link["url"] for link in links}
    titles = {link["title"] for link in links}

    # 短文本（Home / Sign In / Short，均 <8 字符）被过滤
    assert "Home" not in titles
    assert "Sign In" not in titles
    assert "Short" not in titles
    # javascript: 和 # 锚点被过滤
    assert not any(u.startswith("javascript:") for u in urls)
    assert "https://example.com/section/#top" not in urls

    assert "https://example.com/news/model-launch" in urls
    assert "https://external.example.com/deep-dive" in urls


def test_extract_links_respects_limit():
    html_text = "".join(
        '<a href="/p{0}">This is a sufficiently long link title {0}</a>'.format(i)
        for i in range(10)
    )
    links = html_page.extract_links(html_text, base_url="https://example.com/", limit=3)
    assert len(links) == 3


def test_extract_links_empty_input():
    assert html_page.extract_links("") == []


def test_fetch_links_is_get_plus_extract(monkeypatch):
    monkeypatch.setattr(
        http, "get",
        lambda url, timeout=15: _FakeResponse(text=_SAMPLE_HTML),
    )
    links = html_page.fetch_links("https://example.com/section/")
    assert any("deep-dive" in link["url"] for link in links)


# =========================================================================
# engine.remote_sync —— detect_backend 全分支 + sitemap + fetch_source 分发
# =========================================================================
@pytest.mark.parametrize("url,declared,expected", [
    ("https://example.com/anything", "html", "html"),          # declared 优先，压过其它一切启发式
    ("http://export.arxiv.org/api/query?search_query=x", None, "arxiv"),
    ("https://example.com/path/sitemap.xml", None, "sitemap"),  # sitemap 优先于 .xml 后缀
    ("https://example.com/feed.xml", None, "rss"),
    ("https://example.com/rss", None, "rss"),
    ("https://example.com/blog.atom", None, "rss"),
    ("https://github.com/anthropics/claude-code", None, "git"),
    ("https://github.com/anthropics/claude-code/", None, "git"),
    ("https://example.com/news", None, "html"),
    ("", None, "html"),
])
def test_detect_backend_branches(url, declared, expected):
    assert remote_sync.detect_backend(url, declared) == expected


def test_parse_sitemap_extracts_loc_up_to_limit(fixtures_dir):
    xml_text = (fixtures_dir / "sitemap_sample.xml").read_text(encoding="utf-8")
    entries = remote_sync.parse_sitemap(xml_text)
    assert len(entries) == 3
    assert entries[0] == {
        "title": "https://example.com/page-1",
        "url": "https://example.com/page-1",
        "summary": "",
        "date": "",
    }


def test_parse_sitemap_respects_limit(fixtures_dir):
    xml_text = (fixtures_dir / "sitemap_sample.xml").read_text(encoding="utf-8")
    entries = remote_sync.parse_sitemap(xml_text, limit=2)
    assert len(entries) == 2


def test_parse_sitemap_malformed_returns_empty(caplog):
    with caplog.at_level("WARNING"):
        assert remote_sync.parse_sitemap("<urlset><url><loc>oops") == []


def test_fetch_source_dispatches_rss(monkeypatch, fixtures_dir):
    xml_text = (fixtures_dir / "rss_sample.xml").read_text(encoding="utf-8")
    monkeypatch.setattr(remote_sync.http, "get", lambda url, timeout=15: _FakeResponse(text=xml_text))
    entries = remote_sync.fetch_source({"url": "https://example.com/feed.xml", "type": "rss"})
    assert len(entries) == 2


def test_fetch_source_dispatches_arxiv_via_rss_parse(monkeypatch, fixtures_dir):
    xml_text = (fixtures_dir / "atom_sample.xml").read_text(encoding="utf-8")
    monkeypatch.setattr(remote_sync.http, "get", lambda url, timeout=15: _FakeResponse(text=xml_text))
    entries = remote_sync.fetch_source({
        "url": "http://export.arxiv.org/api/query?search_query=cat:cs.CL", "type": "arxiv",
    })
    assert len(entries) == 2


def test_fetch_source_dispatches_sitemap(monkeypatch, fixtures_dir):
    xml_text = (fixtures_dir / "sitemap_sample.xml").read_text(encoding="utf-8")
    monkeypatch.setattr(remote_sync.http, "get", lambda url, timeout=15: _FakeResponse(text=xml_text))
    entries = remote_sync.fetch_source({"url": "https://example.com/sitemap.xml", "type": "sitemap"})
    assert len(entries) == 3


def test_fetch_source_dispatches_git(monkeypatch):
    sentinel = [{"title": "v1.0.0", "url": "https://github.com/o/r/releases/tag/v1.0.0",
                 "summary": "", "date": "", "tags": ["release"], "metrics": {"version": "v1.0.0"}}]
    captured = {}

    def fake_releases(owner_repo, timeout=15):
        captured["owner_repo"] = owner_repo
        return sentinel

    monkeypatch.setattr(remote_sync.gitfeed, "releases", fake_releases)
    entries = remote_sync.fetch_source({"url": "https://github.com/o/r", "type": "git"})
    assert entries is sentinel
    assert captured["owner_repo"] == "o/r"


def test_fetch_source_dispatches_html(monkeypatch):
    sentinel = [{"title": "A sufficiently long article title", "url": "https://example.com/a"}]
    captured = {}

    def fake_fetch_links(url, limit=20):
        captured["url"] = url
        captured["limit"] = limit
        return sentinel

    monkeypatch.setattr(remote_sync.html_page, "fetch_links", fake_fetch_links)
    entries = remote_sync.fetch_source({"url": "https://example.com/news", "type": "html"})
    assert entries is sentinel
    assert captured["limit"] == 20


def test_fetch_source_git_without_resolvable_owner_repo_raises():
    with pytest.raises(ValueError):
        remote_sync.fetch_source({"url": "not-a-github-url", "type": "git"})


# =========================================================================
# engine.cdp —— 惰性 import + 离线兜底（不要求真实联调）
# =========================================================================
def test_cdp_connect_raises_when_playwright_missing():
    # 本仓库/venv 未安装 playwright（可选依赖），预期路径就是 CDPUnavailable
    with pytest.raises(cdp.CDPUnavailable):
        cdp.connect()


def test_cdp_fetch_via_proxy_raises_on_failure(monkeypatch):
    # 物理层 socket 封锁会让任何真实连接尝试失败；这里显式验证异常被归一为 CDPUnavailable
    with pytest.raises(cdp.CDPUnavailable):
        cdp.fetch_via_proxy("https://internal.example.com/x", timeout=1)


def test_cdp_default_urls_are_localhost():
    assert cdp.DEFAULT_CDP_URL.startswith("http://127.0.0.1")
    assert cdp.DEFAULT_PROXY_URL.startswith("http://127.0.0.1")
