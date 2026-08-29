"""test_api.py —— 锁死 spec §10.3：FastAPI 端点、``X-API-Key`` 鉴权、feedback 落盘。

用 ``fastapi.testclient.TestClient`` 直接 import ``api_server.create_app()``（httpx 已装）。
所有用例基于 ``tmp_data_dir``（离线），大多先跑一次 ``sync.run_sync(mock=True)`` 铺好
真实数据契约（items.jsonl / channels.json / hotlist.md / digest.html / sync_meta.json）。
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from qianliyan.cli import api_server, sync
from qianliyan.core import storage


@pytest.fixture
def synced_client(tmp_data_dir, monkeypatch):
    """跑一次 mock sync 铺好完整数据契约，再构造不带鉴权的 TestClient。"""
    monkeypatch.delenv("QLY_API_KEY", raising=False)
    sync.run_sync(mock=True)
    app = api_server.create_app()
    return TestClient(app)


# =========================================================================
# GET /digest
# =========================================================================
def test_get_digest_returns_html(synced_client):
    resp = synced_client.get("/digest")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert len(resp.text) > 100


def test_get_digest_404_when_missing(tmp_data_dir, monkeypatch):
    monkeypatch.delenv("QLY_API_KEY", raising=False)
    client = TestClient(api_server.create_app())
    resp = client.get("/digest")
    assert resp.status_code == 404


# =========================================================================
# GET /items
# =========================================================================
def test_get_items_returns_json_list_with_urls(synced_client):
    resp = synced_client.get("/items")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list) and data
    assert all(it.get("url") for it in data), "每条 item 必带 URL（铁律 2）"


def test_get_items_limit(synced_client):
    resp = synced_client.get("/items", params={"limit": 2})
    assert resp.status_code == 200
    assert len(resp.json()) <= 2


def test_get_items_channel_filter_matches_channels_json(synced_client, tmp_data_dir):
    index = storage.read_json(tmp_data_dir / "channels.json", default={})
    channel_name = next((name for name, sigs in index.items() if sigs), None)
    assert channel_name, "预期 mock 数据下至少一个频道非空"

    resp = synced_client.get("/items", params={"channel": channel_name, "limit": 1000})
    assert resp.status_code == 200
    data = resp.json()
    assert {it["sig"] for it in data} == set(index[channel_name])


def test_get_items_unknown_channel_returns_empty(synced_client):
    resp = synced_client.get("/items", params={"channel": "no-such-channel"})
    assert resp.status_code == 200
    assert resp.json() == []


# =========================================================================
# GET /hotlist
# =========================================================================
def test_get_hotlist_returns_text(synced_client):
    resp = synced_client.get("/hotlist")
    assert resp.status_code == 200
    assert "全局热榜" in resp.text


def test_get_hotlist_empty_when_missing(tmp_data_dir, monkeypatch):
    monkeypatch.delenv("QLY_API_KEY", raising=False)
    client = TestClient(api_server.create_app())
    resp = client.get("/hotlist")
    assert resp.status_code == 200
    assert resp.text == ""


# =========================================================================
# GET /status
# =========================================================================
def test_get_status_returns_sync_meta(synced_client):
    resp = synced_client.get("/status")
    assert resp.status_code == 200
    data = resp.json()
    assert "run_id" in data
    assert "eyes" in data
    assert "totals" in data


def test_get_status_empty_dict_when_missing(tmp_data_dir, monkeypatch):
    monkeypatch.delenv("QLY_API_KEY", raising=False)
    client = TestClient(api_server.create_app())
    resp = client.get("/status")
    assert resp.status_code == 200
    assert resp.json() == {}


# =========================================================================
# POST /sync
# =========================================================================
def test_post_sync_returns_started_true_and_schedules_background_run(tmp_data_dir, monkeypatch):
    monkeypatch.delenv("QLY_API_KEY", raising=False)
    calls = []

    def _fake_run_sync(**kwargs):
        calls.append(kwargs)
        return {"run_id": "fake"}

    monkeypatch.setattr(sync, "run_sync", _fake_run_sync)
    client = TestClient(api_server.create_app())

    resp = client.post("/sync")
    assert resp.status_code == 200
    assert resp.json() == {"started": True}
    assert calls, "POST /sync 应触发 run_sync 后台任务"
    assert calls[0].get("quick") is True


# =========================================================================
# POST /feedback
# =========================================================================
def test_post_feedback_appends_to_jsonl(synced_client, tmp_data_dir):
    resp = synced_client.post("/feedback", json={"sig": "abc123", "action": "up", "note": "good"})
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}

    feedback_path = tmp_data_dir / "feedback.jsonl"
    rows = storage.read_jsonl(feedback_path)
    assert len(rows) == 1
    assert rows[0]["sig"] == "abc123"
    assert rows[0]["action"] == "up"
    assert rows[0]["note"] == "good"
    assert rows[0]["ts"]

    resp2 = synced_client.post("/feedback", json={"sig": "def456", "action": "hide"})
    assert resp2.status_code == 200
    assert len(storage.read_jsonl(feedback_path)) == 2


def test_post_feedback_rejects_invalid_action(synced_client):
    resp = synced_client.post("/feedback", json={"sig": "x", "action": "not-a-valid-action"})
    assert resp.status_code == 400


# =========================================================================
# 鉴权（QLY_API_KEY）
# =========================================================================
def test_api_key_rejects_missing_or_wrong_key(tmp_data_dir, monkeypatch):
    monkeypatch.setenv("QLY_API_KEY", "secret-123")
    client = TestClient(api_server.create_app())

    resp_missing = client.get("/status")
    assert resp_missing.status_code == 401

    resp_wrong = client.get("/status", headers={"X-API-Key": "wrong"})
    assert resp_wrong.status_code == 401


def test_api_key_accepts_matching_key(tmp_data_dir, monkeypatch):
    monkeypatch.setenv("QLY_API_KEY", "secret-123")
    client = TestClient(api_server.create_app())
    resp = client.get("/status", headers={"X-API-Key": "secret-123"})
    assert resp.status_code == 200


def test_no_api_key_env_allows_unauthenticated_access(tmp_data_dir, monkeypatch):
    monkeypatch.delenv("QLY_API_KEY", raising=False)
    client = TestClient(api_server.create_app())
    resp = client.get("/status")
    assert resp.status_code == 200


# =========================================================================
# 可选依赖缺失时的降级（spec §10.3）
# =========================================================================
def test_main_without_fastapi_prints_install_hint_and_returns_nonzero(monkeypatch, capsys):
    monkeypatch.setattr(api_server, "FASTAPI_AVAILABLE", False)
    rc = api_server.main([])
    assert rc == 1
    captured = capsys.readouterr()
    assert "pip install" in captured.out


# =========================================================================
# GET /daily · /daily.html · /story/{sig}（v0.3 版面重构）
# =========================================================================
def _write_daily_products(date_str="2026-08-25"):
    """铺一份最小日报产物：合并首页 + 单视图页 + 一个详情页。"""
    from qianliyan.core import paths

    paths.data_path(api_server.DAILY_ROOT_NAME).write_text("<html>合并首页</html>", encoding="utf-8")
    detail = paths.data_path(api_server.DETAIL_DIR, "abc123.html")
    detail.parent.mkdir(parents=True, exist_ok=True)
    detail.write_text("<html>详情页正文</html>", encoding="utf-8")


def test_get_daily_defaults_to_merged_homepage(tmp_data_dir, monkeypatch):
    """不给 view 时给的是三视图合并首页，而不是某一个单视图页。"""
    monkeypatch.delenv("QLY_API_KEY", raising=False)
    _write_daily_products()
    client = TestClient(api_server.create_app())
    resp = client.get("/daily")
    assert resp.status_code == 200
    assert "合并首页" in resp.text


def test_daily_html_alias_exists_for_detail_page_back_link(tmp_data_dir, monkeypatch):
    """详情页的返回链接是相对路径 ../daily.html——走 HTTP 时必须有这条路由兜住，
    否则从详情页点「返回日报」就是 404。"""
    monkeypatch.delenv("QLY_API_KEY", raising=False)
    _write_daily_products()
    client = TestClient(api_server.create_app())
    assert client.get("/daily.html").status_code == 200


def test_get_detail_page(tmp_data_dir, monkeypatch):
    """``/story/<sig>`` 与 ``/story/<sig>.html`` 都能取到同一页。"""
    monkeypatch.delenv("QLY_API_KEY", raising=False)
    _write_daily_products()
    client = TestClient(api_server.create_app())
    for path in ("/story/abc123", "/story/abc123.html"):
        resp = client.get(path)
        assert resp.status_code == 200, path
        assert "详情页正文" in resp.text


def test_get_detail_page_404_when_missing(tmp_data_dir, monkeypatch):
    monkeypatch.delenv("QLY_API_KEY", raising=False)
    _write_daily_products()
    client = TestClient(api_server.create_app())
    assert client.get("/story/nope").status_code == 404


def test_detail_page_rejects_path_traversal(tmp_data_dir, monkeypatch):
    """这是唯一一处把 URL 片段拼进文件路径的地方——不校验就等于开放整个数据目录。"""
    monkeypatch.delenv("QLY_API_KEY", raising=False)
    _write_daily_products()
    from qianliyan.core import paths

    paths.data_path("secret.html").write_text("不该被读到", encoding="utf-8")
    client = TestClient(api_server.create_app())
    for evil in ("/story/..%2Fsecret.html", "/story/../secret.html", "/story/..%2F..%2Fetc%2Fpasswd"):
        resp = client.get(evil)
        assert resp.status_code != 200, evil
        assert "不该被读到" not in resp.text


def test_daily_view_picks_the_matching_archive_page(tmp_data_dir, monkeypatch):
    """有当日归档产物时，``view`` 要选中对应那一页；缺产物才回退根 daily.html。

    回退逻辑很容易掩盖 view 路由本身失灵——三个 view 都回退到同一个文件时，
    肉眼看响应大小是一样的，分不出是"选对了"还是"根本没选"。
    """
    from qianliyan.core import paths, utils

    monkeypatch.delenv("QLY_API_KEY", raising=False)
    today = utils.now_utc().strftime("%Y-%m-%d")
    for name, body in (
        (api_server.DAILY_MERGED_NAME, "合并首页"),
        ("glance.html", "日报单视图"),
        ("timeline.html", "时间轴单视图"),
        ("deep.html", "深读单视图"),
    ):
        paths.data_path("archive", today, name).write_text(
            "<html>{0}</html>".format(body), encoding="utf-8")

    client = TestClient(api_server.create_app())
    assert "合并首页" in client.get("/daily").text
    assert "日报单视图" in client.get("/daily", params={"view": "glance"}).text
    assert "时间轴单视图" in client.get("/daily", params={"view": "timeline"}).text
    assert "深读单视图" in client.get("/daily", params={"view": "deep"}).text
    # 未知 view 不该 500，落回合并首页
    assert "合并首页" in client.get("/daily", params={"view": "nope"}).text
