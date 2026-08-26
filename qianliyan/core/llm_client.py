"""core/llm_client.py —— 内网 LLM 网关客户端（Anthropic Messages 协议）。

只负责「把 prompt 送出去、把文本/JSON 收回来」，不含任何业务语义。

**Agent 层公约（spec §5）**：所有 ``pipeline/*_agent.py`` 与 ``auto_translate`` 在
``is_available()`` 为假、或调用抛 ``LLMUnavailable`` / 任意异常时，一律走规则回退，
**绝不向上抛**——LLM 是增强项而非依赖项。
"""

from __future__ import annotations

import json
import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional, Sequence, Union

try:  # requests 属核心依赖，缺失时降级为「不可用」而非 ImportError
    import requests
except ImportError:  # pragma: no cover - 依赖缺失属部署问题
    requests = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "http://10.44.198.92:28701"
DEFAULT_MODEL = "Qwen3.6-27B"
DEFAULT_TIMEOUT = 60
ANTHROPIC_VERSION = "2023-06-01"
JSON_ONLY_HINT = "只输出 JSON，不要任何解释文字或代码围栏。"

_FENCE_RE = re.compile(r"^\s*```(?:json|JSON)?\s*|\s*```\s*$")


class LLMUnavailable(Exception):
    """LLM 不可用（无 key / 离线 / 网络失败 / 响应无法解析）。调用方必须走回退。"""


def _strip_fences(text: str) -> str:
    """剥掉 ```json ... ``` 围栏，并尽量截出首个 JSON 对象/数组。"""
    cleaned = (text or "").strip()
    if cleaned.startswith("```"):
        cleaned = _FENCE_RE.sub("", cleaned).strip()
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3].strip()
    if cleaned.startswith("{") or cleaned.startswith("["):
        return cleaned
    starts = [pos for pos in (cleaned.find("{"), cleaned.find("[")) if pos >= 0]
    if starts:
        start = min(starts)
        end = max(cleaned.rfind("}"), cleaned.rfind("]"))
        if end > start:
            return cleaned[start:end + 1].strip()
    return cleaned


class LLMClient:
    """内网 LLM 网关的极简客户端。"""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        timeout: int = DEFAULT_TIMEOUT,
    ) -> None:
        self.base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self.api_key = api_key or ""
        self.model = model or DEFAULT_MODEL
        self.timeout = timeout or DEFAULT_TIMEOUT

    @classmethod
    def from_env(cls) -> "LLMClient":
        """从环境变量构造：``ANTHROPIC_BASE_URL`` / ``ANTHROPIC_API_KEY`` / ``QLY_HAIKU_MODEL``。"""
        return cls(
            base_url=os.environ.get("ANTHROPIC_BASE_URL") or DEFAULT_BASE_URL,
            api_key=os.environ.get("ANTHROPIC_API_KEY") or "",
            model=os.environ.get("QLY_HAIKU_MODEL") or DEFAULT_MODEL,
            timeout=DEFAULT_TIMEOUT,
        )

    def is_available(self) -> bool:
        """有 api_key、装了 requests 且未开启 ``QLY_OFFLINE=1`` 时才算可用。"""
        if requests is None:
            return False
        if os.environ.get("QLY_OFFLINE") == "1":
            return False
        return bool(self.api_key)

    # ---- 文本 ----------------------------------------------------------
    def complete(self, prompt: str, system: Optional[str] = None, max_tokens: int = 1024) -> str:
        """单轮补全，返回纯文本；任何网络/HTTP/解析失败抛 ``LLMUnavailable``。"""
        if not self.is_available():
            raise LLMUnavailable("LLM 不可用：缺少 ANTHROPIC_API_KEY 或处于离线模式")

        payload: Dict[str, Any] = {
            "model": self.model,
            "max_tokens": int(max_tokens),
            "messages": [{"role": "user", "content": prompt or ""}],
        }
        if system:
            payload["system"] = system

        url = self.base_url + "/v1/messages"
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": ANTHROPIC_VERSION,
            "content-type": "application/json",
        }
        try:
            resp = requests.post(url, json=payload, headers=headers, timeout=self.timeout)
        except Exception as exc:  # noqa: BLE001 - requests 异常族统一归一
            raise LLMUnavailable("LLM 请求失败: {0}".format(exc))

        if resp.status_code >= 400:
            raise LLMUnavailable(
                "LLM 返回 HTTP {0}: {1}".format(resp.status_code, (resp.text or "")[:200])
            )
        try:
            data = resp.json()
        except ValueError as exc:
            raise LLMUnavailable("LLM 响应不是 JSON: {0}".format(exc))
        return _extract_text(data)

    # ---- JSON ----------------------------------------------------------
    def complete_json(
        self,
        prompt: str,
        system: Optional[str] = None,
        max_tokens: int = 1024,
    ) -> Union[Dict[str, Any], List[Any]]:
        """要求模型输出 JSON；解析失败重试 1 次，再失败抛 ``LLMUnavailable``。"""
        text = self.complete(prompt, system=system, max_tokens=max_tokens)
        try:
            return json.loads(_strip_fences(text))
        except ValueError:
            logger.debug("LLM 首次 JSON 解析失败，重试一次")

        retry_prompt = "{0}\n\n{1}".format(prompt or "", JSON_ONLY_HINT)
        text = self.complete(retry_prompt, system=system, max_tokens=max_tokens)
        try:
            return json.loads(_strip_fences(text))
        except ValueError as exc:
            raise LLMUnavailable("LLM 输出无法解析为 JSON: {0}".format(exc))

    def batch_json(
        self,
        prompts: Sequence[str],
        system: Optional[str] = None,
        max_workers: int = 4,
    ) -> List[Any]:
        """并发跑一批 prompt，保序返回；单条失败该位置为 ``None``（永不抛）。"""
        items = list(prompts or [])
        if not items:
            return []
        if not self.is_available():
            return [None] * len(items)

        def _one(prompt: str) -> Any:
            try:
                return self.complete_json(prompt, system=system)
            except Exception as exc:  # noqa: BLE001 - 单条失败不影响整批
                logger.warning("batch_json 单条失败: %s", exc)
                return None

        workers = max(1, min(int(max_workers or 1), len(items)))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            return list(pool.map(_one, items))


def _extract_text(data: Any) -> str:
    """从 Anthropic Messages 响应中抽取文本内容。"""
    if isinstance(data, dict):
        content = data.get("content")
        if isinstance(content, list):
            parts = []
            for block in content:
                if isinstance(block, dict):
                    parts.append(str(block.get("text") or ""))
                elif isinstance(block, str):
                    parts.append(block)
            return "".join(parts).strip()
        if isinstance(content, str):
            return content.strip()
        if isinstance(data.get("completion"), str):
            return data["completion"].strip()
    raise LLMUnavailable("LLM 响应格式无法解析: {0!r}".format(str(data)[:200]))
