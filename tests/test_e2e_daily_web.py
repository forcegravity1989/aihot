"""test_e2e_daily_web.py —— 日报网站的端到端：真实链路 + 真实 HTTP + 真实读者动线。

刻意**不做细粒度单测**：这里一个用例覆盖一条读者走得通的路，而不是逐个私有函数断言
返回值。判据一律取「用户实际看到的东西」——落盘的 HTML、HTTP 响应体、页面里的链接是
不是真能点开——而不是内部数据结构长什么样。

链路：``sync.run_sync(mock=True)`` → ``cmd_prepare`` 选稿 → 编辑写短评 →
``cmd_finalize(do_html=True)`` 渲染 → ``TestClient`` 打真实端点 → 跟着页面里的链接走。

全程离线（``tmp_data_dir`` 置 ``QLY_OFFLINE=1``）。
"""

from __future__ import annotations

import re

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from qianliyan.cli import api_server, sync
from qianliyan.cli import daily_digest_all as daily
from qianliyan.core import paths, storage, utils

EDITOR_NOTE = "今日首选。它把 agent 写得快但流程没跟上这个痛点讲透了。"


@pytest.fixture(autouse=True)
def _force_offline(monkeypatch):
    monkeypatch.setenv("QLY_OFFLINE", "1")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("QLY_API_KEY", raising=False)


@pytest.fixture
def site(tmp_data_dir):
    """把整条链路跑完，返回 (TestClient, 日期)。

    走的是真实 CLI 入口——不预制假产物：sync 抓（mock）、prepare 选稿、编辑在草案里写
    短评与中文标题、finalize 渲染出全部页面。任何一环坏了这个 fixture 就起不来。
    """
    sync.run_sync(mock=True)
    date_str = utils.now_utc().strftime("%Y-%m-%d")
    assert daily.cmd_prepare(date_str) == 0, "选稿草案没生成"

    draft_path = paths.data_path("archive", date_str, daily.DRAFT_NAME)
    draft = storage.read_json(draft_path, default={})
    items = draft.get("items") or []
    assert items, "草案里没有候选条目"
    # 编辑（人或 Agent）在环：选前几条、写中文标题与选稿理由
    for idx, entry in enumerate(items):
        entry["selected"] = idx < 4
        if idx == 0:
            entry["title_zh"] = "编辑选中的头条"
            entry["editor_note"] = EDITOR_NOTE
    # 真实抓取里「只到天」和「时间未知」加起来占四成，mock 数据却碰巧条条都有真实时分——
    # 不把这两种塞进链路，时间轴那条断言就是空转的假绿灯（做过变异测试，确实抓不到回归）。
    assert len(items) >= 3, "候选太少，凑不出三种时间精度"
    items[0]["date"] = "{0}T09:30:00+00:00".format(date_str)
    items[0].setdefault("extra", {})["date_precision"] = "exact"
    items[1]["date"] = "{0}T00:00:00+00:00".format(date_str)
    items[1].setdefault("extra", {})["date_precision"] = "day"
    items[2]["date"] = "{0}T07:41:13+00:00".format(date_str)
    items[2].setdefault("extra", {})["date_precision"] = "unknown"
    storage.write_json(draft_path, draft)

    assert daily.cmd_finalize(date_str, do_html=True) == 0, "定稿渲染失败"
    return TestClient(api_server.create_app()), date_str


# =========================================================================
# 读者动线：首页 → 详情页 → 返回
# =========================================================================
def test_reader_can_walk_from_homepage_into_a_story_and_back(site):
    """读者进首页、点开一条、再点「返回日报」——这条路必须整条走得通。

    详情页的返回链接是相对路径 ``../daily.html``，走 HTTP 时会落到 ``/daily.html``；
    这条路由缺了的话点「返回」就是 404，而单测永远发现不了。
    """
    client, _ = site

    home = client.get("/daily")
    assert home.status_code == 200
    assert "千里眼" in home.text

    # 从首页真实抓一条详情页链接，而不是自己拼 sig
    hrefs = re.findall(r'href="(story/[A-Za-z0-9_.-]+\.html)"', home.text)
    assert hrefs, "首页上没有任何详情页链接"

    story = client.get("/" + hrefs[0])
    assert story.status_code == 200
    assert "← 返回日报" in story.text
    assert "打开原文" in story.text

    back = re.search(r'class="dt-back" href="([^"]+)"', story.text).group(1)
    assert back == "../daily.html"
    assert client.get("/daily.html").status_code == 200


def test_homepage_offers_three_views_without_javascript(site):
    """三视图切换必须是纯 CSS——这份 HTML 会被邮件客户端、聊天内嵌预览、文件面板打开，
    那些环境不执行脚本，切换是最基本的导航，不能一被沙箱就点不动。"""
    client, _ = site
    home = client.get("/daily").text

    for view in ("glance", "timeline", "deep"):
        assert 'id="qly-pick-{0}"'.format(view) in home
        assert 'for="qly-pick-{0}"'.format(view) in home
        assert "#qly-pick-{0}:checked ~ .qly-shell #wrap-{0}".format(view) in home
    # 切换控件是 label 不是 button+JS
    assert ".daily-toggle button" not in home
    assert 'data-view="deep"' not in home


def test_homepage_shows_the_full_three_column_chrome(site):
    """顶栏 + 左栏（往期归档 / 类目）+ 主列 + 右栏（热榜 / 信源分布 / 交叉验证说明）。"""
    client, date_str = site
    home = client.get("/daily").text

    assert 'class="qly-topbar"' in home
    assert 'class="qly-sidebar"' in home
    assert 'class="qly-rail"' in home
    assert "往期归档" in home and "is-current" in home
    assert date_str[5:].replace("-", "/") in home, "当天要出现在归档导航里"
    assert "热度榜" in home and "信源分布" in home and "交叉验证" in home


def test_editor_note_reaches_the_reader_in_every_view(site):
    """编辑写的选稿理由必须一路走到页面上。

    ``editor_note`` 是本项目「Agent 在环」的产出——选稿这种要判断力的活由编辑做。
    它一度被写进草案却在所有模板里丢掉：管线在生产「为什么今天选它」，页面一个字都不显示。
    """
    client, date_str = site

    assert EDITOR_NOTE in client.get("/daily").text
    for view in ("glance", "timeline", "deep"):
        page = client.get("/daily", params={"view": view})
        assert page.status_code == 200
        assert EDITOR_NOTE in page.text, "{0} 视图没渲染编辑短评".format(view)

    story = paths.data_path(daily.DETAIL_DIR)
    pages = [p.read_text(encoding="utf-8") for p in story.glob("*.html")]
    assert any(EDITOR_NOTE in p for p in pages), "详情页没渲染编辑短评"


def test_every_page_shares_one_design_system(site):
    """首页 / 三个单视图 / 详情页 / 简报页吃同一份 token，且不残留旧配色。"""
    client, _ = site

    texts = [client.get("/daily").text, client.get("/digest").text]
    texts += [client.get("/daily", params={"view": v}).text for v in ("glance", "timeline", "deep")]
    texts += [p.read_text(encoding="utf-8")
              for p in paths.data_path(daily.DETAIL_DIR).glob("*.html")][:1]

    for text in texts:
        assert "--theme-accent: #d97757" in text
        assert "prefers-color-scheme: dark" in text, "缺暗色适配"
        for legacy in ("--g-brand", "--t-brand", "#2f6df6", "#135e6b"):
            assert legacy not in text, "残留旧配色 {0}".format(legacy)
        # 产物里不许残留未渲染的模板标记，也不许引外部资源（单文件自包含）
        assert "{{" not in text and "{%" not in text
        assert re.search(r"<link[^>]+rel=[\"']?stylesheet", text) is None
        assert re.search(r"<script[^>]+\ssrc=", text) is None


def test_timeline_never_shows_a_fabricated_clock_time(site):
    """时间轴不许把「补出来的时刻」当真实发布时间显示。

    源缺 date 时管线会补当前时刻（一批条目全撞同一秒），只给到天的源一律 00:00。
    两种都不能画成精确到分钟的假时刻——真实抓取里这两种加起来占四成。
    """
    client, _ = site
    page = client.get("/daily", params={"view": "timeline"}).text

    times = re.findall(r'<div class="timeline-time">([^<]*)</div>', page)
    assert times, "时间轴没渲染出条目"
    for label in times:
        assert label in ("全天", "—") or re.fullmatch(r"\d{2}:\d{2}", label), label
        assert label != "00:00", "只到天的条目不该显示成 00:00"
    # 三种精度都必须真的出现在这条链路上，否则上面的循环是空转
    assert "全天" in times, "只到天的条目应显示「全天」"
    assert "—" in times, "时间未知的条目应不给时间"
    assert any(re.fullmatch(r"\d{2}:\d{2}", t) for t in times), "有真实时分的应显示到分"


def test_digest_surfaces_change_intelligence(site):
    """简报页要露出变更情报——版本变更卡此前算了就扔，只活在内存里。"""
    client, _ = site
    digest = client.get("/digest").text

    if "changelog" not in digest and 'id="board-change"' not in digest:
        pytest.skip("mock 数据里没有 changelog 条目")
    assert 'id="board-change"' in digest
    assert "变更情报" in digest


def test_story_route_refuses_path_traversal(site):
    """``/story/<sig>`` 是唯一把 URL 片段拼进文件路径的地方，不校验等于开放整个数据目录。"""
    client, _ = site
    paths.data_path("secret.html").write_text("不该被读到", encoding="utf-8")

    for evil in ("/story/../secret.html", "/story/..%2Fsecret.html", "/story/..%2F..%2Fetc%2Fpasswd"):
        resp = client.get(evil)
        assert resp.status_code != 200, evil
        assert "不该被读到" not in resp.text


def test_detail_pages_do_not_pollute_the_sync_items_directory(site):
    """详情页落 ``story/``，不能落 ``items/``——那是 sync 放各眼原始 jsonl 的地方。"""
    _, _ = site
    items_dir = paths.data_path("items")
    if items_dir.is_dir():
        assert not list(items_dir.glob("*.html")), "详情页写进了 items/"
    assert list(paths.data_path(daily.DETAIL_DIR).glob("*.html")), "story/ 下没有详情页"
