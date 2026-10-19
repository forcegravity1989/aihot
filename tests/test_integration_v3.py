from __future__ import annotations

import pytest

from qianliyan.cli import sync
from qianliyan.core import storage


def test_sync_mock_produces_personas(tmp_data_dir):
    meta = sync.run_sync(mock=True)

    personas_path = tmp_data_dir / "personas.json"
    assert personas_path.is_file(), "应产出 personas.json"
    personas = storage.read_json(personas_path, default=None)
    assert isinstance(personas, list) and personas, "mock 应聚合出至少一个 persona"

    assert meta["totals"]["personas"] == len(personas)

    engagements = [p.get("total_engagement", 0) for p in personas]
    assert engagements == sorted(engagements, reverse=True), "persona 应按 total_engagement 降序"
    for p in personas:
        assert p.get("handle")
        assert p.get("name")
        assert "topics" in p and isinstance(p["topics"], list)
        assert "recent_focus" in p

    by_handle = {str(p["handle"]).casefold(): p for p in personas}
    assert "rauchg" in by_handle
    assert by_handle["rauchg"].get("item_count", 0) >= 2

    md_files = list((tmp_data_dir / "personas").glob("*.md"))
    assert md_files, "应产出 personas/<handle>.md"


def test_sync_mock_writes_personal_score_on_every_item(tmp_data_dir):
    sync.run_sync(mock=True)
    items = storage.read_jsonl(tmp_data_dir / "items.jsonl")
    assert items
    for item in items:
        extra = item.get("extra") or {}
        assert "personal_score" in extra, "每条 item 都应带 extra.personal_score"
        assert isinstance(extra["personal_score"], (int, float))
        assert "personal_reasons" in extra


def test_sync_mock_quick_still_personalizes_and_builds_personas(tmp_data_dir):
    meta = sync.run_sync(mock=True, quick=True)
    items = storage.read_jsonl(tmp_data_dir / "items.jsonl")
    assert all("personal_score" in (it.get("extra") or {}) for it in items)
    assert (tmp_data_dir / "personas.json").is_file()
    assert meta["totals"]["personas"] >= 1


def test_sync_mock_new_channels_are_hit(tmp_data_dir):
    sync.run_sync(mock=True)
    index = storage.read_json(tmp_data_dir / "channels.json", default={})
    assert isinstance(index, dict) and index
    for channel in ("talks", "trending", "practices", "podcasts", "papers"):
        assert index.get(channel), "频道 {0} 应至少命中一条合成 item".format(channel)


def test_sync_mock_digest_has_persona_and_recommend_boards(tmp_data_dir):
    sync.run_sync(mock=True)
    html = (tmp_data_dir / "digest.html").read_text(encoding="utf-8")
    assert "为你推荐" in html, "digest 应含「为你推荐」版面"
    assert "人物画像" in html, "digest 应含「人物画像」版面"
    assert "Guillermo Rauch" in html or "rauchg" in html


from qianliyan.core import schema  # noqa: E402
from qianliyan.eyes import local  # noqa: E402


def test_local_adopts_complete_youtube_item_and_keeps_legal_backend():
    yt_cfg = {"name": "Anthropic YouTube", "type": "youtube", "channel_id": "X",
              "weight": 0.9, "tags": ["official", "talks"], "format": "video", "category": "talks"}
    complete = schema.make_item(
        title="Talk A", url="https://youtube.com/watch?v=abc",
        source="rawtube", source_kind="local", backend="rss", weight=0.85,
        extra={"platform": "youtube", "video_id": "abc", "format": "video"},
    )
    out = local.parse_payload([complete], yt_cfg)[0]
    assert schema.validate_item(out) == []
    assert out["source"] == "Anthropic YouTube"
    assert out["source_list"] == ["Anthropic YouTube"]
    assert out["weight"] == 0.9
    assert out["backend"] == "rss"        
    assert out["extra"]["format"] == "video"
    assert out["extra"]["category"] == "talks"
    assert out["extra"]["source_category"] == "talks"
    assert out["extra"]["source_type"] == "youtube"


def test_local_adopts_github_trending_item_overriding_source_and_weight():
    gh_cfg = {"name": "GitHub Trending (Daily)", "type": "github-trending", "since": "daily",
              "weight": 0.68, "tags": ["github", "trending"], "format": "repo", "category": "trending"}
    complete = schema.make_item(
        title="owner/repo", url="https://github.com/owner/repo",
        source="GitHub Trending", source_kind="local", backend="html", weight=0.65,
        metrics={"stars": 10}, extra={"format": "repo", "language": "Go"},
    )
    out = local.parse_payload([complete], gh_cfg)[0]
    assert schema.validate_item(out) == []
    assert out["source"] == "GitHub Trending (Daily)"
    assert out["weight"] == 0.68
    assert out["backend"] == "html"
    assert out["extra"]["category"] == "trending"
    assert out["extra"]["source_type"] == "github-trending"


def test_local_scrape_entry_legalizes_backend_to_html():
    # scrape 的 backend 必须合法化为 html，不能把 'scrape' 塞进 backend
    sc_cfg = {"name": "Anthropic News", "type": "scrape", "url": "https://www.anthropic.com/news",
              "weight": 0.98, "tags": ["official", "anthropic", "models"],
              "format": "blog", "category": "models"}
    entry = {"title": "Claude ships something", "url": "https://www.anthropic.com/news/x",
             "summary": "body", "date": "2026-08-20", "extra": {"format": "blog"}}
    out = local.parse_payload([entry], sc_cfg)[0]
    assert schema.validate_item(out) == []
    assert out["backend"] == "html"       
    assert out["source"] == "Anthropic News"
    assert out["weight"] == 0.98
    assert out["extra"]["format"] == "blog"
    assert out["extra"]["category"] == "models"
    assert out["extra"]["source_category"] == "models"
    assert out["extra"]["source_type"] == "scrape"


pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from qianliyan.cli import api_server  # noqa: E402
from qianliyan.cli import daily_digest_all as daily  # noqa: E402


@pytest.fixture
def synced_client(tmp_data_dir, monkeypatch):
    monkeypatch.delenv("QLY_API_KEY", raising=False)
    sync.run_sync(mock=True)
    return TestClient(api_server.create_app())


def test_get_personas_endpoint(synced_client):
    resp = synced_client.get("/personas")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list) and data
    assert all("handle" in p for p in data)


def test_get_personas_empty_list_when_missing(tmp_data_dir, monkeypatch):
    monkeypatch.delenv("QLY_API_KEY", raising=False)
    client = TestClient(api_server.create_app())
    resp = client.get("/personas")
    assert resp.status_code == 200
    assert resp.json() == []


def test_get_profile_endpoint(synced_client):
    resp = synced_client.get("/profile")
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body, dict)
    assert "profile" in body and "items" in body
    profile = body["profile"]
    for key in ("tags", "sources", "people", "mute"):
        assert key in profile, "读者画像应含维度 {0}".format(key)
    scores = [(it.get("extra") or {}).get("personal_score", 0.0) for it in body["items"]]
    assert scores == sorted(scores, reverse=True)


def test_get_items_sort_personal(synced_client):
    resp = synced_client.get("/items", params={"sort": "personal", "limit": 1000})
    assert resp.status_code == 200
    data = resp.json()
    assert data
    scores = [(it.get("extra") or {}).get("personal_score", 0.0) for it in data]
    assert scores == sorted(scores, reverse=True), "sort=personal 应按 personal_score 降序"


def test_post_history_appends_to_jsonl(synced_client, tmp_data_dir):
    resp = synced_client.post(
        "/history",
        json={"sig": "abc123", "action": "deepread", "title": "标题", "url": "https://x/y"},
    )
    assert resp.status_code == 200
    assert resp.json() == {"logged": True}

    rows = storage.read_jsonl(tmp_data_dir / "history.jsonl")
    assert rows
    last = rows[-1]
    assert last["sig"] == "abc123"
    assert last["action"] == "deepread"
    assert last["title"] == "标题"
    assert last["ts"]


def test_daily_endpoint_serves_glance_and_deep(synced_client, tmp_data_dir):
    date_str = daily._today()
    items = storage.read_jsonl(tmp_data_dir / "items.jsonl")[:6]
    assert daily._render_daily_html(date_str, items) == 0

    resp_deep = synced_client.get("/daily")
    assert resp_deep.status_code == 200
    assert "text/html" in resp_deep.headers["content-type"]
    assert len(resp_deep.text) > 100

    resp_glance = synced_client.get("/daily", params={"view": "glance"})
    assert resp_glance.status_code == 200
    assert len(resp_glance.text) > 100


def test_daily_endpoint_404_when_no_daily(tmp_data_dir, monkeypatch):
    monkeypatch.delenv("QLY_API_KEY", raising=False)
    client = TestClient(api_server.create_app())
    resp = client.get("/daily")
    assert resp.status_code == 404
