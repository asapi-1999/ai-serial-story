import json
import urllib.parse


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

    site_url = site_url.strip().rstrip("/") + "/"
    try:
        parsed = urllib.parse.urlsplit(site_url)
    except ValueError as exc:
        raise SystemExit("config.json の site_url をURLとして解析できません。") from exc
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or any(character.isspace() for character in site_url)
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise SystemExit(
            "config.json の site_url はクエリやフラグメントを含まないhttp(s) URLである必要があります。"
        )

    return total_episodes, site_url
