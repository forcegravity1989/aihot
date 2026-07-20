import json
import tempfile
import unittest
from pathlib import Path

from aihot.main import ConfigError, load_config


class TestLoadConfig(unittest.TestCase):
    def test_missing_file_raises_config_error_not_raw_exception(self):
        with self.assertRaises(ConfigError):
            load_config("/tmp/definitely-does-not-exist-aihot.json")

    def test_invalid_json_raises_config_error(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            f.write("{not valid json")
            path = f.name
        try:
            with self.assertRaises(ConfigError):
                load_config(path)
        finally:
            Path(path).unlink()

    def test_missing_keywords_field_raises_config_error(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump({"min_score": 1}, f)
            path = f.name
        try:
            with self.assertRaises(ConfigError):
                load_config(path)
        finally:
            Path(path).unlink()

    def test_valid_config_loads(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump({"keywords": ["LLM"]}, f)
            path = f.name
        try:
            cfg = load_config(path)
            self.assertEqual(cfg["keywords"], ["LLM"])
        finally:
            Path(path).unlink()


if __name__ == "__main__":
    unittest.main()
