"""cli/deliver.py —— 四通道分发（spec §10.2）。

```
python -m qianliyan.cli.deliver --channel in-chat|html|welink|email [--topic <频道名>] [--open]
```

四个通道职责各异（spec §10.2 + intent.md 非目标）：

* ``in-chat``：读 ``channels/<topic>.md``（缺省 ``hotlist.md``）打印到 stdout，头部附
  intent.md「设计哲学四条铁律」提醒，供 Agent 转述时遵循。
* ``html``：确认 ``digest.html`` 存在并打印路径；``--open`` 时用 ``webbrowser.open()``。
* ``welink``：**不直连 API**——打印结构化 instruction 块（目标群 / 当日精选条目 / 每条 URL），
  供 Agent 接力 welink-controller skill。
* ``email``：stub——打印"请使用 send-email skill"与建议正文，退出码 0。
"""

from __future__ import annotations

import argparse
import logging
import webbrowser
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from ..core import paths, storage

logger = logging.getLogger("qianliyan.cli.deliver")

#: intent.md「设计哲学四条铁律」——Agent 转述必须遵循
IRON_LAWS = (
    "1. 以本地数据为准，不凭记忆——一切转述都必须来自落盘的数据底座。",
    "2. 每条必带 URL——可溯源是底线。",
    "3. 简体中文 + 人话——面向中文读者的最终呈现。",
    "4. 交叉验证是核心价值——多源报道自动加权（cross_refs → hotness 加成 → 重磅标记）。",
)

DEFAULT_TOPIC = "hotlist"
HOTLIST_LIMIT = 50


def iron_laws_block(title: str = "转述四条铁律（Agent 必须遵循）") -> str:
    lines = ["=== {0} ===".format(title)]
    lines.extend(IRON_LAWS)
    lines.append("=" * (len(title) + 8))
    return "\n".join(lines)


def _channel_path(topic: Optional[str]) -> Path:
    if topic:
        return paths.data_path("channels", "{0}.md".format(topic))
    return paths.data_path("hotlist.md")


def _load_topic_items(topic: Optional[str]) -> List[Dict[str, Any]]:
    """从数据契约（items.jsonl + channels.json）取某频道（或全局热榜）的条目列表。"""
    items = storage.read_jsonl(paths.data_path("items.jsonl"))
    if not topic:
        ranked = sorted(items, key=lambda it: it.get("hotness") or 0.0, reverse=True)
        return ranked[:HOTLIST_LIMIT]

    index = storage.read_json(paths.data_path("channels.json"), default={}) or {}
    sigs = index.get(topic)
    if sigs is None:
        return []
    by_sig = {it.get("sig"): it for it in items if isinstance(it, dict)}
    return [by_sig[sig] for sig in sigs if sig in by_sig]


def _title_of(item: Dict[str, Any]) -> str:
    extra = item.get("extra") if isinstance(item.get("extra"), dict) else {}
    zh = extra.get("title_zh")
    if zh and str(zh).strip():
        return str(zh).strip()
    return str(item.get("title") or "")


# =========================================================================
# 四通道
# =========================================================================
def _deliver_in_chat(topic: Optional[str]) -> int:
    path = _channel_path(topic)
    if not path.is_file():
        print("未找到频道数据文件: {0}".format(path))
        print("请先执行一次 `python -m qianliyan.cli.sync`。")
        return 1
    print(iron_laws_block())
    print()
    print(path.read_text(encoding="utf-8"))
    return 0


def _deliver_html(open_browser: bool) -> int:
    path = paths.data_path("digest.html")
    if not path.is_file():
        print("未找到 digest.html: {0}".format(path))
        print("请先执行一次 `python -m qianliyan.cli.sync`。")
        return 1
    print("digest.html 路径: {0}".format(path))
    if open_browser:
        try:
            webbrowser.open(path.resolve().as_uri())
        except Exception as exc:  # noqa: BLE001 - 打开失败不阻断，路径已打印
            logger.warning("webbrowser.open 失败: %s", exc)
            print("自动打开浏览器失败，请手动打开上面的路径: {0}".format(exc))
    return 0


def _deliver_welink(topic: Optional[str]) -> int:
    items = _load_topic_items(topic)
    print(iron_laws_block("WeLink 推送 instruction（供 Agent 接力 welink-controller skill）"))
    print()
    print("目标群: <请在此填写目标 WeLink 群名称/ID>")
    print("来源频道: {0}".format(topic or DEFAULT_TOPIC))
    print("精选条目（{0} 条）:".format(len(items)))
    if not items:
        print("  （暂无条目，请先执行一次 sync）")
    for idx, item in enumerate(items, start=1):
        print("  {0}. {1}".format(idx, _title_of(item)))
        print("     {0}".format(item.get("url") or ""))
    print()
    print("请 Agent 基于以上条目组织中文推送文案后，调用 welink-controller skill 完成实际发送。")
    return 0


def _deliver_email(topic: Optional[str]) -> int:
    items = _load_topic_items(topic)
    print("email 通道为 stub：请使用 send-email skill 完成实际发送。")
    print()
    print("建议主题: 千里眼 AI 情报简报 · {0}".format(topic or DEFAULT_TOPIC))
    print("建议正文:")
    print(iron_laws_block())
    for idx, item in enumerate(items, start=1):
        print("  {0}. {1} — {2}".format(idx, _title_of(item), item.get("url") or ""))
    return 0


# =========================================================================
# CLI
# =========================================================================
def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m qianliyan.cli.deliver", description="千里眼四通道分发",
    )
    parser.add_argument(
        "--channel", required=True, choices=["in-chat", "html", "welink", "email"],
        help="分发通道",
    )
    parser.add_argument("--topic", default=None, help="频道名（缺省用全局热榜 hotlist）")
    parser.add_argument("--open", action="store_true", help="html 通道：调用 webbrowser 打开")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = _build_arg_parser()
    args = parser.parse_args(argv)

    if args.channel == "in-chat":
        return _deliver_in_chat(args.topic)
    if args.channel == "html":
        return _deliver_html(args.open)
    if args.channel == "welink":
        return _deliver_welink(args.topic)
    if args.channel == "email":
        return _deliver_email(args.topic)
    print("未知通道: {0}".format(args.channel))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
