"""test_daily_v3.py —— spec-v0.3 §6 / §12 / §16：深读 / 浅读日报双视图 + 深读增强。

全部离线：``tmp_data_dir`` 置 ``QLY_OFFLINE=1``，另显式 delenv 掉 API key，
LLM 不可用 → distill 走规则回退，distill 字段仍须齐全、不阻塞。
"""

from __future__ import annotations

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

    # 浅读：标题流 + 已读控件 + localStorage，无大图，交叉验证徽章
    assert "浅读速览" in g and "全部已接收" in g and "标记已读" in g
    assert "localStorage" in g
    assert "<img" not in g, "浅读极简无大图"
    assert "📈" in g or "⚡" in g or "源" in g  # 交叉验证徽章
    assert "模型发布要闻" in g
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

    # 合并页：浅读/深读切换 JS，默认浅读
    assert "浅读" in m and "深读" in m
    assert 'data-view="glance"' in m and 'data-view="deep"' in m
    assert 'show("glance")' in m  # 默认浅读
    assert "wrap-glance" in m and "wrap-deep" in m
    assert "模型发布要闻" in m  # 内嵌了浅读片段
    assert "{{" not in m and "{%" not in m

    # 合并页复制到数据根
    assert daily_root.read_text(encoding="utf-8") == m


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
    assert "浅读" in m and "深读" in m


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
