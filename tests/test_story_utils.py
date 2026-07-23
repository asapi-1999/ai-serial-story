import json
import tempfile
import unittest
from pathlib import Path

from story_utils import load_json, validate_config


class StoryUtilsTests(unittest.TestCase):
    def test_load_json_rejects_missing_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(SystemExit, "必須ファイル"):
                load_json(Path(tmp) / "missing.json")

    def test_load_json_rejects_invalid_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "invalid.json"
            path.write_text("{", encoding="utf-8")
            with self.assertRaisesRegex(SystemExit, "JSON形式"):
                load_json(path)

    def test_load_json_reads_valid_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "valid.json"
            path.write_text(json.dumps({"ok": True}), encoding="utf-8")
            self.assertEqual(load_json(path), {"ok": True})

    def test_validate_config_normalizes_site_url(self):
        total, site_url = validate_config({
            "total_episodes": 5,
            "site_url": "https://example.com/story",
        })
        self.assertEqual(total, 5)
        self.assertEqual(site_url, "https://example.com/story/")

    def test_validate_config_rejects_boolean_episode_count(self):
        with self.assertRaisesRegex(SystemExit, "total_episodes"):
            validate_config({"total_episodes": True, "site_url": "https://example.com"})


if __name__ == "__main__":
    unittest.main()
