"""渲染:打分排序后的条目 → Markdown 日报 + 静态 index.html 汇总页。"""

import html
import re
from pathlib import Path


def render_markdown(items, date_str, keywords):
    lines = [f"# AI 热点日报 · {date_str}", ""]
    lines.append(f"关注面关键词:{', '.join(keywords)}")
    lines.append("")
    lines.append(f"共 {len(items)} 条命中(按命中度排序)")
    lines.append("")
    by_source = {}
    for it in items:
        by_source.setdefault(it["source"], []).append(it)
    source_labels = {"hackernews": "Hacker News · 社区热度", "arxiv": "arXiv · 学术前沿"}
    for src, src_items in by_source.items():
        lines.append(f"## {source_labels.get(src, src)}")
        lines.append("")
        for it in src_items:
            kw = "、".join(it.get("matched_keywords", []))
            lines.append(f"- **[{it['title']}]({it['url']})** _(命中: {kw})_")
            if it.get("summary"):
                snippet = it["summary"][:160]
                lines.append(f"  > {snippet}{'…' if len(it['summary']) > 160 else ''}")
        lines.append("")
    return "\n".join(lines)


def write_digest(items, date_str, keywords, digests_dir):
    digests_dir = Path(digests_dir)
    digests_dir.mkdir(parents=True, exist_ok=True)
    md = render_markdown(items, date_str, keywords)
    out_path = digests_dir / f"{date_str}.md"
    out_path.write_text(md, encoding="utf-8")
    rebuild_index(digests_dir)
    return out_path


_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}\.md$")


def rebuild_index(digests_dir):
    """静态 index.html:列出 digests_dir 下所有真实存在的日报文件,新到旧。
    从磁盘真实扫描,不是从内存里的一次运行结果拼——重跑/补跑历史日期都对。
    """
    digests_dir = Path(digests_dir)
    files = sorted((f.name for f in digests_dir.glob("*.md") if _DATE_RE.match(f.name)), reverse=True)
    items_html = "\n".join(
        f'    <li><a href="{html.escape(f)}">{html.escape(f.removesuffix(".md"))}</a></li>' for f in files
    )
    page = f"""<!doctype html>
<html lang="zh">
<head>
<meta charset="utf-8">
<title>aihot 日报</title>
<style>
  body {{ font-family: -apple-system, sans-serif; max-width: 640px; margin: 40px auto; padding: 0 16px; }}
  h1 {{ font-size: 1.4rem; }}
  li {{ margin: 6px 0; }}
</style>
</head>
<body>
<h1>aihot 日报</h1>
<p>共 {len(files)} 期真实生成的日报(本页由 render.py 每次生成日报后自动重建):</p>
<ul>
{items_html}
</ul>
</body>
</html>
"""
    (digests_dir / "index.html").write_text(page, encoding="utf-8")
