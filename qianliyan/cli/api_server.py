"""cli/api_server.py —— FastAPI 服务（可选依赖，spec §10.3）。

``fastapi``/``uvicorn`` 属可选依赖（``pip install "qianliyan[api]"``），惰性导入——
缺失时本模块 import 仍然成功，只是 ``create_app()`` 会抛出带安装提示的 ``RuntimeError``，
``main()`` 打印安装提示后返回非 0（不得让核心链路 ImportError）。

鉴权：env ``QLY_API_KEY`` 非空时，所有端点要求请求头 ``X-API-Key`` 与之一致，否则 401。
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional, Sequence

from .. import __version__
from ..core import paths, profile, storage, utils

logger = logging.getLogger("qianliyan.cli.api_server")

DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8787
DEFAULT_ITEMS_LIMIT = 50
DEFAULT_PROFILE_ITEMS = 20
FEEDBACK_ACTIONS = ("up", "down", "hide")

#: 深读/浅读日报归档文件名（spec-v0.3 §6/§8，daily_digest_all 定稿产物）
DAILY_VIEWS = {"glance": "glance.html", "deep": "deep.html"}
DAILY_DEFAULT_VIEW = "deep"
DAILY_ROOT_NAME = "daily.html"

try:  # fastapi/uvicorn 属可选依赖，缺失时降级而不拖垮 import（spec §10.3）
    from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException
    from fastapi.responses import HTMLResponse, PlainTextResponse
    from pydantic import BaseModel

    FASTAPI_AVAILABLE = True
except ImportError:  # pragma: no cover - 依赖缺失属部署问题
    FASTAPI_AVAILABLE = False
    BaseModel = object  # type: ignore[assignment,misc]


if FASTAPI_AVAILABLE:

    class FeedbackBody(BaseModel):  # type: ignore[misc]
        """``POST /feedback`` 请求体：``{sig, action: up|down|hide, note?}``。"""

        sig: str
        action: str
        note: Optional[str] = None

    class HistoryBody(BaseModel):  # type: ignore[misc]
        """``POST /history`` 请求体：``{sig, action, title?, url?}``（spec-v0.3 §8）。

        ``action ∈ {seen, open, deepread, received}``（``received`` = 浅读「看过标题即已接收」）。
        """

        sig: str
        action: str
        title: Optional[str] = None
        url: Optional[str] = None


# =========================================================================
# 鉴权
# =========================================================================
if FASTAPI_AVAILABLE:

    def _verify_api_key(
        x_api_key: Optional[str] = Header(default=None, alias="X-API-Key"),
    ) -> None:
        """env ``QLY_API_KEY`` 非空时校验请求头 ``X-API-Key``；不一致抛 401。"""
        expected = os.environ.get("QLY_API_KEY")
        if expected and x_api_key != expected:
            raise HTTPException(status_code=401, detail="缺少或错误的 X-API-Key")


# =========================================================================
# 数据读取小工具（与 cli.deliver 同源逻辑，避免循环 import 就地内联）
# =========================================================================
def _load_items() -> List[Dict[str, Any]]:
    return storage.read_jsonl(paths.data_path("items.jsonl"))


def _personal_score(item: Dict[str, Any]) -> float:
    """取 ``extra.personal_score``（缺失/非数值按 0 计），供 ``sort=personal`` 与 /profile 用。"""
    extra = item.get("extra")
    raw = extra.get("personal_score") if isinstance(extra, dict) else None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return 0.0


def _filter_items(
    items: Sequence[Dict[str, Any]],
    channel: Optional[str],
    limit: int,
    since: Optional[str],
    sort: Optional[str] = None,
) -> List[Dict[str, Any]]:
    rows = [it for it in items if isinstance(it, dict)]

    if channel:
        index = storage.read_json(paths.data_path("channels.json"), default={}) or {}
        sigs = set(index.get(channel) or [])
        rows = [it for it in rows if it.get("sig") in sigs]

    if since:
        since_dt = utils.parse_date(since)
        if since_dt is not None:
            rows = [
                it for it in rows
                if (utils.parse_date(it.get("date")) or utils.now_utc()) >= since_dt
            ]

    if str(sort or "").casefold() == "personal":
        rows.sort(key=_personal_score, reverse=True)
    else:
        rows.sort(key=lambda it: it.get("hotness") or 0.0, reverse=True)
    if limit and limit > 0:
        rows = rows[:limit]
    return rows


# =========================================================================
# App 工厂
# =========================================================================
def create_app() -> FastAPI:  # type: ignore[name-defined]
    """构造 FastAPI app；供 uvicorn 挂载，也供测试用 ``TestClient`` 直接 import。"""
    if not FASTAPI_AVAILABLE:
        raise RuntimeError(
            '缺少 fastapi/uvicorn 依赖，请先执行 pip install "qianliyan[api]"'
        )

    app = FastAPI(
        title="千里眼 API",
        version=__version__,
        dependencies=[Depends(_verify_api_key)],
    )

    @app.get("/digest")
    def get_digest() -> HTMLResponse:  # type: ignore[name-defined]
        path = paths.data_path("digest.html")
        if not path.is_file():
            raise HTTPException(status_code=404, detail="digest.html 不存在，请先执行 sync")
        return HTMLResponse(content=path.read_text(encoding="utf-8"))

    @app.get("/items")
    def get_items(
        channel: Optional[str] = None,
        limit: int = DEFAULT_ITEMS_LIMIT,
        since: Optional[str] = None,
        sort: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        return _filter_items(_load_items(), channel, limit, since, sort)

    @app.get("/hotlist")
    def get_hotlist() -> PlainTextResponse:  # type: ignore[name-defined]
        path = paths.data_path("hotlist.md")
        text = path.read_text(encoding="utf-8") if path.is_file() else ""
        return PlainTextResponse(content=text)

    @app.get("/status")
    def get_status() -> Dict[str, Any]:
        return storage.read_json(paths.data_path("sync_meta.json"), default={}) or {}

    @app.get("/personas")
    def get_personas() -> List[Dict[str, Any]]:
        """人物画像列表（``personas.json``，spec-v0.3 §8）。缺失时返回空列表。"""
        data = storage.read_json(paths.data_path("personas.json"), default=[])
        return data if isinstance(data, list) else []

    @app.get("/profile")
    def get_profile() -> Dict[str, Any]:
        """读者画像（reader.yaml + 派生偏好）+ 前 N 条按 personal_score 排序的 items。"""
        reader_profile = profile.load_reader_profile()
        top = _filter_items(_load_items(), None, DEFAULT_PROFILE_ITEMS, None, "personal")
        return {"profile": reader_profile, "items": top}

    @app.get("/daily")
    def get_daily(view: str = DAILY_DEFAULT_VIEW) -> HTMLResponse:  # type: ignore[name-defined]
        """当日深读/浅读日报 HTML（``view=glance|deep``，缺省 deep，spec-v0.3 §8）。

        优先读 ``archive/<今日>/{glance,deep}.html``；无当日视图则回退数据根 ``daily.html``；
        再无则 404。
        """
        key = str(view or "").casefold()
        name = DAILY_VIEWS.get(key, DAILY_VIEWS[DAILY_DEFAULT_VIEW])
        date_str = utils.now_utc().strftime("%Y-%m-%d")
        path = paths.data_path("archive", date_str, name)
        if not path.is_file():
            path = paths.data_path(DAILY_ROOT_NAME)
        if not path.is_file():
            raise HTTPException(status_code=404, detail="当日日报不存在，请先执行 daily_digest_all")
        return HTMLResponse(content=path.read_text(encoding="utf-8"))

    @app.post("/history")
    def post_history(body: HistoryBody) -> Dict[str, bool]:  # type: ignore[name-defined]
        """记录一条阅读历史（seen/open/deepread/received），追加 ``history.jsonl``。"""
        profile.log_history([{
            "sig": body.sig,
            "action": body.action,
            "title": body.title or "",
            "url": body.url or "",
        }])
        return {"logged": True}

    @app.post("/sync")
    def post_sync(background_tasks: BackgroundTasks) -> Dict[str, bool]:  # type: ignore[name-defined]
        from . import sync as sync_module  # 延迟 import，避免无谓的顶层耦合

        background_tasks.add_task(sync_module.run_sync, quick=True)
        return {"started": True}

    @app.post("/feedback")
    def post_feedback(body: FeedbackBody) -> Dict[str, bool]:  # type: ignore[name-defined]
        if body.action not in FEEDBACK_ACTIONS:
            raise HTTPException(
                status_code=400,
                detail="action 必须是 {0} 之一".format("|".join(FEEDBACK_ACTIONS)),
            )
        record = {
            "sig": body.sig,
            "action": body.action,
            "note": body.note or "",
            "ts": utils.iso(utils.now_utc()),
        }
        path = paths.data_path("feedback.jsonl")
        rows = storage.read_jsonl(path)
        rows.append(record)
        storage.write_jsonl(path, rows)
        return {"ok": True}

    return app


# =========================================================================
# CLI
# =========================================================================
def main(argv: Optional[Sequence[str]] = None) -> int:
    if not FASTAPI_AVAILABLE:
        print('缺少 fastapi/uvicorn 依赖，请先执行：pip install "qianliyan[api]"')
        return 1
    try:
        import uvicorn
    except ImportError:
        print('缺少 uvicorn 依赖，请先执行：pip install "qianliyan[api]"')
        return 1

    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m qianliyan.cli.api_server", description="千里眼 API 服务",
    )
    parser.add_argument("--host", default=None, help="缺省读 env QLY_HOST，默认 0.0.0.0")
    parser.add_argument("--port", type=int, default=None, help="缺省读 env QLY_PORT，默认 8787")
    args = parser.parse_args(argv)

    host = args.host or os.environ.get("QLY_HOST") or DEFAULT_HOST
    port = args.port or int(os.environ.get("QLY_PORT") or DEFAULT_PORT)

    app = create_app()
    logger.info("千里眼 API 监听 %s:%d", host, port)
    uvicorn.run(app, host=host, port=port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
