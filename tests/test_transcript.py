"""test_transcript.py —— spec-v0.3 §18：YouTube 字幕引擎纯函数离线测。

覆盖：``parse_timedtext`` 对两个真实样本（json3 段拼接跳纯 ``\\n``、srv 抽取解 HTML 实体）、
``transcript_text`` 折叠空白、``build_baoyu_page_script`` 含关键片段、``get_transcript`` 在
``QLY_OFFLINE=1`` 返回 ``None`` 且不出网。全部离线（conftest 物理层封 socket 兜底）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from qianliyan.engine import youtube_transcript as yt

REAL = Path(__file__).resolve().parent / "fixtures" / "real"


def _read(name: str) -> str:
    return (REAL / name).read_text(encoding="utf-8")


# =========================================================================
# parse_timedtext —— json3
# =========================================================================
def test_parse_json3_joins_segs_and_skips_newline_events():
    segs = yt.parse_timedtext(_read("timedtext_sample.json3.json"), "json3")
    # 原 5 个 events，其中一个纯 "\n" 段被跳过 → 4 段
    assert len(segs) == 4

    first = segs[0]
    # segs[].utf8 直接拼接（"...launched " + "one of the world's first national"）
    assert first["text"] == "In late 2025, Iceland launched one of the world's first national"
    assert first["start"] == pytest.approx(0.0)
    assert first["dur"] == pytest.approx(4.2)  # tStartMs/dDurationMs → 秒

    # 多段拼接
    assert segs[2]["text"] == "The core idea is that smaller languages need deliberate data stewardship."
    # 纯 "\n" 事件被跳过：不应出现只含空白的段
    assert all(s["text"].strip() for s in segs)


def test_parse_auto_detects_json3():
    raw = _read("timedtext_sample.json3.json")
    assert yt.parse_timedtext(raw, "auto") == yt.parse_timedtext(raw, "json3")


# =========================================================================
# parse_timedtext —— srv/xml
# =========================================================================
def test_parse_srv_extracts_text_and_decodes_entities():
    segs = yt.parse_timedtext(_read("timedtext_sample.srv.xml"), "srv")
    assert len(segs) == 4

    first = segs[0]
    # &#39; 解码为 '
    assert first["text"] == "In late 2025, Iceland launched one of the world's first national"
    assert "&#39;" not in first["text"]
    assert first["start"] == pytest.approx(0.0)
    assert first["dur"] == pytest.approx(4.2)


def test_parse_auto_detects_srv():
    raw = _read("timedtext_sample.srv.xml")
    assert yt.parse_timedtext(raw, "auto") == yt.parse_timedtext(raw, "srv")


def test_parse_empty_and_garbage_return_empty_list():
    assert yt.parse_timedtext("") == []
    assert yt.parse_timedtext("   ") == []
    assert yt.parse_timedtext("not json not xml") == []  # 绝不抛


# =========================================================================
# transcript_text —— 折叠空白 + 跳空段
# =========================================================================
def test_transcript_text_collapses_and_skips_empty():
    segs = [
        {"start": 0, "dur": 1, "text": "  Hello   world  "},
        {"start": 1, "dur": 1, "text": "\n\n"},          # 空段跳过
        {"start": 2, "dur": 1, "text": "line\ttwo"},
        {"start": 3, "dur": 1, "text": ""},               # 空段跳过
    ]
    out = yt.transcript_text(segs)
    assert out == "Hello world line two"
    assert "\n" not in out and "\t" not in out and "  " not in out


def test_transcript_text_on_real_json3_is_clean():
    segs = yt.parse_timedtext(_read("timedtext_sample.json3.json"))
    text = yt.transcript_text(segs)
    assert text.startswith("In late 2025, Iceland launched one of the world's first national")
    assert "AI language initiatives." in text
    assert "\n" not in text and "  " not in text


# =========================================================================
# build_baoyu_page_script —— 含关键片段 + video_id 注入
# =========================================================================
def test_build_baoyu_page_script_contains_key_fragments():
    script = yt.build_baoyu_page_script("dQw4w9WgXcQ")
    for fragment in (
        "INNERTUBE_API_KEY",
        "captionTracks",
        "ytInitialPlayerResponse",
        "/youtubei/v1/player",
        "ANDROID",
        "credentials",
    ):
        assert fragment in script, "baoyu 脚本缺关键片段 {0}".format(fragment)
    # video_id 注入（JSON 字面量，含引号）
    assert '"dQw4w9WgXcQ"' in script


def test_build_baoyu_page_script_escapes_video_id():
    # 恶意 id 必须被 JSON 转义，不能破坏 JS 结构
    script = yt.build_baoyu_page_script('"};alert(1)//')
    assert 'alert(1)' in script  # 作为字符串内容出现
    assert '\\"};alert(1)//' in script  # 引号被转义


# =========================================================================
# get_transcript —— 离线短路：返回 None 且不出网
# =========================================================================
def test_get_transcript_offline_returns_none(monkeypatch):
    monkeypatch.setenv("QLY_OFFLINE", "1")
    # conftest 已物理层封 socket；此处再确认 QLY_OFFLINE 短路，不触发任何网络
    assert yt.get_transcript("dQw4w9WgXcQ") is None


def test_get_transcript_empty_video_id_returns_none(monkeypatch):
    monkeypatch.setenv("QLY_OFFLINE", "1")
    assert yt.get_transcript("") is None
    assert yt.get_transcript(None) is None


def test_get_transcript_never_raises_when_providers_fail(monkeypatch):
    """即便各提供方抛异常，get_transcript 也须收敛为 None（绝不抛）。"""
    monkeypatch.delenv("QLY_OFFLINE", raising=False)
    monkeypatch.delenv("QLY_TRANSCRIPT_PROXY", raising=False)

    def _boom(*args, **kwargs):
        raise RuntimeError("provider exploded")

    monkeypatch.setattr(yt, "_via_cdp", _boom)
    monkeypatch.setattr(yt, "_via_proxy", _boom)
    monkeypatch.setattr(yt, "_via_direct", _boom)
    assert yt.get_transcript("dQw4w9WgXcQ") is None
