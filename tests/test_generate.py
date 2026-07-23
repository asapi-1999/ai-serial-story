import copy
import datetime
import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

from generate import (
    ATTEMPTS,
    JST,
    atomic_write_files,
    fetch_story,
    generate_updates,
    story_response_schema,
    validate_story_payload,
)


CONFIG = {
    "total_episodes": 5,
    "site_url": "https://example.com/story/",
}

CONTINUATION = {
    "title": "続きの題",
    "body": "続きの本文",
    "summary": "続きの要約",
    "new_characters": [],
}

FIRST_EPISODE = {
    "work_title": "新しい物語",
    "title": "始まり",
    "body": "第1話の本文",
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
            "body": "前回の本文",
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
                "body": f"本文{number}",
            })

        updates = generate_updates(
            CONFIG,
            completed_stories,
            self.bible,
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
            self.assertEqual(list(Path(tmp).glob("*.tmp")), [])

    def test_atomic_write_keeps_original_when_first_replace_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "stories.json"
            target.write_text("old", encoding="utf-8")

            with mock.patch("generate.os.replace", side_effect=OSError("failed")):
                with self.assertRaisesRegex(OSError, "failed"):
                    atomic_write_files({target: "new"})

            self.assertEqual(target.read_text(encoding="utf-8"), "old")
            self.assertEqual(list(Path(tmp).glob("*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
