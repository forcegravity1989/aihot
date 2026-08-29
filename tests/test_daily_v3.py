"""test_daily_v3.py —— spec-v0.3 §6 / §12 / §16：深读 / 浅读日报双视图 + 深读增强。

全部离线：``tmp_data_dir`` 置 ``QLY_OFFLINE=1``，另显式 delenv 掉 API key，
LLM 不可用 → distill 走规则回退，distill 字段仍须齐全、不阻塞。
"""

from __future__ import annotations

import re

import pytest

from qianliyan.core import paths, storage, utils
from qianliyan.cli import daily_digest_all as d

DATE = "2026-08-25"


@pytest.fixture(autouse=True)
def _force_offline(monkeypatch):
    """封网双保险：无论本机有无真实 key，深读增强都必须走回退。"""
    monkeypatch.setenv("QLY_OFFLINE", "1")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)


def _draft_item(sig, title, **kw):
    return {
        "sig": sig,
        "title": title,
        "url": kw.get("url", "https://example.com/{0}".format(sig)),
        "source": kw.get("source", "Src"),
        "source_kind": kw.get("source_kind", "local"),
        "backend": kw.get("backend", "rss"),
        "source_list": kw.get("source_list", [kw.get("source", "Src")]),
        "date": kw.get("date", utils.iso(utils.now_utc())),
        "hotness": kw.get("hotness", 0.5),
        "weight": kw.get("weight", 0.7),
        "cross_refs": kw.get("cross_refs", 0),
        "tags": kw.get("tags", ["models"]),
        "badges": kw.get("badges", []),
        "summary": kw.get("summary", "第一句话。第二句话。第三句话。第四句话。"),
        "metrics": kw.get("metrics", {}),
        "extra": kw.get("extra", {}),
        "selected": kw.get("selected", True),
        "editor_note": "",
    }


def _write_draft(date, items):
    storage.write_json(paths.data_path("archive", date, d.DRAFT_NAME), {
        "date": date, "generated_at": utils.iso(utils.now_utc()), "items": items,
    })


# =========================================================================
# format 推断（spec-v0.3 §12）
# =========================================================================
def test_infer_format_prefers_explicit_extra_format():
    assert d.infer_format({"extra": {"format": "podcast"}, "tags": []}) == "podcast"


def test_infer_format_fallbacks():
    assert d.infer_format({"source_kind": "builders", "extra": {"platform": "x"}, "tags": ["x"]}) == "x"
    assert d.infer_format({"backend": "arxiv", "tags": ["arxiv"], "extra": {}}) == "paper"
    assert d.infer_format({"backend": "git", "tags": ["trending"], "extra": {}}) == "repo"
    assert d.infer_format({"extra": {"platform": "youtube"}, "tags": ["video"]}) == "video"
    assert d.infer_format({"tags": ["blog", "official"], "extra": {}}) == "blog"
    assert d.infer_format({"tags": [], "extra": {}}) == "news"


# =========================================================================
# --prepare：候选按 personal_score 回退 hotness
# =========================================================================
def test_prepare_ranks_by_personal_score_then_hotness(tmp_data_dir):
    from qianliyan.core import schema

    a = schema.make_item(title="A 纯热度高", url="https://a/a", source="S", source_kind="local", backend="rss", weight=0.9, tags=["models"])
    a["hotness"] = 0.9  # 无 personal_score → rank 0.9
    b = schema.make_item(title="B 个性化最高", url="https://a/b", source="S", source_kind="local", backend="rss", weight=0.5, tags=["models"])
    b["hotness"] = 0.1
    b["extra"]["personal_score"] = 0.99  # rank 0.99
    c = schema.make_item(title="C 个性化低", url="https://a/c", source="S", source_kind="local", backend="rss", weight=0.5, tags=["models"])
    c["hotness"] = 0.5
    c["extra"]["personal_score"] = 0.2  # rank 0.2

    storage.write_jsonl(tmp_data_dir / "items.jsonl", [a, b, c])
    rc = d.cmd_prepare(DATE)
    assert rc == 0

    draft = storage.read_json(paths.data_path("archive", DATE, d.DRAFT_NAME))
    titles = [it["title"] for it in draft["items"]]
    assert titles[0] == "B 个性化最高", "personal_score 最高者应排首位"
    assert titles.index("B 个性化最高") < titles.index("A 纯热度高") < titles.index("C 个性化低")


# =========================================================================
# --finalize：回退 distill 字段齐全（离线）
# =========================================================================
def test_finalize_fallback_distill_is_complete_offline(tmp_data_dir):
    _write_draft(DATE, [
        _draft_item("s1", "条目一", summary="要点甲。要点乙。要点丙。要点丁。",
                    extra={"og_image": "https://og/1.png", "images": ["https://img/a.png", "https://og/1.png"]}),
        _draft_item("s2", "条目二", summary="单句无标点结尾"),
    ])
    rc = d.cmd_finalize(DATE, do_html=False)
    assert rc == 0

    final = storage.read_json(paths.data_path("archive", DATE, d.FINAL_NAME))
    assert len(final["items"]) == 2
    for it in final["items"]:
        distill = it["distill"]
        for key in ("kp", "chain", "pull", "limits", "theses"):
            assert key in distill, "回退 distill 必须含字段 {0}".format(key)
        assert isinstance(distill["kp"], list)
        assert distill["theses"] == [], "回退 theses 恒为空列表"
        assert "images" in it and isinstance(it["images"], list)
        assert "format" in it

    first = final["items"][0]
    assert first["distill"]["kp"] == ["要点甲。", "要点乙。", "要点丙。"], "kp = 摘要前 3 句"
    # images 合并 og_image + extra.images，保序去重
    assert first["images"] == ["https://og/1.png", "https://img/a.png"]


def test_finalize_without_selected_fails_gracefully(tmp_data_dir):
    _write_draft(DATE, [_draft_item("s1", "未选", selected=False)])
    rc = d.cmd_finalize(DATE, do_html=False)
    assert rc == 1


# =========================================================================
# --html：浅读 / 深读 / 合并页三件套
# =========================================================================
def test_finalize_html_produces_three_views(tmp_data_dir):
    _write_draft(DATE, [
        _draft_item("s1", "模型发布要闻", tags=["blog", "official"], badges=["heavy", "flash"],
                    source_list=["Anthropic News", "Simon Willison"],
                    extra={"format": "blog", "images": ["https://img/hero.png"]}),
        _draft_item("s2", "cool/repo", tags=["trending"], source="GitHub Trending",
                    metrics={"stars": "1200", "stars_period": "340"},
                    extra={"format": "repo", "language": "Python"}),
    ])
    rc = d.cmd_finalize(DATE, do_html=True)
    assert rc == 0

    glance = paths.data_path("archive", DATE, d.GLANCE_NAME)
    deep = paths.data_path("archive", DATE, d.DEEP_NAME)
    merged = paths.data_path("archive", DATE, d.MERGED_NAME)
    daily_root = paths.data_path(d.DAILY_ROOT_NAME)
    for p in (glance, deep, merged, daily_root):
        assert p.is_file(), "缺产物 {0}".format(p)

    g = glance.read_text(encoding="utf-8")
    dp = deep.read_text(encoding="utf-8")
    m = merged.read_text(encoding="utf-8")

    # 日报视图：编号分节 + 标题 + 摘要段 + 已读控件 + localStorage，无大图
    assert "今日日报" in g and "全部已接收" in g and "标记已读" in g
    assert "localStorage" in g
    assert "<img" not in g, "日报视图极简无大图"
    assert "📈" in g or "⚡" in g or "源" in g  # 交叉验证徽章
    assert "模型发布要闻" in g
    assert 'class="report-section-no"' in g, "日报分节要有编号（对齐经典日报版式）"
    assert 'class="report-entry-summary"' in g, "日报条目要带摘要段，不能只有一行标题"
    assert "story/s1.html" in g, "日报条目标题应链到详情页"
    assert "{{" not in g and "{%" not in g

    # 深读：distill 四段 + 交叉验证展开 + 各源 + repo 卡 star/语言
    for seg in ("要点", "脉络", "影响", "局限"):
        assert seg in dp, "深读缺 distill 段落 {0}".format(seg)
    assert "交叉验证" in dp
    assert "Anthropic News" in dp and "Simon Willison" in dp  # source_list 各源
    assert "阅读原文" in dp
    assert "https://img/hero.png" in dp  # 配图
    assert "⭐" in dp and "1200" in dp and "340" in dp and "Python" in dp  # repo 卡
    assert "{{" not in dp and "{%" not in dp

    # 合并首页：日报 / 时间轴 / 深读三视图，切换是**纯 CSS**（沙箱常不执行脚本）
    assert "日报" in m and "时间轴" in m and "深读" in m
    assert 'id="qly-pick-glance"' in m and 'id="qly-pick-timeline"' in m and 'id="qly-pick-deep"' in m
    assert 'for="qly-pick-glance"' in m and 'for="qly-pick-deep"' in m
    assert "#qly-pick-deep:checked ~ .qly-app #wrap-deep" in m  # :checked 驱动显示
    assert "wrap-glance" in m and "wrap-timeline" in m and "wrap-deep" in m
    assert "模型发布要闻" in m  # 内嵌了日报片段
    # 版面：侧栏 + 热点榜（首页不只是三段内容摞在一起）
    assert 'class="qly-sidebar"' in m and 'class="qly-nav"' in m
    assert 'class="hot-topics"' in m and "今日热点" in m
    # 设计系统：唯一 token 源被内联进来，且页面里不该再有第二套配色变量
    assert "--theme-accent" in m and "--surface-card" in m
    assert "--g-brand" not in m and "--t-brand" not in m, "旧的散装配色变量应已清干净"
    assert "{{" not in m and "{%" not in m

    # 合并页复制到数据根
    assert daily_root.read_text(encoding="utf-8") == m


def test_merged_toggle_works_without_javascript(tmp_data_dir):
    """回归用例：切换不能依赖 JS——沙箱预览器（聊天内嵌、文件面板、邮件客户端）
    常常不执行页面脚本，用户点「深读」会毫无反应、只能看到浅读。
    """
    _write_draft(DATE, [_draft_item("s1", "条目一")])
    d.cmd_finalize(DATE, do_html=True)
    merged = (paths.data_path("archive", DATE, d.MERGED_NAME)).read_text(encoding="utf-8")

    # 结构：隐藏 radio + label[for]，纯 CSS :checked 控制显示
    assert '<input class="qly-switch" type="radio"' in merged
    assert "#qly-pick-glance:checked ~ .qly-app #wrap-glance" in merged
    assert "#qly-pick-timeline:checked ~ .qly-app #wrap-timeline" in merged
    assert "#qly-pick-deep:checked ~ .qly-app #wrap-deep" in merged
    # 默认浅读：glance 那个 radio 带 checked
    glance_input = merged[merged.index('id="qly-pick-glance"') - 80: merged.index('id="qly-pick-glance"') + 40]
    assert "checked" in glance_input
    # 关键：切换本身不得再依赖脚本（浅读的「标记已读」仍可用 JS 做渐进增强，
    # 那是可有可无的功能；切换是核心导航，必须无 JS 也能用）
    assert 'data-view="deep"' not in merged, "旧的 JS 切换钩子应已移除"
    assert 'show("glance")' not in merged, "旧的 JS 切换初始化应已移除"
    assert ".daily-toggle button" not in merged, "切换控件应是 label，不是 button+JS"


def test_html_only_reuses_final_doc(tmp_data_dir):
    _write_draft(DATE, [_draft_item("s1", "条目一")])
    d.cmd_finalize(DATE, do_html=False)
    rc = d.cmd_html_only(DATE)
    assert rc == 0
    assert paths.data_path("archive", DATE, d.GLANCE_NAME).is_file()


def test_html_only_without_final_doc_fails(tmp_data_dir):
    rc = d.cmd_html_only(DATE)
    assert rc == 1


# =========================================================================
# 深读论点 + 浅读 format 分组
# =========================================================================
def test_deep_card_highlights_theses_when_present(tmp_data_dir):
    items = [{
        "sig": "s1", "title": "大会演讲", "url": "https://yt/1",
        "source": "Anthropic YouTube", "source_list": ["Anthropic YouTube"],
        "badges": [], "date": utils.iso(utils.now_utc()), "summary": "演讲全文摘要。",
        "tags": ["talks"], "metrics": {},
        "extra": {"format": "talk", "thumbnail": "https://yt/thumb.jpg"},
        "distill": {"kp": ["要点一"], "chain": "脉络串联", "pull": "影响巨大",
                    "limits": "样本有限", "theses": ["论点甲", "论点乙"]},
        "images": [],
    }]
    dp = d.render_deep(DATE, items)
    assert "关键论点" in dp
    assert "论点甲" in dp and "论点乙" in dp
    assert "脉络串联" in dp and "影响巨大" in dp and "样本有限" in dp
    assert "演讲要点" in dp  # talk/video 专属提示
    assert "https://yt/thumb.jpg" in dp  # 缩略图


def test_glance_groups_by_format_in_order(tmp_data_dir):
    now = utils.now_utc()

    def mk(sig, title, fmt):
        return {"sig": sig, "title": title, "url": "https://a/" + sig, "source": "S",
                "source_list": ["S"], "badges": [], "date": utils.iso(now),
                "summary": "", "tags": [], "metrics": {}, "extra": {"format": fmt}}

    items = [mk("n", "资讯条", "news"), mk("b", "博客条", "blog"),
             mk("p", "论文条", "paper"), mk("x", "推文条", "x")]
    g = d.render_glance(DATE, items)

    # 分组标题（图标 + 中文）都在
    for icon, label in (("📰", "资讯"), ("📝", "博客"), ("📄", "论文"), ("🐦", "X 动态")):
        assert icon in g and label in g

    # FORMAT_ORDER：news < blog < paper < x
    assert g.index("资讯") < g.index("博客") < g.index("论文") < g.index("X 动态")


def test_render_functions_survive_empty_items(tmp_data_dir):
    assert "今日暂无条目" in d.render_glance(DATE, [])
    assert "今日暂无条目" in d.render_deep(DATE, [])
    m = d.render_merged(DATE, [])
    assert "日报" in m and "时间轴" in m and "深读" in m


# =========================================================================
# YouTube 字幕接入深读（spec-v0.3 §18.2）—— 离线以 monkeypatch 提供字幕，全程不出网
# =========================================================================
def test_finalize_attaches_transcript_and_distills_from_it(tmp_data_dir, monkeypatch):
    """video/talk + video_id：抓字幕全文存 extra.transcript，distill 以字幕为输入（非 RSS 摘要）。"""
    fake_transcript = "字幕第一句。字幕第二句。字幕第三句。字幕第四句。"
    seen = {}

    def _fake_get(video_id, *args, **kwargs):
        seen["video_id"] = video_id
        return fake_transcript

    monkeypatch.setattr(d.youtube_transcript, "get_transcript", _fake_get)

    _write_draft(DATE, [
        _draft_item("v1", "大会演讲", tags=["talks"], summary="RSS media:description 占位摘要。",
                    extra={"format": "talk", "video_id": "VID123"}),
    ])
    rc = d.cmd_finalize(DATE, do_html=False)
    assert rc == 0
    assert seen["video_id"] == "VID123", "应对 video_id 抓字幕"

    final = storage.read_json(paths.data_path("archive", DATE, d.FINAL_NAME))
    it = final["items"][0]
    assert it["extra"]["transcript"] == fake_transcript
    # 离线无 LLM → kp 回退取「字幕全文」前 3 句，而非 RSS 摘要
    assert it["distill"]["kp"] == ["字幕第一句。", "字幕第二句。", "字幕第三句。"]


def test_finalize_transcript_none_falls_back_to_summary(tmp_data_dir, monkeypatch):
    """字幕抓取失败/None：不写 extra.transcript，distill 回退用 RSS 摘要。"""
    monkeypatch.setattr(d.youtube_transcript, "get_transcript", lambda *a, **k: None)
    _write_draft(DATE, [
        _draft_item("v1", "演讲", tags=["talks"], summary="摘要甲。摘要乙。摘要丙。",
                    extra={"format": "talk", "video_id": "VID"}),
    ])
    d.cmd_finalize(DATE, do_html=False)
    final = storage.read_json(paths.data_path("archive", DATE, d.FINAL_NAME))
    it = final["items"][0]
    assert "transcript" not in it["extra"]
    assert it["distill"]["kp"] == ["摘要甲。", "摘要乙。", "摘要丙。"]


def test_finalize_non_video_never_fetches_transcript(tmp_data_dir, monkeypatch):
    """非 video/talk 条目不触发字幕抓取（不该调用 get_transcript）。"""
    def _boom(*a, **k):
        raise AssertionError("非 video/talk 不应抓字幕")

    monkeypatch.setattr(d.youtube_transcript, "get_transcript", _boom)
    _write_draft(DATE, [_draft_item("b1", "博客", tags=["blog"], extra={"format": "blog"})])
    assert d.cmd_finalize(DATE, do_html=False) == 0


# =========================================================================
# 叙事↔实证 corroboration 在深读卡呈现（spec-v0.3 §19.3）
# =========================================================================
def _corrob_item(verdict, **extra_kw):
    extra = {"format": "talk", "corroboration": {
        "claim": "Fable5 减少 80% 提示词",
        "evidence": "changelog: -30,636 tokens",
        "verdict": verdict,
    }}
    extra.update(extra_kw)
    return {
        "sig": "c1", "title": "Fable5 提示词大幅缩减", "url": "https://x/talk",
        "source": "Talk", "source_list": ["Talk"], "badges": [],
        "date": utils.iso(utils.now_utc()), "summary": "断言摘要。", "tags": ["talks"],
        "metrics": {}, "extra": extra,
        "distill": {"kp": ["k"], "chain": "", "pull": "", "limits": "", "theses": []},
        "images": [],
    }


def test_deep_card_renders_corroboration_when_present(tmp_data_dir):
    dp = d.render_deep(DATE, [_corrob_item("corroborated")])
    assert "🔬 实证" in dp
    assert "Fable5 减少 80% 提示词" in dp
    assert "changelog: -30,636 tokens" in dp
    assert "deep-corrob-corroborated" in dp
    assert "{{" not in dp and "{%" not in dp


def test_deep_card_corroboration_verdict_labels(tmp_data_dir):
    assert "🔬 存疑" in d.render_deep(DATE, [_corrob_item("unverified")])
    assert "🔬 矛盾" in d.render_deep(DATE, [_corrob_item("contradicted")])


def test_deep_card_omits_corroboration_when_absent_or_unknown(tmp_data_dir):
    plain = {
        "sig": "n1", "title": "无实证", "url": "https://x/n", "source": "S",
        "source_list": ["S"], "badges": [], "date": utils.iso(utils.now_utc()),
        "summary": "s", "tags": [], "metrics": {}, "extra": {"format": "news"},
        "distill": {"kp": [], "chain": "", "pull": "", "limits": "", "theses": []},
        "images": [],
    }
    dp = d.render_deep(DATE, [plain])
    # CSS 里恒有 .deep-corrob 类名，判据用「渲染出的元素」与徽章 emoji
    assert 'class="deep-corrob' not in dp
    assert "🔬" not in dp
    # 未知 verdict 也不呈现
    assert d._corroboration_view({"corroboration": {"verdict": "bogus"}}) is None


# =========================================================================
# 时间轴视图 & 详情页（v0.3 版面重构）
# =========================================================================
def test_timeline_groups_by_day_and_orders_newest_first(tmp_data_dir):
    """时间轴按自然日分组、日内新的在前——这是它和日报视图（按类目分节）的分工。"""
    items = [
        _draft_item("old", "前天的事", date="2026-08-23T02:00:00+00:00"),
        _draft_item("new", "今天的事", date="2026-08-25T09:30:00+00:00"),
        _draft_item("mid", "今天早些", date="2026-08-25T01:15:00+00:00"),
    ]
    html = d.render_timeline(DATE, items, now=utils.parse_date("2026-08-25T12:00:00+00:00"))

    assert "8月25日" in html and "8月23日" in html
    assert html.index("8月25日") < html.index("8月23日"), "近的日期在上"
    assert html.index("今天的事") < html.index("今天早些"), "日内新的在前"
    assert "09:30" in html and "01:15" in html
    assert 'class="timeline-dot"' in html and 'class="timeline-rail"' in html
    assert "{{" not in html and "{%" not in html


def test_timeline_keeps_undated_items_in_a_separate_tail_group(tmp_data_dir):
    """解析不出时间的条目单独归到「时间未知」，不硬塞进某一天污染时序。"""
    items = [
        _draft_item("ok", "有时间", date="2026-08-25T09:30:00+00:00"),
        _draft_item("bad", "没时间", date="not-a-date"),
    ]
    html = d.render_timeline(DATE, items)
    assert "时间未知" in html
    assert html.index("有时间") < html.index("没时间")


def test_detail_page_written_for_each_item_in_both_locations(tmp_data_dir):
    """每条一页详情页，归档与数据根各落一份，返回链接各自指对自己那份日报页。"""
    _write_draft(DATE, [
        _draft_item("s1", "模型发布要闻", source_list=["A News", "B Blog"],
                    extra={"format": "blog"}),
        _draft_item("s2", "另一条"),
    ])
    assert d.cmd_finalize(DATE, do_html=True) == 0

    archived = paths.data_path("archive", DATE, d.DETAIL_DIR, "s1.html")
    rooted = paths.data_path(d.DETAIL_DIR, "s1.html")
    for p in (archived, rooted, paths.data_path(d.DETAIL_DIR, "s2.html")):
        assert p.is_file(), "缺详情页 {0}".format(p)

    page = rooted.read_text(encoding="utf-8")
    assert "模型发布要闻" in page
    assert "AI 导读" in page and "深读提炼" in page
    assert "A News" in page and "B Blog" in page  # 来源清单
    assert "打开原文" in page
    assert "--theme-accent" in page, "详情页要吃同一套设计系统"
    assert "{{" not in page and "{%" not in page

    # 返回链接：数据根那份回 daily.html，归档那份回 digest.html
    assert 'href="../{0}"'.format(d.DAILY_ROOT_NAME) in page
    assert 'href="../{0}"'.format(d.MERGED_NAME) in archived.read_text(encoding="utf-8")


def test_detail_pages_do_not_collide_with_sync_items_dir(tmp_data_dir):
    """详情页目录不能是 items/——那是 cli.sync 放各眼原始 jsonl 的地方。"""
    assert d.DETAIL_DIR != "items"
    _write_draft(DATE, [_draft_item("s1", "条目一")])
    d.cmd_finalize(DATE, do_html=True)
    stray = list(paths.data_path("items").glob("*.html")) if paths.data_path("items").is_dir() else []
    assert not stray, "详情页不该写进 items/"


def test_all_pages_share_one_token_source(tmp_data_dir):
    """设计系统只有一份：三个视图页与详情页都内联同一套 token，且不得残留旧配色变量。"""
    _write_draft(DATE, [_draft_item("s1", "条目一")])
    assert d.cmd_finalize(DATE, do_html=True) == 0

    pages = [
        paths.data_path("archive", DATE, d.GLANCE_NAME),
        paths.data_path("archive", DATE, d.TIMELINE_NAME),
        paths.data_path("archive", DATE, d.DEEP_NAME),
        paths.data_path(d.DAILY_ROOT_NAME),
        paths.data_path(d.DETAIL_DIR, "s1.html"),
    ]
    for path in pages:
        text = path.read_text(encoding="utf-8")
        assert "--theme-accent: #135e6b" in text, "{0} 没吃到设计系统".format(path.name)
        assert "prefers-color-scheme: dark" in text, "{0} 缺暗色适配".format(path.name)
        for legacy in ("--g-brand", "--t-brand", "#2f6df6"):
            assert legacy not in text, "{0} 残留旧配色 {1}".format(path.name, legacy)


def test_theme_css_comments_do_not_trip_whole_page_assertions():
    """护栏：设计系统 CSS 会被**原样内联进每一个产物页**，它的注释因此处在全文断言的
    射程内。历史上这里踩过三次——注释里写了模板标记、写了外链引入关键字、提了一句旧
    配色 hex，分别误触发了「不得残留未渲染模板标记」「页面必须单文件自包含」「不得
    残留旧配色」三条断言。规则：讲这些概念可以，别写它们的字面量。
    """
    from qianliyan.pipeline import theme

    css = theme.load_theme_css()
    forbidden = ("{" + "{", "{" + "%", "@" + "import", "#2f6df6", "--g-brand", "--t-brand")
    hit = [token for token in forbidden if token in css]
    assert not hit, "_theme.css 含会误触发全文断言的字面量: {0}".format(hit)
    assert not re.search(r"url\(\s*['\"]?https?://", css), "_theme.css 不得引外部资源"


def test_timeline_renders_three_time_precisions_honestly(tmp_data_dir):
    """时间轴不得把三种精度混成一种。

    源缺 date 时 core.schema 会补当前时刻（一批条目全撞同一秒），只给到天的源一律
    00:00——两种都会被渲染成精确到分钟的假时刻。读者必须能分辨「04:26 发布」、
    「这天发的，不知道几点」和「根本不知道什么时候」。
    """
    items = [
        _draft_item("exact", "有真实时分", date="2026-08-25T09:30:00+00:00",
                    extra={"date_precision": "exact"}),
        _draft_item("day", "只知道是这天", date="2026-08-25T00:00:00+00:00",
                    extra={"date_precision": "day"}),
        _draft_item("unknown", "时间是补的", date="2026-08-25T23:59:59+00:00",
                    extra={"date_precision": "unknown"}),
    ]
    html = d.render_timeline(DATE, items)

    assert ">09:30<" in html, "精确的要显示到分"
    assert ">全天<" in html, "只到天的不许编造时分"
    assert ">—<" in html, "未知的干脆不给时间"
    assert ">23:59<" not in html, "补出来的时刻不许当真实发布时间显示"

    # 日内排序：有真实时分的在前，日粒度/未知的沉到末尾——否则「补成当前时刻」的
    # 条目会冒充成今天最新，排在最上面
    assert html.index("有真实时分") < html.index("只知道是这天")
    assert html.index("有真实时分") < html.index("时间是补的")


def test_timeline_infers_precision_for_legacy_rows(tmp_data_dir):
    """老数据没有 date_precision（这字段是后加的）：零点当日粒度，其余当精确。"""
    items = [
        _draft_item("legacy_day", "老的日粒度", date="2026-08-25T00:00:00+00:00"),
        _draft_item("legacy_exact", "老的有时分", date="2026-08-25T09:30:00+00:00"),
    ]
    html = d.render_timeline(DATE, items)
    assert ">全天<" in html and ">09:30<" in html
