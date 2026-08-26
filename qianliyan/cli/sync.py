"""cli/sync.py —— 主编排器（spec §10.1）。

职责：确定要跑的眼 → 并行抓取并逐眼记账 → 写抓取快照 → 按眼增量合并原始池 →
去重打分并盖章 → （非 quick）og_image 补全 + 翻译/路由/三个 agent 标注 →
频道路由与写盘 → 写全局热榜 → 渲染 HTML 简报 → 写 ``sync_meta.json`` →
（可选）data 目录 git 快照。

``--mock``：跳过真实抓取，从 ``tests/fixtures/mock_items.jsonl`` 读入并按眼过滤
（S5 离线端到端），同时强制 ``QLY_OFFLINE=1``。真实抓取路径统一经 ``eyes.EYES``
分发，单眼异常在本模块记账、不外溢（S1）。
"""

from __future__ import annotations

import argparse
import logging
import os
import subprocess
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from ..core import og_image, paths, profile, storage, utils
from ..eyes import CHANGE_EYES, EYES
from ..pipeline import (
    auto_translate,
    change_intel,
    channels,
    headline_cluster_agent,
    headline_fit_agent,
    model_cluster_agent,
    persona,
    report,
    routing,
)

logger = logging.getLogger("qianliyan.cli.sync")

#: mock fixture 相对仓库根的路径（repo 根定位统一走 ``core.paths.repo_root()``）
MOCK_FIXTURE_RELPATH = ("tests", "fixtures", "mock_items.jsonl")
HOTLIST_LIMIT = 50
HOTLIST_TITLE = "🔥 全局热榜 Top 50"


# =========================================================================
# 小工具
# =========================================================================
def _mock_fixture_path() -> Path:
    """mock 数据文件路径（测试可 monkeypatch 本函数以注入改写过 date 的临时副本）。"""
    return paths.repo_root().joinpath(*MOCK_FIXTURE_RELPATH)


def _new_run_id(now: datetime) -> str:
    return "{0}-{1}".format(now.strftime("%Y%m%dT%H%M%SZ"), uuid.uuid4().hex[:4])


def _eye_cfg(name: str) -> Dict[str, Any]:
    """按 Wave1/2 约定的 cfg 语义为每眼组装配置（各眼 cfg 语义不互通，逐一处理）。"""
    if name == "aihot":
        sources_cfg = paths.load_yaml_config("sources")
        return dict(sources_cfg.get("aihot") or {})
    if name == "insights":
        sources_cfg = paths.load_yaml_config("sources")
        return dict(sources_cfg.get("insights") or {})
    if name == "local":
        return paths.load_yaml_config("sources")
    if name == "builders":
        return paths.load_yaml_config("builders")
    if name == "company":
        return {}
    return {}


def _load_mock_pool(eye_names: Sequence[str]) -> Dict[str, List[Dict[str, Any]]]:
    """读 mock fixture，按 ``source_kind`` 分桶，只保留本次要跑的眼。"""
    rows = storage.read_jsonl(_mock_fixture_path())
    pool: Dict[str, List[Dict[str, Any]]] = {name: [] for name in eye_names}
    for row in rows:
        kind = row.get("source_kind")
        if kind in pool:
            pool[kind].append(dict(row))
    return pool


def _fetch_one(
    name: str,
    mock_pool: Optional[Dict[str, List[Dict[str, Any]]]],
    since: Any,
) -> "tuple[List[Dict[str, Any]], Dict[str, Any]]":
    """单眼抓取 + 记账；异常在此吞掉（sync 统一记账，单眼故障不外溢，S1）。"""
    started = time.perf_counter()
    try:
        if mock_pool is not None:
            items = list(mock_pool.get(name) or [])
        else:
            fn = EYES[name]
            items = list(fn(_eye_cfg(name), since=since) or [])
        duration = time.perf_counter() - started
        return items, {
            "ok": True,
            "count": len(items),
            "duration_s": round(duration, 3),
            "error": None,
        }
    except Exception as exc:  # noqa: BLE001 - 单眼故障统一记账，绝不外溢
        duration = time.perf_counter() - started
        logger.warning("眼 %s 抓取失败: %s", name, exc)
        return [], {
            "ok": False,
            "count": 0,
            "duration_s": round(duration, 3),
            "error": str(exc),
        }


def _change_eye_cfg(name: str, mock: bool) -> Dict[str, Any]:
    """变更情报眼的 cfg。

    ``--mock``：指向仓库内已入库的真实 fixture（离线安全、确定性），使其在离线端到端里也有数；
    真实同步：返回空 cfg，各眼用模块默认 raw 端点（离线时自然抓取失败，由上层记 ok=False）。
    """
    if not mock:
        return {}
    real_dir = paths.repo_root() / "tests" / "fixtures" / "real"
    if name == "cc_prompts":
        return {"local_path": str(real_dir / "ccprompts_changelog.md")}
    if name == "plugins_official":
        return {
            "marketplace_path": str(real_dir / "plugins_official_marketplace.json"),
            "bump_path": str(real_dir / "plugins_official_bump.json"),
        }
    return {}


def _fetch_change_one(
    name: str,
    fn: Any,
    mock: bool,
    since: Any,
) -> "tuple[List[Dict[str, Any]], Dict[str, Any]]":
    """单个变更情报眼抓取 + 记账；异常在此吞掉（离线失败记 ok=False，不外溢，§19.4）。"""
    started = time.perf_counter()
    try:
        items = list(fn(_change_eye_cfg(name, mock), since=since) or [])
        return items, {
            "ok": True,
            "count": len(items),
            "duration_s": round(time.perf_counter() - started, 3),
            "error": None,
        }
    except Exception as exc:  # noqa: BLE001 - 变更源故障统一记账，绝不外溢
        logger.warning("变更情报眼 %s 抓取失败: %s", name, exc)
        return [], {
            "ok": False,
            "count": 0,
            "duration_s": round(time.perf_counter() - started, 3),
            "error": str(exc),
        }


def _write_snapshot(date_str: str, name: str, items: Sequence[Dict[str, Any]]) -> None:
    try:
        storage.write_jsonl(paths.data_path("items", date_str, "{0}.jsonl".format(name)), items)
    except Exception as exc:  # noqa: BLE001 - 审计快照失败不许拖垮主链路
        logger.warning("写抓取快照失败 (%s): %s", name, exc)


def _git_snapshot(run_id: str) -> None:
    if os.environ.get("QLY_GIT_SNAPSHOT", "1") == "0":
        return
    try:
        data_dir = paths.resolve_data_dir()
        if not (data_dir / ".git").is_dir():
            return
        subprocess.run(
            ["git", "add", "-A"], cwd=str(data_dir),
            capture_output=True, timeout=30, check=False,
        )
        subprocess.run(
            ["git", "commit", "-m", "sync {0}".format(run_id)], cwd=str(data_dir),
            capture_output=True, timeout=30, check=False,
        )
    except Exception as exc:  # noqa: BLE001 - 静默失败（spec §10.1.8）
        logger.debug("data 目录 git 快照跳过: %s", exc)


# =========================================================================
# 主编排
# =========================================================================
def run_sync(
    eyes: Optional[Sequence[str]] = None,
    quick: bool = False,
    since: Any = None,
    no_html: bool = False,
    strict: bool = False,
    mock: bool = False,
) -> Dict[str, Any]:
    """跑一轮完整同步，返回本轮 ``sync_meta``（同时写盘 ``sync_meta.json``）。"""
    if mock:
        os.environ["QLY_OFFLINE"] = "1"

    eye_names = [name for name in (eyes or EYES.keys()) if name in EYES]
    if not eye_names:
        eye_names = list(EYES.keys())

    now = utils.now_utc()
    run_id = _new_run_id(now)
    started_at = utils.iso(now)
    date_str = now.strftime("%Y-%m-%d")

    mock_pool = _load_mock_pool(eye_names) if mock else None

    eyes_meta: "Dict[str, Dict[str, Any]]" = {}
    new_items: List[Dict[str, Any]] = []
    for name in eye_names:
        items, meta = _fetch_one(name, mock_pool, since)
        eyes_meta[name] = meta
        new_items.extend(items)
        _write_snapshot(date_str, name, items)

    # 变更情报源（Wave H1，spec-v0.3 §19）：仅在全量同步（未指定 --eye 子集）时额外拉取，
    # 其 item source_kind=local，折入原始池的 local 桶；单列 change_meta，不污染 meta["eyes"]
    # （核心眼注册表锁死为五只，见 eyes/__init__.py）。
    change_meta: "Dict[str, Dict[str, Any]]" = {}
    if eyes is None:
        for name, fn in CHANGE_EYES.items():
            items, meta = _fetch_change_one(name, fn, mock, since)
            change_meta[name] = meta
            new_items.extend(items)
            _write_snapshot(date_str, name, items)

    raw_pool_path = paths.data_path("raw_pool.jsonl")
    old_raw = storage.read_jsonl(raw_pool_path)
    max_age_days = os.environ.get("QLY_POOL_MAX_AGE_DAYS")
    # 只有抓取成功的眼才以本次快照为准；失败眼保留旧池数据（故障 ≠ 空结果，S1）
    ran_ok_kinds = [name for name in eye_names if eyes_meta.get(name, {}).get("ok")]
    merged_raw = storage.merge_pool_by_eyes(old_raw, new_items, ran_ok_kinds, max_age_days, now)
    storage.write_jsonl(raw_pool_path, merged_raw)

    deduped = utils.dedup_and_score(merged_raw, now)
    fetched_at = utils.iso(now)
    for item in deduped:
        item["sync_run_id"] = run_id
        item["fetched_at"] = fetched_at

    if not quick:
        try:
            og_image.enrich(deduped)
        except Exception as exc:  # noqa: BLE001 - og_image 已自行吞异常，这里是最后一道保险
            logger.warning("og_image.enrich 意外失败（已忽略）: %s", exc)

    if not quick:
        try:
            auto_translate.translate(deduped)
        except Exception as exc:  # noqa: BLE001 - agent 回退公约的最后一道保险
            logger.warning("auto_translate.translate 意外失败（已忽略）: %s", exc)
        for annotate in (routing.annotate, model_cluster_agent.annotate,
                          headline_fit_agent.annotate, headline_cluster_agent.annotate):
            try:
                annotate(deduped)
            except Exception as exc:  # noqa: BLE001
                logger.warning("%s 意外失败（已忽略）: %s", annotate.__module__, exc)

    # 读者画像 + 个性化打分 + 人物画像（spec-v0.3 §7）。
    # --quick 也执行：其内部 LLM 增强可选，但 personal_score / persona 结构字段必须落。
    # personalize 原地写 extra.personal_score；build_personas 回退不阻塞、绝不向上抛。
    personas: List[Dict[str, Any]] = []
    try:
        reader_profile = profile.load_reader_profile()
        profile.personalize(deduped, reader_profile)
    except Exception as exc:  # noqa: BLE001 - 个性化失败退化为不加权，不拖垮主链路
        logger.warning("personalize 意外失败（已忽略，退化为 hotness）: %s", exc)
    try:
        personas = persona.build_personas(deduped)
        persona.write_personas(personas)
    except Exception as exc:  # noqa: BLE001 - 人物画像失败不影响其余产物
        logger.warning("人物画像构建/写盘意外失败（已忽略）: %s", exc)
        personas = []

    # 变更情报加工（spec-v0.3 §19）：绑定版本变更卡 + 叙事↔实证映射（原地写 extra.corroboration）
    # + 业界资产演进。挂载点：write_personas 之后、items.jsonl 落盘之前，使 corroboration 随 item 落盘。
    # --quick 跳过 LLM（传 llm=None，纯 regex 回退）；结构字段照落。全程绝不外溢。
    change_cards: List[Dict[str, Any]] = []
    industry: List[Dict[str, Any]] = []
    try:
        change_cards = change_intel.bind_changes(deduped)
        industry = change_intel.industry_evolution(deduped)
        llm = None if quick else change_intel.make_llm()
        change_intel.cross_map_claims(deduped, llm=llm)
    except Exception as exc:  # noqa: BLE001 - 变更情报加工失败不拖垮主链路
        logger.warning("change_intel 加工意外失败（已忽略）: %s", exc)

    # items.jsonl 在全部富化（og_image/翻译/agent 标注/个性化/变更情报）之后落盘，
    # 使 API /items?sort=personal 与 /profile 能读到 personal_score（spec-v0.3 §8）。
    storage.write_jsonl(paths.data_path("items.jsonl"), deduped)

    routed = channels.run_all(deduped)

    top50 = sorted(deduped, key=lambda it: it.get("hotness") or 0.0, reverse=True)[:HOTLIST_LIMIT]
    hotlist_text = channels.render_channel_md(HOTLIST_TITLE, top50, utils.iso(now))
    try:
        paths.data_path("hotlist.md").write_text(hotlist_text, encoding="utf-8")
    except OSError as exc:
        logger.warning("写 hotlist.md 失败: %s", exc)

    if not no_html:
        try:
            report.render_html(deduped, routed, personas=personas)
        except FileNotFoundError as exc:
            logger.warning("简报模板缺失，跳过 HTML 渲染: %s", exc)
        except Exception as exc:  # noqa: BLE001 - 渲染失败不许拖垮整轮 sync
            logger.warning("HTML 渲染意外失败（已忽略）: %s", exc)

    corroborated = sum(
        1 for it in deduped
        if (it.get("extra") or {}).get("corroboration", {}).get("verdict") == "corroborated"
    )
    finished_at = utils.iso(utils.now_utc())
    totals = {
        "raw": len(merged_raw),
        "deduped": len(deduped),
        "heavy": sum(1 for it in deduped if "heavy" in (it.get("badges") or [])),
        "flash": sum(1 for it in deduped if "flash" in (it.get("badges") or [])),
        "personas": len(personas),
        "changes": len(change_cards),
        "corroborated": corroborated,
        "industry": len(industry),
    }
    meta: Dict[str, Any] = {
        "run_id": run_id,
        "started_at": started_at,
        "finished_at": finished_at,
        "eyes": eyes_meta,
        "change_eyes": change_meta,
        "totals": totals,
    }
    storage.write_json(paths.data_path("sync_meta.json"), meta)

    _git_snapshot(run_id)

    logger.info(
        "sync 完成 run_id=%s raw=%d deduped=%d heavy=%d flash=%d",
        run_id, totals["raw"], totals["deduped"], totals["heavy"], totals["flash"],
    )
    return meta


# =========================================================================
# CLI
# =========================================================================
def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m qianliyan.cli.sync", description="千里眼主编排器",
    )
    parser.add_argument("--quick", action="store_true", help="跳过 agent 五连与 og_image")
    parser.add_argument(
        "--eye", action="append", dest="eye", choices=sorted(EYES.keys()), default=None,
        help="只跑指定的眼，可重复指定（默认全部）",
    )
    parser.add_argument("--since", default=None, help="仅抓取该时间之后的内容（ISO 8601）")
    parser.add_argument("--no-html", action="store_true", help="跳过 HTML 简报渲染")
    parser.add_argument("--strict", action="store_true", help="任一眼失败则进程退出码非 0")
    parser.add_argument("--status", action="store_true", help="不跑 sync，只读 sync_meta.json 打印")
    parser.add_argument("--mock", action="store_true", help="离线端到端：从 mock fixture 读入")
    return parser


def _print_status() -> int:
    meta = storage.read_json(paths.data_path("sync_meta.json"), default=None)
    if not meta:
        print("尚无 sync_meta.json，请先执行一次 `python -m qianliyan.cli.sync`。")
        return 1

    print("run_id      : {0}".format(meta.get("run_id")))
    print("started_at  : {0}".format(meta.get("started_at")))
    print("finished_at : {0}".format(meta.get("finished_at")))
    print("眼状态:")
    for name, info in (meta.get("eyes") or {}).items():
        mark = "OK  " if info.get("ok") else "FAIL"
        line = "  - {0:<10s} {1}  count={2:<5}  {3:.3f}s".format(
            name, mark, info.get("count", 0), float(info.get("duration_s") or 0.0)
        )
        if info.get("error"):
            line += "  error={0}".format(info.get("error"))
        print(line)

    totals = meta.get("totals") or {}
    print(
        "totals      : raw={0} deduped={1} heavy={2} flash={3} personas={4}".format(
            totals.get("raw"), totals.get("deduped"), totals.get("heavy"),
            totals.get("flash"), totals.get("personas"),
        )
    )
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = _build_arg_parser()
    args = parser.parse_args(argv)

    if args.status:
        return _print_status()

    meta = run_sync(
        eyes=args.eye,
        quick=args.quick,
        since=args.since,
        no_html=args.no_html,
        strict=args.strict,
        mock=args.mock,
    )

    print("sync 完成：run_id={0}".format(meta.get("run_id")))
    for name, info in (meta.get("eyes") or {}).items():
        mark = "OK" if info.get("ok") else "FAIL"
        print("  - {0}: {1} count={2}".format(name, mark, info.get("count")))
    totals = meta.get("totals") or {}
    print(
        "totals: raw={0} deduped={1} heavy={2} flash={3} personas={4}".format(
            totals.get("raw"), totals.get("deduped"), totals.get("heavy"),
            totals.get("flash"), totals.get("personas"),
        )
    )

    if args.strict:
        failed = [n for n, info in (meta.get("eyes") or {}).items() if not info.get("ok", True)]
        if failed:
            print("--strict：以下眼失败 → {0}".format(", ".join(failed)))
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
