import json


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
