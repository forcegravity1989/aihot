"""cli/health_check.py —— 信源连通性探测（spec §10.6）。

逐信源 GET 探测（10s 超时，2xx/3xx 判定为 OK）：远眼各 url、aihot base_url、
builders raw url、insights raw url；``company`` 默认跳过，``--include-internal``
时才测 CDP 连通性。``QLY_OFFLINE=1`` 时全部 SKIP。

退出码 = 失败信源数（SKIP 不计入失败）。
"""

from __future__ import annotations

import argparse
import logging
import os
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ..core import paths
from ..engine import http, remote_sync

logger = logging.getLogger("qianliyan.cli.health_check")

TIMEOUT = 10
STATUS_OK = "OK"
STATUS_FAIL = "FAIL"
STATUS_SKIP = "SKIP"


def _is_offline() -> bool:
    return os.environ.get("QLY_OFFLINE") == "1"


# =========================================================================
# 探测目标收集
# =========================================================================
def _local_targets(sources_cfg: Dict[str, Any]) -> List[Tuple[str, str, str]]:
    out: List[Tuple[str, str, str]] = []
    for src in sources_cfg.get("sources") or []:
        if not isinstance(src, dict):
            continue
        url = src.get("url") or ""
        if not url:
            continue
        name = str(src.get("name") or url)
        backend = remote_sync.detect_backend(url, src.get("type"))
        out.append((name, backend, url))
    return out


def _aihot_target(sources_cfg: Dict[str, Any]) -> List[Tuple[str, str, str]]:
    cfg = sources_cfg.get("aihot") or {}
    base_url = str(cfg.get("base_url") or "https://aihot.virxact.com").rstrip("/")
    endpoint = str(cfg.get("endpoint") or "/api/news")
    categories = cfg.get("categories") or ["llm"]
    category = categories[0] if categories else "llm"
    url = "{0}{1}?category={2}".format(base_url, endpoint, category)
    return [("AIHot", "rest", url)]


def _builders_target(builders_cfg: Dict[str, Any]) -> List[Tuple[str, str, str]]:
    repo = builders_cfg.get("repo")
    branch = builders_cfg.get("branch") or "main"
    data_path = builders_cfg.get("data_path")
    if not repo or not data_path:
        return []
    url = "https://raw.githubusercontent.com/{0}/{1}/{2}".format(repo, branch, data_path)
    return [("Builders Feed", "raw_json", url)]


def _insights_target(sources_cfg: Dict[str, Any]) -> List[Tuple[str, str, str]]:
    cfg = sources_cfg.get("insights") or {}
    repo = cfg.get("repo") or "anthropics/claude-code-insights"
    branch = cfg.get("branch") or "main"
    data_paths = cfg.get("data_paths") or ["data/plugins.json"]
    data_path = data_paths[0] if data_paths else "data/plugins.json"
    url = "https://raw.githubusercontent.com/{0}/{1}/{2}".format(repo, branch, data_path)
    return [("Insights Feed", "raw_json", url)]


def collect_targets() -> List[Tuple[str, str, str]]:
    """收集所有要探测的 (name, backend, url)（不含 company —— 单独处理）。"""
    sources_cfg = paths.load_yaml_config("sources")
    builders_cfg = paths.load_yaml_config("builders")

    targets: List[Tuple[str, str, str]] = []
    targets.extend(_aihot_target(sources_cfg))
    targets.extend(_builders_target(builders_cfg))
    targets.extend(_insights_target(sources_cfg))
    targets.extend(_local_targets(sources_cfg))
    return targets


# =========================================================================
# 探测
# =========================================================================
def _probe_url(name: str, backend: str, url: str) -> Dict[str, Any]:
    if _is_offline():
        return {
            "name": name, "backend": backend, "url": url,
            "status": STATUS_SKIP, "duration_s": 0.0, "error": "QLY_OFFLINE=1",
        }
    started = time.perf_counter()
    try:
        resp = http.get(url, timeout=TIMEOUT)
        duration = time.perf_counter() - started
        ok = 200 <= resp.status_code < 400
        return {
            "name": name, "backend": backend, "url": url,
            "status": STATUS_OK if ok else STATUS_FAIL,
            "duration_s": round(duration, 3),
            "error": None if ok else "HTTP {0}".format(resp.status_code),
        }
    except Exception as exc:  # noqa: BLE001 - 探测失败归一为 FAIL，不许崩溃整轮探测
        duration = time.perf_counter() - started
        return {
            "name": name, "backend": backend, "url": url,
            "status": STATUS_FAIL, "duration_s": round(duration, 3), "error": str(exc),
        }


def _probe_company() -> Dict[str, Any]:
    name = "内网 company（CDP）"
    if _is_offline():
        return {
            "name": name, "backend": "cdp", "url": "(CDP 常驻浏览器)",
            "status": STATUS_SKIP, "duration_s": 0.0, "error": "QLY_OFFLINE=1",
        }
    started = time.perf_counter()
    try:
        from ..engine import cdp

        playwright_ctx, browser = cdp.connect()
        duration = time.perf_counter() - started
        try:
            browser.close()
        finally:
            playwright_ctx.stop()
        return {
            "name": name, "backend": "cdp", "url": "(CDP 常驻浏览器)",
            "status": STATUS_OK, "duration_s": round(duration, 3), "error": None,
        }
    except Exception as exc:  # noqa: BLE001
        duration = time.perf_counter() - started
        return {
            "name": name, "backend": "cdp", "url": "(CDP 常驻浏览器)",
            "status": STATUS_FAIL, "duration_s": round(duration, 3), "error": str(exc),
        }


def run_health_check(include_internal: bool = False) -> List[Dict[str, Any]]:
    """跑一轮全量探测，返回逐信源结果列表。"""
    results = [_probe_url(name, backend, url) for name, backend, url in collect_targets()]
    if include_internal:
        results.append(_probe_company())
    else:
        results.append({
            "name": "内网 company（CDP）", "backend": "cdp", "url": "(CDP 常驻浏览器)",
            "status": STATUS_SKIP, "duration_s": 0.0, "error": "默认跳过，加 --include-internal 测试",
        })
    return results


# =========================================================================
# 输出
# =========================================================================
def _print_table(results: Sequence[Dict[str, Any]]) -> None:
    name_w = max([len("信源")] + [len(str(r["name"])) for r in results])
    backend_w = max([len("后端")] + [len(str(r["backend"])) for r in results])
    header = "{0}  {1}  {2}  {3}  {4}".format(
        "信源".ljust(name_w), "后端".ljust(backend_w), "状态".ljust(4), "耗时(s)".rjust(7), "错误摘要",
    )
    print(header)
    print("-" * len(header))
    for row in results:
        error = (row.get("error") or "")[:80]
        print(
            "{0}  {1}  {2}  {3}  {4}".format(
                str(row["name"]).ljust(name_w),
                str(row["backend"]).ljust(backend_w),
                str(row["status"]).ljust(4),
                "{0:.3f}".format(row.get("duration_s") or 0.0).rjust(7),
                error,
            )
        )


# =========================================================================
# CLI
# =========================================================================
def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m qianliyan.cli.health_check", description="千里眼信源连通性探测",
    )
    parser.add_argument(
        "--include-internal", action="store_true", help="额外测试内网 company 的 CDP 连通性",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = _build_arg_parser()
    args = parser.parse_args(argv)

    results = run_health_check(include_internal=args.include_internal)
    _print_table(results)

    failed = sum(1 for r in results if r["status"] == STATUS_FAIL)
    skipped = sum(1 for r in results if r["status"] == STATUS_SKIP)
    print()
    print("共 {0} 个信源：OK={1} FAIL={2} SKIP={3}".format(
        len(results), len(results) - failed - skipped, failed, skipped,
    ))
    return failed


if __name__ == "__main__":
    raise SystemExit(main())
