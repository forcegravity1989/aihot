"""关键词关注面打分法 —— 见 .claude/standards/skill-standards.md 蒸馏的方法论。

命中数 = 分数;0 分不上日报,没有例外。
"""


def score(item, keywords):
    haystack = (item.get("title", "") + " " + item.get("summary", "")).lower()
    hits = [kw for kw in keywords if kw.lower() in haystack]
    return len(hits), hits


def filter_and_score(items, keywords, min_score=1):
    scored = []
    for item in items:
        s, hits = score(item, keywords)
        if s >= min_score:
            scored.append({**item, "match_score": s, "matched_keywords": hits})
    scored.sort(key=lambda i: i["match_score"], reverse=True)
    return scored
