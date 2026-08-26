"""engine/http.py —— 全项目唯一出网口（``engine/cdp.py`` 除外）。

统一 Chrome UA、显式超时；``QLY_OFFLINE=1`` 时任何请求一律快速抛 ``OfflineError``，
不做真实连接尝试——离线/测试环境下不许挂起。
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional

try:  # requests 属核心依赖，缺失时降级为清晰报错而非 ImportError 拖垮 import
    import requests
except ImportError:  # pragma: no cover - 依赖缺失属部署问题
    requests = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
DEFAULT_TIMEOUT = 15
#: §19.4 量级保护默认上限（5MB）——只有显式传入 ``max_bytes`` 的调用才启用流式截断，
#: 保证既有 ``get``/``get_json`` 调用（默认 ``max_bytes=None``）与既有 test_engines 行为不变。
DEFAULT_MAX_BYTES = 5 * 1024 * 1024
_STREAM_CHUNK = 65536


class OfflineError(RuntimeError):
    """``QLY_OFFLINE=1`` 时任何 ``engine.http`` 出网尝试均抛出此异常。"""


def _check_offline() -> None:
    if os.environ.get("QLY_OFFLINE") == "1":
        raise OfflineError("QLY_OFFLINE=1，禁止出网")


def _headers(extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    headers = {"User-Agent": BROWSER_UA}
    if extra:
        headers.update(extra)
    return headers


def _read_capped(resp: Any, max_bytes: int, url: str) -> None:
    """流式读取响应体至多 ``max_bytes`` 字节，超限截断并 warning，回写到 ``resp._content``。

    §19.4 量级保护：防止整取巨型文件（如 insights 的 31MB/75MB json）。响应不支持
    ``iter_content`` 时静默跳过（保持原响应），失败绝不外溢。
    """
    iter_content = getattr(resp, "iter_content", None)
    if not callable(iter_content):
        return
    chunks = []
    total = 0
    truncated = False
    try:
        for chunk in iter_content(chunk_size=_STREAM_CHUNK):
            if not chunk:
                continue
            chunks.append(chunk)
            total += len(chunk)
            if total >= max_bytes:
                truncated = True
                break
    except Exception as exc:  # noqa: BLE001 - 流式读取失败不外溢
        logger.warning("流式读取响应失败（url=%s）: %s", url, exc)
        return
    finally:
        try:
            resp.close()
        except Exception:  # noqa: BLE001
            pass
    data = b"".join(chunks)[:max_bytes]
    if truncated:
        logger.warning("响应超过 max_bytes=%d，已截断（url=%s）", max_bytes, url)
    resp._content = data
    resp._content_consumed = True


def get(
    url: str,
    timeout: int = DEFAULT_TIMEOUT,
    headers: Optional[Dict[str, str]] = None,
    max_bytes: Optional[int] = None,
) -> Any:
    """GET 请求，统一 Chrome UA 与显式超时；返回原始 ``requests.Response``（不自动 raise_for_status，
    调用方按需检查 ``status_code``，供 health_check 之类只想探测连通性的场景使用）。

    ``max_bytes`` 为 ``None``（默认）时行为与既有完全一致（一次性读取，不流式）；
    传入正整数时改为流式读取并在超限时截断（§19.4 量级保护）。
    """
    _check_offline()
    if requests is None:  # pragma: no cover - 依赖缺失属部署问题
        raise RuntimeError("缺少 requests 依赖（核心依赖），请 pip install requests")
    logger.debug("GET %s", url)
    if not max_bytes or int(max_bytes) <= 0:
        return requests.get(url, timeout=timeout, headers=_headers(headers))
    resp = requests.get(url, timeout=timeout, headers=_headers(headers), stream=True)
    _read_capped(resp, int(max_bytes), url)
    return resp


def get_json(
    url: str,
    timeout: int = DEFAULT_TIMEOUT,
    headers: Optional[Dict[str, str]] = None,
    max_bytes: Optional[int] = None,
) -> Any:
    """GET 并解析 JSON；HTTP/JSON 错误原样向上抛，由调用方（各眼 fetch）决定容错策略。

    ``max_bytes`` 语义同 :func:`get`（默认 ``None`` 不改变既有行为）。
    """
    resp = get(url, timeout=timeout, headers=headers, max_bytes=max_bytes)
    return resp.json()


def get_text(
    url: str,
    timeout: int = DEFAULT_TIMEOUT,
    headers: Optional[Dict[str, str]] = None,
    max_bytes: Optional[int] = DEFAULT_MAX_BYTES,
) -> str:
    """GET 并返回文本；默认启用 §19.4 量级保护（``max_bytes=DEFAULT_MAX_BYTES``）。

    供变更情报眼抓取 CHANGELOG.md / daily-insight.md 等文本文件使用。
    """
    resp = get(url, timeout=timeout, headers=headers, max_bytes=max_bytes)
    text = getattr(resp, "text", None)
    if text is not None:
        return text
    content = getattr(resp, "content", b"") or b""
    if isinstance(content, bytes):
        return content.decode("utf-8", "replace")
    return str(content)
