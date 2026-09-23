"""pipeline/svg_charts.py —— 日报对比图：数据 → 内联 SVG。

视觉语言取自 ppt-master（hugohe3/ppt-master）的图表模板：浅色网格、坐标轴刻度、数值直接
标在图上、白描边的数据点、主角一行/一点用强调色。它的模板是写死坐标的示例 SVG，面向
「AI 逐页改写」；日报要的是**数据进、图出、数字不经过任何生成环节**，所以这里按同样的
画法用代码生成。

颜色一律走 class（``qc-*``，定义在 ``templates/_theme.css``），不写死十六进制——
明暗两套主题由页面 token 自动切换。

三种图（``chart["type"]``）：

* ``bar``：横向条形，``rows: [{label, value, note?, highlight?}]``；
* ``dumbbell``：前代→新版，``rows: [{label, from, to, highlight?}]``，右侧标变化量；
* ``scatter``：两个指标的关系（如分数 vs 成本），
  ``points: [{label, x, y, highlight?}]`` + ``x_label`` / ``y_label``。

输入不合格（非数字、点数不够）时 :func:`clean` 返回 None，调用方就不画——宁缺毋滥。
"""

from __future__ import annotations

import html
import math
from typing import Any, Dict, List, Optional, Sequence, Tuple

__all__ = ["TYPES", "clean", "render"]

TYPES = ("bar", "dumbbell", "scatter")
#: 条形/哑铃的行数范围，散点的点数范围
ROWS_RANGE = (2, 8)
POINTS_RANGE = (2, 10)

#: 画布宽度（viewBox 单位；实际按容器宽度等比缩放）
WIDTH = 640
_CURRENCY = ("$", "¥", "￥", "€", "£")


# ---------------------------------------------------------------------------
# 校验
# ---------------------------------------------------------------------------
def _num(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _text(value: Any, limit: int = 40) -> str:
    return str(value or "").strip()[:limit]


def clean(raw: Any) -> Optional[Dict[str, Any]]:
    """规整并校验图表数据；不合格返回 None。"""
    if not isinstance(raw, dict):
        return None
    kind = str(raw.get("type") or "bar").strip().lower()
    title = _text(raw.get("title"), 60)
    if kind not in TYPES or not title:
        return None
    unit = _text(raw.get("unit"), 8)

    if kind == "scatter":
        points = []
        for row in raw.get("points") if isinstance(raw.get("points"), list) else []:
            if not isinstance(row, dict):
                continue
            x, y = _num(row.get("x")), _num(row.get("y"))
            label = _text(row.get("label"), 28)
            if x is None or y is None or not label:
                continue
            points.append({"label": label, "x": x, "y": y, "highlight": bool(row.get("highlight"))})
        if not (POINTS_RANGE[0] <= len(points) <= POINTS_RANGE[1]):
            return None
        return {
            "type": kind, "title": title, "unit": unit, "points": points,
            "x_label": _text(raw.get("x_label"), 30), "y_label": _text(raw.get("y_label"), 30),
            "x_unit": _text(raw.get("x_unit"), 8), "y_unit": _text(raw.get("y_unit"), 8),
        }

    rows = []
    for row in raw.get("rows") if isinstance(raw.get("rows"), list) else []:
        if not isinstance(row, dict):
            continue
        label = _text(row.get("label"), 28)
        if not label:
            continue
        if kind == "dumbbell":
            start, end = _num(row.get("from")), _num(row.get("to"))
            if start is None or end is None:
                continue
            rows.append({"label": label, "from": start, "to": end, "highlight": bool(row.get("highlight"))})
        else:
            value = _num(row.get("value"))
            if value is None or value < 0:
                continue
            rows.append({"label": label, "value": value, "note": _text(row.get("note"), 16),
                         "highlight": bool(row.get("highlight"))})
    if not (ROWS_RANGE[0] <= len(rows) <= ROWS_RANGE[1]):
        return None
    out: Dict[str, Any] = {"type": kind, "title": title, "unit": unit, "rows": rows}
    if kind == "dumbbell":
        out["from_label"] = _text(raw.get("from_label"), 16)
        out["to_label"] = _text(raw.get("to_label"), 16)
    return out


# ---------------------------------------------------------------------------
# 刻度与格式
# ---------------------------------------------------------------------------
def _nice_ticks(lo: float, hi: float, count: int = 5) -> List[float]:
    """1/2/2.5/5×10^k 的整齐刻度，覆盖 [lo, hi]。"""
    if hi <= lo:
        hi = lo + 1.0
    raw_step = (hi - lo) / max(1, count)
    power = 10 ** math.floor(math.log10(raw_step))
    step = next(m * power for m in (1, 2, 2.5, 5, 10) if m * power >= raw_step)
    start = math.floor(lo / step) * step
    ticks = []
    value = start
    while value <= hi + step * 1e-9:
        ticks.append(round(value, 10))
        value += step
    if ticks[-1] < hi:
        ticks.append(round(ticks[-1] + step, 10))
    return ticks


def _fmt(value: float) -> str:
    if abs(value - round(value)) < 1e-9:
        return "{0:,}".format(int(round(value)))
    text = "{0:,.2f}".format(value).rstrip("0").rstrip(".")
    return text


def _with_unit(value: float, unit: str) -> str:
    if unit in _CURRENCY:
        return "{0}{1}".format(unit, _fmt(value))
    return "{0}{1}".format(_fmt(value), unit)


def _e(text: Any) -> str:
    return html.escape(str(text), quote=True)


def _svg(height: float, body: Sequence[str], title: str) -> str:
    return (
        '<svg class="qc" viewBox="0 0 {w} {h:.0f}" role="img" aria-label="{t}" '
        'preserveAspectRatio="xMidYMid meet" xmlns="http://www.w3.org/2000/svg">{b}</svg>'
    ).format(w=WIDTH, h=height, t=_e(title), b="".join(body))


# ---------------------------------------------------------------------------
# 条形
# ---------------------------------------------------------------------------
def _bar(chart: Dict[str, Any]) -> str:
    rows, unit = chart["rows"], chart["unit"]
    label_w, value_w, note_w = 190, 64, (56 if any(r["note"] for r in rows) else 0)
    left, right = label_w, WIDTH - value_w - note_w - 8
    row_h, top = 34, 12
    ticks = _nice_ticks(0.0, max(r["value"] for r in rows) or 1.0, 4)
    span = ticks[-1] or 1.0
    bottom = top + row_h * len(rows)
    body: List[str] = []

    for tick in ticks:
        x = left + (right - left) * tick / span
        body.append('<line class="qc-grid" x1="{0:.1f}" y1="{1}" x2="{0:.1f}" y2="{2}"/>'.format(x, top - 4, bottom))
        body.append('<text class="qc-tick" x="{0:.1f}" y="{1}" text-anchor="middle">{2}</text>'.format(
            x, bottom + 16, _e(_with_unit(tick, unit))))
    for i, row in enumerate(rows):
        cy = top + row_h * i + row_h / 2
        width = max(2.0, (right - left) * row["value"] / span)
        hl = " is-hl" if row["highlight"] else ""
        body.append('<text class="qc-label{0}" x="{1}" y="{2:.1f}" text-anchor="end" dominant-baseline="middle">{3}</text>'.format(
            hl, left - 10, cy, _e(row["label"])))
        body.append('<rect class="qc-bar{0}" x="{1}" y="{2:.1f}" width="{3:.1f}" height="16" rx="4"/>'.format(
            hl, left, cy - 8, width))
        body.append('<text class="qc-value{0}" x="{1:.1f}" y="{2:.1f}" dominant-baseline="middle">{3}</text>'.format(
            hl, left + width + 8, cy, _e(_with_unit(row["value"], unit))))
        if row["note"]:
            body.append('<text class="qc-note" x="{0}" y="{1:.1f}" text-anchor="end" dominant-baseline="middle">{2}</text>'.format(
                WIDTH - 4, cy, _e(row["note"])))
    return _svg(bottom + 26, body, chart["title"])


# ---------------------------------------------------------------------------
# 哑铃：前代 → 新版
# ---------------------------------------------------------------------------
def _dumbbell(chart: Dict[str, Any]) -> str:
    rows, unit = chart["rows"], chart["unit"]
    label_w, delta_w = 170, 70
    left, right = label_w, WIDTH - delta_w
    row_h, top = 36, 30
    values = [r["from"] for r in rows] + [r["to"] for r in rows]
    lo, hi = min(values), max(values)
    pad = (hi - lo) * 0.08 or 1.0
    ticks = _nice_ticks(max(0.0, lo - pad) if lo >= 0 else lo - pad, hi + pad, 4)
    t0, t1 = ticks[0], ticks[-1]

    def sx(v: float) -> float:
        return left + (right - left) * (v - t0) / ((t1 - t0) or 1.0)

    bottom = top + row_h * len(rows)
    body: List[str] = []
    # 图例：空心点 = 前代，实心点 = 新版
    from_label = chart.get("from_label") or "之前"
    to_label = chart.get("to_label") or "之后"
    body.append('<circle class="qc-dot-from" cx="{0}" cy="12" r="5"/>'.format(left))
    body.append('<text class="qc-tick" x="{0}" y="12" dominant-baseline="middle">{1}</text>'.format(left + 10, _e(from_label)))
    body.append('<circle class="qc-dot-to" cx="{0}" cy="12" r="5"/>'.format(left + 110))
    body.append('<text class="qc-tick" x="{0}" y="12" dominant-baseline="middle">{1}</text>'.format(left + 120, _e(to_label)))
    for tick in ticks:
        x = sx(tick)
        body.append('<line class="qc-grid" x1="{0:.1f}" y1="{1}" x2="{0:.1f}" y2="{2}"/>'.format(x, top - 4, bottom))
        body.append('<text class="qc-tick" x="{0:.1f}" y="{1}" text-anchor="middle">{2}</text>'.format(
            x, bottom + 16, _e(_with_unit(tick, unit))))
    for i, row in enumerate(rows):
        cy = top + row_h * i + row_h / 2
        x0, x1 = sx(row["from"]), sx(row["to"])
        hl = " is-hl" if row["highlight"] else ""
        delta = row["to"] - row["from"]
        body.append('<text class="qc-label{0}" x="{1}" y="{2:.1f}" text-anchor="end" dominant-baseline="middle">{3}</text>'.format(
            hl, left - 12, cy, _e(row["label"])))
        body.append('<line class="qc-stem{0}" x1="{1:.1f}" y1="{2:.1f}" x2="{3:.1f}" y2="{2:.1f}"/>'.format(hl, x0, cy, x1))
        body.append('<circle class="qc-dot-from" cx="{0:.1f}" cy="{1:.1f}" r="6"/>'.format(x0, cy))
        body.append('<circle class="qc-dot-to{0}" cx="{1:.1f}" cy="{2:.1f}" r="7"/>'.format(hl, x1, cy))
        # 数值标在点外侧，避免压在连线上
        lo_x, hi_x = (x0, x1) if x0 <= x1 else (x1, x0)
        lo_v, hi_v = (row["from"], row["to"]) if x0 <= x1 else (row["to"], row["from"])
        body.append('<text class="qc-tick" x="{0:.1f}" y="{1:.1f}" text-anchor="end" dominant-baseline="middle">{2}</text>'.format(
            lo_x - 10, cy, _e(_fmt(lo_v))))
        body.append('<text class="qc-value{0}" x="{1:.1f}" y="{2:.1f}" dominant-baseline="middle">{3}</text>'.format(
            hl, hi_x + 10, cy, _e(_fmt(hi_v))))
        sign = "+" if delta > 0 else ("−" if delta < 0 else "±")
        body.append('<text class="qc-delta{0}" x="{1}" y="{2:.1f}" text-anchor="end" dominant-baseline="middle">{3}{4}</text>'.format(
            " is-up" if delta > 0 else (" is-down" if delta < 0 else ""), WIDTH - 4, cy, sign, _e(_fmt(abs(delta)))))
    return _svg(bottom + 26, body, chart["title"])


# ---------------------------------------------------------------------------
# 散点：两个指标的关系
# ---------------------------------------------------------------------------
def _scatter(chart: Dict[str, Any]) -> str:
    points = chart["points"]
    left, right, top, bottom = 64, WIDTH - 24, 16, 256
    xs, ys = [p["x"] for p in points], [p["y"] for p in points]
    xt = _nice_ticks(min(0.0, min(xs)), max(xs) * 1.08 or 1.0, 5)
    y_lo = min(ys)
    y_hi = max(ys)
    ypad = (y_hi - y_lo) * 0.15 or 1.0
    yt = _nice_ticks(max(0.0, y_lo - ypad) if y_lo >= 0 else y_lo - ypad, y_hi + ypad, 4)

    def sx(v: float) -> float:
        return left + (right - left) * (v - xt[0]) / ((xt[-1] - xt[0]) or 1.0)

    def sy(v: float) -> float:
        return bottom - (bottom - top) * (v - yt[0]) / ((yt[-1] - yt[0]) or 1.0)

    body: List[str] = []
    for tick in yt:
        y = sy(tick)
        body.append('<line class="qc-grid" x1="{0}" y1="{1:.1f}" x2="{2}" y2="{1:.1f}"/>'.format(left, y, right))
        body.append('<text class="qc-tick" x="{0}" y="{1:.1f}" text-anchor="end" dominant-baseline="middle">{2}</text>'.format(
            left - 8, y, _e(_with_unit(tick, chart["y_unit"]))))
    for tick in xt:
        x = sx(tick)
        body.append('<line class="qc-grid" x1="{0:.1f}" y1="{1}" x2="{0:.1f}" y2="{2}"/>'.format(x, top, bottom))
        body.append('<text class="qc-tick" x="{0:.1f}" y="{1}" text-anchor="middle">{2}</text>'.format(
            x, bottom + 16, _e(_with_unit(tick, chart["x_unit"]))))
    body.append('<line class="qc-axis" x1="{0}" y1="{1}" x2="{0}" y2="{2}"/>'.format(left, top, bottom))
    body.append('<line class="qc-axis" x1="{0}" y1="{1}" x2="{2}" y2="{1}"/>'.format(left, bottom, right))
    if chart["x_label"]:
        body.append('<text class="qc-axis-title" x="{0:.1f}" y="{1}" text-anchor="middle">{2}</text>'.format(
            (left + right) / 2, bottom + 36, _e(chart["x_label"])))
    if chart["y_label"]:
        body.append('<text class="qc-axis-title" x="14" y="{0:.1f}" text-anchor="middle" transform="rotate(-90 14 {0:.1f})">{1}</text>'.format(
            (top + bottom) / 2, _e(chart["y_label"])))
    # 先画普通点、再画主角，主角压在最上层
    for point in sorted(points, key=lambda p: p["highlight"]):
        cx, cy = sx(point["x"]), sy(point["y"])
        hl = " is-hl" if point["highlight"] else ""
        anchor, dx = ("end", -11) if cx > right - 150 else ("start", 11)
        body.append('<circle class="qc-point{0}" cx="{1:.1f}" cy="{2:.1f}" r="{3}"/>'.format(
            hl, cx, cy, 8 if hl else 6.5))
        body.append('<text class="qc-point-label{0}" x="{1:.1f}" y="{2:.1f}" text-anchor="{3}" dominant-baseline="middle">{4}</text>'.format(
            hl, cx + dx, cy, anchor, _e(point["label"])))
    return _svg(bottom + (46 if chart["x_label"] else 26), body, chart["title"])


def render(chart: Optional[Dict[str, Any]]) -> str:
    """已校验的图表数据 → SVG 字符串；None 返回空串。"""
    if not chart:
        return ""
    kind = chart.get("type") or "bar"
    if kind == "dumbbell":
        return _dumbbell(chart)
    if kind == "scatter":
        return _scatter(chart)
    return _bar(chart)
