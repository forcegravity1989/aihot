"""pipeline/change_intel.py —— 变更情报加工层（spec-v0.3 §19，本系统最高价值层）。

在统一池上做三件事（全部纯计算 + 可选 LLM 增强，失败绝不外溢）：

* :func:`extract_subject`  —— 从 item 抽「主题键」（模型/版本/特性词；changelog item 用
  ``extra.subject`` / ``extra.version``）。
* :func:`bind_changes`     —— 把同主题/版本的提示词变更与插件变更绑成「版本变更卡」。
* :func:`cross_map_claims` —— 叙事↔实证映射（**核心**）：对叙事类 item 的数字断言，找同主题实证类
  item（changelog 的 token_delta）比对方向/量级，原地写 ``extra.corroboration``。
* :func:`industry_evolution` —— 从 insights item 提「新入场/上升」项目（业界资产演进）。

边界铁律：本层只依赖 ``core``（含可选 ``core.llm_client``），不 import eyes/engine/cli。
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

__all__ = [
    "extract_subject",
    "bind_changes",
    "cross_map_claims",
    "industry_evolution",
    "make_llm",
]

# —— 叙事类 format（talk/video 的观点、blog/news 的说法）——
NARRATIVE_FORMATS = frozenset(["video", "talk", "blog", "news"])
RISING_TOP_N = 10
#: corroborated 的实证量级下限（token 绝对值）——方向吻合且量级非平凡才算实证
MAGNITUDE_MIN = 500

#: 模型族 + 可选版本号：Fable5 / opus-5 / sonnet 5 / haiku / gpt-5 / gemini 2.5
_MODEL_RE = re.compile(
    r"\b(fable|opus|sonnet|haiku|gpt|gemini|llama|qwen|deepseek|grok|kimi|glm)"
    r"[\s\-]?(\d+(?:\.\d+)?)?",
    re.IGNORECASE,
)
#: claude code X.Y.Z
_CCVER_RE = re.compile(r"claude[\s\-]?code[\s\-]*v?(\d+\.\d+\.\d+)", re.IGNORECASE)
#: 提示词/token 话题——单独出现太宽泛（"token"在 AI 报道里随处可见，会把完全不相关
#: 的文章都判成"提示词话题"），只作为 _mentions_claude_code 之外的第二道门槛用，
#: 两者都命中才算真的在说"Claude Code 的提示词/token 规模变了"。
_PROMPT_TOPIC_RE = re.compile(r"(prompt|token|提示词|系统提示|system\s*prompt)", re.IGNORECASE)
#: 判断文本是否真的在说 Claude Code（而不只是恰好出现 token/prompt 这类通用词）
_CLAUDE_CODE_MENTION_RE = re.compile(r"claude[\s\-]?code", re.IGNORECASE)


def _mentions_claude_code(text: str) -> bool:
    return bool(_CLAUDE_CODE_MENTION_RE.search(text)) or bool(_CCVER_RE.search(text))
#: 数字断言：80% / 3x / 2倍
_NUM_CLAIM_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(%|％|percent|pct|x|×|倍)", re.IGNORECASE)
#: 方向词
_DOWN_RE = re.compile(
    r"(reduc|cut|shrink|smaller|slim|less|lower|drop|down|trim|save|saving|shorter|"
    r"减少|降低|缩减|精简|下降|砍|省|变短|更短|更小)",
    re.IGNORECASE,
)
_UP_RE = re.compile(
    r"(increase|grow|grew|growth|larger|bigger|longer|more|boost|gain|double|triple|"
    r"翻|增加|提升|上升|扩|涨|变长|更长|更大|更多)",
    re.IGNORECASE,
)

CLAIM_SNIPPET_MAX = 140


# =========================================================================
# 小工具
# =========================================================================
def _extra(item: Any) -> Dict[str, Any]:
    if not isinstance(item, dict):
        return {}
    extra = item.get("extra")
    return extra if isinstance(extra, dict) else {}


def _text(item: Dict[str, Any]) -> str:
    return "{0} {1}".format(item.get("title") or "", item.get("summary") or "")


def _is_changelog(item: Any) -> bool:
    return isinstance(item, dict) and _extra(item).get("format") == "changelog"


def _is_narrative(item: Any) -> bool:
    return isinstance(item, dict) and _extra(item).get("format") in NARRATIVE_FORMATS


def _normalize_model(match: "re.Match") -> str:
    family = match.group(1).lower()
    ver = match.group(2)
    return "{0}-{1}".format(family, ver) if ver else family


# =========================================================================
# 主题抽取
# =========================================================================
def extract_subject(item: Dict[str, Any]) -> Optional[str]:
    """抽「主题键」：changelog 用 ``extra.subject``；叙事类从标题/摘要抽模型/版本/特性词。

    返回归一化后的小写字符串；抽不到返回 ``None``。永不抛。
    """
    try:
        if not isinstance(item, dict):
            return None
        extra = _extra(item)
        if extra.get("format") == "changelog":
            subject = extra.get("subject")
            return str(subject) if subject else None

        text = _text(item)
        model = _MODEL_RE.search(text)
        if model:
            return _normalize_model(model)
        ccver = _CCVER_RE.search(text)
        if ccver:
            return "claude-code-{0}".format(ccver.group(1))
        if _mentions_claude_code(text):
            return "claude-code"
        if _PROMPT_TOPIC_RE.search(text):
            return "prompts"
        return None
    except Exception as exc:  # noqa: BLE001 - 抽取失败退化为无主题
        logger.debug("extract_subject 失败: %s", exc)
        return None


# =========================================================================
# 绑定：版本变更卡
# =========================================================================
def bind_changes(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """把同主题/版本的变更 item（提示词 + 插件）绑成「版本变更卡」列表。永不抛。"""
    try:
        groups: "Dict[tuple, List[Dict[str, Any]]]" = {}
        for item in items or []:
            if not _is_changelog(item):
                continue
            extra = _extra(item)
            subject = str(extra.get("subject") or "unknown")
            version = extra.get("version")
            key = (subject, str(version) if version else None)
            groups.setdefault(key, []).append(item)

        cards: List[Dict[str, Any]] = []
        for (subject, version), group in groups.items():
            token_delta = 0
            for it in group:
                delta = _extra(it).get("token_delta")
                if isinstance(delta, (int, float)):
                    token_delta += int(delta)
            cards.append({
                "subject": subject,
                "version": version,
                "count": len(group),
                "token_delta": token_delta,
                "titles": [str(it.get("title") or "") for it in group],
                "refs": [str(it.get("sig") or "") for it in group],
                "urls": [str(it.get("url") or "") for it in group],
            })

        cards.sort(key=lambda c: (c["subject"], c.get("version") or ""), reverse=True)
        return cards
    except Exception as exc:  # noqa: BLE001
        logger.warning("bind_changes 失败（已忽略）: %s", exc)
        return []


# =========================================================================
# 叙事↔实证映射（核心）
# =========================================================================
def _claim_direction(text: str, unit: str) -> Optional[str]:
    down = _DOWN_RE.search(text)
    up = _UP_RE.search(text)
    if down and not up:
        return "down"
    if up and not down:
        return "up"
    if down and up:
        return "down" if down.start() < up.start() else "up"
    # 纯倍数（3x / 2倍）默认表增长
    if unit in ("x", "×", "倍"):
        return "up"
    return None


def _snippet(text: str, at: int) -> str:
    start = max(0, at - 60)
    end = min(len(text), at + 80)
    snippet = text[start:end].strip()
    return snippet[:CLAIM_SNIPPET_MAX]


def _extract_claim_regex(item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    text = _text(item)
    match = _NUM_CLAIM_RE.search(text)
    if not match:
        return None
    try:
        number = float(match.group(1))
    except (TypeError, ValueError):
        return None
    unit = match.group(2).lower()
    return {
        "number": number,
        "unit": unit,
        "direction": _claim_direction(text, unit),
        # 两道门槛都过：真的提到 Claude Code + 真的在说 prompt/token 规模，
        # 不能只靠"token"这种通用词单独出现就判定（见上方 _PROMPT_TOPIC_RE 注释）
        "is_prompt_topic": bool(_PROMPT_TOPIC_RE.search(text)) and _mentions_claude_code(text),
        "text": _snippet(text, match.start()),
    }


def _llm_refine_claim(llm: Any, item: Dict[str, Any], claim: Dict[str, Any]) -> Dict[str, Any]:
    """可选：用 LLM 精修断言方向/话题判断；任何失败原样返回 regex 结果。"""
    try:
        if llm is None or not llm.is_available():
            return claim
        prompt = (
            "下面是一段关于 AI 模型/提示词的说法，判断其数字断言的方向与话题。"
            "只输出 JSON：{{\"direction\":\"down|up|unknown\",\"is_prompt_topic\":true|false}}。\n\n"
            "说法：{0}"
        ).format(_text(item)[:500])
        data = llm.complete_json(prompt)
        if isinstance(data, dict):
            direction = data.get("direction")
            if direction in ("down", "up"):
                claim = dict(claim, direction=direction)
            elif direction == "unknown":
                claim = dict(claim, direction=None)
            if isinstance(data.get("is_prompt_topic"), bool):
                claim = dict(claim, is_prompt_topic=data["is_prompt_topic"])
    except Exception as exc:  # noqa: BLE001 - LLM 增强失败回退 regex
        logger.debug("_llm_refine_claim 回退 regex: %s", exc)
    return claim


def _prompt_evidence(evidence: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [e for e in evidence if _extra(e).get("subject") == "claude-code-prompts"]


def _match_evidence(
    narrative: Dict[str, Any],
    claim: Dict[str, Any],
    evidence: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """为一条叙事断言找同主题实证（changelog）。无匹配返回空列表。"""
    if not evidence:
        return []
    # 提示词/token 话题 → 直接对上 cc_prompts changelog（token_delta 实证）
    if claim.get("is_prompt_topic"):
        cc = _prompt_evidence(evidence)
        if cc:
            return cc
    # 否则按主题键相等匹配
    subject = extract_subject(narrative)
    if not subject:
        return []
    return [e for e in evidence if extract_subject(e) == subject]


def _judge(claim: Dict[str, Any], matched: List[Dict[str, Any]]) -> "tuple[str, Dict[str, Any]]":
    """比对断言方向与实证 token_delta，返回 ``(verdict, evidence_dict)``。"""
    total = 0
    refs: List[str] = []
    for e in matched:
        delta = _extra(e).get("token_delta")
        if isinstance(delta, (int, float)):
            total += int(delta)
        url = e.get("url")
        if url:
            refs.append(str(url))
    evidence_dict: Dict[str, Any] = {
        "kind": "changelog",
        "subject": _extra(matched[0]).get("subject") if matched else None,
        "token_delta_total": total,
        "count": len(matched),
        "refs": refs[:5],
    }
    evidence_dir = "down" if total < 0 else ("up" if total > 0 else "flat")
    claim_dir = claim.get("direction")
    if claim_dir is None:
        return "unverified", evidence_dict
    if claim_dir == evidence_dir and abs(total) >= MAGNITUDE_MIN:
        return "corroborated", evidence_dict
    if {claim_dir, evidence_dir} == {"up", "down"}:
        return "contradicted", evidence_dict
    return "unverified", evidence_dict


def cross_map_claims(items: List[Dict[str, Any]], llm: Any = None) -> None:
    """核心：叙事断言 ↔ changelog 实证映射，原地写 ``extra.corroboration``。永不抛。

    ``llm`` 可选（``core.llm_client.LLMClient``）；离线/不可用一律走 regex 回退。
    """
    try:
        pool = [it for it in (items or []) if isinstance(it, dict)]
        evidence = [it for it in pool if _is_changelog(it) and _extra(it).get("token_delta") is not None]
        # llm 由调用方决定（sync 在 --quick 时传 None 以跳过 LLM）；None 即纯 regex 回退。

        for item in pool:
            if not _is_narrative(item):
                continue
            try:
                claim = _extract_claim_regex(item)
                if not claim:
                    continue
                claim = _llm_refine_claim(llm, item, claim)
                matched = _match_evidence(item, claim, evidence)
                if matched:
                    verdict, evidence_dict = _judge(claim, matched)
                else:
                    verdict, evidence_dict = "unverified", None
                corroboration: Dict[str, Any] = {
                    "claim": claim.get("text") or "",
                    "evidence": evidence_dict,
                    "verdict": verdict,
                }
                if verdict == "corroborated":
                    corroboration["badge"] = "verified"
                extra = item.get("extra")
                if not isinstance(extra, dict):
                    extra = {}
                    item["extra"] = extra
                extra["corroboration"] = corroboration
            except Exception as exc:  # noqa: BLE001 - 单条失败不影响整批
                logger.debug("cross_map_claims 单条失败（已忽略）: %s", exc)
    except Exception as exc:  # noqa: BLE001 - 整体失败绝不外溢
        logger.warning("cross_map_claims 失败（已忽略）: %s", exc)


def make_llm() -> Any:
    """尽力构造 ``LLMClient``（供调用方在非 --quick 时传给 :func:`cross_map_claims`）。

    不可用/构造失败返回 ``None``（绝不抛）；即便返回了 client，其 ``is_available()`` 在离线/
    无 key 时仍为假，cross_map 会自然回退 regex。
    """
    try:
        from ..core.llm_client import LLMClient
        return LLMClient.from_env()
    except Exception as exc:  # noqa: BLE001
        logger.debug("LLMClient 构造失败，走 regex 回退: %s", exc)
        return None


# =========================================================================
# 业界资产演进
# =========================================================================
def _looks_like_newcomer(name: str, summary: str) -> bool:
    blob = "{0} {1}".format(name or "", summary or "").casefold()
    return any(kw in blob for kw in ("skill", "agent", "harness", "mcp", "memory", "swarm"))


def industry_evolution(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """从 insights item（``extra.format=='repo'`` / ``source_kind=='insights'``）提业界资产演进。永不抛。"""
    try:
        result: List[Dict[str, Any]] = []
        for item in items or []:
            if not isinstance(item, dict):
                continue
            extra = _extra(item)
            is_repo = extra.get("format") == "repo"
            if not (is_repo or item.get("source_kind") == "insights"):
                continue
            metrics = item.get("metrics") if isinstance(item.get("metrics"), dict) else {}
            rank = metrics.get("rank")
            name = str(item.get("title") or "")
            summary = str(item.get("summary") or "")
            result.append({
                "name": name,
                "url": str(item.get("url") or ""),
                "rank": rank,
                "stars": metrics.get("stars"),
                "forks": metrics.get("forks"),
                "summary": summary,
                "rising": bool(isinstance(rank, int) and rank <= RISING_TOP_N),
                "newcomer": _looks_like_newcomer(name, summary),
            })
        result.sort(key=lambda r: r["rank"] if isinstance(r.get("rank"), int) else 10 ** 9)
        return result
    except Exception as exc:  # noqa: BLE001
        logger.warning("industry_evolution 失败（已忽略）: %s", exc)
        return []
