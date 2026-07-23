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
SERIES_METADATA_KEYS = (
    "genre",
    "style_guide",
    "themes",
    "episode_plan",
    "open_threads",
    "character_states",
)

RUBY_GUIDE = (
    "・難読な漢字・固有名詞・印象づけたい語にはルビ（ふりがな）を振ってよい。\n"
    "  書式は青空文庫式で、読みは全角の《 》で囲む。読みはひらがなかカタカナで書くこと。\n"
    "  ・漢字のすぐ後ろに《よみ》を置くと、その漢字列にルビが付く。例：葦原《あしはら》、案山子《かかし》\n"
    "  ・ひらがな・カタカナ・英字に振る場合や、熟語の一部だけに振る場合は、\n"
    "    全角の縦棒「｜」で親文字の開始位置を示す。例：｜物語《ものがたり》、思い出《おもいで》は「｜思い出《おもいで》」\n"
    "  ・ルビは多用せず、効果的な箇所に絞ること。\n"
)

PROSE_GUIDE = (
    "・説明だけで進めず、人物の行動・会話・感覚描写を通して場面を描くこと\n"
    "・同じ語尾・比喩・前話の要約を繰り返さず、文の長短とリズムに変化をつけること\n"
    "・会話・行動・情景描写の比率を場面に応じて調整し、必要な余韻を残すこと\n"
    "・各話で人物に意味のある選択、発見、関係性または状況の変化を起こすこと\n"
    "・設定を一度に説明せず、物語上必要な情報を場面の中で自然に明かすこと\n"
)


def fresh_bible():
    return {
        "work_title": "",
        "world": "",
        "characters": [],
        "synopsis": [],
        "genre": "",
        "style_guide": "",
        "themes": [],
        "episode_plan": [],
        "open_threads": [],
        "character_states": [],
    }


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


def series_metadata_errors(data, path, total_episodes=None, allow_legacy=False):
    if allow_legacy and not any(key in data for key in SERIES_METADATA_KEYS):
        return []

    errors = []
    for key in ("genre", "style_guide"):
        value = data.get(key)
        if not isinstance(value, str) or not value.strip():
            errors.append(f"{path}.{key} は空でない文字列である必要があります")

    for key, allow_empty in (("themes", False), ("open_threads", True)):
        values = data.get(key)
        if not isinstance(values, list):
            errors.append(f"{path}.{key} は配列である必要があります")
            continue
        if not allow_empty and not values:
            errors.append(f"{path}.{key} は空にできません")
        for index, value in enumerate(values):
            if not isinstance(value, str) or not value.strip():
                errors.append(f"{path}.{key}[{index}] は空でない文字列である必要があります")

    episode_plan = data.get("episode_plan")
    if not isinstance(episode_plan, list):
        errors.append(f"{path}.episode_plan は配列である必要があります")
    else:
        if not episode_plan:
            errors.append(f"{path}.episode_plan は空にできません")
        if total_episodes is not None and len(episode_plan) != total_episodes:
            errors.append(
                f"{path}.episode_plan は全{total_episodes}話分必要です"
            )
        for index, plan in enumerate(episode_plan):
            item_path = f"{path}.episode_plan[{index}]"
            if not isinstance(plan, dict):
                errors.append(f"{item_path} はオブジェクトである必要があります")
                continue
            episode = plan.get("episode")
            if isinstance(episode, bool) or not isinstance(episode, int):
                errors.append(f"{item_path}.episode は整数である必要があります")
            elif episode != index + 1:
                errors.append(f"{item_path}.episode は {index + 1} である必要があります")
            outline = plan.get("outline")
            if not isinstance(outline, str) or not outline.strip():
                errors.append(f"{item_path}.outline は空でない文字列である必要があります")

    character_states = data.get("character_states")
    if not isinstance(character_states, list):
        errors.append(f"{path}.character_states は配列である必要があります")
    else:
        if not character_states:
            errors.append(f"{path}.character_states は空にできません")
        names = set()
        for index, state in enumerate(character_states):
            item_path = f"{path}.character_states[{index}]"
            if not isinstance(state, dict):
                errors.append(f"{item_path} はオブジェクトである必要があります")
                continue
            for key in ("name", "state"):
                value = state.get(key)
                if not isinstance(value, str) or not value.strip():
                    errors.append(f"{item_path}.{key} は空でない文字列である必要があります")
            name = state.get("name")
            if isinstance(name, str) and name.strip():
                normalized_name = name.strip()
                if normalized_name in names:
                    errors.append(f"{path}.character_states に人物名の重複があります: {normalized_name}")
                names.add(normalized_name)
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

    if stories:
        errors.extend(
            series_metadata_errors(
                bible,
                "bible.json",
                allow_legacy=True,
            )
        )

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


def character_state_schema():
    return {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "人物名",
            },
            "state": {
                "type": "string",
                "description": "今回の話終了時点の目的・感情・関係性・居場所を簡潔に記述",
            },
        },
        "required": ["name", "state"],
        "additionalProperties": False,
    }


def episode_plan_schema(total_episodes):
    schema = {
        "type": "array",
        "description": "連載全話の構成案。各話の役割と主要な展開を記述",
        "items": {
            "type": "object",
            "properties": {
                "episode": {
                    "type": "integer",
                    "description": "1から始まる話数",
                },
                "outline": {
                    "type": "string",
                    "description": "この話で進める対立・発見・転換を1〜2文で記述",
                },
            },
            "required": ["episode", "outline"],
            "additionalProperties": False,
        },
        "minItems": total_episodes or 1,
    }
    if total_episodes is not None:
        schema["maxItems"] = total_episodes
    return schema


def series_metadata_schema(total_episodes):
    return {
        "genre": {
            "type": "string",
            "description": "作品の中心ジャンルと、必要なら副ジャンル",
        },
        "style_guide": {
            "type": "string",
            "description": "視点・時制・語彙・文章リズム・会話量・雰囲気を再現できる文体指針",
        },
        "themes": {
            "type": "array",
            "description": "作品を通して扱う中心テーマ",
            "items": {"type": "string"},
            "minItems": 1,
        },
        "episode_plan": episode_plan_schema(total_episodes),
        "open_threads": {
            "type": "array",
            "description": "今回終了時点で未回収の伏線・謎・約束。最終話ですべて解決した場合は空配列",
            "items": {"type": "string"},
        },
        "character_states": {
            "type": "array",
            "description": "主要人物それぞれの今回終了時点の状態",
            "items": character_state_schema(),
            "minItems": 1,
        },
    }


def story_response_schema(first_episode, total_episodes=None):
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
    metadata = series_metadata_schema(total_episodes)
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
            **metadata,
        }
        required = [
            "work_title",
            "title",
            "body",
            "summary",
            "world",
            "characters",
            *SERIES_METADATA_KEYS,
        ]
    else:
        properties = {
            **common,
            "new_characters": {
                "type": "array",
                "description": "今回新たに登場した人物。いなければ空配列",
                "items": person_schema(),
            },
            **metadata,
        }
        required = [
            "title",
            "body",
            "summary",
            "new_characters",
            *SERIES_METADATA_KEYS,
        ]

    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
        "propertyOrdering": required,
    }


def validate_story_payload(story, first_episode, total_episodes=None):
    if not isinstance(story, dict):
        return ["応答のルートがオブジェクトではありません"]

    required_strings = ["title", "body", "summary"]
    people_key = "characters" if first_episode else "new_characters"
    if first_episode:
        required_strings.extend(["work_title", "world"])

    errors = []
    errors.extend(
        series_metadata_errors(
            story,
            "応答",
            total_episodes=total_episodes,
        )
    )
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


def format_string_list(values, empty_text):
    if not isinstance(values, list) or not values:
        return empty_text
    return "\n".join(f"・{value}" for value in values)


def format_episode_plan(plans, empty_text):
    if not isinstance(plans, list) or not plans:
        return empty_text
    return "\n".join(
        f"・第{plan.get('episode', '?')}話：{plan.get('outline', '')}"
        for plan in plans
        if isinstance(plan, dict)
    ) or empty_text


def format_character_states(states, empty_text):
    if not isinstance(states, list) or not states:
        return empty_text
    return "\n".join(
        f"・{state.get('name', '不明')}：{state.get('state', '')}"
        for state in states
        if isinstance(state, dict)
    ) or empty_text


def build_prompt(episode_number, total_episodes, today, bible, stories):
    if episode_number == 1:
        return (
            "あなたはプロの小説家です。\n"
            f"これから全{total_episodes}話で完結する連載小説を新しく始めます。\n"
            f"以下の条件で第1話（全{total_episodes}話）を書いてください：\n"
            "・ジャンルは自由（ファンタジー／SF／ミステリー／恋愛／ホラー／歴史／日常など何でも可）。"
            "今回の作品のジャンルと作風を自由に選ぶこと\n"
            "・独自の世界観とメインキャラクターを設定すること\n"
            f"・執筆前に全{total_episodes}話の役割と主要展開を設計し、episode_planに"
            f"第1話から第{total_episodes}話まで順番に記録すること\n"
            "・genre、style_guide、themesは以後の全話で維持できる具体性を持たせること。"
            "style_guideには視点・時制・語彙・文のリズム・会話量・雰囲気を含めること\n"
            f"・全{total_episodes}話で物語が完結する構成を念頭に、第1話では世界観と"
            "物語の核となる謎・目的を提示すること\n"
            "・続きが気になる終わり方にすること\n"
            "・open_threadsには第1話終了時点の未回収要素を、character_statesには"
            "主要人物全員の現在地・感情・関係性を記録すること\n"
            + PROSE_GUIDE
            + "・日本語で1000字程度\n"
            "・作品タイトルと今回のタイトルのどちらにも話数を含めないこと\n"
            + RUBY_GUIDE
            + f"・更新日：{today}\n"
            "・指定されたJSON Schemaの各項目を、物語の内容に合わせて埋めること\n"
            "・JSONの管理情報は本文中に箇条書きや説明文として露出させないこと"
        )

    legacy_text = "（未設定。既存本文と設定から推定し、今回の応答で補完すること）"
    chars = "\n".join(
        f"・{person['name']}：{person['desc']}"
        for person in bible.get("characters", [])
    ) or "（未設定）"
    synopsis = "\n".join(bible.get("synopsis", [])) or "（なし）"
    themes = format_string_list(bible.get("themes"), legacy_text)
    episode_plan = format_episode_plan(bible.get("episode_plan"), legacy_text)
    open_threads = format_string_list(
        bible.get("open_threads"),
        "（なし。未設定の場合は既存本文から推定して補完すること）",
    )
    character_states = format_character_states(
        bible.get("character_states"),
        legacy_text,
    )
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
        f"## ジャンル\n{bible.get('genre') or legacy_text}\n\n"
        f"## 文体指針\n{bible.get('style_guide') or legacy_text}\n\n"
        f"## テーマ\n{themes}\n\n"
        f"## 全話構成\n{episode_plan}\n\n"
        f"## 未回収の要素\n{open_threads}\n\n"
        f"## 人物の現在状態\n{character_states}\n\n"
        f"## あらすじ（各話1行）\n{synopsis}\n\n"
        f"# 直近のエピソード（全文）\n{recent}"
        "---\n"
        f"上記の続きとなる第{episode_number}話（全{total_episodes}話）を書いてください。\n"
        "以下の条件を守ってください：\n"
        "・これまでのジャンル・作風・世界観・登場人物を引き継ぐこと\n"
        "・genre、style_guide、themes、episode_planは既存値を変更せず、そのまま応答に含めること。"
        "未設定の場合だけ既存本文から推定して全項目を補完すること\n"
        "・episode_planにおける今回の役割を果たし、前後の話の役割を先取りしすぎないこと\n"
        "・open_threadsは今回回収した要素を除き、新たに生じた未回収要素を加えること。"
        "最終話ではすべて回収して空配列にすること\n"
        "・character_statesは今回終了時点の主要人物全員の状態に更新すること\n"
        "・直近のエピソードの終わりから自然につながること\n"
        + PROSE_GUIDE
        + ending_rule
        + "・日本語で1000字程度\n"
        f"・タイトルに「第{episode_number}話」などの話数を含めないこと\n"
        + RUBY_GUIDE
        + f"・更新日：{today}\n"
        "・指定されたJSON Schemaの各項目を、今回の物語の内容に合わせて埋めること\n"
        "・JSONの管理情報は本文中に箇条書きや説明文として露出させないこと"
    )


def fetch_story(
    prompt_text,
    first_episode,
    api_key,
    total_episodes=None,
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
                "responseJsonSchema": story_response_schema(
                    first_episode,
                    total_episodes,
                ),
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
            errors = validate_story_payload(
                story,
                first_episode,
                total_episodes,
            )
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


def apply_series_metadata(bible, generated, preserve_static):
    if not preserve_static:
        for key in ("genre", "style_guide"):
            bible[key] = generated[key].strip()
        bible["themes"] = [value.strip() for value in generated["themes"]]
        bible["episode_plan"] = [
            {
                "episode": plan["episode"],
                "outline": plan["outline"].strip(),
            }
            for plan in generated["episode_plan"]
        ]

    bible["open_threads"] = [
        value.strip() for value in generated["open_threads"]
    ]
    bible["character_states"] = [
        {
            "name": state["name"].strip(),
            "state": state["state"].strip(),
        }
        for state in generated["character_states"]
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
    metadata_errors = []
    if stories:
        metadata_errors = series_metadata_errors(
            bible,
            "bible.json",
            total_episodes=total_episodes,
            allow_legacy=True,
        )
    if metadata_errors:
        raise SystemExit(
            "保存データが不正です:\n- " + "\n- ".join(metadata_errors)
        )
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

    preserve_static_metadata = (
        episode_number != 1 and bool(bible.get("genre"))
    )
    prompt = build_prompt(
        episode_number,
        total_episodes,
        today,
        bible,
        stories,
    )
    generated = generate_content(
        prompt,
        episode_number == 1,
        total_episodes,
    )
    errors = validate_story_payload(
        generated,
        episode_number == 1,
        total_episodes,
    )
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

    apply_series_metadata(
        bible,
        generated,
        preserve_static_metadata,
    )
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
        lambda prompt, first_episode, total_episodes: fetch_story(
            prompt,
            first_episode,
            api_key,
            total_episodes,
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
