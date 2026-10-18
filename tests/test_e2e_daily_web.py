"""test_e2e_daily_web.py —— 日报网站的端到端：真实链路 + 真实 HTTP + 真实读者动线。

刻意**不做细粒度单测**：这里一个用例覆盖一条读者走得通的路，而不是逐个私有函数断言
返回值。判据一律取「用户实际看到的东西」——落盘的 HTML、HTTP 响应体、页面里的链接是
不是真能点开——而不是内部数据结构长什么样。

链路：``sync.run_sync(mock=True)`` → ``cmd_prepare`` 选稿 → 编辑写短评 →
``cmd_finalize(do_html=True)`` 渲染 → ``TestClient`` 打真实端点 → 跟着页面里的链接走。

全程离线（``tmp_data_dir`` 置 ``QLY_OFFLINE=1``）。
"""

from __future__ import annotations

import html
import json
import os
import re
import urllib.parse

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
        if idx < 4:
            entry["editor_rank"] = idx + 1
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


def test_homepage_is_three_parts_from_shallow_to_deep(site):
    """首页 = ① 看图知今天 → ② N 秒读完今天 → ③ 正文，按读者肯花的时间从少到多排（ADR D0）。

    不再有视图 tab 和右栏：三个部分本身就是三层入口。② 默认展开（不是 <details>），每行标深浅档、
    带「看过即接收」的勾；① 只放头条与深读的图位，浅读不进图区。
    """
    client, date_str = site
    home = client.get("/daily").text
    speed, brief, body = home.find('id="part-speed"'), home.find('id="part-brief"'), home.find('id="part-body"')
    assert 0 < speed < brief < body, "三个部分的顺序错了"
    assert body < home.find('id="wrap-glance"'), "正文在第三部分里"
    for gone in ("qly-pick-", "wrap-timeline", "wrap-deep", 'class="qly-rail"', "qly-tabs"):
        assert gone not in home, "旧的 tab / 右栏还在：{0}".format(gone)
    brief_html = home[brief:body]
    assert "<details" not in brief_html, "速读是导入层，默认展开"
    assert re.search(r"\d+ 秒读完今天", brief_html), "秒数按字数算，不写死 30"

    final = storage.read_json(paths.data_path("archive", date_str, daily.FINAL_NAME), default={})
    assert final["items"]
    for entry in final["items"]:
        sig, depth = entry["sig"], entry.get("depth")
        assert depth in ("lead", "deep", "glance")
        row = re.search(r'<li class="rb-row is-{0}" data-sig="{1}" data-depth="{0}">.*?</li>'.format(depth, sig),
                        brief_html, re.S)
        assert row, "速读里少了一条或没标深浅：{0}".format(sig)
        assert 'class="gread rb-check" data-sig="{0}"'.format(sig) in row.group(0), "速读行没有接收勾"
        assert ('data-sig="{0}"'.format(sig) in home[speed:brief]) == (depth != "glance"), "图区只放头条与深读"
    assert 'href="#part-speed"' in home and 'href="#part-brief"' in home and 'href="#part-body"' in home


def test_sidebar_is_a_table_of_contents_for_this_issue(site):
    """左栏是「本期导读」：每条正文的标题都在目录里、点了能落到那一条；关注开关在侧栏里
    （模型发布必看、不给开关）；往期压成一行。页首第二部分是「N 秒读完今天」：每条一句话、能跳。

    旧版左栏是一串往期日期 + 「时间轴天数 / 重磅 0」这类统计，右栏热度榜与版面顺序重复——
    都不帮读者找到自己要读的东西（issue #51）。
    """
    client, date_str = site
    home = client.get("/daily").text
    draft = storage.read_json(paths.data_path("archive", date_str, daily.DRAFT_NAME), default={})
    picked = [e for e in draft["items"] if e.get("selected")]
    assert picked

    sidebar = home.split('<aside class="qly-sidebar"', 1)[1].split("</aside>", 1)[0]
    brief = home.split('id="part-brief"', 1)[1].split('id="part-body"', 1)[0]
    glance = home.split('id="wrap-glance"', 1)[1].split("</main>", 1)[0]
    for entry in picked:
        sig, title = entry["sig"], daily._display_title(entry)
        assert 'href="#e-{0}"'.format(sig) in sidebar and title in sidebar, "目录里少了一条：{0}".format(title)
        assert 'id="e-{0}"'.format(sig) in glance, "目录链接落不到日报页上的那一条"
        assert 'href="#e-{0}"'.format(sig) in brief, "速读里少了一条"
    assert re.search(r"\d+ 秒读完今天", brief)
    assert 'data-follow="agent"' in sidebar and 'data-follow="models"' not in sidebar
    assert "往期" in sidebar and date_str[5:].replace("-", "/") in sidebar, "当天要出现在往期条里"
    assert "热度榜" not in home and "信源分布" not in home and "时间轴天数" not in home


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


# =========================================================================
# 铁律：这些跟版面长什么样无关，只跟「不能变成什么样」有关
# =========================================================================
def test_runtime_data_never_lands_inside_the_repo(site, tmp_data_dir):
    """数据与代码隔离——运行时产物必须全部落在 QLY_DATA_DIR，结构性地在仓库之外。

    这条破了，某次 git add 就会把 items.jsonl、digest.html、channels/*.md 提进仓库。
    判据是「跑完整条链路后，仓库树里有没有多出运行时产物」，不是去问 paths 模块。
    """
    from pathlib import Path

    _, _ = site
    repo = Path(__file__).resolve().parent.parent

    # 链路产物确实落在数据目录
    for name in ("items.jsonl", "digest.html", "daily.html", "hotlist.md", "sync_meta.json"):
        assert (tmp_data_dir / name).exists(), "{0} 没落在数据目录".format(name)
    assert (tmp_data_dir / "archive").is_dir()

    # 仓库里不许出现同名运行时产物（fixtures 下的样本除外）
    for pattern in ("items.jsonl", "daily.html", "hotlist.md", "sync_meta.json"):
        strays = [p for p in repo.rglob(pattern)
                  if "fixtures" not in p.parts and ".venv" not in p.parts
                  and ".git" not in p.parts and "worktrees" not in p.parts]
        assert not strays, "运行时产物漏进仓库: {0}".format(strays)


def test_offline_mode_lets_the_whole_pipeline_finish_without_network(site):
    """QLY_OFFLINE=1 下整条链路必须跑完并出页面——LLM 与正文抓取是增强项不是依赖项。

    这个 fixture 全程 QLY_OFFLINE=1 且没有 API key，它能起来本身就是断言：
    深读提炼走了规则回退，没有因为网络不可用而阻断主链路；回退时页面照样有内容可读。
    """
    client, date_str = site
    deep = client.get("/daily", params={"view": "deep"})
    assert deep.status_code == 200
    glance = client.get("/daily", params={"view": "glance"}).text
    # 回退时每条都得有能读的内容（摘要顶上），而不是一格格「—」的空壳：
    # 规则回退的「四段提炼」只是原文前三句 + 三个破折号，曾把英文原句和网页报错文字当成要点。
    # 深读 tab 只列头条与深读（ADR D1），其余条目的内容在日报页上看
    final = storage.read_json(paths.data_path("archive", date_str, daily.FINAL_NAME), default={})
    for entry in final["items"]:
        summary = daily._summary_text(entry)
        if summary:
            assert summary[:20] in glance, "离线回退下条目没有内容：{0}".format(entry.get("title"))
            if entry.get("depth") != "glance":
                assert summary[:20] in deep.text, "深读页上头条/深读没有内容：{0}".format(entry.get("title"))
    assert '<span class="dsec-k">脉络</span><p>—</p>' not in deep.text, "深读页留着没有内容的提炼空壳"


def test_every_item_on_every_page_carries_a_traceable_url(site):
    """铁律 2：每条必带 URL，可溯源是底线。"""
    client, _ = site
    from qianliyan.core import storage

    pool = list(storage.read_jsonl(paths.data_path("items.jsonl")))
    assert pool, "池是空的"
    assert all(str(it.get("url") or "").strip() for it in pool), "有条目没有 URL"

    digest = client.get("/digest").text
    missing = [it["url"] for it in pool[:40] if it["url"] not in digest]
    assert not missing, "简报页漏了这些条目的原文链接: {0}".format(missing[:3])


def test_titles_are_escaped_so_a_hostile_feed_cannot_inject_markup(site):
    """信源标题是外部输入，必须转义——否则一条挂着 <script> 的 RSS 就能注进页面。"""
    from qianliyan.core import storage
    from qianliyan.pipeline import report

    pool = list(storage.read_jsonl(paths.data_path("items.jsonl")))
    pool[0]["title"] = '<script>alert("xss")</script>恶意标题'
    html = report.render_html(pool, out_path=False)

    assert '<script>alert("xss")</script>' not in html
    assert "&lt;script&gt;" in html, "标题没被转义"


def test_hot_ranking_reflects_freshness_and_cross_validation(tmp_data_dir):
    """热度排序是产品的核心判断，必须一路走到读者看到的榜单上。

    走真实打分器 ``dedup_and_score`` + 真实渲染，判据取**页面上的先后顺序**而不是
    hotness 的具体数值——公式可以调，"新的压过旧的""多源压过单源"这两条不能反。
    """
    from qianliyan.core import schema, storage, utils
    from qianliyan.pipeline import report

    now = utils.now_utc()

    def item(title, days_ago, sources, weight=0.9):
        made = schema.make_item(
            title=title, url="https://example.com/{0}".format(title),
            source=sources[0], source_kind="local", backend="rss", weight=weight,
            date=utils.iso(now - __import__("datetime").timedelta(days=days_ago)),
        )
        made["source_list"] = list(sources)
        made["cross_refs"] = len(sources) - 1
        return made

    pool = utils.dedup_and_score([
        # 陈旧但当初权重很高 vs 新鲜但权重平庸——衰减**量级**不够的话，上周的旧闻会一直
        # 压在今天的新闻上面。只比"新的排在旧的前面"是分辨不出半衰期被改坏的。
        item("上周旧闻", 30, ["A"], weight=0.99),
        item("今天新闻", 0, ["A"], weight=0.50),
        item("今天多源", 0, ["A", "B", "C", "D"], weight=0.50),
    ], now)
    storage.write_jsonl(paths.data_path("items.jsonl"), pool)
    html = report.render_html(pool, out_path=False)

    order = [html.index(t) for t in ("今天多源", "今天新闻", "上周旧闻")]
    assert order == sorted(order), "热榜顺序应是 今天多源 > 今天新闻 > 上周旧闻"


def test_offline_run_never_opens_a_socket(tmp_data_dir, monkeypatch):
    """``QLY_OFFLINE=1`` 下跑完整条链路，一个字节都不许出网。

    这不是靠"mock 数据碰巧不联网"来成立的：链路里深读增强会对摘要过短的条目去抓正文，
    真的会走到 engine.http。装一个 socket 哨兵，出网就当场炸——封网开关一旦失效，
    这个用例立刻红。
    """
    import socket

    monkeypatch.setenv("QLY_OFFLINE", "1")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    opened = []

    def _guard(self, *args, **kwargs):
        opened.append(args[0] if args else "?")
        raise AssertionError("离线模式下尝试出网: {0}".format(args[:1]))

    monkeypatch.setattr(socket.socket, "connect", _guard)
    monkeypatch.setattr(socket.socket, "connect_ex", _guard)

    sync.run_sync(mock=True)
    date_str = utils.now_utc().strftime("%Y-%m-%d")
    assert daily.cmd_prepare(date_str) == 0

    draft_path = paths.data_path("archive", date_str, daily.DRAFT_NAME)
    draft = storage.read_json(draft_path, default={})
    items = draft.get("items") or []
    for idx, entry in enumerate(items):
        entry["selected"] = idx < 3
        entry["summary"] = "很短"          # 逼深读增强去抓正文，真的走到 engine.http
    storage.write_json(draft_path, draft)

    assert daily.cmd_finalize(date_str, do_html=True) == 0, "离线下整条链路应跑完"
    assert not opened, "离线模式漏出网了: {0}".format(opened)


def test_one_source_cannot_monopolize_a_channel(tmp_data_dir):
    """频道内单源席位上限：有得选时不许一家独占，没得选时也不许把频道饿空。

    真实数据上量过：15 个非空频道里 7 个的 top10 被同一个源占满 9~10 席——practices 全是
    Anthropic Engineering、talks 全是 OpenAI YouTube、change-intel 全是系统提示词。新加的
    信源更是一席都排不进去,抓回来也看不见。所以上限必须是**软**的：铺不满 limit 时按热度
    回填,否则 trending（只有 daily/weekly 两个源）这类频道会直接空掉。
    """
    from qianliyan.core import schema, utils
    from qianliyan.pipeline import channels as C

    def item(source, title, hotness):
        made = schema.make_item(
            title=title, url="https://example.com/{0}".format(title.replace(" ", "-")),
            source=source, source_kind="local", backend="rss", weight=0.9,
            date=utils.iso(utils.now_utc()), tags=["models"],
        )
        made["hotness"] = hotness
        return made

    # 一个源霸榜（热度最高的 10 条全是它），另有 3 个源各 1 条
    pool = [item("Loud Source", "loud {0}".format(i), 0.99 - i * 0.001) for i in range(10)]
    pool += [item(name, "quiet " + name, 0.5) for name in ("Alt A", "Alt B", "Alt C")]

    channel = {"name": "t", "title": "t", "limit": 30, "max_per_source": 3,
               "match": {"tags_any": ["models"]}}
    routed = C.route(pool, [channel])["t"]

    top = [str(i.get("source")) for i in routed[:6]]
    assert top.count("Loud Source") <= 3, "单源占了 {0} 席，上限没生效".format(top.count("Loud Source"))
    assert {"Alt A", "Alt B", "Alt C"} <= set(top), "被压下的其它源没能进前排"
    # 软上限：总条数不变，超出上限的仍按热度回填在后面
    assert len(routed) == len(pool), "软上限不该让频道少收条目"

    # 只有一个源的频道不许被饿空
    single = C.route(pool[:10], [channel])["t"]
    assert len(single) == 10, "频道只有一个源时，上限不该把它砍掉"


def test_archive_nav_keeps_past_issues_when_recent_days_only_have_drafts(site):
    """往期归档不许被「只有草案、没定稿」的日子挤掉。

    真实发生过：定时任务每天只备草案、连续 19 天没人定稿，侧栏 14 天窗口被这些空目录占满，
    之前所有出过的日报从导航里消失，读者只看得到当天。
    """
    import shutil

    client, date_str = site
    archive = paths.data_path("archive")
    # 一期很早以前真出过的日报
    shutil.copytree(archive / date_str, archive / "2000-01-01")
    # 之后 20 天只有草案
    for day in range(1, 21):
        stub = archive / "2000-02-{0:02d}".format(day)
        stub.mkdir()
        (stub / daily.DRAFT_NAME).write_text('{"items": []}', encoding="utf-8")

    assert daily.cmd_html_only(date_str) == 0
    home = client.get("/daily").text
    assert "2000-01-01/digest.html" in home, "出过的日报被只有草案的日子挤出了往期归档"
    assert "2000-02-01" not in home, "没定稿的日子不该出现在往期归档"


def test_an_undated_article_does_not_stay_fresh_forever(tmp_data_dir, monkeypatch):
    """源不给日期的旧文，第二天再抓到时不许又变成「刚发布」。

    真实发生过：Anthropic News 的 8 月旧文、两百多个老版本的提示词变更，每轮 sync 都被补成
    当前时刻，永远 0 小时前、热度 0.98，把真正的新闻挤出候选池前排。
    """
    from datetime import timedelta

    from qianliyan.core import schema

    fixture = tmp_data_dir / "fixture.jsonl"
    monkeypatch.setattr(sync, "_mock_fixture_path", lambda: fixture)
    base = utils.now_utc()
    # 两种「没日期」都真实存在：None 在建条目时被补成抓取时刻；空串一路留到打分才被当成 now
    urls = {"none": "https://example.com/news/an-old-post", "empty": "https://example.com/news/another"}

    def crawl(at):
        monkeypatch.setattr(utils, "now_utc", lambda: at)
        rows = [
            schema.make_item(
                title="Old post " + kind, url=url, source="Scraped Blog",
                source_kind="local", backend="scrape", weight=0.98,
                date=None if kind == "none" else "",
            )
            for kind, url in urls.items()
        ]
        storage.write_jsonl(fixture, rows)
        sync.run_sync(eyes=["local"], mock=True, no_html=True, quick=True)
        pool = {it["url"]: it for it in storage.read_jsonl(paths.data_path("items.jsonl"))}
        return {kind: pool[url] for kind, url in urls.items()}

    first = crawl(base)
    later = crawl(base + timedelta(days=3))

    for kind in urls:
        assert later[kind]["date"] == first[kind]["date"], "没日期的旧文（{0}）被重新盖上了新的抓取时间".format(kind)
        assert later[kind]["hotness"] < first[kind]["hotness"], "三天后再见到它（{0}），热度不该还和第一次一样".format(kind)


def test_one_launch_takes_one_seat_and_its_other_reports_stay_reachable(tmp_data_dir, monkeypatch):
    """同一次发布的多条报道，在选稿草案里只占一个席位；其余报道留在详情页可点。

    真实发生过：GPT-6 Sol 发布在 09-23 的候选池里占了 5 席（其中两条标题只差「较/比」一个字），
    编辑要自己认出它们是一回事。但也不能合过头——点评、下游产品更新、同系列的另一款模型
    都是别的新闻。
    """
    from qianliyan.core import schema

    now = utils.iso(utils.now_utc())

    def row(title, source, kind="aihot", weight=0.85):
        return schema.make_item(
            title=title, url="https://example.com/" + str(abs(hash(title))),
            source=source, source_kind=kind, backend="rss", weight=weight, date=now,
            tags=["models"],
        )

    launch = [
        row("Introducing GPT‑6 Sol and Luna", "OpenAI News", kind="local", weight=0.95),
        row("OpenAI 发布 GPT-6 Sol 和 GPT-6 Luna，API 价格较 GPT-5.6 促销价低 50%", "AIHOT"),
        row("OpenAI 发布 GPT-6 Sol 和 GPT-6 Luna，API 价格比 GPT-5.6 促销价低 50%", "AIHOT"),
        row("OpenAI GPT-6 Sol 和 GPT-6 Luna 上线 OpenRouter", "AIHOT"),
    ]
    others = [
        row("Sam Altman 称 GPT-6 Sol 和 Luna 按任务定价在市场上没有对手", "AIHOT"),
        row("Claude Opus 5.5 发布：较 Opus 5 降价提速", "AIHOT"),
        row("Claude Code v2.1.280 发布，新增 Claude Opus 5.5 为默认 Opus 模型", "AIHOT"),
        row("Qwen 发布 Qwen3.8-LiveTranslate 实时同传模型", "AIHOT"),
        row("Qwen 发布原生全模态模型 Qwen3.8-Omni-Flash", "AIHOT"),
        # 字面几乎一样、数字不同：两次不同的变更，不是同一条快讯的两次转述
        row("Claude Code 2.1.261 提示词变更 · +1,296 tokens · 9 项", "Claude Code 系统提示词", kind="local"),
        row("Claude Code 2.1.64 提示词变更 · +1,291 tokens · 9 项", "Claude Code 系统提示词", kind="local"),
    ]
    fixture = tmp_data_dir / "fixture.jsonl"
    storage.write_jsonl(fixture, launch + others)
    monkeypatch.setattr(sync, "_mock_fixture_path", lambda: fixture)

    sync.run_sync(eyes=["aihot", "local"], mock=True, no_html=True)
    date_str = utils.now_utc().strftime("%Y-%m-%d")
    assert daily.cmd_prepare(date_str) == 0
    draft_path = paths.data_path("archive", date_str, daily.DRAFT_NAME)
    draft = storage.read_json(draft_path, default={})
    titles = [e["title"] for e in draft["items"]]

    seats = [t for t in titles if t in {r["title"] for r in launch}]
    assert len(seats) == 1, "同一次发布占了 {0} 个席位：{1}".format(len(seats), seats)
    for other in others:
        assert other["title"] in titles, "不是同一件事却被并掉了：{0}".format(other["title"])

    head = next(e for e in draft["items"] if e["title"] in seats)
    assert {"OpenAI News", "AIHOT"} <= set(head["source_list"]), "被并掉的报道没有计入来源"

    for entry in draft["items"]:
        entry["selected"] = entry is head
    storage.write_json(draft_path, draft)
    assert daily.cmd_finalize(date_str, do_html=True) == 0

    client = TestClient(api_server.create_app())
    story = client.get("/story/{0}.html".format(head["sig"])).text
    assert "同一事件的其它报道" in story
    assert "上线 OpenRouter" in story, "被并掉的报道在详情页上找不到了"


def test_the_editors_lead_story_leads_the_page(tmp_data_dir):
    """编辑定的头条必须排在页面最前——不管它属于哪个方向、发布得早还是晚；同一栏里按编辑排序。

    真实发生过：OpenAI 官方的 GPT-6 Sol 公告属于「博客」格式，版面固定先排「资讯」区、区内按
    时间排，头条于是成了全页第 7 条，压在一条安全漏洞后面。
    """
    sync.run_sync(mock=True)
    date_str = utils.now_utc().strftime("%Y-%m-%d")
    assert daily.cmd_prepare(date_str) == 0
    draft_path = paths.data_path("archive", date_str, daily.DRAFT_NAME)
    draft = storage.read_json(draft_path, default={})
    # 挑跨格式的条目：同一格式里排序本来就可能碰巧对，跨区才考得出分区顺序
    picked, seen = [], set()
    for entry in draft["items"]:
        fmt = daily.infer_format(entry)
        if fmt not in seen or len(picked) >= 3:
            picked.append(entry)
            seen.add(fmt)
        if len(picked) == 5:
            break
    assert len(seen) >= 2, "候选里凑不出两种格式，断言会空转"
    # 编辑把默认版面里最靠后的那条定为头条：倒着排
    for rank, entry in enumerate(reversed(picked), start=1):
        entry["selected"] = True
        entry["editor_rank"] = rank
        entry["title_zh"] = "编辑排序第{0}条".format(rank)
    storage.write_json(draft_path, draft)
    assert daily.cmd_finalize(date_str, do_html=True) == 0

    # 版面按方向分栏（issue #49）：头条单独在最前；同一栏里按编辑的排序
    from qianliyan.pipeline import tracks

    cfg = tracks.load()
    track_of = {"编辑排序第{0}条".format(rank): tracks.track_of(entry, cfg)
                for rank, entry in enumerate(reversed(picked), start=1)}
    client = TestClient(api_server.create_app())
    for view in ("glance", None):
        page = client.get("/daily", params={"view": view} if view else {}).text
        body = page.split('id="wrap-glance"', 1)[-1]
        positions = {t: body.find(t) for t in track_of}
        assert all(p >= 0 for p in positions.values()), "有选中的条目没上版面"
        assert positions["编辑排序第1条"] == min(positions.values()), "编辑定的头条没排在最前（{0}）".format(view)
        for tid in set(track_of.values()):
            ranked = [positions[t] for t in sorted(track_of, key=lambda t: int(re.search(r"\d+", t).group()))
                      if track_of[t] == tid and t != "编辑排序第1条"]
            assert ranked == sorted(ranked), "{0} 栏内没按编辑排序（{1}）".format(tid, view)


# =========================================================================
# 定时任务里没人值守：编辑 Agent 选稿 → 定稿 → 读者看到当天日报
# =========================================================================
FAKE_EDITOR = r'''
import json, re, sys
prompt = sys.stdin.read()
sigs = re.findall(r"^=== sig: (\S+)", prompt, re.M)
if sigs:  # 写要点的简报
    open(sys.argv[1] + ".brief", "w", encoding="utf-8").write(prompt)
    print(json.dumps({"briefs": [{"sig": s, "brief": ["要点甲·" + s, "要点乙·" + s, "要点丙·" + s]} for s in sigs]}))
    sys.exit(0)
open(sys.argv[1], "w", encoding="utf-8").write(prompt)
ids = [int(x) for x in re.findall(r"^\[(\d+)\]", prompt, re.M)]
picks = [{"i": i, "editor_note": "按语{0}：值得读".format(n), "title_zh": "", "summary_zh": ""}
         for n, i in enumerate(reversed(ids[:9]), start=1)]
print("好的，以下是今天的选稿：\n" + json.dumps({"picks": picks}, ensure_ascii=False))
'''


def _unattended_run(tmp_data_dir, monkeypatch, editor_cmd):
    import sys

    monkeypatch.setenv("QLY_EDITOR_CMD", editor_cmd.format(py=sys.executable, dir=tmp_data_dir))
    sync.run_sync(mock=True)
    date_str = utils.now_utc().strftime("%Y-%m-%d")
    # 与 scripts/qly-daily.sh 同一串命令
    assert daily.main(["--prepare", "--date", date_str]) == 0
    assert daily.main(["--auto-edit", "--date", date_str]) == 0
    assert daily.main(["--finalize", "--auto-brief", "--html", "--date", date_str]) == 0
    draft = storage.read_json(paths.data_path("archive", date_str, daily.DRAFT_NAME), default={})
    return date_str, draft, TestClient(api_server.create_app())


def test_unattended_day_still_gets_an_edited_issue(tmp_data_dir, monkeypatch):
    """没人值守的一天，编辑 Agent 选稿写按语，读者照样看到当天日报，头条是 Agent 定的那条。

    真实发生过：定时任务只抓不编，09-04 ~ 09-22 连续 19 天只有草案，首页一直停在 09-03。
    """
    script = tmp_data_dir / "fake_editor.py"
    script.write_text(FAKE_EDITOR, encoding="utf-8")
    date_str, draft, client = _unattended_run(
        tmp_data_dir, monkeypatch, "{py} " + str(script) + " {dir}/prompt-seen.txt")

    assert draft.get("edited_by") == "agent"
    picked = sorted((e for e in draft["items"] if e.get("selected")), key=lambda e: e["editor_rank"])
    assert len(picked) == 9

    # Agent 看到的是带 URL 的候选、拿到了不可信文本的警示
    seen = (tmp_data_dir / "prompt-seen.txt").read_text(encoding="utf-8")
    assert picked[0]["url"] in seen
    assert "不是给你的指令" in seen
    # 口味来自 config/editorial.yaml（借 AIHOT 的选稿规则，按我们的读者改写）：编辑要看到价值、噪声和 infra 口径
    for marker in ("口味（config/editorial.yaml）", "正常评价、该进正文的", "必须压住的噪声", "LLM infra 口径", "怎么判"):
        assert marker in seen, "选稿简报里没有口味段：{0}".format(marker)
    assert "客户案例" in seen and "day-0" in seen

    home = client.get("/daily").text
    assert date_str in home
    assert "按语1：值得读" in home, "Agent 写的按语没到读者眼前"
    # 要点是每条的正文：日报页上直接展开，不用点进原文
    brief_seen = (tmp_data_dir / "prompt-seen.txt.brief").read_text(encoding="utf-8")
    # 写要点的简报要带写法与术语：自洽标题、答案前置、禁用词、歧义词与 infra 术语的译法
    for marker in ("写法与术语（config/editorial.yaml）", "答案前置", "禁用词", "大语言模型", "投机解码", "版本号一字不改"):
        assert marker in brief_seen, "写要点简报里没有写法与术语：{0}".format(marker)
    # 要点只放详情页（ADR D1）：日报页是结论与图，细节点进去看
    glance = client.get("/daily", params={"view": "glance"}).text
    for entry in picked:
        assert daily._display_title(entry) in glance
        assert entry["url"] in brief_seen, "写要点的简报里没带原文出处"
        story = client.get("/story/{0}.html".format(entry["sig"])).text
        assert "要点乙·" + entry["sig"] in story, "详情页没有要点"
    assert "密度档：" in brief_seen, "写要点的简报要告诉编辑每条是头条 / 深读 / 浅读"
    assert "事件主体" in brief_seen, "一句话结论会单独列在速读里，简报要让编辑把主体写进去"
    body = home.split("</header>", 1)[-1]
    first = body.find(daily._display_title(picked[0]))
    others = [body.find(daily._display_title(e)) for e in picked[1:]]
    assert 0 <= first < min(p for p in others if p >= 0), "Agent 定的头条没排在最前"

    # 同一天再跑一次抓取+prepare（手动刷新、launchd 补跑），编辑的活不许被抹掉
    sync.run_sync(mock=True)
    assert daily.main(["--prepare", "--date", date_str]) == 0
    again = storage.read_json(paths.data_path("archive", date_str, daily.DRAFT_NAME), default={})
    notes = {e["sig"]: e.get("editor_note") for e in again["items"] if e.get("selected")}
    assert notes == {e["sig"]: e["editor_note"] for e in picked}, "重跑 prepare 抹掉了编辑选稿"
    # 再跑 auto-edit（这回 Agent 坏了）：已有选稿就不许重选，否则会被规则回退顶掉
    import sys
    monkeypatch.setenv("QLY_EDITOR_CMD", "{0} -c 'import sys; sys.exit(3)'".format(sys.executable))
    assert daily.main(["--auto-edit", "--date", date_str]) == 0
    after = storage.read_json(paths.data_path("archive", date_str, daily.DRAFT_NAME), default={})
    assert after.get("edited_by") == "agent", "已有选稿时 auto-edit 不该重选"
    assert {e["sig"] for e in after["items"] if e.get("selected")} == set(notes)


def test_unattended_day_publishes_even_when_the_editor_agent_is_down(tmp_data_dir, monkeypatch):
    """编辑 Agent 挂了（没装、没登录、超时、胡说八道），当天也要有一期——按规则选。"""
    date_str, draft, client = _unattended_run(tmp_data_dir, monkeypatch, "{py} -c 'import sys; sys.exit(3)'")

    assert draft.get("edited_by") == "rules"
    picked = [e for e in draft["items"] if e.get("selected")]
    assert 1 <= len(picked) <= daily.AUTO_PICK_TARGET
    from qianliyan.pipeline import channels as C

    groups = C.load_source_groups()
    per_source = {}
    for entry in picked:
        key = C.source_key(entry, groups)
        per_source[key] = per_source.get(key, 0) + 1
    available = {C.source_key(e, groups) for e in draft["items"]}
    assert len(available) * daily.AUTO_MAX_PER_SOURCE >= daily.AUTO_PICK_TARGET, "候选源太少，断言会空转"
    assert len(picked) == daily.AUTO_PICK_TARGET
    assert max(per_source.values()) <= daily.AUTO_MAX_PER_SOURCE, "规则回退让一家占满了版面：{0}".format(per_source)

    home = client.get("/daily").text
    assert date_str in home
    assert daily._display_title(picked[0]) in home


def test_scheduled_task_can_edit_over_the_fallback_and_stage_a_publishable_page(tmp_data_dir, monkeypatch):
    """定时任务那条路：规则回退先出了一期 → 任务读简报、写 picks → 替换回退选稿 → 整理发布目录。

    发布目录要能直接当 artifact 发：外站图片会被 artifact 的内容安全策略拦成破图，
    往期链接在只发当天一期时是死链——两样都不许留下；详情页的「返回日报」要能落地。
    """
    import json
    import subprocess
    import sys

    from pathlib import Path

    repo = Path(__file__).resolve().parent.parent

    date_str, draft, _ = _unattended_run(tmp_data_dir, monkeypatch, "{py} -c 'import sys; sys.exit(3)'")
    assert draft["edited_by"] == "rules"

    prompt_run = subprocess.run(["bash", str(repo / "scripts" / "qly-publish.sh"), "prompt", date_str],
                                capture_output=True, text=True, cwd="/")
    assert prompt_run.returncode == 0, prompt_run.stderr
    ids = [int(x) for x in re.findall(r"^\[(\d+)\]", prompt_run.stdout, re.M)]
    assert len(ids) == len(draft["items"])

    # 提炼里的数字必须逐字出现在原文（ADR D3）：把下面要用的数字和那句原话放进候选的摘要里，
    # 模拟「编辑照抄原文」；没在原文里的数字会被程序丢掉
    draft_path = paths.data_path("archive", date_str, daily.DRAFT_NAME)
    current = storage.read_json(draft_path, default={})
    for i in ids[-9:]:
        current["items"][i]["summary"] = (str(current["items"][i].get("summary") or "") +
            " 原文里的数字：$2/$10、-50%、33.2%、16.6、11.1×、18→33、65→73、$0.27、$3.0、26.9、10。"
            "「一句能传开的话」，某人说。")
    storage.write_json(draft_path, current)
    # 前四条由任务定为深读（编辑给的 depth 优先于规则）——提炼全套只给头条与深读
    picks = [{"i": i, "editor_note": "任务按语{0}".format(n), "title_zh": "", "summary_zh": "",
              "depth": "lead" if n == 1 else ("deep" if n <= 4 else "glance")}
             for n, i in enumerate(ids[-9:], start=1)]
    picks_file = tmp_data_dir / "picks.json"
    picks_file.write_text(json.dumps({"picks": picks}, ensure_ascii=False), encoding="utf-8")
    applied = subprocess.run(["bash", str(repo / "scripts" / "qly-publish.sh"), "apply", str(picks_file), date_str],
                             capture_output=True, text=True, cwd="/")
    assert applied.returncode == 0, applied.stdout + applied.stderr

    after = storage.read_json(paths.data_path("archive", date_str, daily.DRAFT_NAME), default={})
    assert after["edited_by"] == "agent"
    chosen = sorted((e for e in after["items"] if e.get("selected")), key=lambda e: e["editor_rank"])
    assert [e["editor_note"] for e in chosen] == ["任务按语{0}".format(n) for n in range(1, 10)], \
        "规则回退的选稿没被任务的选稿干净替换"

    # 编辑过的草案不许被再一次 apply 覆盖
    again = subprocess.run(["bash", str(repo / "scripts" / "qly-publish.sh"), "apply", str(picks_file), date_str],
                           capture_output=True, text=True, cwd="/")
    assert "不覆盖" in again.stdout

    # 任务读原文写要点 → 写进定稿、重渲染；日报页上直接看到
    brief_prompt = subprocess.run(["bash", str(repo / "scripts" / "qly-publish.sh"), "brief-prompt", date_str],
                                  capture_output=True, text=True, cwd="/")
    assert brief_prompt.returncode == 0, brief_prompt.stderr
    sigs = re.findall(r"^=== sig: (\S+)", brief_prompt.stdout, re.M)
    assert sorted(sigs) == sorted(e["sig"] for e in chosen)
    briefs_file = tmp_data_dir / "briefs.json"
    # 可视化数据：第一条给数字 + 合法对比图；第二条的图里混进非数字，整张图必须被拒（数字不能靠猜）
    viz = {
        sigs[0]: {"takeaway": "这是一句提炼出的结论", "meme": "「一句能传开的话」——某人",
                  "logic": {"chain": ["起因环节", "关键机制", "最终结果"], "focus": 1,
                            "evidence": ["证据甲 33.2%"], "caveat": "代价是某件事"},
                  "stats": [{"value": "$2/$10", "label": "输入/输出价格"}, {"value": "-50%", "label": "降价幅度"}],
                  "chart": {"title": "基准得分对比", "unit": "%", "rows": [
                      {"label": "主角模型", "value": 33.2, "highlight": True},
                      {"label": "对手模型", "value": 16.6, "note": "11.1×"}]}},
        sigs[1]: {"takeaway": "第二条的结论", "logic": {"chain": ["只有一步的链不成立"]},
                  "stats": [], "chart": {"title": "坏图", "unit": "%", "rows": [
                      {"label": "甲", "value": "大约三成"}, {"label": "乙", "value": 20}]}},
        sigs[2]: {"chart": {"type": "dumbbell", "title": "前代到新版", "unit": "%",
                            "from_label": "旧版", "to_label": "新版", "rows": [
                      {"label": "指标一", "from": 18, "to": 33}, {"label": "指标二", "from": 65, "to": 73}]}},
        sigs[3]: {"chart": {"type": "scatter", "title": "得分与成本", "x_label": "每任务成本", "y_label": "得分",
                            "x_unit": "$", "y_unit": "%", "points": [
                      {"label": "便宜模型", "x": 0.27, "y": 33.2, "highlight": True},
                      {"label": "贵模型", "x": 3.0, "y": 26.9},
                      {"label": "坏点", "x": "很贵", "y": 10}]}},
    }
    briefs_file.write_text(json.dumps({"briefs": [
        dict({"sig": sig, "brief": ["任务要点一·" + sig, "任务要点二·" + sig]}, **viz.get(sig, {}))
        for sig in sigs
    ] + [{"sig": "not-a-pick", "brief": ["不该出现的要点"]}]}, ensure_ascii=False), encoding="utf-8")
    applied = subprocess.run(["bash", str(repo / "scripts" / "qly-publish.sh"), "apply-briefs", str(briefs_file), date_str],
                             capture_output=True, text=True, cwd="/")
    assert applied.returncode == 0, applied.stdout + applied.stderr
    status = subprocess.run(["bash", str(repo / "scripts" / "qly-publish.sh"), "status", date_str],
                            capture_output=True, text=True, cwd="/").stdout
    assert "briefs={0}/{0}".format(len(sigs)) in status

    # 给页面塞一张外站图，确认发布前会被剥掉
    root_page = paths.data_path("daily.html")
    root_page.write_text(root_page.read_text(encoding="utf-8").replace(
        "</body>", '<img src="https://pbs.twimg.com/x.jpg" alt="x"></body>'), encoding="utf-8")
    out = tmp_data_dir / "stage"
    staged = subprocess.run(["bash", str(repo / "scripts" / "qly-publish.sh"), "stage", str(out)],
                            capture_output=True, text=True, cwd="/")
    assert staged.returncode == 0, staged.stderr
    index = (out / "index.html").read_text(encoding="utf-8")
    assert "任务按语1" in index
    # 要点只放详情页（ADR D1）：发布目录里每条的详情页都要有
    for sig in sigs:
        assert "任务要点一·" + sig in (out / "story" / "{0}.html".format(sig)).read_text(encoding="utf-8"), "要点没进发布页"
    assert "不该出现的要点" not in index
    # 关键数字与对比图：数值原样上版、条宽按数值归一（33.2 最大 → 100%，16.6 → 50%）
    assert "$2/$10" in index and "降价幅度" in index, "关键数字没上版"
    # 提炼是日报卡片的主体：结论、梗、逻辑链（关键一环高亮）、证据与代价；
    # 有结论的条目，细节要点只留在详情页，日报页不再铺一屏字
    glance = index.split('id="wrap-glance"', 1)[-1]
    assert "这是一句提炼出的结论" in glance and "「一句能传开的话」——某人" in glance
    assert '<div class="lg-node is-hl">关键机制</div>' in glance, "逻辑链的关键一环没高亮"
    assert "起因环节" in glance and "最终结果" in glance and "证据甲 33.2%" in glance and "代价是某件事" in glance
    assert "任务要点一·" + sigs[0] not in glance, "有结论的条目，要点不该再铺在日报页"
    story0 = (out / "story" / "{0}.html".format(sigs[0])).read_text(encoding="utf-8")
    assert "任务要点一·" + sigs[0] in story0 and "这是一句提炼出的结论" in story0, "详情页要有结论也要有细节要点"
    assert "只有一步的链不成立" not in index, "不成链的逻辑图不该被画出来"
    # 条形图：条长严格按数值（33.2 : 16.6 = 2 : 1），主角高亮
    assert "基准得分对比" in index and "33.2%" in index and "11.1×" in index
    hl = float(re.search(r'<rect class="qc-bar is-hl"[^>]* width="([\d.]+)"', index).group(1))
    other = float(re.search(r'<rect class="qc-bar"[^>]* width="([\d.]+)"', index).group(1))
    assert abs(hl / other - 2.0) < 0.02, "条长没按数值比例画：{0} vs {1}".format(hl, other)
    assert "坏图" not in index, "含非数字的对比图不该被画出来"
    # 哑铃图：图例用编辑给的前后名称，右侧标变化量
    assert "前代到新版" in index and "旧版" in index and "+15" in index and "+8" in index
    # 散点图：两个点、轴标题都在，主角点高亮
    assert "得分与成本" in index and "每任务成本" in index and 'class="qc-point is-hl"' in index
    assert "坏点" not in index, "坐标不是数字的点不该被画出来"
    assert not re.search(r'<img[^>]+src="https?://', index), "外站图片留在了发布页里"
    assert 'href="archive/' not in index, "往期死链留在了发布页里"
    stories = re.findall(r'href="(story/[A-Za-z0-9_.-]+\.html)"', index)
    assert stories and all((out / s).is_file() for s in stories), "首页链接的详情页没一起整理进来"
    assert (out / "daily.html").is_file(), "详情页的「返回日报」会落空"


# =========================================================================
# 按方向分栏（issue #49）：模型发布所有人必看，其余方向由读者选关注
# =========================================================================
TRACK_EDITOR = r'''
import json, re, sys
prompt = sys.stdin.read()
sigs = re.findall(r"^=== sig: (\S+)", prompt, re.M)
if sigs:
    print(json.dumps({"briefs": [{"sig": s, "takeaway": "结论·" + s} for s in sigs]}))
    sys.exit(0)
open(sys.argv[1], "w", encoding="utf-8").write(prompt)
rows = re.findall(r"^\[(\d+)\].*\n.*规则方向：(\w+)", prompt, re.M)
ids = [int(i) for i, _ in rows]
# 编辑的方向判断优先于规则：前 9 条轮流标到各个方向（和规则怎么判无关），再挑 3 条快讯
order = ["agent", "models", "training", "infra", "research", "agent", "models", "training", "infra"]
picks = [{"i": i, "track": order[n], "editor_note": "按语{0}".format(n + 1), "title_zh": "正文{0}".format(n + 1)}
         for n, i in enumerate(ids[:9])]
quick = [{"i": i, "track": "infra", "title_zh": "快讯标题{0}".format(n), "summary_zh": "快讯一句话{0}".format(n)}
         for n, i in enumerate(ids[9:12], start=1)]
quick.append({"i": ids[0], "track": "infra", "title_zh": "和正文重复的快讯", "summary_zh": "不该出现"})
print(json.dumps({"picks": picks, "quick": quick}, ensure_ascii=False))
'''


def _sections(page: str) -> dict:
    """把日报页切成 {方向 id: (section 开标签, section 内容)}，按页面顺序。"""
    out = {}
    for m in re.finditer(r'(<section class="report-section trk-sec[^"]*" id="trk-(\w+)"[^>]*>)(.*?)</section>',
                         page, re.S):
        out[m.group(2)] = (m.group(1), m.group(3))
    return out


def test_page_is_organised_by_track_with_model_releases_for_everyone(tmp_data_dir, monkeypatch):
    """读者关心的方向不一样：模型发布所有人都要看，其余方向默认只展开 Agent 实践，
    别的方向收成「标题 + 一句结论」、由读者自己选关注；每栏尾挂这个方向的快讯。

    编辑标的方向说了算（规则只是兜底），和正文同一条的快讯不许重复出现。
    """
    import subprocess
    import sys
    from pathlib import Path

    script = tmp_data_dir / "track_editor.py"
    script.write_text(TRACK_EDITOR, encoding="utf-8")
    date_str, draft, client = _unattended_run(
        tmp_data_dir, monkeypatch, "{py} " + str(script) + " {dir}/prompt-seen.txt")
    assert draft.get("edited_by") == "agent"

    seen = (tmp_data_dir / "prompt-seen.txt").read_text(encoding="utf-8")
    assert "models：模型发布" in seen and "所有人必看" in seen, "选稿简报里没告诉编辑有哪些方向"
    assert "规则方向：" in seen

    glance = client.get("/daily", params={"view": "glance"}).text
    secs = _sections(glance)
    ids = list(secs)
    assert ids[0] == "lead" and "正文1" in secs["lead"][1], "编辑定的头条没单独放在最前"
    assert ids[1] == "models" and "is-everyone" in secs["models"][0], "模型发布没排在头条之后、没标必看"
    assert "正文2" in secs["models"][1] and "正文7" in secs["models"][1]
    # 默认关注 Agent 实践：展开；没关注的方向折叠（只看标题和结论）
    assert "is-compact" not in secs["agent"][0] and "正文6" in secs["agent"][1]
    for tid in ("training", "infra", "research"):
        assert "is-compact" in secs[tid][0], "{0} 默认该折叠".format(tid)
    # 编辑标到训练的两条（不管规则怎么判）落在训练栏
    assert "正文3" in secs["training"][1] and "正文8" in secs["training"][1]
    # 关注开关：模型发布不在开关里（必看），Agent 默认按下
    assert re.search(r'data-follow="agent" aria-pressed="true"', glance)
    assert 'data-follow="models"' not in glance, "必看的方向不该能被取消关注"
    # 快讯：挂在编辑标的方向栏尾，一行标题 + 一句话；和正文重复的那条被丢掉
    for n in (1, 2, 3):
        assert "快讯标题{0}".format(n) in secs["infra"][1] and "快讯一句话{0}".format(n) in secs["infra"][1]
    assert "和正文重复的快讯" not in glance

    repo = Path(__file__).resolve().parent.parent
    status = subprocess.run(["bash", str(repo / "scripts" / "qly-publish.sh"), "status", date_str],
                            capture_output=True, text=True, cwd="/").stdout
    assert "tracks=models:2,agent:2,training:2,infra:2,research:1,industry:0" in status, status
    assert "empty_tracks=industry" in status and "quick=3" in status, status

    # 重跑抓取 + prepare（launchd 补跑），编辑挑的快讯和方向不许丢
    sync.run_sync(mock=True)
    assert daily.main(["--prepare", "--date", date_str]) == 0
    assert daily.main(["--finalize", "--html", "--date", date_str]) == 0
    glance = client.get("/daily", params={"view": "glance"}).text
    secs = _sections(glance)
    assert "快讯一句话2" in secs["infra"][1], "重跑 prepare 抹掉了编辑挑的快讯"
    assert "正文3" in secs["training"][1], "重跑 prepare 抹掉了编辑标的方向"
    del sys


def test_rule_fallback_gives_every_track_with_fresh_news_a_seat(tmp_data_dir, monkeypatch):
    """编辑 Agent 挂了、按规则选稿时，也不许让分数最高的方向占满 12 席：
    每个有新鲜候选的方向至少分到一席，其余新鲜候选进快讯（不和正文重复）。"""
    from datetime import timedelta

    from qianliyan.pipeline import tracks

    import sys
    monkeypatch.setenv("QLY_EDITOR_CMD", "{0} -c 'import sys; sys.exit(3)'".format(sys.executable))
    sync.run_sync(mock=True)
    date_str = utils.now_utc().strftime("%Y-%m-%d")
    assert daily.main(["--prepare", "--date", date_str]) == 0
    draft_path = paths.data_path("archive", date_str, daily.DRAFT_NAME)
    draft = storage.read_json(draft_path, default={})
    cfg = tracks.load()
    # 让排在草案**末尾**（分数最低）的训练、产品行业条目各一条变新鲜：只按分数取的话它们进不了前 12
    fresh_at = utils.iso(utils.now_utc() - timedelta(hours=2))
    want = ("training", "industry")
    low = [next(e for e in reversed(draft["items"]) if tracks.rule_track(e, cfg) == tid) for tid in want]
    positions = [draft["items"].index(e) for e in low]
    assert min(positions) >= daily.AUTO_PICK_TARGET, "这两条本来就排进前 12，断言会空转"
    # 别的候选都新鲜、这两个方向只剩这两条新鲜：名额只能按方向分，
    # 不能靠「别的都太旧」或同方向的高分条目碰巧挤进来
    stale_at = utils.iso(utils.now_utc() - timedelta(days=10))
    for entry in draft["items"]:
        stale = tracks.rule_track(entry, cfg) in want and entry not in low
        entry["date"] = stale_at if stale else fresh_at
    storage.write_json(draft_path, draft)

    assert daily.main(["--auto-edit", "--date", date_str]) == 0
    draft = storage.read_json(draft_path, default={})
    # 人工编过的旧草案没有快讯：定稿时按规则补挑，且不许和正文讲同一个发布
    # （09-23 头条是 GPT-6 Sol，规则快讯里又挂了官方公告、Altman 的话、ChatGPT 推送三条转述）
    for entry in draft["items"]:
        entry.pop("quick", None)
    selected = [e for e in draft["items"] if e.get("selected")]
    echo = next(e for e in draft["items"] if not e.get("selected"))
    selected[-1]["title"] = "Introducing GPT-9 Nova"
    echo["title"] = "GPT-9 Nova 在 ChatGPT 推送"
    storage.write_json(draft_path, draft)
    assert daily.main(["--finalize", "--html", "--date", date_str]) == 0
    draft = storage.read_json(draft_path, default={})
    assert draft.get("edited_by") == "rules"
    picked = [e for e in draft["items"] if e.get("selected")]
    assert len(picked) == daily.AUTO_PICK_TARGET
    got = {tracks.track_of(e, cfg) for e in picked}
    assert set(want) <= got, "有新鲜稿的方向没分到一席：{0}".format(got)
    assert {e["sig"] for e in low} <= {e["sig"] for e in picked}

    final = storage.read_json(paths.data_path("archive", date_str, daily.FINAL_NAME), default={})
    quick = final.get("quick") or []
    assert quick, "规则兜底没挑快讯"
    assert not {q["sig"] for q in quick} & {e["sig"] for e in picked}, "快讯和正文重复"
    assert echo["sig"] not in {q["sig"] for q in quick}, "快讯里挂了正文那次发布的转述"
    glance = TestClient(api_server.create_app()).get("/daily", params={"view": "glance"}).text
    assert daily._display_title(quick[0]) in glance


# =========================================================================
# 原文配图（issue #52）：卡片主视觉用原文自己的图，下载到本地随页发布
# =========================================================================
#: 1×1 的合法 PNG——_download_image 按文件头认格式
_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000d4944415478da63f8cfc0f01f0005000201a5a3a3a50000000049454e44ae426082")


class _FakeHTTP:
    def __init__(self, pages):
        self.pages = pages
        self.requested = []

    def get(self, url, timeout=15, headers=None, max_bytes=None):
        from qianliyan.engine import http

        self.requested.append(url)
        if url in self.pages:
            body, ctype = self.pages[url]
            return _Resp(body.encode("utf-8"), ctype, url)
        if "fig3-h100-performance" in url:
            # 超过上限的图：http.get 的 max_bytes 会静默截断，截断的就是半张坏图
            return _Resp(_PNG + b"\0" * (max_bytes or 3000000), "image/png", url)
        if any(host in url for host in ("pytorch.org/wp-content/", "cdn.prod.website-files.com", "pbs.twimg.com",
                                        "epoch.ai/assets/", "substackcdn.com", "pbs.substack.com")):
            return _Resp(_PNG, "image/png", url)
        raise http.OfflineError("测试里没有这个地址: " + url)


class _Resp:
    def __init__(self, content, ctype, url):
        self.content, self.url, self.status_code = content, url, 200
        self.headers = {"content-type": ctype}
        # 同 requests：响应头没声明 charset 的 text/html 按 ISO-8859-1 解
        self.text = content.decode("utf-8" if "charset=utf-8" in ctype else "latin-1", "replace")


def test_article_figures_become_the_card_visual_and_ship_with_the_page(tmp_data_dir, monkeypatch):
    """原文里的实验图、架构图才是最有信息量的东西：日报卡片的主视觉用原文配图（带原图注），
    logo / 头像不许混进来；原文有图时，日报页不再画我们自己的对比图（详情页照留）。
    图片下载到本地、随页发布——外站地址在 artifact 里会被拦成破图。"""
    import subprocess
    from pathlib import Path

    from qianliyan.engine import http

    fixtures = Path(__file__).resolve().parent / "fixtures" / "real"
    pytorch_url = "https://pytorch.org/blog/hardware-agnostic-models-in-vllm/"
    claude_url = "https://claude.com/blog/what-a-task-costs-on-opus-5-5"
    epoch_url = "https://epoch.ai/publications/the-plunging-price-of-thought"
    latent_url = "https://www.latent.space/p/ainews-xiaomi-mimo-v26-pro-1t-a42b"
    html_utf8 = "text/html; charset=utf-8"
    fake = _FakeHTTP({
        pytorch_url: ((fixtures / "pytorch_vllm_article.html").read_text(encoding="utf-8"), html_utf8),
        claude_url: ((fixtures / "claude_blog_task_cost.html").read_text(encoding="utf-8"), html_utf8),
        # epoch.ai 真实响应头就不带 charset
        epoch_url: ((fixtures / "epoch_article.html").read_text(encoding="utf-8"), "text/html"),
        latent_url: ((fixtures / "latent_space_ainews.html").read_text(encoding="utf-8"), html_utf8),
    })
    monkeypatch.setattr(http, "get", fake.get)

    sync.run_sync(mock=True)
    date_str = utils.now_utc().strftime("%Y-%m-%d")
    assert daily.cmd_prepare(date_str) == 0
    draft_path = paths.data_path("archive", date_str, daily.DRAFT_NAME)
    draft = storage.read_json(draft_path, default={})
    a, b, c, d, e = draft["items"][:5]
    for rank, entry in enumerate((a, b, c, d, e), start=1):
        entry["selected"], entry["editor_rank"] = True, rank
        # 深读有每方向 / 每信源组上限（ADR D2）：各条分开放，这里考的是配图
        entry["source"], entry["source_list"] = "信源{0}".format(rank), ["信源{0}".format(rank)]
        entry["track"] = ["models", "infra", "agent", "research", "industry"][rank - 1]
    a["url"], a["title_zh"] = pytorch_url, "vLLM 走向硬件无关"
    # 对比图的数字要能在原文里找到（ADR D3 逐字核对）：3.4% 与 3% 都在 pytorch 那篇里
    a["chart"] = {"type": "bar", "title": "我们自绘的对比图", "unit": "%",
                  "rows": [{"label": "甲", "value": 3.4}, {"label": "乙", "value": 3}]}
    b["url"], b["title_zh"] = claude_url, "一个任务花多少钱"
    c["url"], c["title_zh"] = "https://x.com/someone/status/1", "一条推文"
    c.setdefault("extra", {})["images"] = ["https://pbs.twimg.com/media/X.jpg?format=jpg&amp;name=large"]
    d["url"], d["title_zh"] = epoch_url, "AI 降价比任何技术都快"
    e["url"], e["title_zh"] = latent_url, "小米 MiMo 登顶开放权重"
    storage.write_json(draft_path, draft)
    assert daily.cmd_finalize(date_str, do_html=True) == 0

    assert not [u for u in fake.requested if "logo" in u.lower() or "avatar" in u.lower()], \
        "logo / 头像被当成配图下载了"
    assert "https://pbs.twimg.com/media/X.jpg?format=jpg&name=large" in fake.requested, "推文配图地址里的 &amp; 没解码"

    client = TestClient(api_server.create_app())
    home = client.get("/daily").text
    glance = home.split('id="wrap-glance"', 1)[1].split("</main>", 1)[0]

    def card(sig):
        return glance.split('id="e-{0}"'.format(sig), 1)[1].split("</article>", 1)[0]

    # /daily 回的是归档页（archive/<日期>/digest.html），图片路径是 ../../media/x——从 /daily 解析正好落到 /media/x
    srcs = re.findall(r'<img src="(?:\.\./)*(media/[0-9a-f]{16}\.png)"', card(a["sig"]))
    assert srcs, "原文配图没当卡片主视觉"
    assert (paths.data_path() / srcs[0]).is_file()
    assert "我们自绘的对比图" not in card(a["sig"]), "原文有图时日报页不该再画自绘对比图"
    assert "Fig A. Price per million tokens." in card(b["sig"]), "图注没用原文的 figcaption"
    assert "图源" in card(b["sig"])
    # 推文没有正文可深读 → 浅读行，浅读行不放图（ADR D1）；推文配图在详情页
    assert 'data-depth="glance"' in card(c["sig"]) and "<img" not in card(c["sig"]), "浅读行不该有图"
    assert re.search(r'<img src="\.\./media/[0-9a-f]{16}\.png"', client.get("/story/{0}.html".format(c["sig"])).text), \
        "推文配图没到详情页"
    # Epoch 的主图是交互图表，静态版在 <noscript> 里；页面不声明 charset，图注里的「–」不能解成乱码
    assert "figure-1.png" in " ".join(fake.requested), "noscript 里的主图没收"
    assert "(2021–26)" in card(d["sig"]), "页面没按 UTF-8 解码，图注成了乱码"
    # Substack：正文里的推文截图和两张图表要，正文外的刊物 logo、40px 头像不要；
    # 图片地址本身带逗号（w_1456,c_limit,…），srcset 切错就会拼出不存在的地址
    latent = [u for u in fake.requested if "substack" in u]
    assert "https://pbs.substack.com/media/HSxCs7wa0AANR91.jpg" in latent, "正文里的推文截图没收"
    charts = [u for u in latent if u.startswith("https://substackcdn.com/image/fetch/") and ",c_limit," in u]
    assert len(charts) == 2, "srcset 没按「逗号 + 空白」切：{0}".format(latent)
    assert not [u for u in latent if "e_trim" in u or "w_40,h_40" in u], "刊物 logo / 头像被当成配图"

    story_a = client.get("/story/{0}.html".format(a["sig"])).text
    assert "我们自绘的对比图" in story_a, "详情页要保留自绘对比图"
    assert len(re.findall(r'<img src="\.\./media/', story_a)) == 2, "超过大小上限（会被截断）的图不该下载"
    story_b = client.get("/story/{0}.html".format(b["sig"])).text
    assert len(re.findall(r'<img src="\.\./media/[0-9a-f]{16}\.png"', story_b)) == 4, "详情页要放全部原文配图"
    assert "Fig D. Migration from Opus 4.8" in story_b

    got = client.get("/" + srcs[0])
    assert got.status_code == 200 and got.headers["content-type"] == "image/png" and got.content == _PNG
    assert client.get("/media/..%2Fitems.jsonl").status_code == 404
    (paths.data_path() / "0123456789abcdef.png").write_bytes(_PNG)   # 数据根下、media/ 外
    assert client.get("/media/..%2F0123456789abcdef.png").status_code == 404, "配图路由能被路径穿越"
    assert client.get("/media/0123456789abcdef.svg").status_code == 404

    # 归档页（archive/<日期>/digest.html）里的相对路径要能落到同一张图
    archive_page = paths.data_path("archive", date_str, daily.MERGED_NAME)
    rel = re.search(r'<img src="(\.\./\.\./media/[0-9a-f]{16}\.png)"', archive_page.read_text(encoding="utf-8"))
    assert rel and (archive_page.parent / rel.group(1)).resolve().is_file()

    # 数据根的首页（对外入口、也是发布的来源）用 media/x
    root_page = paths.data_path("daily.html").read_text(encoding="utf-8")
    assert '<img src="{0}"'.format(srcs[0]) in root_page

    # 发布目录：本地配图一起带上，<img> 保留
    repo = Path(__file__).resolve().parent.parent
    out = tmp_data_dir / "stage"
    staged = subprocess.run(["bash", str(repo / "scripts" / "qly-publish.sh"), "stage", str(out)],
                            capture_output=True, text=True, cwd="/")
    assert staged.returncode == 0, staged.stderr
    files = staged.stdout.split("files=", 1)[1].split()
    assert srcs[0] in files and (out / srcs[0]).is_file(), "配图没随页进发布目录"
    assert '<img src="{0}"'.format(srcs[0]) in (out / "index.html").read_text(encoding="utf-8")
    assert '<img src="../media/' in (out / "story" / "{0}.html".format(b["sig"])).read_text(encoding="utf-8")


# =========================================================================
# 三档密度（docs/adr-daily-format.md）：头条 / 深读 / 浅读按条目分，原文图是证据层、提炼是导读层
# =========================================================================
def _issue_with_three_depths(tmp_data_dir, monkeypatch):
    """跑完整链路，草案里造出三种原料：厚正文 + 原文图、厚正文 + 硬数字、薄正文；
    提炼里混一个原文没有的数字。返回 (client, date, 各条 sig)。"""
    import sys

    monkeypatch.setenv("QLY_EDITOR_CMD", "{0} -c 'import sys; sys.exit(3)'".format(sys.executable))
    sync.run_sync(mock=True)
    date_str = utils.now_utc().strftime("%Y-%m-%d")
    assert daily.cmd_prepare(date_str) == 0
    draft_path = paths.data_path("archive", date_str, daily.DRAFT_NAME)
    draft = storage.read_json(draft_path, default={})
    items = draft["items"]
    assert len(items) >= 6
    media = paths.data_path("media")
    media.mkdir(parents=True, exist_ok=True)
    (media / "aaaaaaaaaaaaaaaa.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 64)
    (media / "bbbbbbbbbbbbbbbb.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 64)
    # 定稿时配图由 finalize 从原文图地址下载；离线下把两个地址映射到本地文件
    monkeypatch.setattr(daily, "_download_image",
                        lambda url: "media/aaaaaaaaaaaaaaaa.png" if "f.png" in url else "media/bbbbbbbbbbbbbbbb.png")
    thick = "The throughput improved by 3.3x on the new kernel while latency fell to 120 ms. " * 60
    roles = {}
    # 深读有每方向 / 每信源组的上限：各条分开放，考的是原料门槛而不是配额
    for idx, entry in enumerate(items[:6]):
        entry["selected"] = True
        entry["editor_rank"] = idx + 1
        entry["source"], entry["source_list"] = "信源{0}".format(idx), ["信源{0}".format(idx)]
        entry["track"] = ["models", "infra", "agent", "training", "research", "industry"][idx]
        entry["editor_note"] = "为什么重要·{0}".format(entry["sig"])
        entry["takeaway"] = "一句结论·{0}".format(entry["sig"])
        entry["brief"] = ["细节要点·{0}".format(entry["sig"])]
        entry["meme"] = "这句梗原文里没有·{0}".format(entry["sig"])
        entry["logic"] = {"chain": ["起因", "机制", "结果"], "focus": 1, "evidence": ["延迟降到 120 ms"], "caveat": ""}
        extra = entry.setdefault("extra", {})
        extra["og_image"] = "https://example.org/og.png"
        if idx in (0, 1):  # 头条 + 深读：厚正文 + 原文图
            extra["fulltext"], extra["fulltext_chars"] = thick, len(thick)
            extra["article_images"] = [{"src": "https://example.org/f.png",
                                        "caption": "原文图注·{0}".format(entry["sig"]), "alt": ""}]
            entry["figure_note"] = "看柱子最高那根·{0}".format(entry["sig"])
            entry["stats"] = [{"value": "3.3x", "label": "吞吐提升"}, {"value": "99%", "label": "原文没有这个数"}]
            roles.setdefault("figured", []).append(entry["sig"])
        elif idx == 2:  # 深读：厚正文 + 硬数字，只有 og 封面 → 封面不上页，数字卡顶上
            extra["fulltext"], extra["fulltext_chars"] = thick, len(thick)
            extra["article_images"] = []
            entry["stats"] = [{"value": "120 ms", "label": "延迟"}]
            roles["numbers"] = entry["sig"]
        else:  # 薄正文：无论排名多靠前都只能浅读
            extra["fulltext"], extra["fulltext_chars"] = "short note 3.3x", 15
            extra["article_images"] = [{"src": "https://example.org/f.png",
                                        "caption": "薄条目的图·{0}".format(entry["sig"]), "alt": ""}]
            entry["stats"] = [{"value": "3.3x", "label": "吞吐提升"}]
            roles.setdefault("thin", []).append(entry["sig"])
    storage.write_json(draft_path, draft)
    assert daily.cmd_finalize(date_str, do_html=True) == 0
    return TestClient(api_server.create_app()), date_str, roles


def test_issue_is_tiered_by_item_not_by_track(tmp_data_dir, monkeypatch):
    """一期 = 1 头条 + 若干深读 + 其余浅读：深读只给「原料厚」的条目；浅读只有标题和一句结论；
    og 封面永远不上日报页；原文图下有解释图注；提炼块标「示意」；深读 tab 只列头条与深读。"""
    client, date_str, roles = _issue_with_three_depths(tmp_data_dir, monkeypatch)
    final = storage.read_json(paths.data_path("archive", date_str, daily.FINAL_NAME), default={})
    depth = {e["sig"]: e.get("depth") for e in final["items"]}
    lead, deep_fig = roles["figured"]
    assert depth[lead] == "lead"
    assert depth[deep_fig] == "deep"
    assert depth[roles["numbers"]] == "deep", "厚正文 + 硬数字、没原文图的条目也够深读"
    assert all(depth[s] == "glance" for s in roles["thin"]), "薄正文的条目只能浅读，排名再靠前也不行"

    glance = client.get("/daily", params={"view": "glance"}).text
    # 浅读行：标题 + 一句结论 + 原文链接，没有图、逻辑链、数字卡、短评、梗
    for sig in roles["thin"]:
        row = re.search(r'<article[^>]*data-sig="{0}".*?</article>'.format(sig), glance, re.S).group(0)
        assert 'data-depth="glance"' in row
        assert "一句结论·" + sig in row
        assert "薄条目的图·" + sig not in row and "src-fig" not in row, "浅读行不该有图"
        assert "viz-logic" not in row and "viz-stats" not in row and "为什么重要" not in row
    # 头条：原文图 + 解释图注 + 逻辑链 + 数字卡；封面图不出现
    row = re.search(r'<article[^>]*data-sig="{0}".*?</article>'.format(lead), glance, re.S).group(0)
    assert "看这里：看柱子最高那根·" + lead in row and "原文图注·" + lead in row
    assert "bbbbbbbbbbbbbbbb.png" not in glance, "og 封面不许上日报页"
    assert "viz-logic" in row and "示意 · 编辑整理" in row and "为什么重要·" + lead in row
    # 深读（有图）：一张原文图 + 图注；逻辑链只给头条；数字卡让位给原文图
    row = re.search(r'<article[^>]*data-sig="{0}".*?</article>'.format(deep_fig), glance, re.S).group(0)
    assert 'data-depth="deep"' in row and "原文图注·" + deep_fig in row and "viz-logic" not in row
    # 深读（无图）：数字卡顶上
    row = re.search(r'<article[^>]*data-sig="{0}".*?</article>'.format(roles["numbers"]), glance, re.S).group(0)
    assert "viz-stats" in row and "120 ms" in row
    # 要点只放详情页
    assert "细节要点·" + lead not in glance
    assert "细节要点·" + lead in client.get("/story/{0}.html".format(lead)).text
    # 页首有本期字数 / 分钟
    assert re.search(r"本期约 \d+ 字 · \d+ 分钟", glance)
    # 深读 tab 只列头条 + 深读
    deep = client.get("/daily", params={"view": "deep"}).text
    assert "一句结论·" + lead in deep and "一句结论·" + deep_fig in deep
    for sig in roles["thin"]:
        assert "一句结论·" + sig not in deep, "浅读条目不该出现在深读 tab"


def test_fabricated_numbers_and_paraphrased_memes_never_reach_the_page(tmp_data_dir, monkeypatch):
    """提炼里的数字必须逐字出现在原文：原文没有的数字卡被丢掉，原话之外的梗被丢掉。"""
    client, date_str, roles = _issue_with_three_depths(tmp_data_dir, monkeypatch)
    lead = roles["figured"][0]
    page = client.get("/daily", params={"view": "glance"}).text
    row = re.search(r'<article[^>]*data-sig="{0}".*?</article>'.format(lead), page, re.S).group(0)
    assert "3.3x" in row, "原文里有的数字应该保留"
    assert "原文没有这个数" not in row and "99%" not in row, "原文没有的数字必须丢掉"
    assert "这句梗原文里没有" not in page, "不是原文原话的梗必须丢掉"
    assert "120 ms" in row, "逻辑链里核对得上的证据保留"


def test_brief_lines_carry_their_subject_and_tick_marks_received(tmp_data_dir, monkeypatch):
    """「N 秒读完今天」每行要能脱离标题单独读：一句话结论里没有事件主体（标题里的产品 / 机构名一个都
    没出现）就退回用标题；秒数按实际字数算、取整到 5 秒；行上的勾和正文行的「标记已读」是同一类按钮，
    图区的块也带同一个 sig（看过即接收，三处同步置灰）。"""
    import html as html_lib

    client, date_str, roles = _issue_with_three_depths(tmp_data_dir, monkeypatch)
    final_path = paths.data_path("archive", date_str, daily.FINAL_NAME)
    final = storage.read_json(final_path, default={})
    by_sig = {e["sig"]: e for e in final["items"]}
    lead, deep_fig = roles["figured"]
    lead_title = daily._display_title(by_sig[lead])
    tokens = sorted(daily._latin_tokens(lead_title))
    subject = tokens[0] if tokens else lead_title[:3]
    by_sig[lead]["takeaway"] = "{0} 这次把默认配置改了".format(subject)
    storage.write_json(final_path, final)
    assert daily.cmd_html_only(date_str) == 0

    home = TestClient(api_server.create_app()).get("/daily").text
    brief = home.split('id="part-brief"', 1)[1].split('id="part-body"', 1)[0]
    lead_row = re.search(r'<li class="rb-row is-lead" data-sig="{0}".*?</li>'.format(lead), brief, re.S).group(0)
    assert "这次把默认配置改了" in lead_row, "带主体的结论原样上速读"
    deep_row = re.search(r'<li class="rb-row is-deep" data-sig="{0}".*?</li>'.format(deep_fig), brief, re.S).group(0)
    deep_title = html_lib.escape(daily._display_title(by_sig[deep_fig]), quote=True)
    assert ">{0}</a>".format(deep_title) in deep_row, "没主体的结论要退回标题"
    assert ">一句结论·{0}</a>".format(deep_fig) not in deep_row
    assert "深读" in deep_row and "rb-d" in deep_row, "速读行要标出深浅"

    secs = int(re.search(r">(\d+) 秒读完今天", home).group(1))
    assert secs % 5 == 0 and 10 <= secs <= 300
    # 勾、图块、正文行三处共用 sig；勾是 .gread（正文「标记已读」同一个事件链）
    assert 'class="gread rb-check" data-sig="{0}"'.format(lead) in brief
    assert re.search(r'<a class="sp-tile is-lead has-fig" href="#e-{0}" data-sig="{0}"'.format(lead), home)
    assert 'class="gread" data-sig="{0}"'.format(lead) in home.split('id="wrap-glance"', 1)[1]
    assert 'querySelectorAll(".gread")' in home
    assert 'querySelectorAll(".rb-row[data-sig], .sp-tile[data-sig]")' in home


def test_codex_illustration_fills_the_slot_only_when_the_source_has_no_figure(tmp_data_dir, monkeypatch, tmp_path):
    """示意图（Codex 按原文生成）只给没有信息图的条目补位：页面上标「示意 · Codex 生成」而不是
    「图源：原文」，图区的块也带角标；原文有信息图的条目给了示意图也不收；示意图不算深读原料。"""
    client, date_str, roles = _issue_with_three_depths(tmp_data_dir, monkeypatch)
    lead, deep_fig = roles["figured"]
    numbers = roles["numbers"]
    synth = tmp_path / "synth.png"
    synth.write_bytes(b"\x89PNG\r\n\x1a\n" + b"synth" * 20)
    plan = tmp_path / "depth.json"
    plan.write_text(json.dumps({"depth": [
        {"sig": numbers, "depth": "deep", "synth_image": str(synth), "figure_note": "看橙色箭头那一步"},
        {"sig": deep_fig, "depth": "deep", "figure": 0, "synth_image": str(synth)},
        # 头条的原文图被编辑判成装饰图（figure=-1）：等于没有信息图，示意图要收
        {"sig": lead, "depth": "lead", "figure": -1, "synth_image": str(synth)},
    ]}), encoding="utf-8")
    assert daily.cmd_apply_depth(date_str, str(plan)) == 0
    final = storage.read_json(paths.data_path("archive", date_str, daily.FINAL_NAME), default={})
    by_sig = {e["sig"]: e for e in final["items"]}
    assert by_sig[numbers]["depth"] == "deep" and by_sig[numbers]["figures"][0]["kind"] == "synth"
    assert all(f.get("kind") != "synth" for f in by_sig[deep_fig]["figures"]), "原文有信息图的条目不收示意图"
    assert by_sig[lead]["figures"][0]["kind"] == "synth", "只有装饰图的条目要收示意图"
    assert [f["kind"] for f in by_sig[lead]["figures"][1:]] == ["decorative"]
    # 重跑一遍（换图、改图注都会重跑）：旧示意图被换掉，不能变成「装饰图」残留在详情页
    assert daily.cmd_apply_depth(date_str, str(plan)) == 0
    final = storage.read_json(paths.data_path("archive", date_str, daily.FINAL_NAME), default={})
    by_sig = {e["sig"]: e for e in final["items"]}
    assert [f["kind"] for f in by_sig[lead]["figures"]] == ["synth", "decorative"], "重跑后示意图残留"
    assert daily.cmd_html_only(date_str) == 0

    home = TestClient(api_server.create_app()).get("/daily").text
    row = re.search(r'<article[^>]*data-sig="{0}".*?</article>'.format(numbers), home, re.S).group(0)
    assert "src-fig" in row and "示意 · Codex" in row and "看这里：看橙色箭头那一步" in row
    assert "图源：" not in row, "示意图不许署成原文图"
    assert "viz-stats" not in row, "有了视觉位，数字卡退场"
    tile = re.search(r'<a class="sp-tile is-deep has-fig is-synth" href="#e-{0}".*?</a>'.format(numbers), home, re.S)
    assert tile and "示意 · Codex 生成" in tile.group(0)
    deep_tile = re.search(r'<a class="sp-tile is-deep has-fig" href="#e-{0}"'.format(deep_fig), home)
    assert deep_tile, "原文图的块不带示意角标"
    lead_row = re.search(r'<article[^>]*data-sig="{0}".*?</article>'.format(lead), home, re.S).group(0)
    assert "示意 · Codex" in lead_row and "原文图注·" + lead not in lead_row, "头条的装饰图不上页，示意图顶上"
    story = client.get("/story/{0}.html".format(numbers)).text
    assert "示意 · Codex" in story and "图源：" not in story.split("示意 · Codex", 1)[0]


def test_wechat_post_package_carries_the_three_parts_with_local_images(tmp_data_dir, monkeypatch, tmp_path):
    """公众号稿和首页同一份三部分内容：速读图 → N 秒读完（头条 / 深读加粗）→ 头条 / 深读 / 浅读；
    qly-publish.sh wechat-article 打成发布包时，正文图全部换成包内 imgs/ 相对路径、文件真的拷进去，
    发布脚本（baoyu-post-to-wechat）拿到的是一个自包含目录。"""
    import subprocess
    from pathlib import Path

    client, date_str, roles = _issue_with_three_depths(tmp_data_dir, monkeypatch)
    lead, deep_fig = roles["figured"]
    final = storage.read_json(paths.data_path("archive", date_str, daily.FINAL_NAME), default={})
    by_sig = {e["sig"]: e for e in final["items"]}
    md = paths.data_path("archive", date_str, daily.WECHAT_NAME).read_text(encoding="utf-8")
    # 标题 = 「千里眼 AI 日报 MM.DD｜」+ 头条那句；超过 64 字就去掉前缀，不在词中间截断
    title = md.split("\n", 2)[1][len("title: "):]
    head = daily._display_title(by_sig[lead])
    assert len(title) <= daily.WECHAT_TITLE_MAX, title
    assert title.endswith(head) or title == head[:daily.WECHAT_TITLE_MAX - 1] + "…", title
    assert "coverImage: imgs/cover.png" in md
    brief, body = md.index("秒读完今天"), md.index("## 今日头条")
    assert brief < body < md.index("## 深读") < md.index("## 浅读")
    # 这条的一句话结论里没有主体，速读退回用标题（同首页规则），且加粗
    assert "1. **{0}**".format(daily._display_title(by_sig[lead])) in md[brief:body], "速读里头条要加粗"
    for sig in roles["thin"]:
        assert "**{0}**".format(daily._display_title(by_sig[sig])) in md[md.index("## 浅读"):], "浅读只列标题"
        assert "薄条目的图·" + sig not in md
    assert "看这里：" not in md or "**看这里**" in md
    assert "原文没有这个数" not in md, "核对丢掉的数字也不许进公众号稿"

    repo = Path(daily.__file__).resolve().parents[2]
    out = tmp_path / "pkg"
    res = subprocess.run(["bash", str(repo / "scripts" / "qly-publish.sh"), "wechat-article", str(out), date_str],
                         capture_output=True, text=True, env=dict(os.environ, QLY_DATA_DIR=str(tmp_data_dir)))
    assert res.returncode == 0, res.stderr
    article = (out / "article.md").read_text(encoding="utf-8")
    refs = re.findall(r"!\[[^\]]*\]\(([^)]+)\)", article)
    assert refs and all(r.startswith("imgs/") for r in refs), refs
    assert all((out / r).is_file() for r in refs)
    assert "../../media/" not in article

    # 发布用的 HTML 正文：和首页同一套 token 与三部分结构；公众号只认行内样式，外链统一编号放文末
    html = (out / "article.html").read_text(encoding="utf-8")
    body = html.split("<body", 1)[1]
    assert "<style" not in body and "class=" not in body, "公众号会剥掉 <style> 与 class，样式必须全部行内"
    assert body.index(">看图知今天<") < body.index("秒读完今天<") < body.index(">正文<") < body.index(">原文链接<")
    assert "#d97757" in body and "#fefdfa" in body and "#faf9f5" in body, "要用首页同一套色"
    assert "<a " not in body and "href=" not in body, "公众号正文外链不可点，改成文末编号"
    assert "font-style:italic" not in body and "<em" not in body, "中文不用斜体"
    for e in final["items"]:
        assert e["url"] in body, "每条原文链接都要进文末"
    imgs = re.findall(r'<img\b[^>]*\bsrc="([^"]+)"', body)
    assert imgs and all(src.startswith("imgs/") and (out / src).is_file() for src in imgs), imgs
    lead_card = body.split(">正文<", 1)[1]
    assert "为什么重要" in lead_card and "↓" in lead_card, "头条卡要有逻辑链与为什么重要"


def test_wechat_newspic_package_is_speed_image_cover_and_brief_text(tmp_data_dir, monkeypatch, tmp_path):
    """公众号默认推贴图（newspic）：--html 出标题 + 纯文本文案，qly-publish.sh wechat 配上速读图（在前）与封面（在后）
    打成 index.html + imgs/。贴图标题 ≤20 字、正文 ≤1000 字、纯文本（没有链接、没有 Markdown 记号），
    N 秒读完的每一句按版面顺序都在；超字数时先丢头条那段、再从末尾收行，收尾那句始终在。"""
    import subprocess
    from pathlib import Path

    client, date_str, roles = _issue_with_three_depths(tmp_data_dir, monkeypatch)
    lead = roles["figured"][0]
    final = storage.read_json(paths.data_path("archive", date_str, daily.FINAL_NAME), default={})
    by_sig = {e["sig"]: e for e in final["items"]}
    arch = paths.data_path("archive", date_str)
    post = json.loads((arch / daily.NEWSPIC_NAME).read_text(encoding="utf-8"))
    assert post["title"] == "千里眼 AI 日报 · " + date_str[5:] and len(post["title"]) <= daily.NEWSPIC_TITLE_MAX
    text = post["text"]
    assert len(text) <= daily.NEWSPIC_TEXT_MAX
    assert "http" not in text and "**" not in text and "](" not in text, "贴图正文是纯文本"
    assert text.startswith("今天 {0} 条正文".format(len(final["items"])))
    assert "1. " + daily._display_title(by_sig[lead]) in text, "第一句是头条（没主体的结论退回标题）"
    numbered = re.findall(r"^(\d+)\. ", text, re.M)
    assert numbered == [str(i) for i in range(1, len(final["items"]) + 1)], "每条一句、按版面顺序编号"
    assert text.rstrip().endswith("见千里眼日报。")

    # 打包：3:4 封面在前（贴图首图即封面）、速读图在后
    (arch / "speed.png").write_bytes(b"\x89PNG speed")
    (arch / "cover-3x4.png").write_bytes(b"\x89PNG cover")
    repo = Path(daily.__file__).resolve().parents[2]
    out = tmp_path / "newspic"
    res = subprocess.run(["bash", str(repo / "scripts" / "qly-publish.sh"), "wechat", str(out), date_str],
                         capture_output=True, text=True, env=dict(os.environ, QLY_DATA_DIR=str(tmp_data_dir)))
    assert res.returncode == 0, res.stderr
    report = dict(line.split("=", 1) for line in res.stdout.splitlines() if "=" in line)
    assert report["cover"] == "manual" and report["images"] == "imgs/01-cover.png imgs/02-speed.png"
    page = (out / "index.html").read_text(encoding="utf-8")
    assert re.findall(r'<img src="([^"]+)"', page) == ["imgs/01-cover.png", "imgs/02-speed.png"], "贴图首图是 3:4 封面"
    assert (out / "imgs" / "01-cover.png").read_bytes() == b"\x89PNG cover"
    assert (out / "imgs" / "02-speed.png").read_bytes() == b"\x89PNG speed"
    assert "<title>{0}</title>".format(post["title"]) in page and "<a " not in page
    # 发布脚本的纯文本化：</p> → 换行、去标签、解实体，要能还原出同一份文案
    plain = html.unescape(re.sub(r"<[^>]+>", "", page.split("<body>", 1)[1].replace("</p>", "\n")))
    assert plain.strip() == text.strip()

    # 超字数：先丢头条那段，再从末尾收行并说明还剩几条；开头和收尾都留着
    monkeypatch.setattr(daily, "NEWSPIC_TEXT_MAX", 160)
    assert daily.cmd_html_only(date_str) == 0
    short = json.loads((arch / daily.NEWSPIC_NAME).read_text(encoding="utf-8"))["text"]
    assert len(short) <= 160 and "头条：" not in short
    assert short.startswith("今天 ") and short.rstrip().endswith("见千里眼日报。") and "条见日报" in short


def test_cover_is_standard_base_with_overlaid_text_and_packs_when_no_manual_cover(tmp_data_dir, monkeypatch, tmp_path):
    """封面不再每天靠 Codex 现画：--html 用入仓的无字标准底图叠上「千里眼 / AI 日报 / 日期 / 头条那句」，
    无头 Chrome 截成 2 倍的 cover-auto.png（1880×800，2.35:1，正文用）与 cover-3x4-auto.png（1080×1440，贴图首图）；
    打贴图包时没有手工 cover-3x4.png 就用自动的 3:4，有手工的仍然手工优先。"""
    import struct
    import subprocess
    from pathlib import Path

    client, date_str, roles = _issue_with_three_depths(tmp_data_dir, monkeypatch)
    lead = roles["figured"][0]
    final = storage.read_json(paths.data_path("archive", date_str, daily.FINAL_NAME), default={})
    by_sig = {e["sig"]: e for e in final["items"]}
    arch = paths.data_path("archive", date_str)
    page = (arch / daily.COVER_NAME).read_text(encoding="utf-8")
    for text in ("千里眼", "AI 日报", date_str, daily._display_title(by_sig[lead])):
        assert text in page, text
    base = re.search(r'<img class="base" src="file://([^"]+)"', page).group(1)
    assert Path(urllib.parse.unquote(base)).is_file(), "标准底图要入仓"
    tall = (arch / daily.COVER_3X4_NAME).read_text(encoding="utf-8")
    for text in ("千里眼", "AI 日报", date_str, daily._display_title(by_sig[lead]),
                 "今天 {0} 条正文".format(len(final["items"]))):
        assert text in tall, text
    assert 'url("file://' in tall and "cover-base.jpg" in tall, "竖版用同一张标准底图"

    if daily._chrome_binary() is None:
        pytest.skip("没有 Chrome，截图那一半验不了")
    monkeypatch.setenv("QLY_COVER_PNG", "1")
    assert daily.cmd_html_only(date_str) == 0
    png = (arch / daily.COVER_PNG_NAME).read_bytes()
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    assert struct.unpack(">II", png[16:24]) == (2 * daily.COVER_WIDTH, 2 * daily.COVER_HEIGHT)
    tall_png = (arch / daily.COVER_3X4_PNG_NAME).read_bytes()
    w, h = struct.unpack(">II", tall_png[16:24])
    assert (w, h) == (1080, 1440) and w * 4 == h * 3, "贴图封面必须 3:4"

    repo = Path(daily.__file__).resolve().parents[2]
    env = dict(os.environ, QLY_DATA_DIR=str(tmp_data_dir))

    def pack(name):
        out = tmp_path / name
        res = subprocess.run(["bash", str(repo / "scripts" / "qly-publish.sh"), "wechat", str(out), date_str],
                             capture_output=True, text=True, env=env)
        assert res.returncode == 0, res.stderr
        return out, dict(line.split("=", 1) for line in res.stdout.splitlines() if "=" in line)

    out, report = pack("auto")
    assert report["cover"] == "auto" and report["images"].startswith("imgs/01-cover.png")
    assert (out / "imgs" / "01-cover.png").read_bytes() == tall_png, "贴图首图用 3:4，不用横版"
    (arch / "cover-3x4.png").write_bytes(b"\x89PNG manual")
    out, report = pack("manual")
    assert report["cover"] == "manual" and (out / "imgs" / "01-cover.png").read_bytes() == b"\x89PNG manual"


def test_wechat_push_saves_newspic_draft_once_per_day(tmp_data_dir, monkeypatch, tmp_path):
    """定时任务打完贴图包紧接着 qly-publish.sh wechat-push 推进公众号草稿箱：在凭证目录下跑发布脚本、
    --type newspic、署名千里眼；成功记 pushed.json，重跑跳过（一天只进一份草稿），--force 才重推；
    发布脚本失败要非零退出、不留 pushed.json。用假发布器代替 npx bun wechat-api.ts。"""
    import subprocess
    from pathlib import Path

    client, date_str, roles = _issue_with_three_depths(tmp_data_dir, monkeypatch)
    arch = paths.data_path("archive", date_str)
    (arch / "speed.png").write_bytes(b"\x89PNG speed")
    repo = Path(daily.__file__).resolve().parents[2]
    creds = tmp_path / "creds"
    (creds / ".baoyu-skills").mkdir(parents=True)
    (creds / ".baoyu-skills" / ".env").write_text("WECHAT_APP_ID=x\n", encoding="utf-8")
    api = tmp_path / "wechat-api.ts"
    api.write_text("// fake\n", encoding="utf-8")
    calls = tmp_path / "calls.log"
    fake = tmp_path / "fake-bun.sh"
    fake.write_text("#!/bin/bash\n"
                    'echo "$PWD|$*" >> "{0}"\n'
                    '[ -n "$FAIL" ] && {{ echo "[wechat-api] errcode 40164 invalid ip" >&2; exit 1; }}\n'
                    "echo '[wechat-api] Publishing to draft...' >&2\n"
                    'printf \'{{\\n  "success": true,\\n  "media_id": "MID-1",\\n  "title": "T",\\n  "articleType": "newspic"\\n}}\\n\'\n'
                    .format(calls), encoding="utf-8")
    fake.chmod(0o755)
    env = dict(os.environ, QLY_DATA_DIR=str(tmp_data_dir), QLY_WECHAT_RUNNER=str(fake),
               QLY_WECHAT_API=str(api), QLY_WECHAT_CREDS_DIR=str(creds))

    def run(*args, **extra):
        return subprocess.run(["bash", str(repo / "scripts" / "qly-publish.sh")] + list(args),
                              capture_output=True, text=True, env=dict(env, **extra))

    pkg = arch / "wechat-newspic"
    assert run("wechat", str(pkg), date_str).returncode == 0
    res = run("wechat-push", str(pkg))
    assert res.returncode == 0, res.stderr
    report = dict(line.split("=", 1) for line in res.stdout.splitlines() if "=" in line)
    assert report["pushed"] == "yes" and report["media_id"] == "MID-1"
    cwd, argv = calls.read_text(encoding="utf-8").splitlines()[0].split("|", 1)
    assert Path(cwd).resolve() == creds.resolve(), "发布脚本按 cwd 找凭证，要在凭证目录下跑"
    assert argv == "{0} {1} --type newspic --author 千里眼".format(api, (pkg / "index.html").resolve())
    assert json.loads((pkg / "pushed.json").read_text(encoding="utf-8"))["media_id"] == "MID-1"

    again = run("wechat-push", str(pkg))
    assert again.returncode == 0 and "pushed=skip" in again.stdout and "media_id=MID-1" in again.stdout
    assert len(calls.read_text(encoding="utf-8").splitlines()) == 1, "推过的不再推"
    assert run("wechat-push", str(pkg), "--force").returncode == 0
    assert len(calls.read_text(encoding="utf-8").splitlines()) == 2

    (pkg / "pushed.json").unlink()
    bad = run("wechat-push", str(pkg), FAIL="1")
    assert bad.returncode != 0 and "40164" in bad.stderr
    assert not (pkg / "pushed.json").exists(), "失败不能记成已推"


def test_daily_deepdive_article_is_briefed_checked_rendered_and_pushed_as_news(tmp_data_dir, monkeypatch, tmp_path):
    """每天第二篇（正文）是一篇原文的深度解读：deepdive-prompt 自动挑原料最厚、有原文图的头条，简报带原文全文与配图；
    编辑交回的 JSON 关键数字必须逐字出自原文、配图下标合法，否则打回；通过后渲染成公众号行内样式正文 + 「深度解读」封面，
    wechat-deep 打正文包（图改成 imgs/、必须有封面），wechat-push 认出是正文、带封面按 news 推。"""
    import subprocess
    from pathlib import Path

    client, date_str, roles = _issue_with_three_depths(tmp_data_dir, monkeypatch)
    lead = roles["figured"][0]
    final = storage.read_json(paths.data_path("archive", date_str, daily.FINAL_NAME), default={})
    by_sig = {e["sig"]: e for e in final["items"]}
    arch = paths.data_path("archive", date_str)
    repo = Path(daily.__file__).resolve().parents[2]
    env = dict(os.environ, QLY_DATA_DIR=str(tmp_data_dir))

    def run(*args, **extra):
        return subprocess.run(["bash", str(repo / "scripts" / "qly-publish.sh")] + list(args),
                              capture_output=True, text=True, env=dict(env, **extra))

    brief = run("deepdive-prompt", date_str)
    assert brief.returncode == 0, brief.stderr
    head, _, rest = brief.stdout.partition("## 原文全文")
    assert "- sig：" + lead in head, "有原文图的头条优先"
    assert "The throughput improved by 3.3x" in rest, "简报要带原文全文"
    assert "0. [原文图]" in head and "aaaaaaaaaaaaaaaa.png" in head
    assert roles["figured"][1] in rest and roles["numbers"] in rest, "其余厚原料的头条 / 深读列为备选"
    assert all(sig not in brief.stdout for sig in roles["thin"]), "薄原料不配做深度解读"

    para = "这一段解释新 kernel 的做法和它为什么能把吞吐提上去，并说明适用条件与代价。" * 6
    doc = {"sig": lead, "title": "某团队新 kernel 吞吐提升 3.3x，延迟降到 120 ms", "summary": "一句话摘要",
           "lede": "导语先给结论：" + para,
           "sections": [{"heading": "做法", "paras": [para, para], "figure": 0, "figure_note": "看这里：最高那根柱"},
                        {"heading": "结果", "paras": [para, para], "figure": None, "figure_note": ""},
                        {"heading": "代价", "paras": [para, para], "figure": None, "figure_note": ""}],
           "numbers": ["3.3x", "120 ms"], "caveats": ["只测了一种卡。"], "so_what": ["值得在自己的负载上复测。"]}

    def apply(d, name):
        f = tmp_path / name
        f.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
        return run("deepdive", str(f), date_str)

    bad = apply(dict(doc, numbers=["3.3x", "9.9x"], sections=[dict(doc["sections"][0], figure=5)] + doc["sections"][1:]),
                "bad.json")
    assert bad.returncode != 0 and "9.9x" in bad.stdout + bad.stderr and "figure=5" in bad.stdout + bad.stderr
    assert not (arch / "deepdive.html").exists(), "没通过校验不许出稿"

    ok = apply(doc, "ok.json")
    assert ok.returncode == 0, ok.stdout + ok.stderr
    report = dict(line.split("=", 1) for line in ok.stdout.splitlines() if "=" in line)
    assert report["title"] == doc["title"] and int(report["chars"]) >= 1500
    page = (arch / "deepdive.html").read_text(encoding="utf-8")
    body = page.split("<body", 1)[1]
    assert "<style" not in body and "class=" not in body and "<a " not in body, "公众号只认行内样式、外链不可点"
    assert "font-style:italic" not in body
    for text in (doc["lede"][:20], "关键数字", "3.3x", "120 ms", "局限与存疑", "对你意味着什么", by_sig[lead]["url"]):
        assert text in body, text
    assert body.index(">01<") < body.index(">02<") < body.index(">03<")
    assert '../../media/aaaaaaaaaaaaaaaa.png' in body and "看这里：最高那根柱" in body
    assert "title: {0}".format(doc["title"]) in (arch / "deepdive.md").read_text(encoding="utf-8")
    assert "深度解读" in (arch / "cover-deep.html").read_text(encoding="utf-8")

    # 正文必须有封面：测试环境关了截图（QLY_COVER_PNG=0），没封面就不许打包
    out = arch / "wechat-deep"
    nocover = run("wechat-deep", str(out), date_str)
    assert nocover.returncode != 0 and "封面" in nocover.stderr
    (arch / "cover-deep.png").write_bytes(b"\x89PNG deep cover")
    packed = run("wechat-deep", str(out), date_str)
    assert packed.returncode == 0, packed.stderr
    article = (out / "article.html").read_text(encoding="utf-8")
    srcs = re.findall(r'<img\b[^>]*\bsrc="([^"]+)"', article)
    assert srcs == ["imgs/aaaaaaaaaaaaaaaa.png"] and (out / srcs[0]).is_file()
    assert (out / "imgs" / "cover.png").read_bytes() == b"\x89PNG deep cover"
    assert (out / "article.md").is_file()

    creds = tmp_path / "creds"
    (creds / ".baoyu-skills").mkdir(parents=True)
    (creds / ".baoyu-skills" / ".env").write_text("WECHAT_APP_ID=x\n", encoding="utf-8")
    api = tmp_path / "wechat-api.ts"
    api.write_text("// fake\n", encoding="utf-8")
    calls = tmp_path / "calls.log"
    fake = tmp_path / "fake-bun.sh"
    fake.write_text("#!/bin/bash\n"
                    'echo "$*" >> "{0}"\n'
                    'printf \'{{"success": true, "media_id": "MID-NEWS", "title": "T", "articleType": "news"}}\\n\'\n'
                    .format(calls), encoding="utf-8")
    fake.chmod(0o755)
    pushed = run("wechat-push", str(out), QLY_WECHAT_RUNNER=str(fake), QLY_WECHAT_API=str(api),
                 QLY_WECHAT_CREDS_DIR=str(creds))
    assert pushed.returncode == 0, pushed.stderr
    assert "pushed=yes" in pushed.stdout and "media_id=MID-NEWS" in pushed.stdout
    argv = calls.read_text(encoding="utf-8").strip()
    assert argv == "{0} {1} --type news --cover {2} --author 千里眼".format(
        api, (out / "article.html").resolve(), (out / "imgs" / "cover.png").resolve())


def test_scheduled_task_commands_cover_the_new_editing_steps(tmp_data_dir, monkeypatch, tmp_path):
    """定时任务只能调 qly-publish.sh：status 要按密度档报提炼覆盖（浅读不要逻辑链）、报哪些头条 / 深读
    没有信息图；figures 列出可选主图；depth 写定档并重新渲染；--synth-prompts 只挑没有信息图的头条 / 深读。"""
    import subprocess
    from pathlib import Path

    client, date_str, roles = _issue_with_three_depths(tmp_data_dir, monkeypatch)
    repo = Path(daily.__file__).resolve().parents[2]
    env = dict(os.environ, QLY_DATA_DIR=str(tmp_data_dir), QLY_SPEED_PNG="0")

    def run(*args):
        res = subprocess.run(["bash", str(repo / "scripts" / "qly-publish.sh")] + list(args),
                             capture_output=True, text=True, env=env)
        assert res.returncode == 0, res.stdout + res.stderr
        return res.stdout

    status = dict(line.split("=", 1) for line in run("status", date_str).splitlines() if "=" in line)
    total = int(status["distilled"].split("/")[1])
    assert status["distilled"] == "{0}/{0}".format(total), "浅读、深读不要求逻辑链"
    assert status["depths"].startswith("lead:1,")
    assert roles["numbers"] in status["deep_without_figure"].split(","), "没有原文信息图的深读要点名"

    figs = run("figures", date_str)
    lead = roles["figured"][0]
    assert "== {0}\tdepth=lead".format(lead) in figs and "kind=article" in figs and "aaaaaaaaaaaaaaaa.png" in figs

    out = tmp_path / "synth"
    res = subprocess.run([str(Path(daily.__file__).resolve().parents[2] / ".venv" / "bin" / "python")
                          if (repo / ".venv" / "bin" / "python").exists() else "python3",
                          "-m", "qianliyan.cli.daily_digest_all", "--synth-prompts", str(out), "--date", date_str],
                         capture_output=True, text=True, env=env, cwd=str(repo))
    assert res.returncode == 0, res.stderr
    picked = sorted(p.stem for p in (out / "prompts").glob("*.md"))
    assert picked == [roles["numbers"]], picked
    prompt = (out / "prompts" / "{0}.md".format(roles["numbers"])).read_text(encoding="utf-8")
    assert prompt.startswith("aspect: 16:9") and "【原文全文】" in prompt and "120 ms" in prompt

    plan = tmp_path / "depth.json"
    plan.write_text(json.dumps({"depth": [{"sig": roles["thin"][0], "depth": "glance"},
                                          {"sig": lead, "depth": "lead", "figure_note": "改过的图注"}]}),
                    encoding="utf-8")
    assert "密度档已写入 2 条" in run("depth", str(plan), date_str)
    assert "看这里：改过的图注" in client.get("/daily").text


def test_editor_figure_choices_survive_a_second_finalize(tmp_data_dir, monkeypatch, tmp_path):
    """07:30 的定时抓取会对当天再 finalize 一次、重新抓原文配图；编辑定的主图（「没有信息图」）和挂上的
    示意图必须接回来，不能回到原文的品牌横幅。"""
    client, date_str, roles = _issue_with_three_depths(tmp_data_dir, monkeypatch)
    lead, numbers = roles["figured"][0], roles["numbers"]
    synth = tmp_path / "synth.png"
    synth.write_bytes(b"\x89PNG\r\n\x1a\n" + b"keep" * 30)
    plan = tmp_path / "depth.json"
    plan.write_text(json.dumps({"depth": [
        {"sig": lead, "depth": "lead", "figure": -1, "synth_image": str(synth), "figure_note": "看示意图"},
        {"sig": numbers, "depth": "deep", "synth_image": str(synth)},
    ]}), encoding="utf-8")
    assert daily.cmd_apply_depth(date_str, str(plan)) == 0
    assert daily.cmd_finalize(date_str, do_html=True) == 0, "第二次定稿"

    final = storage.read_json(paths.data_path("archive", date_str, daily.FINAL_NAME), default={})
    by_sig = {e["sig"]: e for e in final["items"]}
    assert by_sig[lead]["depth"] == "lead" and by_sig[lead]["figure_note"] == "看示意图"
    assert [f["kind"] for f in by_sig[lead]["figures"]] == ["synth", "decorative"], "装饰判定与示意图要接回来"
    assert by_sig[numbers]["figures"][0]["kind"] == "synth"
    row = re.search(r'<article[^>]*data-sig="{0}".*?</article>'.format(lead), client.get("/daily").text, re.S).group(0)
    assert "示意 · Codex" in row and "原文图注·" + lead not in row


def test_reapplying_the_same_depth_file_keeps_the_same_main_figure(tmp_data_dir, monkeypatch, tmp_path):
    """定档里的 figure 指抓取时的原始序号（qly-publish.sh figures 打印的那个），不是当前位置：同一份定档跑两遍、
    或定稿后再定一次，主图都不能漂到别的图上。也可以直接写文件名。"""
    client, date_str, roles = _issue_with_three_depths(tmp_data_dir, monkeypatch)
    lead = roles["figured"][0]
    final_path = paths.data_path("archive", date_str, daily.FINAL_NAME)
    final = storage.read_json(final_path, default={})
    entry = next(e for e in final["items"] if e["sig"] == lead)
    for name in ("cccccccccccccccc.png", "dddddddddddddddd.png"):
        (paths.data_path("media") / name).write_bytes(b"\x89PNG\r\n\x1a\n" + name.encode() * 4)
    entry["figures"] = [dict(entry["figures"][0], n=0),
                        {"file": "media/cccccccccccccccc.png", "src": "https://x/c.png", "kind": "article", "n": 1},
                        {"file": "media/dddddddddddddddd.png", "src": "https://x/d.png", "kind": "article", "n": 2}]
    storage.write_json(final_path, final)
    plan = tmp_path / "depth.json"
    plan.write_text(json.dumps({"depth": [{"sig": lead, "depth": "lead", "figure": 2}]}), encoding="utf-8")
    for _ in range(2):
        assert daily.cmd_apply_depth(date_str, str(plan)) == 0
        first = storage.read_json(final_path, default={})
        main = next(e for e in first["items"] if e["sig"] == lead)["figures"][0]["file"]
        assert main == "media/dddddddddddddddd.png", main
    plan.write_text(json.dumps({"depth": [{"sig": lead, "depth": "lead", "figure": "cccccccccccccccc.png"}]}),
                    encoding="utf-8")
    assert daily.cmd_apply_depth(date_str, str(plan)) == 0
    main = next(e for e in storage.read_json(final_path, default={})["items"] if e["sig"] == lead)["figures"][0]["file"]
    assert main == "media/cccccccccccccccc.png"


def test_tiering_rechecks_numbers_and_quotes_written_by_other_entry_points(tmp_data_dir, monkeypatch, tmp_path):
    """定稿可能是旧代码或手改写的（没过核对）：定档时再逐字核一遍，原文没有的数字、不是原话的梗不上页。"""
    client, date_str, roles = _issue_with_three_depths(tmp_data_dir, monkeypatch)
    lead = roles["figured"][0]
    final_path = paths.data_path("archive", date_str, daily.FINAL_NAME)
    final = storage.read_json(final_path, default={})
    entry = next(e for e in final["items"] if e["sig"] == lead)
    entry["meme"] = "一句编出来的梗"
    entry["stats"] = [{"value": "77%", "label": "原文没有"}, {"value": "3.3x", "label": "吞吐提升"}]
    storage.write_json(final_path, final)
    plan = tmp_path / "depth.json"
    plan.write_text(json.dumps({"depth": [{"sig": lead, "depth": "lead"}]}), encoding="utf-8")
    assert daily.cmd_apply_depth(date_str, str(plan)) == 0
    assert daily.cmd_html_only(date_str) == 0
    page = TestClient(api_server.create_app()).get("/daily").text
    assert "一句编出来的梗" not in page and "77%" not in page
    assert "3.3x" in page

    # 照译不算编：原文写 november 30，中文写 11 月 30 日
    final = storage.read_json(final_path, default={})
    entry = next(e for e in final["items"] if e["sig"] == lead)
    entry["extra"]["fulltext"] += " Prices rise after November 30."
    entry["stats"] = [{"value": "3.3x", "label": "11 月 30 日前"}]
    storage.write_json(final_path, final)
    assert daily.cmd_apply_depth(date_str, str(plan)) == 0
    entry = next(e for e in storage.read_json(final_path, default={})["items"] if e["sig"] == lead)
    assert entry["stats"] == [{"value": "3.3x", "label": "11 月 30 日前"}]
