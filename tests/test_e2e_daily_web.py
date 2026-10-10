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
    # 回退时每条都得有能读的内容（摘要顶上），而不是一格格「—」的空壳：
    # 规则回退的「四段提炼」只是原文前三句 + 三个破折号，曾把英文原句和网页报错文字当成要点
    final = storage.read_json(paths.data_path("archive", date_str, daily.FINAL_NAME), default={})
    for entry in final["items"]:
        summary = daily._summary_text(entry)
        if summary:
            assert summary[:20] in deep.text, "离线回退下条目没有内容：{0}".format(entry.get("title"))
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
        body = page.split('id="wrap-timeline"', 1)[0].split("</header>", 1)[-1]
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

    home = client.get("/daily").text
    assert date_str in home
    assert "按语1：值得读" in home, "Agent 写的按语没到读者眼前"
    # 要点是每条的正文：日报页上直接展开，不用点进原文
    brief_seen = (tmp_data_dir / "prompt-seen.txt.brief").read_text(encoding="utf-8")
    glance = client.get("/daily", params={"view": "glance"}).text
    deep = client.get("/daily", params={"view": "deep"}).text
    for entry in picked:
        assert "要点甲·" + entry["sig"] in glance, "要点没直接展开在日报（浅读）页上"
        assert "要点甲·" + entry["sig"] in deep, "深读页没有要点"
        assert entry["url"] in brief_seen, "写要点的简报里没带原文出处"
    story = client.get("/story/{0}.html".format(picked[0]["sig"])).text
    assert "要点乙·" + picked[0]["sig"] in story, "详情页没有要点"
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

    picks = [{"i": i, "editor_note": "任务按语{0}".format(n), "title_zh": "", "summary_zh": ""}
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
    assert all("任务要点一·" + sig in index for sig in sigs), "要点没进发布页"
    assert "不该出现的要点" not in index
    # 关键数字与对比图：数值原样上版、条宽按数值归一（33.2 最大 → 100%，16.6 → 50%）
    assert "$2/$10" in index and "降价幅度" in index, "关键数字没上版"
    # 提炼是日报卡片的主体：结论、梗、逻辑链（关键一环高亮）、证据与代价；
    # 有结论的条目，细节要点只留在详情页，日报页不再铺一屏字
    glance = index.split('id="wrap-timeline"', 1)[0]
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
    assert re.search(r'data-track="agent" aria-pressed="true"', glance)
    assert not re.search(r'class="trk-chip" data-track="models"', glance), "必看的方向不该能被取消关注"
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
