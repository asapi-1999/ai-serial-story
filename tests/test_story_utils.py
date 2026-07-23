import json
import tempfile
import unittest
from pathlib import Path

from story_utils import load_json, missing_story_sections, section, validate_config


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

    def test_section_keeps_unknown_brackets_inside_body(self):
        text = (
            "【タイトル】検証\n"
            "【本文】\n冒頭\n【警告】これは本文内の表現\n末尾\n"
            "【今回のあらすじ】要約\n"
            "【新登場人物】なし"
        )
        self.assertEqual(section("本文", text), "冒頭\n【警告】これは本文内の表現\n末尾")

    def test_missing_sections_checks_episode_specific_format(self):
        continuation = (
            "【タイトル】検証\n【本文】本文\n"
            "【今回のあらすじ】要約\n【新登場人物】なし"
        )
        self.assertEqual(missing_story_sections(continuation, False), [])
        self.assertEqual(
            missing_story_sections(continuation, True),
            ["【作品タイトル】", "【世界観】", "【登場人物】"],
        )

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
