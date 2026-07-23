import copy
import datetime
import html
import http.client
import json
import os
import re
import shutil
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

from story_utils import load_json, validate_config


JST = datetime.timezone(datetime.timedelta(hours=9))
ATTEMPTS = 4
MIN_BODY_LENGTH = 600
MODEL_OPTIONS = (
    ("gemini-3.5-flash", {"thinkingLevel": "medium"}),
    ("gemini-2.5-flash", {"thinkingBudget": 2048}),
)
FALLBACK_HTTP_CODES = frozenset({404, 408, 429, 500, 502, 503, 504})

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


def is_valid_iso_date(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return False
    try:
        datetime.date.fromisoformat(value)
    except ValueError:
        return False
    return True


def is_matching_display_date(value, iso_date):
    if (
        not isinstance(value, str)
        or not re.fullmatch(r"\d{4}年\d{2}月\d{2}日", value)
        or not is_valid_iso_date(iso_date)
    ):
        return False
    try:
        parsed = datetime.datetime.strptime(value, "%Y年%m月%d日").date()
    except ValueError:
        return False
    return parsed.isoformat() == iso_date


def episode_list_errors(episodes, path):
    errors = []
    if not isinstance(episodes, list):
        return [f"{path} は配列である必要があります"]

    for index, story in enumerate(episodes):
        item_path = f"{path}[{index}]"
        if not isinstance(story, dict):
            errors.append(f"{item_path} はオブジェクトである必要があります")
            continue
        episode = story.get("episode")
        if isinstance(episode, bool) or not isinstance(episode, int):
            errors.append(f"{item_path}.episode は整数である必要があります")
        elif episode != index + 1:
            errors.append(f"{item_path}.episode は {index + 1} である必要があります")
        for key in ("title", "date", "body"):
            value = story.get(key)
            if not isinstance(value, str) or not value.strip():
                errors.append(f"{item_path}.{key} は空でない文字列である必要があります")
            elif key == "body" and len(value.strip()) < MIN_BODY_LENGTH:
                errors.append(
                    f"{item_path}.body は{MIN_BODY_LENGTH}文字以上である必要があります"
                )
        if not is_valid_iso_date(story.get("iso")):
            errors.append(f"{item_path}.iso は YYYY-MM-DD 形式の実在する日付である必要があります")
        elif not is_matching_display_date(story.get("date"), story["iso"]):
            errors.append(f"{item_path}.date は iso と同じ日を YYYY年MM月DD日 形式で表す必要があります")
    return errors


def validate_state(stories, bible, library):
    errors = []
    if not isinstance(stories, list):
        errors.append("stories.json のルートは配列である必要があります")
    if not isinstance(bible, dict):
        errors.append("bible.json のルートはオブジェクトである必要があります")
    if not isinstance(library, list):
        errors.append("library.json のルートは配列である必要があります")
    if errors:
        raise SystemExit("保存データが不正です:\n- " + "\n- ".join(errors))

    errors.extend(episode_list_errors(stories, "stories.json"))

    for key in ("work_title", "world"):
        value = bible.get(key)
        if not isinstance(value, str):
            errors.append(f"bible.json.{key} は文字列である必要があります")
        elif stories and not value.strip():
            errors.append(f"bible.json.{key} は連載中に空にできません")

    characters = bible.get("characters")
    if not isinstance(characters, list):
        errors.append("bible.json.characters は配列である必要があります")
    else:
        if stories and not characters:
            errors.append("bible.json.characters は連載中に空にできません")
        for index, person in enumerate(characters):
            path = f"bible.json.characters[{index}]"
            if not isinstance(person, dict):
                errors.append(f"{path} はオブジェクトである必要があります")
                continue
            for key in ("name", "desc"):
                value = person.get(key)
                if not isinstance(value, str) or not value.strip():
                    errors.append(f"{path}.{key} は空でない文字列である必要があります")

    synopsis = bible.get("synopsis")
    if not isinstance(synopsis, list):
        errors.append("bible.json.synopsis は配列である必要があります")
    else:
        if len(synopsis) != len(stories):
            errors.append("bible.json.synopsis の件数は stories.json の話数と一致する必要があります")
        for index, summary in enumerate(synopsis):
            if not isinstance(summary, str) or not summary.strip():
                errors.append(f"bible.json.synopsis[{index}] は空でない文字列である必要があります")

    for index, work in enumerate(library):
        path = f"library.json[{index}]"
        if not isinstance(work, dict):
            errors.append(f"{path} はオブジェクトである必要があります")
            continue
        if not isinstance(work.get("work_title"), str) or not work["work_title"].strip():
            errors.append(f"{path}.work_title は空でない文字列である必要があります")
        if not is_valid_iso_date(work.get("completed")):
            errors.append(f"{path}.completed は YYYY-MM-DD 形式の実在する日付である必要があります")
        total = work.get("total_episodes")
        if isinstance(total, bool) or not isinstance(total, int) or total < 1:
            errors.append(f"{path}.total_episodes は1以上の整数である必要があります")
        episodes = work.get("episodes")
        errors.extend(episode_list_errors(episodes, f"{path}.episodes"))
        if isinstance(episodes, list):
            if not episodes:
                errors.append(f"{path}.episodes は空にできません")
            if isinstance(total, int) and not isinstance(total, bool) and total != len(episodes):
                errors.append(f"{path}.total_episodes は episodes の件数と一致する必要があります")
            if (
                episodes
                and is_valid_iso_date(work.get("completed"))
                and work["completed"] != episodes[-1].get("iso")
            ):
                errors.append(f"{path}.completed は最終話の iso と一致する必要があります")

    if errors:
        raise SystemExit("保存データが不正です:\n- " + "\n- ".join(errors))


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
        elif key == "body" and len(value.strip()) < MIN_BODY_LENGTH:
            errors.append(
                f"body が短すぎます（{len(value.strip())}文字、最低{MIN_BODY_LENGTH}文字）"
            )

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
    last_err = None
    total_attempts = 0
    for model_index, (model, thinking_config) in enumerate(MODEL_OPTIONS):
        has_fallback = model_index < len(MODEL_OPTIONS) - 1
        fallback_eligible = False
        payload = json.dumps({
            "contents": [{"parts": [{"text": prompt_text}]}],
            "generationConfig": {
                "maxOutputTokens": 12000,
                "thinkingConfig": thinking_config,
                "responseMimeType": "application/json",
                "responseJsonSchema": story_response_schema(first_episode),
            },
        }, ensure_ascii=False).encode("utf-8")
        url = (
            "https://generativelanguage.googleapis.com/v1beta/"
            f"models/{model}:generateContent"
        )

        for attempt in range(ATTEMPTS):
            if attempt:
                wait = 5 * attempt
                print(
                    f"{model} 再試行（{attempt + 1}/{ATTEMPTS}）… "
                    f"{wait}秒待機。直前の理由: {last_err}"
                )
                sleep(wait)
            try:
                total_attempts += 1
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
            except urllib.error.HTTPError as exc:
                last_err = f"API呼び出し失敗: HTTP {exc.code} {exc.reason}"
                fallback_eligible = exc.code in FALLBACK_HTTP_CODES
                if has_fallback and fallback_eligible:
                    break
                continue
            except (
                urllib.error.URLError,
                TimeoutError,
                http.client.HTTPException,
                json.JSONDecodeError,
                UnicodeDecodeError,
            ) as exc:
                last_err = f"API呼び出し失敗: {exc}"
                fallback_eligible = True
                continue

            fallback_eligible = False
            if not isinstance(result, dict):
                last_err = "API応答のルートがオブジェクトではありません"
                continue
            candidates = result.get("candidates")
            if not isinstance(candidates, list) or not candidates:
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

            content = candidate.get("content")
            if not isinstance(content, dict):
                last_err = "候補のcontentがオブジェクトではありません"
                continue
            parts = content.get("parts")
            if not isinstance(parts, list):
                last_err = "候補のpartsが配列ではありません"
                continue
            response_chunks = []
            invalid_part = False
            for part in parts:
                if not isinstance(part, dict):
                    invalid_part = True
                    break
                if part.get("thought"):
                    continue
                text = part.get("text", "")
                if not isinstance(text, str):
                    invalid_part = True
                    break
                response_chunks.append(text)
            if invalid_part:
                last_err = "候補のpartsに不正な要素があります"
                continue
            response_text = "".join(response_chunks).strip()
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
            usage = result.get("usageMetadata")
            usage_text = ""
            if isinstance(usage, dict):
                counts = []
                for key, label in (
                    ("promptTokenCount", "入力"),
                    ("candidatesTokenCount", "出力"),
                    ("thoughtsTokenCount", "思考"),
                ):
                    if isinstance(usage.get(key), int):
                        counts.append(f"{label}{usage[key]}")
                if counts:
                    usage_text = "（トークン: " + " / ".join(counts) + "）"
            print(f"生成モデル: {model}{usage_text}")
            return story

        if has_fallback and fallback_eligible:
            next_model = MODEL_OPTIONS[model_index + 1][0]
            print(f"{model} を利用できないため {next_model} に切り替えます: {last_err}")
            continue
        break

    raise SystemExit(f"Gemini生成に{total_attempts}回失敗しました。最後の理由: {last_err}")


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
        episode_url = html.escape(
            f"{site_url}work.html?work={work_index}#ep{story['episode']}",
            quote=True,
        )
        items += f"""
    <item>
      <title>第{story['episode']}話 {html.escape(story['title'])}</title>
      <link>{episode_url}</link>
      <guid isPermaLink="false">w{work_index}-ep-{story['episode']}</guid>
      <pubDate>{published}</pubDate>
      <description>{html.escape(story['body'])}</description>
    </item>"""

    channel_title = bible.get("work_title") or "AI連載物語"
    escaped_site_url = html.escape(site_url, quote=True)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>{html.escape(channel_title)}</title>
    <link>{escaped_site_url}</link>
    <description>AIが毎週月曜に連載する全{total_episodes}話完結の物語</description>
    <language>ja</language>{items}
  </channel>
</rss>"""


def generate_updates(config, stories, bible, library, generate_content, now):
    validate_state(stories, bible, library)
    total_episodes, site_url = validate_config(config)
    if len(stories) > total_episodes:
        raise SystemExit(
            f"stories.json が設定上限の全{total_episodes}話を超えています。"
        )
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
    backup_files = {}
    replaced_targets = []
    preserve_backups = False
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

            if target.exists():
                with tempfile.NamedTemporaryFile(
                    mode="w+b",
                    dir=target.parent,
                    prefix=f".{target.name}.",
                    suffix=".bak",
                    delete=False,
                ) as backup:
                    with target.open("rb") as source:
                        shutil.copyfileobj(source, backup)
                    backup.flush()
                    os.fsync(backup.fileno())
                    backup_files[target] = Path(backup.name)

        for target, temporary in temporary_files.items():
            replaced_targets.append(target)
            os.replace(temporary, target)
    except BaseException as exc:
        rollback_errors = []
        for target in reversed(replaced_targets):
            backup = backup_files.get(target)
            try:
                if backup is not None:
                    os.replace(backup, target)
                elif target.exists():
                    target.unlink()
            except OSError as rollback_exc:
                rollback_errors.append(f"{target}: {rollback_exc}")
        if rollback_errors:
            preserve_backups = True
            raise RuntimeError(
                "ファイル更新とロールバックの両方に失敗しました: "
                + "; ".join(rollback_errors)
                + "。復旧用の.bakファイルを保持します。"
            ) from exc
        raise
    finally:
        cleanup_paths = list(temporary_files.values())
        if not preserve_backups:
            cleanup_paths.extend(backup_files.values())
        for path in cleanup_paths:
            if path.exists():
                path.unlink()


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
