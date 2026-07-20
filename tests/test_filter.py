import unittest

from aihot.filter import filter_and_score, score


class TestScore(unittest.TestCase):
    def test_case_insensitive_match(self):
        s, hits = score({"title": "New llm released"}, ["LLM"])
        self.assertEqual(s, 1)
        self.assertEqual(hits, ["LLM"])

    def test_no_match_is_zero(self):
        s, hits = score({"title": "Local weather forecast"}, ["LLM", "agent"])
        self.assertEqual(s, 0)
        self.assertEqual(hits, [])

    def test_searches_summary_too(self):
        s, _ = score({"title": "Untitled", "summary": "about reasoning models"}, ["reasoning"])
        self.assertEqual(s, 1)


class TestFilterAndScore(unittest.TestCase):
    def test_zero_score_excluded_no_exceptions(self):
        items = [{"title": "About GPT"}, {"title": "About gardening"}]
        out = filter_and_score(items, ["GPT"], min_score=1)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["title"], "About GPT")

    def test_sorted_by_score_descending(self):
        items = [
            {"title": "GPT"},
            {"title": "GPT agent reasoning"},
            {"title": "GPT agent"},
        ]
        out = filter_and_score(items, ["GPT", "agent", "reasoning"], min_score=1)
        scores = [i["match_score"] for i in out]
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_empty_keywords_excludes_everything(self):
        items = [{"title": "Anything at all"}]
        out = filter_and_score(items, [], min_score=1)
        self.assertEqual(out, [])


if __name__ == "__main__":
    unittest.main()
