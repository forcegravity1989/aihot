"""test_sources_config.py —— config/sources.yaml 是代码（CLAUDE.md「数据与代码隔离」），按代码的标准校验。

全离线。逐条过 ``sources:`` 列表：类型必须是 engine 认得的；url 不重复；``git`` 类的 URL 必须能解析出
owner/repo（否则 fetch_source 运行时才抛 ValueError，日报那天才发现）；``youtube`` 类必须带
channel_id 或 playlist_id；每条都带 name/weight/format/category。顺带钉住 issue #59 补进来的 infra 一手口径。
"""

from __future__ import annotations

from pathlib import Path

import yaml

from qianliyan.engine import remote_sync

CONFIG = Path(__file__).resolve().parents[1] / "config" / "sources.yaml"
KNOWN_TYPES = {"rss", "scrape", "youtube", "arxiv", "git", "github-trending"}
KNOWN_FORMATS = {"blog", "news", "paper", "podcast", "repo", "talk", "video"}


def _sources():
    return yaml.safe_load(CONFIG.read_text(encoding="utf-8"))["sources"]


def test_every_source_is_well_formed_and_unique():
    sources = _sources()
    assert len(sources) >= 48
    seen_urls, seen_names = set(), set()
    for src in sources:
        label = src.get("name") or src
        assert src.get("name"), "信源缺 name：{0}".format(src)
        assert src["name"] not in seen_names, "重名信源：{0}".format(label)
        seen_names.add(src["name"])
        assert src.get("type") in KNOWN_TYPES, "{0}: 未知 type {1}".format(label, src.get("type"))
        assert isinstance(src.get("weight"), (int, float)) and 0 < src["weight"] <= 1, label
        assert src.get("format") in KNOWN_FORMATS, "{0}: format {1}".format(label, src.get("format"))
        assert src.get("category"), "{0}: 缺 category".format(label)
        assert isinstance(src.get("tags"), list) and src["tags"], "{0}: 缺 tags".format(label)
        if src["type"] == "github-trending":
            continue
        if src["type"] == "youtube":
            assert src.get("channel_id") or src.get("playlist_id"), "{0}: youtube 要 channel_id/playlist_id".format(label)
            continue
        url = src.get("url")
        assert url and url.startswith("http"), "{0}: 缺 url".format(label)
        assert url not in seen_urls, "重复 url：{0}".format(url)
        seen_urls.add(url)
        assert remote_sync.detect_backend(url, src["type"]) == src["type"]


def test_git_sources_resolve_to_owner_repo_so_releases_feed_can_be_built():
    gits = [s for s in _sources() if s["type"] == "git"]
    assert {s["name"] for s in gits} >= {
        "vLLM Releases", "SGLang Releases", "TensorRT-LLM Releases",
        "FlashInfer Releases", "FlashAttention Releases",
    }
    for src in gits:
        owner_repo = remote_sync._owner_repo_from_url(src["url"])
        assert owner_repo and owner_repo.count("/") == 1, "{0}: 解析不出 owner/repo".format(src["name"])
        assert "infra" in src["tags"] and "release" in src["tags"] and src["category"] == "infra"
        assert src["format"] == "repo"


def test_infra_first_party_coverage_is_present():
    """infra 读者要的一手口径：推理引擎、算子库、训练框架都得有官方来源，模型厂商里 Mistral 不再缺席。"""
    names = {s["name"] for s in _sources()}
    for required in ("vLLM Blog", "vLLM Releases", "SGLang Releases", "PyTorch Blog",
                     "FlashAttention Releases", "NVIDIA Developer Blog", "Mistral News"):
        assert required in names, "缺信源：{0}".format(required)
