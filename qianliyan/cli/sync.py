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

MOCK_FIXTURE_RELPATH = ("tests", "fixtures", "mock_items.jsonl")
HOTLIST_LIMIT = 50
HOTLIST_TITLE = "🔥 全局热榜 Top 50"


def _mock_fixture_path() -> Path:
    """测试会 monkeypatch 本函数注入临时副本。"""
    return paths.repo_root().joinpath(*MOCK_FIXTURE_RELPATH)


def _new_run_id(now: datetime) -> str:
    return "{0}-{1}".format(now.strftime("%Y%m%dT%H%M%SZ"), uuid.uuid4().hex[:4])


def _eye_cfg(name: str) -> Dict[str, Any]:
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
    """异常在此吞掉：单眼故障不外溢。"""
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
    except Exception as exc:  # noqa: BLE001 - 静默失败
        logger.debug("data 目录 git 快照跳过: %s", exc)


def _carry_first_seen_dates(
    new_items: Sequence[Dict[str, Any]], old_items: Sequence[Dict[str, Any]], now: datetime,
) -> int:
    """源没给日期的条目沿用首见日期，否则每轮被补成 now，旧文永远霸占新鲜度前排；返回沿用条数。"""
    first_seen: Dict[str, str] = {}
    for old in old_items or []:
        date = old.get("date")
        if not date:
            continue
        for key in (old.get("url"), old.get("sig")):
            if key and (key not in first_seen or str(date) < first_seen[key]):
                first_seen[key] = str(date)

    carried = 0
    for item in new_items:
        extra = item.get("extra")
        if not isinstance(extra, dict) or extra.get("date_precision") != "unknown":
            continue
        prior = first_seen.get(item.get("url") or "") or first_seen.get(item.get("sig") or "")
        if prior and prior != item.get("date"):
            item["date"] = prior
            carried += 1
        elif not prior and not item.get("date"):
            item["date"] = utils.iso(now)
    return carried


def run_sync(
    eyes: Optional[Sequence[str]] = None,
    quick: bool = False,
    since: Any = None,
    no_html: bool = False,
    strict: bool = False,
    mock: bool = False,
) -> Dict[str, Any]:
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

    # 变更情报源只在全量同步时拉，折入 local 桶，单列 change_meta 不污染 meta['eyes']
    change_meta: "Dict[str, Dict[str, Any]]" = {}
    if eyes is None:
        for name, fn in CHANGE_EYES.items():
            items, meta = _fetch_change_one(name, fn, mock, since)
            change_meta[name] = meta
            new_items.extend(items)
            _write_snapshot(date_str, name, items)

    raw_pool_path = paths.data_path("raw_pool.jsonl")
    old_raw = storage.read_jsonl(raw_pool_path)
    carried = _carry_first_seen_dates(new_items, old_raw, now)
    if carried:
        logger.info("无日期条目沿用首见时间 %d 条", carried)
    max_age_days = os.environ.get("QLY_POOL_MAX_AGE_DAYS")
    # 失败眼保留旧池数据：故障 ≠ 空结果
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

    # --quick 也执行：personal_score / persona 字段必须落
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

    # 须在 items.jsonl 落盘前执行，corroboration 才能随 item 落盘
    change_cards: List[Dict[str, Any]] = []
    industry: List[Dict[str, Any]] = []
    try:
        change_cards = change_intel.bind_changes(deduped)
        industry = change_intel.industry_evolution(deduped)
        llm = None if quick else change_intel.make_llm()
        change_intel.cross_map_claims(deduped, llm=llm)
    except Exception as exc:  # noqa: BLE001 - 变更情报加工失败不拖垮主链路
        logger.warning("change_intel 加工意外失败（已忽略）: %s", exc)

    # 须在全部富化之后落盘，API 的 sort=personal 与 /profile 才读得到 personal_score
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
