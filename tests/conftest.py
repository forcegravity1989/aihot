"""tests/conftest.py —— 全量测试共用 fixtures。

铁律：**所有测试必须离线**。两道保险——
  1. ``tmp_data_dir`` 把 ``QLY_OFFLINE=1`` 一并 monkeypatch 上（业务层的离线开关）；
  2. autouse 的 ``_no_real_network`` 直接掐断 socket 连接（物理层兜底）。
确有必要联网的用例请显式标 ``@pytest.mark.allow_network``——正常情况下不该存在。
"""

from __future__ import annotations

import socket
from datetime import timedelta
from pathlib import Path

import pytest

from qianliyan.core import schema, utils

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "allow_network: 允许该用例建立真实 socket 连接（默认全部禁止）"
    )


@pytest.fixture(autouse=True)
def _no_real_network(request, monkeypatch):
    """物理层封网：除 AF_UNIX 外，任何真实 socket 连接一律报错。"""
    if request.node.get_closest_marker("allow_network"):
        return

    af_unix = getattr(socket, "AF_UNIX", None)

    def _guard(name):
        real = getattr(socket.socket, name)

        def _blocked(self, address, *args, **kwargs):
            if af_unix is not None and getattr(self, "family", None) == af_unix:
                return real(self, address, *args, **kwargs)
            raise RuntimeError("测试禁止真实网络连接: {0!r}".format(address))

        return _blocked

    for name in ("connect", "connect_ex"):
        monkeypatch.setattr(socket.socket, name, _guard(name))


@pytest.fixture
def fixtures_dir() -> Path:
    """``tests/fixtures/`` 目录（各眼原始 payload 样例与 mock item 池）。"""
    return FIXTURES_DIR


@pytest.fixture
def tmp_data_dir(tmp_path, monkeypatch) -> Path:
    """把数据根目录指向 tmp，并强制离线、关闭 data 目录 git 快照。"""
    data_dir = tmp_path / "qly-data"
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("QLY_DATA_DIR", str(data_dir))
    monkeypatch.setenv("QLY_OFFLINE", "1")
    monkeypatch.setenv("QLY_GIT_SNAPSHOT", "0")
    monkeypatch.delenv("QLY_AUTH_DIR", raising=False)
    monkeypatch.delenv("QLY_BROWSER_PROFILE", raising=False)
    monkeypatch.delenv("QLY_POOL_MAX_AGE_DAYS", raising=False)
    return data_dir


@pytest.fixture
def sample_items():
    """≥8 条覆盖面完整的 mock item：多源同题、release、中英文、无日期、陈旧条目。

    其中「Anthropic ships Claude Opus 5」由 4 个信源报道（同一 sig，标点/大小写各异），
    合并后 ``cross_refs == 3`` 且最高 weight 0.98 又在 24h 内 —— 同时触发 heavy 与 flash。
    """
    now = utils.now_utc()

    def at(**kwargs):
        return utils.iso(now - timedelta(**kwargs))

    return [
        # --- 同题多源（4 源）：触发 heavy + flash ---
        schema.make_item(
            title="Anthropic ships Claude Opus 5",
            url="https://www.anthropic.com/news/claude-opus-5",
            source="Anthropic News",
            source_kind="local",
            backend="html",
            weight=0.98,
            date=at(hours=2),
            summary="Anthropic released Claude Opus 5 today.",
            tags=["official", "anthropic", "models"],
        ),
        schema.make_item(
            title="Anthropic Ships Claude Opus 5!",
            url="https://simonwillison.net/2026/opus-5",
            source="Simon Willison",
            source_kind="local",
            backend="rss",
            weight=0.80,
            date=at(hours=3),
            summary=(
                "A long hands-on write-up of Claude Opus 5 covering the new context window, "
                "pricing, and how it compares with the previous generation of frontier models."
            ),
            tags=["kol", "dev", "models"],
        ),
        schema.make_item(
            title="ANTHROPIC SHIPS CLAUDE OPUS 5",
            url="https://www.latent.space/p/opus-5",
            source="Latent Space",
            source_kind="local",
            backend="rss",
            weight=0.75,
            date=at(hours=5),
            summary="Podcast notes.",
            tags=["kol", "podcast"],
        ),
        schema.make_item(
            title="anthropic  ships,  claude opus 5",
            url="https://huggingface.co/blog/opus-5",
            source="HuggingFace Blog",
            source_kind="local",
            backend="rss",
            weight=0.85,
            date=at(hours=8),
            summary="Community reaction roundup.",
            tags=["vendor", "huggingface", "models"],
        ),
        # --- release 特例：同版本号、不同信源 → 两条独立签名 ---
        schema.make_item(
            title="v2.1.0",
            url="https://github.com/anthropics/claude-code/releases/tag/v2.1.0",
            source="Claude Code Releases",
            source_kind="local",
            backend="git",
            weight=0.97,
            date=at(days=1),
            summary="Claude Code 2.1.0 release notes.",
            tags=["official", "claude-code", "release"],
            metrics={"version": "v2.1.0"},
        ),
        schema.make_item(
            title="v2.1.0",
            url="https://github.com/mirror/claude-code/releases/tag/v2.1.0",
            source="Claude Code Mirror",
            source_kind="local",
            backend="git",
            weight=0.60,
            date=at(days=1, hours=2),
            summary="Mirror repo tag.",
            tags=["claude-code", "release"],
            metrics={"version": "v2.1.0"},
        ),
        # --- 中文条目（内眼） ---
        schema.make_item(
            title="心声社区：公司大模型平台升级公告",
            url="https://xinsheng.internal/thread/10086",
            source="心声社区",
            source_kind="company",
            backend="cdp",
            weight=0.85,
            date=at(hours=20),
            summary="平台完成一次算力扩容与推理框架升级。",
            tags=["company", "internal"],
        ),
        # --- 无日期条目（洞眼） ---
        schema.make_item(
            title="Mystery plugin ranking without a date",
            url="https://example.com/insights/plugins",
            source="claude-code-insights",
            source_kind="insights",
            backend="raw_json",
            weight=0.60,
            date="",
            summary="",
            tags=["plugins", "insights"],
            metrics={"rank": 1},
        ),
        # --- 陈旧条目（左眼） ---
        schema.make_item(
            title="An old AI hot take from a month ago",
            url="https://aihot.virxact.com/news/old",
            source="AIHot",
            source_kind="aihot",
            backend="rest",
            weight=0.90,
            date=at(days=30),
            summary="Stale but high weight.",
            tags=["llm"],
            extra={"category": "llm"},
        ),
        # --- 右眼 X 动态 ---
        schema.make_item(
            title="karpathy: the best agent harness is the one you can debug",
            url="https://x.com/karpathy/status/1",
            source="@karpathy",
            source_kind="builders",
            backend="raw_json",
            weight=0.80,
            date=at(hours=30),
            summary="the best agent harness is the one you can debug",
            tags=["x", "builders"],
            extra={"platform": "x"},
        ),
    ]
