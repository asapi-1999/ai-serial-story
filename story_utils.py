import json
import re


SECTION_TAGS = (
    "作品タイトル",
    "タイトル",
    "本文",
    "世界観",
    "登場人物",
    "今回のあらすじ",
    "新登場人物",
)
SECTION_TAG_PATTERN = "|".join(re.escape(name) for name in SECTION_TAGS)


def load_json(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError as e:
        raise SystemExit(f"必須ファイル {path} が見つかりません。") from e
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise SystemExit(f"{path} を読み込めません。JSON形式を確認してください: {e}") from e


def validate_config(config):
    if not isinstance(config, dict):
        raise SystemExit("config.json のルートはオブジェクトである必要があります。")

    total_episodes = config.get("total_episodes")
    if isinstance(total_episodes, bool) or not isinstance(total_episodes, int) or total_episodes < 1:
        raise SystemExit("config.json の total_episodes は1以上の整数である必要があります。")

    site_url = config.get("site_url")
    if not isinstance(site_url, str) or not site_url.strip():
        raise SystemExit("config.json の site_url は空でない文字列である必要があります。")

    return total_episodes, site_url.strip().rstrip("/") + "/"


def section(tag, text):
    """【タグ】の中身を次の既知のセクションまたは末尾まで取り出す。"""
    m = re.search(
        rf"^【{re.escape(tag)}】[ \t　]*(.*?)(?=^【(?:{SECTION_TAG_PATTERN})】|\Z)",
        text,
        re.MULTILINE | re.DOTALL,
    )
    return m.group(1).strip() if m else ""


def section_line(tag, text):
    """【タグ】と同じ行の中身だけを取り出す。"""
    m = re.search(
        rf"^【{re.escape(tag)}】[ \t　]*([^\r\n]*)",
        text,
        re.MULTILINE,
    )
    return m.group(1).strip() if m else ""


def missing_story_sections(text, first_episode):
    """話数に応じた必須セクションのうち、欠けているものを返す。"""
    required = ["タイトル", "本文", "今回のあらすじ"]
    if first_episode:
        required = ["作品タイトル", *required, "世界観", "登場人物"]
    else:
        required.append("新登場人物")

    missing = []
    for tag in required:
        value = section_line(tag, text) if tag in ("作品タイトル", "タイトル") else section(tag, text)
        if not value:
            missing.append(f"【{tag}】")
    return missing
