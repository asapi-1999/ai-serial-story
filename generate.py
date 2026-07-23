import copy
import datetime
import html
import http.client
import json
import os
import re
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

from story_utils import load_json, validate_config


JST = datetime.timezone(datetime.timedelta(hours=9))
ATTEMPTS = 4
MODEL = "gemini-2.5-flash"

RUBY_GUIDE = (
    "・難読な漢字・固有名詞・印象づけたい語にはルビ（ふりがな）を振ってよい。\n"
    "  書式は青空文庫式で、読みは全角の《 》で囲む。読みはひらがなかカタカナで書くこと。\n"
    "  ・漢字のすぐ後ろに《よみ》を置くと、その漢字列にルビが付く。例：葦原《あしはら》、案山子《かかし》\n"
    "  ・ひらがな・カタカナ・英字に振る場合や、熟語の一部だけに振る場合は、\n"
    "    全角の縦棒「｜」で親文字の開始位置を示す。例：｜物語《ものがたり》、思い出《おもいで》は「｜思い出《おもいで》」\n"
    "  ・ルビは多用せず、効果的な箇所に絞ること。\n"
)


def fresh_bible():
    return {"work_title": "", "world": "", "characters": [], "synopsis": []}


def validate_state(stories, bible, library):
    if not isinstance(stories, list):
        raise SystemExit("stories.json のルートは配列である必要があります。")
    if not isinstance(bible, dict):
        raise SystemExit("bible.json のルートはオブジェクトである必要があります。")
    if not isinstance(library, list):
        raise SystemExit("library.json のルートは配列である必要があります。")


def person_schema():
    return {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "人物名",
            },
            "description": {
                "type": "string",
                "description": "人物の役割・特徴を簡潔に説明した文章",
            },
        },
        "required": ["name", "description"],
        "additionalProperties": False,
    }


def story_response_schema(first_episode):
    common = {
        "title": {
            "type": "string",
            "description": "今回の話のタイトル。話数は含めない",
        },
        "body": {
            "type": "string",
            "description": "日本語で1000字程度の小説本文",
        },
        "summary": {
            "type": "string",
            "description": "今回の話の内容を1文で要約した文章",
        },
    }
    if first_episode:
        properties = {
            "work_title": {
                "type": "string",
                "description": "連載全体の作品タイトル。話数は含めない",
            },
            **common,
            "world": {
                "type": "string",
                "description": "物語の舞台と設定を2〜3文で要約した文章",
            },
            "characters": {
                "type": "array",
                "description": "第1話に登場する主要人物",
                "items": person_schema(),
                "minItems": 1,
            },
        }
        required = ["work_title", "title", "body", "summary", "world", "characters"]
    else:
        properties = {
            **common,
            "new_characters": {
                "type": "array",
                "description": "今回新たに登場した人物。いなければ空配列",
                "items": person_schema(),
            },
        }
        required = ["title", "body", "summary", "new_characters"]

    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
        "propertyOrdering": required,
    }


def validate_story_payload(story, first_episode):
    if not isinstance(story, dict):
        return ["応答のルートがオブジェクトではありません"]

    required_strings = ["title", "body", "summary"]
    people_key = "characters" if first_episode else "new_characters"
    if first_episode:
        required_strings.extend(["work_title", "world"])

    errors = []
    for key in required_strings:
        value = story.get(key)
        if not isinstance(value, str) or not value.strip():
            errors.append(f"{key} が空でない文字列ではありません")

    people = story.get(people_key)
    if not isinstance(people, list):
        errors.append(f"{people_key} が配列ではありません")
    else:
        if first_episode and not people:
            errors.append("characters が空です")
        for index, person in enumerate(people):
            if not isinstance(person, dict):
                errors.append(f"{people_key}[{index}] がオブジェクトではありません")
                continue
            for key in ("name", "description"):
                value = person.get(key)
                if not isinstance(value, str) or not value.strip():
                    errors.append(f"{people_key}[{index}].{key} が空でない文字列ではありません")
    return errors


def build_prompt(episode_number, total_episodes, today, bible, stories):
    if episode_number == 1:
        return (
            "あなたはプロの小説家です。\n"
            f"これから全{total_episodes}話で完結する連載小説を新しく始めます。\n"
            f"以下の条件で第1話（全{total_episodes}話）を書いてください：\n"
            "・ジャンルは自由（ファンタジー／SF／ミステリー／恋愛／ホラー／歴史／日常など何でも可）。"
            "今回の作品のジャンルと作風を自由に選ぶこと\n"
            "・独自の世界観とメインキャラクターを設定すること\n"
            f"・全{total_episodes}話で物語が完結する構成を念頭に、第1話では世界観と"
            "物語の核となる謎・目的を提示すること\n"
            "・続きが気になる終わり方にすること\n"
            "・日本語で1000字程度\n"
            "・作品タイトルと今回のタイトルのどちらにも話数を含めないこと\n"
            + RUBY_GUIDE
            + f"・更新日：{today}\n"
            "・指定されたJSON Schemaの各項目を、物語の内容に合わせて埋めること"
        )

    chars = "\n".join(
        f"・{person['name']}：{person['desc']}"
        for person in bible.get("characters", [])
    ) or "（未設定）"
    synopsis = "\n".join(bible.get("synopsis", [])) or "（なし）"
    recent = "".join(
        f"第{story['episode']}話「{story['title']}」\n{story['body']}\n\n"
        for story in stories[-2:]
    )
    is_final = episode_number == total_episodes
    if is_final:
        ending_rule = (
            f"・これは最終話（第{total_episodes}話）です。これまでに張られた伏線や謎を回収し、"
            "物語をきれいに完結させること\n"
        )
    else:
        ending_rule = (
            f"・全{total_episodes}話構成のうちの第{episode_number}話として、"
            f"最終話（第{total_episodes}話）での完結に向けて物語を前進させること\n"
            "・続きが気になる終わり方にすること\n"
        )

    return (
        f"あなたはプロの小説家です。全{total_episodes}話で完結する連載小説の続きを書きます。\n\n"
        f"# 作品タイトル\n{bible.get('work_title') or '（未設定）'}\n\n"
        "# これまでの設定\n"
        f"## 世界観\n{bible.get('world') or '（未設定）'}\n\n"
        f"## 登場人物\n{chars}\n\n"
        f"## あらすじ（各話1行）\n{synopsis}\n\n"
        f"# 直近のエピソード（全文）\n{recent}"
        "---\n"
        f"上記の続きとなる第{episode_number}話（全{total_episodes}話）を書いてください。\n"
        "以下の条件を守ってください：\n"
        "・これまでのジャンル・作風・世界観・登場人物を引き継ぐこと\n"
        "・直近のエピソードの終わりから自然につながること\n"
        + ending_rule
        + "・日本語で1000字程度\n"
        f"・タイトルに「第{episode_number}話」などの話数を含めないこと\n"
        + RUBY_GUIDE
        + f"・更新日：{today}\n"
        "・指定されたJSON Schemaの各項目を、今回の物語の内容に合わせて埋めること"
    )


def fetch_story(
    prompt_text,
    first_episode,
    api_key,
    urlopen=urllib.request.urlopen,
    sleep=time.sleep,
):
    payload = json.dumps({
        "contents": [{"parts": [{"text": prompt_text}]}],
        "generationConfig": {
            "maxOutputTokens": 12000,
            "thinkingConfig": {"thinkingBudget": 2048},
            "responseMimeType": "application/json",
            "responseJsonSchema": story_response_schema(first_episode),
        },
    }, ensure_ascii=False).encode("utf-8")
    url = (
        "https://generativelanguage.googleapis.com/v1beta/"
        f"models/{MODEL}:generateContent"
    )
    last_err = None

    for attempt in range(ATTEMPTS):
        if attempt:
            wait = 5 * attempt
            print(f"再試行（{attempt + 1}/{ATTEMPTS}）… {wait}秒待機。直前の理由: {last_err}")
            sleep(wait)
        try:
            req = urllib.request.Request(
                url,
                data=payload,
                headers={
                    "Content-Type": "application/json",
                    "x-goog-api-key": api_key,
                },
            )
            with urlopen(req, timeout=120) as response:
                result = json.loads(response.read())
        except (
            urllib.error.URLError,
            TimeoutError,
            http.client.HTTPException,
            json.JSONDecodeError,
            UnicodeDecodeError,
        ) as exc:
            last_err = f"API呼び出し失敗: {exc}"
            continue

        if not isinstance(result, dict):
            last_err = "API応答のルートがオブジェクトではありません"
            continue
        candidates = result.get("candidates")
        if not candidates:
            last_err = f"候補ゼロ（promptFeedback={result.get('promptFeedback', {})}）"
            continue
        candidate = candidates[0]
        if not isinstance(candidate, dict):
            last_err = "候補がオブジェクトではありません"
            continue
        finish = candidate.get("finishReason", "")
        if finish != "STOP":
            detail = candidate.get("finishMessage")
            last_err = f"生成が正常終了しませんでした（finishReason={finish or 'なし'}"
            if detail:
                last_err += f", finishMessage={detail}"
            last_err += "）"
            continue

        parts = candidate.get("content", {}).get("parts") or []
        response_text = "".join(
            part.get("text", "")
            for part in parts
            if isinstance(part, dict) and not part.get("thought")
        ).strip()
        if not response_text:
            last_err = "構造化出力が取得できませんでした"
            continue
        try:
            story = json.loads(response_text)
        except json.JSONDecodeError as exc:
            last_err = f"構造化出力のJSON解析失敗: {exc}"
            continue
        errors = validate_story_payload(story, first_episode)
        if errors:
            last_err = "構造化出力が不正: " + "; ".join(errors)
            continue
        return story

    raise SystemExit(f"Gemini生成に{ATTEMPTS}回失敗しました。最後の理由: {last_err}")


def normalize_title(title, episode_number):
    title = re.sub(
        r"^第?\s*\d+\s*話[「『：:\s]*",
        "",
        title,
    ).strip().strip("「」『』").strip()
    return title or f"第{episode_number}話"


def convert_people(people):
    return [
        {
            "name": person["name"].strip(),
            "desc": person["description"].strip(),
        }
        for person in people
    ]


def build_rss(site_url, total_episodes, stories, bible, library):
    work_index = len(library)
    items = ""
    for story in reversed(stories):
        published = (
            datetime.datetime.strptime(story["iso"], "%Y-%m-%d")
            .replace(tzinfo=JST)
            .strftime("%a, %d %b %Y %H:%M:%S +0900")
        )
        items += f"""
    <item>
      <title>第{story['episode']}話 {html.escape(story['title'])}</title>
      <link>{site_url}work.html?work={work_index}#ep{story['episode']}</link>
      <guid isPermaLink="false">w{work_index}-ep-{story['episode']}</guid>
      <pubDate>{published}</pubDate>
      <description>{html.escape(story['body'])}</description>
    </item>"""

    channel_title = bible.get("work_title") or "AI連載物語"
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>{html.escape(channel_title)}</title>
    <link>{site_url}</link>
    <description>AIが毎週月曜に連載する全{total_episodes}話完結の物語</description>
    <language>ja</language>{items}
  </channel>
</rss>"""


def generate_updates(config, stories, bible, library, generate_content, now):
    validate_state(stories, bible, library)
    total_episodes, site_url = validate_config(config)
    stories = copy.deepcopy(stories)
    bible = copy.deepcopy(bible)
    library = copy.deepcopy(library)
    today = now.strftime("%Y年%m月%d日")
    today_iso = now.strftime("%Y-%m-%d")
    episode_number = len(stories) + 1
    starting_new_work = episode_number > total_episodes
    completed_work = None

    if starting_new_work:
        completed_work = {
            "work_title": bible.get("work_title") or "無題の物語",
            "completed": stories[-1].get("iso") or today_iso,
            "total_episodes": len(stories),
            "episodes": stories,
        }
        print(
            f"前作「{completed_work['work_title']}」が全{total_episodes}話で完結。"
            "新しい作品を開始します。"
        )
        stories = []
        bible = fresh_bible()
        episode_number = 1

    prompt = build_prompt(
        episode_number,
        total_episodes,
        today,
        bible,
        stories,
    )
    generated = generate_content(prompt, episode_number == 1)
    errors = validate_story_payload(generated, episode_number == 1)
    if errors:
        raise SystemExit("生成結果が不正: " + "; ".join(errors))

    title = normalize_title(generated["title"], episode_number)
    body = generated["body"].strip()
    summary = generated["summary"].strip()

    if episode_number == 1:
        work_title = generated["work_title"].strip().strip("「」『』").strip()
        bible["work_title"] = work_title or "無題の物語"
        bible["world"] = generated["world"].strip()
        bible["characters"] = convert_people(generated["characters"])
    else:
        existing = {person["name"] for person in bible.get("characters", [])}
        for person in convert_people(generated["new_characters"]):
            if person["name"] not in existing:
                bible.setdefault("characters", []).append(person)
                existing.add(person["name"])

    bible.setdefault("synopsis", []).append(
        f"第{episode_number}話「{title}」：{summary}"
    )
    stories.append({
        "episode": episode_number,
        "title": title,
        "date": today,
        "iso": today_iso,
        "body": body,
    })
    if completed_work is not None:
        library.append(completed_work)

    rss = build_rss(site_url, total_episodes, stories, bible, library)
    return {
        "stories": stories,
        "bible": bible,
        "library": library,
        "rss": rss,
        "episode_number": episode_number,
        "title": title,
        "body_length": len(body),
    }


def serialize_updates(updates):
    return {
        "stories.json": json.dumps(
            updates["stories"], ensure_ascii=False, indent=2
        ) + "\n",
        "bible.json": json.dumps(
            updates["bible"], ensure_ascii=False, indent=2
        ) + "\n",
        "library.json": json.dumps(
            updates["library"], ensure_ascii=False, indent=2
        ) + "\n",
        "rss.xml": updates["rss"],
    }


def atomic_write_files(contents):
    temporary_files = {}
    try:
        for filename, content in contents.items():
            target = Path(filename)
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                newline="\n",
                dir=target.parent,
                prefix=f".{target.name}.",
                suffix=".tmp",
                delete=False,
            ) as temporary:
                temporary.write(content)
                temporary.flush()
                os.fsync(temporary.fileno())
                temporary_files[target] = Path(temporary.name)

        for target, temporary in temporary_files.items():
            os.replace(temporary, target)
    finally:
        for temporary in temporary_files.values():
            if temporary.exists():
                temporary.unlink()


def main():
    config = load_json("config.json")
    stories = load_json("stories.json")
    bible = load_json("bible.json")
    library = load_json("library.json")
    validate_state(stories, bible, library)
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise SystemExit("環境変数 GEMINI_API_KEY が設定されていません。")

    updates = generate_updates(
        config,
        stories,
        bible,
        library,
        lambda prompt, first_episode: fetch_story(
            prompt,
            first_episode,
            api_key,
        ),
        datetime.datetime.now(JST),
    )
    atomic_write_files(serialize_updates(updates))
    print(
        f"完了：第{updates['episode_number']}話「{updates['title']}」"
        f"（{updates['body_length']}字）"
    )


if __name__ == "__main__":
    main()
