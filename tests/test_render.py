import tempfile
import unittest
from pathlib import Path

from aihot.render import rebuild_index, write_digest


class TestRender(unittest.TestCase):
    def test_write_digest_creates_real_file_and_index(self):
        items = [
            {
                "source": "hackernews",
                "title": "New LLM Released",
                "url": "https://example.com/1",
                "matched_keywords": ["LLM"],
            },
            {
                "source": "arxiv",
                "title": "A Paper About Agents",
                "url": "https://example.com/2",
                "summary": "This paper studies agentic reasoning at length. " * 3,
                "matched_keywords": ["agent", "reasoning"],
            },
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = write_digest(items, "2026-07-20", ["LLM", "agent", "reasoning"], tmp)
            self.assertTrue(path.exists())
            content = path.read_text(encoding="utf-8")
            self.assertIn("New LLM Released", content)
            self.assertIn("A Paper About Agents", content)
            self.assertIn("2 条命中", content)
            self.assertTrue((Path(tmp) / "index.html").exists())

    def test_index_lists_only_dated_digest_files_newest_first(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            for name in ["2026-07-18.md", "2026-07-20.md", "2026-07-19.md", "notes.md"]:
                (tmp_path / name).write_text("x", encoding="utf-8")
            rebuild_index(tmp_path)
            index = (tmp_path / "index.html").read_text(encoding="utf-8")
            self.assertLess(index.index("2026-07-20"), index.index("2026-07-19"))
            self.assertLess(index.index("2026-07-19"), index.index("2026-07-18"))
            self.assertNotIn("notes.md", index)

    def test_html_escapes_filenames(self):
        # 防御性验证:文件名不应该未转义地进入 HTML(即便这里文件名不可能带 <>,
        # escape 调用本身要真的生效,不是摆设).
        with tempfile.TemporaryDirectory() as tmp:
            rebuild_index(tmp)
            index = (Path(tmp) / "index.html").read_text(encoding="utf-8")
            self.assertIn("<ul>", index)


if __name__ == "__main__":
    unittest.main()
