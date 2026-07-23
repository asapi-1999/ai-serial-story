import copy
import datetime
import json
import os
import tempfile
import unittest
import urllib.error
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest import mock

from generate import (
    ATTEMPTS,
    JST,
    MIN_BODY_LENGTH,
    atomic_write_files,
    build_rss,
    fetch_story,
    generate_updates,
    story_response_schema,
    validate_state,
    validate_story_payload,
)
from story_utils import validate_config


CONFIG = {
    "total_episodes": 5,
    "site_url": "https://example.com/story/",
}

VALID_BODY = "物語の本文です。" * 100

CONTINUATION = {
    "title": "続きの題",
    "body": VALID_BODY,
    "summary": "続きの要約",
    "new_characters": [],
}

FIRST_EPISODE = {
    "work_title": "新しい物語",
    "title": "始まり",
    "body": VALID_BODY,
    "summary": "第1話の要約",
    "world": "新しい世界の設定。",
    "characters": [
        {"name": "主人公", "description": "物語の主人公。"},
    ],
}


class FakeResponse:
    def __init__(self, value):
        self.value = value

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def read(self):
        return json.dumps(self.value, ensure_ascii=False).encode("utf-8")


def api_response(text, finish_reason="STOP"):
    return {
        "candidates": [{
            "finishReason": finish_reason,
            "content": {"parts": [{"text": text}]},
        }],
    }


class StructuredOutputTests(unittest.TestCase):
    def test_first_episode_schema_requires_series_metadata(self):
        schema = story_response_schema(True)
        self.assertEqual(
            schema["required"],
            ["work_title", "title", "body", "summary", "world", "characters"],
        )
        self.assertFalse(schema["additionalProperties"])

    def test_payload_validation_rejects_empty_body(self):
        story = copy.deepcopy(CONTINUATION)
        story["body"] = ""
        self.assertIn(
            "body が空でない文字列ではありません",
            validate_story_payload(story, False),
        )

    def test_payload_validation_rejects_short_body(self):
        story = copy.deepcopy(CONTINUATION)
        story["body"] = "短い本文"
        self.assertIn(
            f"body が短すぎます（4文字、最低{MIN_BODY_LENGTH}文字）",
            validate_story_payload(story, False),
        )

    def test_fetch_story_retries_invalid_json_and_safety_stop(self):
        responses = iter([
            api_response("not-json"),
            api_response(json.dumps(CONTINUATION), "SAFETY"),
            api_response(json.dumps(CONTINUATION, ensure_ascii=False)),
        ])
        waits = []

        def urlopen(request, timeout):
            self.assertEqual(timeout, 120)
            request_body = json.loads(request.data)
            generation_config = request_body["generationConfig"]
            self.assertEqual(generation_config["responseMimeType"], "application/json")
            self.assertIn("responseJsonSchema", generation_config)
            self.assertEqual(
                generation_config["thinkingConfig"],
                {"thinkingLevel": "medium"},
            )
            return FakeResponse(next(responses))

        result = fetch_story(
            "prompt",
            False,
            "test-key",
            urlopen=urlopen,
            sleep=waits.append,
        )

        self.assertEqual(result, CONTINUATION)
        self.assertEqual(waits, [5, 10])

    def test_fetch_story_retries_malformed_candidate_shape(self):
        responses = iter([
            {"candidates": {"unexpected": "object"}},
            api_response(json.dumps(CONTINUATION)),
        ])
        waits = []

        result = fetch_story(
            "prompt",
            False,
            "test-key",
            urlopen=lambda request, timeout: FakeResponse(next(responses)),
            sleep=waits.append,
        )

        self.assertEqual(result, CONTINUATION)
        self.assertEqual(waits, [5])

    def test_fetch_story_falls_back_to_2_5_flash_on_rate_limit(self):
        calls = []

        def urlopen(request, timeout):
            calls.append((request.full_url, json.loads(request.data)))
            if "gemini-3.5-flash" in request.full_url:
                raise urllib.error.HTTPError(
                    request.full_url,
                    429,
                    "Too Many Requests",
                    None,
                    None,
                )
            return FakeResponse(api_response(json.dumps(CONTINUATION)))

        result = fetch_story(
            "prompt",
            False,
            "test-key",
            urlopen=urlopen,
            sleep=lambda seconds: None,
        )

        self.assertEqual(result, CONTINUATION)
        self.assertEqual(len(calls), 2)
        self.assertIn("gemini-3.5-flash", calls[0][0])
        self.assertEqual(
            calls[0][1]["generationConfig"]["thinkingConfig"],
            {"thinkingLevel": "medium"},
        )
        self.assertIn("gemini-2.5-flash", calls[1][0])
        self.assertEqual(
            calls[1][1]["generationConfig"]["thinkingConfig"],
            {"thinkingBudget": 2048},
        )

    def test_fetch_story_does_not_fall_back_for_invalid_content(self):
        urls = []

        def urlopen(request, timeout):
            urls.append(request.full_url)
            return FakeResponse(api_response("not-json"))

        with self.assertRaisesRegex(SystemExit, f"{ATTEMPTS}回失敗"):
            fetch_story(
                "prompt",
                False,
                "test-key",
                urlopen=urlopen,
                sleep=lambda seconds: None,
            )

        self.assertEqual(len(urls), ATTEMPTS)
        self.assertTrue(all("gemini-3.5-flash" in url for url in urls))

    def test_fetch_story_fails_after_all_invalid_responses(self):
        def urlopen(request, timeout):
            return FakeResponse(api_response("not-json"))

        with self.assertRaisesRegex(SystemExit, f"{ATTEMPTS}回失敗"):
            fetch_story(
                "prompt",
                False,
                "test-key",
                urlopen=urlopen,
                sleep=lambda seconds: None,
            )


class GenerationTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime.datetime(2026, 7, 23, 9, 0, tzinfo=JST)
        self.stories = [{
            "episode": 1,
            "title": "前回",
            "date": "2026年07月13日",
            "iso": "2026-07-13",
            "body": VALID_BODY,
        }]
        self.bible = {
            "work_title": "連載作品",
            "world": "世界設定",
            "characters": [{"name": "主人公", "desc": "主人公。"}],
            "synopsis": ["第1話「前回」：前回の要約"],
        }

    def test_continuation_updates_copies_without_mutating_inputs(self):
        original_stories = copy.deepcopy(self.stories)
        original_bible = copy.deepcopy(self.bible)

        updates = generate_updates(
            CONFIG,
            self.stories,
            self.bible,
            [],
            lambda prompt, first: copy.deepcopy(CONTINUATION),
            self.now,
        )

        self.assertEqual(self.stories, original_stories)
        self.assertEqual(self.bible, original_bible)
        self.assertEqual(len(updates["stories"]), 2)
        self.assertEqual(updates["stories"][-1]["title"], "続きの題")
        self.assertIn("w0-ep-2", updates["rss"])

    def test_new_work_archives_completed_work(self):
        completed_stories = []
        for number in range(1, 6):
            completed_stories.append({
                "episode": number,
                "title": f"第{number}章",
                "date": f"2026年07月{number:02d}日",
                "iso": f"2026-07-{number:02d}",
                "body": VALID_BODY + str(number),
            })

        completed_bible = copy.deepcopy(self.bible)
        completed_bible["synopsis"] = [f"第{number}話の要約" for number in range(1, 6)]

        updates = generate_updates(
            CONFIG,
            completed_stories,
            completed_bible,
            [],
            lambda prompt, first: copy.deepcopy(FIRST_EPISODE),
            self.now,
        )

        self.assertEqual(len(updates["library"]), 1)
        self.assertEqual(updates["library"][0]["completed"], "2026-07-05")
        self.assertEqual(updates["library"][0]["total_episodes"], 5)
        self.assertEqual(updates["stories"][0]["episode"], 1)
        self.assertIn("w1-ep-1", updates["rss"])

    def test_generation_failure_does_not_mutate_inputs(self):
        original_stories = copy.deepcopy(self.stories)

        def fail(prompt, first):
            raise SystemExit("generation failed")

        with self.assertRaisesRegex(SystemExit, "generation failed"):
            generate_updates(
                CONFIG,
                self.stories,
                self.bible,
                [],
                fail,
                self.now,
            )
        self.assertEqual(self.stories, original_stories)

    def test_generation_rejects_current_story_over_configured_limit(self):
        stories = []
        for number in range(1, 7):
            stories.append({
                "episode": number,
                "title": f"第{number}章",
                "date": f"2026年07月{number:02d}日",
                "iso": f"2026-07-{number:02d}",
                "body": VALID_BODY,
            })
        bible = copy.deepcopy(self.bible)
        bible["synopsis"] = [f"第{number}話の要約" for number in range(1, 7)]

        with self.assertRaisesRegex(SystemExit, "設定上限"):
            generate_updates(
                CONFIG,
                stories,
                bible,
                [],
                lambda prompt, first: copy.deepcopy(FIRST_EPISODE),
                self.now,
            )


class StateValidationTests(unittest.TestCase):
    def setUp(self):
        self.stories = [{
            "episode": 1,
            "title": "始まり",
            "date": "2026年07月23日",
            "iso": "2026-07-23",
            "body": VALID_BODY,
        }]
        self.bible = {
            "work_title": "作品",
            "world": "世界設定",
            "characters": [{"name": "主人公", "desc": "主人公の説明"}],
            "synopsis": ["第1話の要約"],
        }

    def test_validate_state_accepts_complete_state(self):
        validate_state(self.stories, self.bible, [])

    def test_validate_state_rejects_invalid_iso_date(self):
        self.stories[0]["iso"] = "2026-02-30"
        with self.assertRaisesRegex(SystemExit, "実在する日付"):
            validate_state(self.stories, self.bible, [])

    def test_validate_state_rejects_display_date_mismatch(self):
        self.stories[0]["date"] = "2026年07月22日"
        with self.assertRaisesRegex(SystemExit, "iso と同じ日"):
            validate_state(self.stories, self.bible, [])

    def test_validate_state_rejects_synopsis_count_mismatch(self):
        self.bible["synopsis"] = []
        with self.assertRaisesRegex(SystemExit, "話数と一致"):
            validate_state(self.stories, self.bible, [])

    def test_validate_state_rejects_short_stored_body(self):
        self.stories[0]["body"] = "短い本文"
        with self.assertRaisesRegex(SystemExit, f"{MIN_BODY_LENGTH}文字以上"):
            validate_state(self.stories, self.bible, [])

    def test_validate_state_rejects_archive_episode_count_mismatch(self):
        library = [{
            "work_title": "完結作品",
            "completed": "2026-07-23",
            "total_episodes": 2,
            "episodes": copy.deepcopy(self.stories),
        }]
        with self.assertRaisesRegex(SystemExit, "episodes の件数と一致"):
            validate_state(self.stories, self.bible, library)


class RssTests(unittest.TestCase):
    def test_build_rss_escapes_site_url(self):
        stories = [{
            "episode": 1,
            "title": "始まり",
            "date": "2026年07月23日",
            "iso": "2026-07-23",
            "body": VALID_BODY,
        }]
        bible = {"work_title": "作品"}

        rss = build_rss(
            "https://example.com/a&b/",
            5,
            stories,
            bible,
            [],
        )

        self.assertIn("https://example.com/a&amp;b/", rss)
        ET.fromstring(rss)


class RepositoryIntegrityTests(unittest.TestCase):
    def test_repository_data_and_rss_are_consistent(self):
        root = Path(__file__).resolve().parents[1]

        def read_json(filename):
            return json.loads((root / filename).read_text(encoding="utf-8"))

        config = read_json("config.json")
        stories = read_json("stories.json")
        bible = read_json("bible.json")
        library = read_json("library.json")
        total_episodes, site_url = validate_config(config)
        validate_state(stories, bible, library)
        expected_rss = build_rss(
            site_url,
            total_episodes,
            stories,
            bible,
            library,
        )
        actual_rss = (root / "rss.xml").read_text(encoding="utf-8")

        self.assertEqual(actual_rss, expected_rss)
        ET.fromstring(actual_rss)


class AtomicWriteTests(unittest.TestCase):
    def test_atomic_write_replaces_all_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            first = Path(tmp) / "first.json"
            second = Path(tmp) / "second.xml"
            first.write_text("old", encoding="utf-8")
            second.write_text("old", encoding="utf-8")

            atomic_write_files({
                first: "new-json",
                second: "new-xml",
            })

            self.assertEqual(first.read_text(encoding="utf-8"), "new-json")
            self.assertEqual(second.read_text(encoding="utf-8"), "new-xml")
            self.assertFalse(any(path.suffix in {".tmp", ".bak"} for path in Path(tmp).iterdir()))

    def test_atomic_write_keeps_original_when_first_replace_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "stories.json"
            target.write_text("old", encoding="utf-8")
            real_replace = os.replace

            def fail_new_file_replace(source, destination):
                if Path(source).suffix == ".tmp":
                    raise OSError("failed")
                return real_replace(source, destination)

            with mock.patch("generate.os.replace", side_effect=fail_new_file_replace):
                with self.assertRaisesRegex(OSError, "failed"):
                    atomic_write_files({target: "new"})

            self.assertEqual(target.read_text(encoding="utf-8"), "old")
            self.assertFalse(any(path.suffix in {".tmp", ".bak"} for path in Path(tmp).iterdir()))

    def test_atomic_write_rolls_back_when_second_replace_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            first = Path(tmp) / "first.json"
            second = Path(tmp) / "second.json"
            first.write_text("old-first", encoding="utf-8")
            second.write_text("old-second", encoding="utf-8")
            real_replace = os.replace
            replacement_count = 0

            def fail_second_replacement(source, target):
                nonlocal replacement_count
                if Path(source).suffix == ".tmp":
                    replacement_count += 1
                    if replacement_count == 2:
                        raise OSError("second replace failed")
                return real_replace(source, target)

            with mock.patch("generate.os.replace", side_effect=fail_second_replacement):
                with self.assertRaisesRegex(OSError, "second replace failed"):
                    atomic_write_files({first: "new-first", second: "new-second"})

            self.assertEqual(first.read_text(encoding="utf-8"), "old-first")
            self.assertEqual(second.read_text(encoding="utf-8"), "old-second")
            self.assertFalse(any(path.suffix in {".tmp", ".bak"} for path in Path(tmp).iterdir()))

    def test_atomic_write_preserves_backups_when_rollback_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            first = Path(tmp) / "first.json"
            second = Path(tmp) / "second.json"
            first.write_text("old-first", encoding="utf-8")
            second.write_text("old-second", encoding="utf-8")
            real_replace = os.replace
            replacement_count = 0

            def fail_replacement_and_rollback(source, target):
                nonlocal replacement_count
                suffix = Path(source).suffix
                if suffix == ".tmp":
                    replacement_count += 1
                    if replacement_count == 2:
                        raise OSError("replace failed")
                elif suffix == ".bak":
                    raise OSError("rollback failed")
                return real_replace(source, target)

            with mock.patch("generate.os.replace", side_effect=fail_replacement_and_rollback):
                with self.assertRaisesRegex(RuntimeError, "復旧用"):
                    atomic_write_files({first: "new-first", second: "new-second"})

            self.assertTrue(any(path.suffix == ".bak" for path in Path(tmp).iterdir()))


if __name__ == "__main__":
    unittest.main()
