"""engine/youtube_transcript.py —— YouTube 字幕/转写引擎（spec-v0.3 §18）。

现实（本机 curl 实测已核）：从数据中心 IP 纯 ``requests`` 抓字幕基本拿不到——WEB client
返回 UNPLAYABLE、ANDROID/IOS 公开 key 失效、watch 页 1MB+ 常超时。baoyu-skills 的
``youtube/transcript.ts`` 之所以稳，正因它是**浏览器方案**：在 watch 页内读
``window.ytInitialPlayerResponse`` + 页面自带 ``ytcfg.data_.INNERTUBE_API_KEY``，用**页面
自身会话**（``credentials:'include'``）以 ``clientName:"ANDROID"`` POST ``/youtubei/v1/player``
取 ``captions.playerCaptionsTracklistRenderer.captionTracks``（优先非 asr），再 fetch
``track.baseUrl`` 拿字幕 XML 解析。

因此本模块设计为**可插拔提供方 + 优雅降级**（与全系统「可选增强、失败有回退」一致）：

* :func:`parse_timedtext` / :func:`transcript_text` —— **纯函数**，离线单测（对真实样本
  ``timedtext_sample.json3.json`` / ``timedtext_sample.srv.xml``）。
* :func:`build_baoyu_page_script` —— 生成可在浏览器 ``evaluate`` 的 JS（复刻 baoyu），供
  provider 1（web-access CDP）注入。
* :func:`get_transcript` —— 多级提供方，任一成功即返回纯文本；全失败 / ``QLY_OFFLINE=1``
  返回 ``None``，**绝不抛异常、离线绝不出网**。

提供方顺序（spec-v0.3 §18.4）：
  1. web-access CDP（首选）：经 :mod:`qianliyan.engine.cdp` 连常驻 Chrome → goto watch 页
     → evaluate baoyu 脚本 → 解析。playwright/CDP 不可用则跳过。
  2. 配置代理 ``QLY_TRANSCRIPT_PROXY``（pod2txt / 自托管 youtube-transcript-api）。
  3. 直连 best-effort（innertube player → captionTracks → timedtext，住宅 IP 下可成）。
  4. 都失败 → ``None``。
"""

from __future__ import annotations

import json
import logging
import os
import re
from html import unescape
from typing import Any, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

__all__ = [
    "parse_timedtext",
    "transcript_text",
    "build_baoyu_page_script",
    "get_transcript",
]

#: 默认字幕语言优先级（英文优先，兼顾简繁）。
DEFAULT_LANG_PREF: Tuple[str, ...] = ("en", "zh-Hans", "zh", "zh-Hant")

#: innertube ANDROID client 公开 key（直连 best-effort 用；失效属预期，回退不阻塞）。
_INNERTUBE_KEY = "AIzaSyA8eiZmM1FaDVjRy-df2KTyQ_vz_yYM39w"
_INNERTUBE_CLIENT = {"clientName": "ANDROID", "clientVersion": "20.10.38", "hl": "en"}

_WS_RE = re.compile(r"\s+")
_TAG_RE = re.compile(r"<[^>]+>")
_SRV_TEXT_RE = re.compile(r"<text\b([^>]*)>([\s\S]*?)</text>", re.IGNORECASE)
_SRV_P_RE = re.compile(r"<p\b([^>]*)>([\s\S]*?)</p>", re.IGNORECASE)

_CDP_NAV_TIMEOUT_MS = 20000

# 视频 id 注入占位符（避免 str.format 与 JS 花括号打架）。
_VIDEO_ID_PLACEHOLDER = "__QLY_VIDEO_ID__"


# =========================================================================
# 纯函数：字幕解析 + 文本拼接
# =========================================================================
def _clean_xml_text(raw: str) -> str:
    """srv/xml 段内文本清洗：剥除内嵌标签（srv3 的 ``<s>`` 词级标签）+ 解 HTML 实体。"""
    return unescape(_TAG_RE.sub("", raw or "")).strip()


def _parse_json3(text: str) -> List[Dict[str, Any]]:
    """json3：``events[].segs[].utf8`` 拼接，跳过拼接后为纯空白（如单独 ``\\n``）的段。"""
    data = json.loads(text)
    events = data.get("events") if isinstance(data, dict) else None
    out: List[Dict[str, Any]] = []
    for event in events or []:
        if not isinstance(event, dict):
            continue
        segs = event.get("segs")
        if not segs:
            continue
        buf = "".join(
            str(seg.get("utf8") or "") for seg in segs if isinstance(seg, dict)
        )
        if not buf.strip():
            continue
        out.append({
            "start": _to_float(event.get("tStartMs")) / 1000.0,
            "dur": _to_float(event.get("dDurationMs")) / 1000.0,
            "text": buf.strip(),
        })
    return out


def _attr_float(attrs: str, name: str) -> float:
    match = re.search(r'\b' + re.escape(name) + r'="([\d.\-]+)"', attrs or "")
    return _to_float(match.group(1)) if match else 0.0


def _parse_srv(text: str) -> List[Dict[str, Any]]:
    """srv/xml：抽 ``<text start dur>`` 段（秒），无则回退 ``<p t d>`` 段（毫秒）。"""
    out: List[Dict[str, Any]] = []
    for attrs, body in _SRV_TEXT_RE.findall(text):
        cleaned = _clean_xml_text(body)
        if not cleaned:
            continue
        out.append({
            "start": _attr_float(attrs, "start"),
            "dur": _attr_float(attrs, "dur"),
            "text": cleaned,
        })
    if out:
        return out
    for attrs, body in _SRV_P_RE.findall(text):
        cleaned = _clean_xml_text(body)
        if not cleaned:
            continue
        out.append({
            "start": _attr_float(attrs, "t") / 1000.0,
            "dur": _attr_float(attrs, "d") / 1000.0,
            "text": cleaned,
        })
    return out


def _to_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def parse_timedtext(text: str, fmt: str = "auto") -> List[Dict[str, Any]]:
    """把 timedtext 解析为 ``[{start(float 秒), dur(float 秒), text(str)}]``。

    ``fmt`` ∈ ``{"auto", "json3", "srv"/"xml"}``；``auto`` 按内容嗅探（``{``/``[`` → json3，
    否则 srv/xml）。纯函数、不出网；任何解析异常一律回退空列表（绝不抛）。
    """
    if not text or not str(text).strip():
        return []
    raw = str(text)
    kind = (fmt or "auto").strip().lower()
    if kind == "auto":
        stripped = raw.lstrip()
        kind = "json3" if stripped[:1] in ("{", "[") else "srv"

    primary = _parse_json3 if kind in ("json3", "json") else _parse_srv
    secondary = _parse_srv if primary is _parse_json3 else _parse_json3
    try:
        result = primary(raw)
        if result:
            return result
    except Exception as exc:  # noqa: BLE001 - 解析失败回退另一格式，绝不抛
        logger.debug("parse_timedtext 主格式(%s)失败，尝试回退: %s", kind, exc)
    try:
        return secondary(raw)
    except Exception as exc:  # noqa: BLE001
        logger.debug("parse_timedtext 回退格式亦失败: %s", exc)
        return []


def transcript_text(segments: Sequence[Dict[str, Any]]) -> str:
    """段落列表拼接为纯文本：跳空段、段内折叠空白、以单空格连接。"""
    parts: List[str] = []
    for seg in segments or []:
        value = seg.get("text") if isinstance(seg, dict) else seg
        cleaned = _WS_RE.sub(" ", str(value or "").strip()).strip()
        if cleaned:
            parts.append(cleaned)
    return _WS_RE.sub(" ", " ".join(parts)).strip()


# =========================================================================
# baoyu 页内脚本（供 provider 1 的 CDP evaluate）
# =========================================================================
#: 复刻 baoyu-skills 的浏览器取字幕流程（页内 evaluate）。以异步 IIFE 返回 Promise，
#: playwright 的 ``page.evaluate`` 会自动 await；产出 ``{segments:[{start,dur,text}]}`` 或
#: ``{error}``。视频 id 由 :func:`build_baoyu_page_script` 注入 ``_VIDEO_ID_PLACEHOLDER``。
_BAOYU_JS_TEMPLATE = r"""(async function () {
  "use strict";
  function decodeEntities(s) {
    try {
      var ta = document.createElement("textarea");
      ta.innerHTML = String(s || "").replace(/<[^>]+>/g, "");
      return ta.value;
    } catch (e) { return String(s || ""); }
  }
  function pickApiKey() {
    try {
      if (window.ytcfg) {
        if (window.ytcfg.data_ && window.ytcfg.data_.INNERTUBE_API_KEY) {
          return window.ytcfg.data_.INNERTUBE_API_KEY;
        }
        if (typeof window.ytcfg.get === "function") {
          var k = window.ytcfg.get("INNERTUBE_API_KEY");
          if (k) return k;
        }
      }
    } catch (e) {}
    return null;
  }
  function parseCaptionXml(xml) {
    var segs = [], m;
    var reText = /<text\b([^>]*)>([\s\S]*?)<\/text>/g;
    while ((m = reText.exec(xml)) !== null) {
      var a = m[1] || "";
      var sM = /\bstart="([\d.\-]+)"/.exec(a);
      var dM = /\bdur="([\d.\-]+)"/.exec(a);
      segs.push({
        start: sM ? parseFloat(sM[1]) : 0,
        dur: dM ? parseFloat(dM[1]) : 0,
        text: decodeEntities(m[2])
      });
    }
    if (!segs.length) {
      var reP = /<p\b([^>]*)>([\s\S]*?)<\/p>/g;
      while ((m = reP.exec(xml)) !== null) {
        var ap = m[1] || "";
        var tM = /\bt="(\d+)"/.exec(ap);
        var ddM = /\bd="(\d+)"/.exec(ap);
        segs.push({
          start: tM ? parseInt(tM[1], 10) / 1000 : 0,
          dur: ddM ? parseInt(ddM[1], 10) / 1000 : 0,
          text: decodeEntities(m[2])
        });
      }
    }
    return segs;
  }
  try {
    var VIDEO_ID = __QLY_VIDEO_ID__;
    var apiKey = pickApiKey();
    var pr = window.ytInitialPlayerResponse || null;
    if (apiKey) {
      try {
        var resp = await fetch("/youtubei/v1/player?key=" + apiKey, {
          method: "POST",
          credentials: "include",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            videoId: VIDEO_ID,
            context: { client: { clientName: "ANDROID", clientVersion: "20.10.38", hl: "en" } }
          })
        });
        if (resp && resp.ok) { pr = await resp.json(); }
      } catch (e) {}
    }
    if (!pr) { return { error: "no player response" }; }
    var caps = (pr.captions || {}).playerCaptionsTracklistRenderer || {};
    var tracks = caps.captionTracks || [];
    if (!tracks.length) { return { error: "no caption tracks" }; }
    var track = null, i;
    for (i = 0; i < tracks.length; i++) {
      if (tracks[i] && tracks[i].kind !== "asr") { track = tracks[i]; break; }
    }
    if (!track) { track = tracks[0]; }
    if (!track || !track.baseUrl) { return { error: "no baseUrl" }; }
    var xmlResp = await fetch(track.baseUrl, { credentials: "include" });
    var xml = await xmlResp.text();
    return {
      segments: parseCaptionXml(xml),
      lang: track.languageCode || "",
      kind: track.kind || ""
    };
  } catch (e) {
    return { error: String(e) };
  }
})()
"""


def build_baoyu_page_script(video_id: str) -> str:
    """返回可在浏览器 ``evaluate`` 的 JS 字符串（复刻 baoyu 取字幕流程），注入 ``video_id``。"""
    return _BAOYU_JS_TEMPLATE.replace(
        _VIDEO_ID_PLACEHOLDER, json.dumps(str(video_id or ""))
    )


# =========================================================================
# 提供方链
# =========================================================================
def _is_offline() -> bool:
    return os.environ.get("QLY_OFFLINE") == "1"


def _segments_from_result(result: Any) -> List[Dict[str, Any]]:
    """CDP evaluate 返回的 ``{segments:[...]}`` 归一为标准段列表。"""
    if not isinstance(result, dict):
        return []
    segs = result.get("segments")
    if not isinstance(segs, list):
        return []
    out: List[Dict[str, Any]] = []
    for seg in segs:
        if not isinstance(seg, dict):
            continue
        text = str(seg.get("text") or "").strip()
        if not text:
            continue
        out.append({
            "start": _to_float(seg.get("start")),
            "dur": _to_float(seg.get("dur")),
            "text": text,
        })
    return out


def _via_cdp(video_id: str) -> Optional[str]:
    """provider 1：经 web-access / 常驻 Chrome 的 CDP 打开 watch 页并注入 baoyu 脚本取字幕。

    ``engine.cdp`` 不可用（缺 playwright / 连不上）或任何异常 → 返回 ``None`` 跳过下一提供方。
    """
    if _is_offline():
        return None
    try:
        from . import cdp
    except Exception as exc:  # noqa: BLE001 - import 失败视作不可用
        logger.debug("engine.cdp 不可用，跳过 CDP 字幕: %s", exc)
        return None

    playwright_ctx = browser = None
    try:
        playwright_ctx, browser = cdp.connect()
        page = browser.new_page()
        try:
            page.goto(
                "https://www.youtube.com/watch?v={0}".format(video_id),
                timeout=_CDP_NAV_TIMEOUT_MS,
            )
            result = page.evaluate(build_baoyu_page_script(video_id))
        finally:
            try:
                page.close()
            except Exception:  # noqa: BLE001
                pass
        text = transcript_text(_segments_from_result(result))
        return text or None
    except cdp.CDPUnavailable as exc:
        logger.debug("CDP 通道不可用，跳过 CDP 字幕: %s", exc)
        return None
    except Exception as exc:  # noqa: BLE001 - 绝不抛，交给下一提供方
        logger.debug("CDP 取字幕失败 (video_id=%s): %s", video_id, exc)
        return None
    finally:
        try:
            if browser is not None:
                browser.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            if playwright_ctx is not None:
                playwright_ctx.stop()
        except Exception:  # noqa: BLE001
            pass


def _proxy_urls(proxy: str, video_id: str) -> List[str]:
    """由代理基址拼候选 URL：``{proxy}?v={id}`` 与 ``{proxy}/{id}``。"""
    proxy = proxy.strip()
    urls: List[str] = []
    sep = "&" if "?" in proxy else "?"
    urls.append("{0}{1}v={2}".format(proxy, sep, video_id))
    urls.append("{0}/{1}".format(proxy.rstrip("/"), video_id))
    # 去重保序
    seen = set()
    out = []
    for u in urls:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def _text_from_body(body: str) -> Optional[str]:
    """代理响应体 → 纯文本：json3/srv 走 parse，纯文本直接折叠返回。"""
    if not body or not body.strip():
        return None
    head = body.lstrip()[:1]
    if head in ("{", "[", "<"):
        text = transcript_text(parse_timedtext(body, "auto"))
        if text:
            return text
    # 纯文本（pod2txt 之类直接给转写全文）
    collapsed = _WS_RE.sub(" ", body).strip()
    return collapsed or None


def _via_proxy(video_id: str) -> Optional[str]:
    """provider 2：配置了 ``QLY_TRANSCRIPT_PROXY`` 时经代理取字幕（json3/srv/纯文本）。"""
    if _is_offline():
        return None
    proxy = os.environ.get("QLY_TRANSCRIPT_PROXY")
    if not proxy or not proxy.strip():
        return None
    try:
        from . import http
    except Exception as exc:  # noqa: BLE001
        logger.debug("engine.http 不可用，跳过代理字幕: %s", exc)
        return None

    for url in _proxy_urls(proxy, video_id):
        try:
            resp = http.get(url)
            if getattr(resp, "status_code", 0) != 200:
                continue
            text = _text_from_body(getattr(resp, "text", "") or "")
            if text:
                return text
        except Exception as exc:  # noqa: BLE001 - 单个候选失败试下一个
            logger.debug("代理字幕候选失败 %s: %s", url, exc)
            continue
    return None


def _via_direct(video_id: str, lang_pref: Sequence[str]) -> Optional[str]:
    """provider 3：直连 best-effort——innertube player→captionTracks→timedtext&fmt=json3。

    数据中心 IP 下大概率失败（公开 key 失效 / UNPLAYABLE），住宅 IP 或 YouTube 放松时可成；
    任何异常一律 ``None``，交回 :func:`get_transcript` 收敛为 ``None``（不阻塞深读回退）。
    """
    if _is_offline():
        return None
    try:
        import requests
    except Exception as exc:  # noqa: BLE001 - 依赖缺失视作不可用
        logger.debug("requests 不可用，跳过直连字幕: %s", exc)
        return None
    try:
        from . import http
    except Exception:  # noqa: BLE001
        http = None  # type: ignore[assignment]

    try:
        player_url = (
            "https://www.youtube.com/youtubei/v1/player?key={0}".format(_INNERTUBE_KEY)
        )
        payload = {"videoId": video_id, "context": {"client": dict(_INNERTUBE_CLIENT)}}
        resp = requests.post(
            player_url,
            json=payload,
            headers={"User-Agent": "com.google.android.youtube/20.10.38 (Linux; U; Android 14)"},
            timeout=15,
        )
        if resp.status_code >= 400:
            return None
        data = resp.json()
    except Exception as exc:  # noqa: BLE001
        logger.debug("直连 innertube player 失败 (video_id=%s): %s", video_id, exc)
        return None

    tracks = (
        ((data.get("captions") or {}).get("playerCaptionsTracklistRenderer") or {})
        .get("captionTracks")
    ) or []
    if not isinstance(tracks, list) or not tracks:
        return None

    track = _pick_track(tracks, lang_pref)
    base_url = str((track or {}).get("baseUrl") or "")
    if not base_url:
        return None
    fetch_url = base_url + ("&fmt=json3" if "fmt=" not in base_url else "")

    try:
        if http is not None:
            xml_resp = http.get(fetch_url)
            body = getattr(xml_resp, "text", "") or ""
        else:  # pragma: no cover - http 缺失属异常部署
            body = requests.get(fetch_url, timeout=15).text
    except Exception as exc:  # noqa: BLE001
        logger.debug("直连抓字幕 baseUrl 失败: %s", exc)
        return None

    return transcript_text(parse_timedtext(body, "auto")) or None


def _pick_track(tracks: Sequence[Dict[str, Any]], lang_pref: Sequence[str]) -> Optional[Dict[str, Any]]:
    """按语言偏好 + 「非 asr 优先」挑一条字幕轨。"""
    def _lang(t: Dict[str, Any]) -> str:
        return str(t.get("languageCode") or "").lower()

    non_asr = [t for t in tracks if isinstance(t, dict) and t.get("kind") != "asr"]
    pools = (non_asr, [t for t in tracks if isinstance(t, dict)])
    for pool in pools:
        for pref in lang_pref:
            pl = str(pref).lower()
            for t in pool:
                if _lang(t) == pl or _lang(t).startswith(pl.split("-")[0]):
                    return t
    for pool in pools:
        if pool:
            return pool[0]
    return None


def get_transcript(
    video_id: str,
    lang_pref: Sequence[str] = DEFAULT_LANG_PREF,
) -> Optional[str]:
    """多级提供方取 YouTube 字幕纯文本；任一成功即返回，全失败/离线返回 ``None``。

    提供方顺序：① web-access CDP（首选）② ``QLY_TRANSCRIPT_PROXY`` 代理 ③ 直连 best-effort。
    ``QLY_OFFLINE=1`` 直接 ``None``（不出网）。**本函数绝不抛异常**。
    """
    if _is_offline():
        return None
    vid = str(video_id or "").strip()
    if not vid:
        return None
    prefs = tuple(lang_pref) if lang_pref else DEFAULT_LANG_PREF

    for provider in (
        lambda: _via_cdp(vid),
        lambda: _via_proxy(vid),
        lambda: _via_direct(vid, prefs),
    ):
        try:
            text = provider()
        except Exception as exc:  # noqa: BLE001 - 双保险：提供方内已自兜底，这里再兜一层
            logger.debug("字幕提供方异常 (video_id=%s): %s", vid, exc)
            text = None
        if text and text.strip():
            return text.strip()
    return None
