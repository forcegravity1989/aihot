from __future__ import annotations

import logging
from typing import Any, Dict, Iterable, List

from ..core import paths

logger = logging.getLogger(__name__)

CONFIG_NAME = "editorial.yaml"


def load() -> Dict[str, Any]:
    try:
        raw = paths.load_yaml_config(CONFIG_NAME)
    except Exception as exc:  # noqa: BLE001 - 口味配置读坏不该让日报出不来
        logger.warning("读 %s 失败，简报里不带口味规则: %s", CONFIG_NAME, exc)
        return {}
    return raw if isinstance(raw, dict) else {}


def _lines(values: Any) -> List[str]:
    if isinstance(values, str):
        return [values.strip()] if values.strip() else []
    if isinstance(values, Iterable):
        return [str(v).strip() for v in values if str(v).strip()]
    return []


def _bullets(title: str, values: Any) -> List[str]:
    rows = _lines(values)
    if not rows:
        return []
    return [title] + ["- {0}".format(r) for r in rows]


def _pairs(title: str, mapping: Any) -> List[str]:
    if not isinstance(mapping, dict) or not mapping:
        return []
    return [title] + ["- {0}：{1}".format(k, v) for k, v in mapping.items() if str(k).strip()]


def selection_text(cfg: Dict[str, Any]) -> str:
    sel = cfg.get("selection") if isinstance(cfg, dict) else None
    if not isinstance(sel, dict):
        return ""
    out: List[str] = []
    intro = str(sel.get("intro") or "").strip()
    if intro:
        out.append(intro)
    out += _bullets("正常评价、该进正文的：", sel.get("value"))
    out += _bullets("LLM infra 口径（做 infra 的人今天会不会改变选型、配置或排期）：", sel.get("infra"))
    out += _bullets("必须压住的噪声：", sel.get("noise"))
    out += _bullets("怎么判：", sel.get("judgement"))
    return "\n".join(out)


def writing_text(cfg: Dict[str, Any]) -> str:
    if not isinstance(cfg, dict):
        return ""
    out: List[str] = []
    wr = cfg.get("writing")
    if isinstance(wr, dict):
        out += _bullets("中文标题：", wr.get("title"))
        out += _bullets("摘要与结论：", wr.get("summary"))
        banned = _lines(wr.get("banned"))
        if banned:
            out.append("禁用词（出现就改写）：" + "；".join(banned))
    gl = cfg.get("glossary")
    if isinstance(gl, dict):
        out += _pairs("歧义词一律按 AI 含义：", gl.get("ambiguous"))
        out += _pairs("infra 术语：", gl.get("infra"))
        out += _bullets("一律保留英文：", gl.get("keep_english"))
        out += _bullets("中国厂商优先官方中文名：", gl.get("chinese_brands"))
    return "\n".join(out)
