import urllib.request
import urllib.error
import json
import os
import datetime
import re
import time
import html

JST = datetime.timezone(datetime.timedelta(hours=9))
now = datetime.datetime.now(JST)
today = now.strftime("%Y年%m月%d日")
today_iso = now.strftime("%Y-%m-%d")

# GitHub Pages の公開URL（RSSのリンク用）。Pages の設定に合わせて調整する。
SITE_URL = "https://asapi-1999.github.io/ai-serial-story/"


def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


stories = load_json("stories.json", [])
bible = load_json("bible.json", {"world": "", "characters": [], "synopsis": []})
episode_number = len(stories) + 1


def section(tag, text):
    """【タグ】の中身を次の【…】または末尾まで取り出す。"""
    m = re.search(r"【" + tag + r"】\s*([\s\S]*?)(?=\n?【|$)", text)
    return m.group(1).strip() if m else ""


def section_line(tag, text):
    """【タグ】と同じ行の中身だけを取り出す（タイトルなど1行用）。
    改行や次の【…】で必ず打ち切るので、本文を巻き込まない。"""
    m = re.search(r"【" + tag + r"】[ \t　]*([^\n【]*)", text)
    return m.group(1).strip() if m else ""


def parse_people(text):
    """1行1人「名前：説明」をパースして辞書のリストに。"""
    people = []
    for line in text.splitlines():
        line = line.strip().lstrip("・-*").strip()
        if not line or line in ("なし", "無し", "特になし"):
            continue
        pair = re.split(r"[：:]", line, maxsplit=1)
        if len(pair) == 2 and pair[0].strip():
            people.append({"name": pair[0].strip(), "desc": pair[1].strip()})
    return people


# ----- プロンプト組み立て -----
# 本文中のルビ指定方法（サイト側の <ruby> 変換ルールに対応した青空文庫式）。
RUBY_GUIDE = (
    "・難読な漢字・固有名詞・印象づけたい語にはルビ（ふりがな）を振ってよい。\n"
    "  書式は青空文庫式で、読みは全角の《 》で囲む。読みはひらがなかカタカナで書くこと。\n"
    "  ・漢字のすぐ後ろに《よみ》を置くと、その漢字列にルビが付く。例：葦原《あしはら》、案山子《かかし》\n"
    "  ・ひらがな・カタカナ・英字に振る場合や、熟語の一部だけに振る場合は、\n"
    "    全角の縦棒「｜」で親文字の開始位置を示す。例：｜物語《ものがたり》、思い出《おもいで》は「｜思い出《おもいで》」\n"
    "  ・ルビは多用せず、効果的な箇所に絞ること。\n"
)

if episode_number == 1:
    prompt = (
        "あなたはプロの小説家です。\n"
        "これから長編ファンタジー小説の連載を始めます。\n"
        "以下の条件で第1話を書いてください：\n"
        "・独自の世界観とメインキャラクターを設定すること\n"
        "・続きが気になる終わり方にすること\n"
        "・日本語で1000字程度\n"
        "・タイトルに「第1話」などの話数は含めないこと\n"
        + RUBY_GUIDE +
        "・更新日：" + today + "\n\n"
        "以下の形式で、各セクションを必ず出力してください：\n"
        "【タイトル】ここにタイトル（話数は含めない）\n"
        "【本文】\nここに本文\n"
        "【世界観】物語の舞台・設定を2〜3文で要約\n"
        "【登場人物】1行に1人、「名前：説明」の形式で記載\n"
        "【今回のあらすじ】この話の内容を1文で要約"
    )
else:
    chars = "\n".join(
        f"・{p['name']}：{p['desc']}" for p in bible.get("characters", [])
    ) or "（未設定）"
    synopsis = "\n".join(bible.get("synopsis", [])) or "（なし）"
    recent = ""
    for s in stories[-2:]:
        recent += f"第{s['episode']}話「{s['title']}」\n{s['body']}\n\n"

    prompt = (
        "あなたはプロの小説家です。連載中のファンタジー小説の続きを書きます。\n\n"
        "# これまでの設定\n"
        "## 世界観\n" + (bible.get("world") or "（未設定）") + "\n\n"
        "## 登場人物\n" + chars + "\n\n"
        "## あらすじ（各話1行）\n" + synopsis + "\n\n"
        "# 直近のエピソード（全文）\n" + recent +
        "---\n"
        "上記の続きとなる第" + str(episode_number) + "話を書いてください。\n"
        "以下の条件を守ってください：\n"
        "・登場人物・世界観は引き継ぐこと\n"
        "・直近のエピソードの終わりから自然につながること\n"
        "・続きが気になる終わり方にすること\n"
        "・日本語で1000字程度\n"
        "・タイトルに「第" + str(episode_number) + "話」などの話数は含めないこと\n"
        + RUBY_GUIDE +
        "・更新日：" + today + "\n\n"
        "以下の形式で、各セクションを必ず出力してください：\n"
        "【タイトル】ここにタイトル（話数は含めない）\n"
        "【本文】\nここに本文\n"
        "【今回のあらすじ】この話の内容を1文で要約\n"
        "【新登場人物】今回新たに登場した人物を「名前：説明」で記載（いなければ「なし」）"
    )


# ----- Gemini 呼び出し（リトライ付き・APIキーはヘッダーで送る）-----
def call_gemini(prompt_text):
    payload = json.dumps({
        "contents": [{"parts": [{"text": prompt_text}]}],
        "generationConfig": {
            "maxOutputTokens": 8000,
            # 思考も少しだけ使わせて整合性を上げつつ、本文の出力枠も確保する。
            # （0にすると思考オフ、-1で動的。打ち切り対策で十分な枠を残すこと）
            "thinkingConfig": {"thinkingBudget": 2048},
        },
    }).encode()

    url = ("https://generativelanguage.googleapis.com/v1beta/"
           "models/gemini-2.5-flash:generateContent")
    last_err = None
    for attempt in range(3):
        try:
            req = urllib.request.Request(
                url,
                data=payload,
                headers={
                    "Content-Type": "application/json",
                    "x-goog-api-key": os.environ["GEMINI_API_KEY"],
                },
            )
            with urllib.request.urlopen(req, timeout=120) as r:
                return json.loads(r.read())
        except urllib.error.URLError as e:
            last_err = e
            wait = 5 * (attempt + 1)
            print(f"API呼び出し失敗（{attempt + 1}/3）: {e} … {wait}秒後に再試行")
            time.sleep(wait)
    raise SystemExit(f"API呼び出しに3回失敗しました: {last_err}")


res = call_gemini(prompt)
cand = res["candidates"][0]
finish = cand.get("finishReason", "")
parts = cand.get("content", {}).get("parts")
if not parts or "text" not in parts[0]:
    raise SystemExit(f"本文が取得できませんでした（finishReason={finish}）: {res}")
story = parts[0]["text"]
if finish == "MAX_TOKENS":
    raise SystemExit("出力がトークン上限で打ち切られました。maxOutputTokens を増やしてください。")


# ----- パース -----
# タイトルは必ず1行だけ取り出す（【本文】マーカーが欠けても本文を巻き込まない）。
title = section_line("タイトル", story)
# モデルが「第N話「…」」のように話数を付けた場合は除去する。
title = re.sub(r"^第?\s*\d+\s*話[「『：:\s]*", "", title).strip().strip("「」『』").strip()
if not title:
    title = f"第{episode_number}話"

# 本文は【本文】マーカーから抽出する。マーカーが欠けた回に全文へフォールバックすると
# タイトル・あらすじまで本文に混入するため、抽出できなければエラーで止める（壊れたデータを保存しない）。
body = section("本文", story)
if not body:
    raise SystemExit(
        "【本文】マーカーが見つからず本文を抽出できませんでした。"
        f"出力フォーマットを確認してください:\n{story[:500]}"
    )
summary = section("今回のあらすじ", story) or title

# ----- ストーリーバイブル更新 -----
if episode_number == 1:
    bible["world"] = section("世界観", story)
    bible["characters"] = parse_people(section("登場人物", story))
else:
    existing = {p["name"] for p in bible.get("characters", [])}
    for p in parse_people(section("新登場人物", story)):
        if p["name"] not in existing:
            bible.setdefault("characters", []).append(p)
            existing.add(p["name"])

bible.setdefault("synopsis", []).append(
    f"第{episode_number}話「{title}」：{summary}"
)

# ----- 保存 -----
stories.append({
    "episode": episode_number,
    "title": title,
    "date": today,
    "iso": today_iso,
    "body": body,
})

with open("stories.json", "w", encoding="utf-8") as f:
    json.dump(stories, f, ensure_ascii=False, indent=2)

with open("bible.json", "w", encoding="utf-8") as f:
    json.dump(bible, f, ensure_ascii=False, indent=2)

# ----- RSS生成 -----
items = ""
for s in reversed(stories):
    iso = s.get("iso") or today_iso
    pub = (datetime.datetime.strptime(iso, "%Y-%m-%d")
           .replace(tzinfo=JST)
           .strftime("%a, %d %b %Y %H:%M:%S +0900"))
    items += f"""
    <item>
      <title>第{s['episode']}話 {html.escape(s['title'])}</title>
      <link>{SITE_URL}#ep{s['episode']}</link>
      <guid isPermaLink="false">ep-{s['episode']}</guid>
      <pubDate>{pub}</pubDate>
      <description>{html.escape(s['body'])}</description>
    </item>"""

rss = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>AI連載物語</title>
    <link>{SITE_URL}</link>
    <description>AIが毎週月曜に連載するファンタジー小説</description>
    <language>ja</language>{items}
  </channel>
</rss>"""

with open("rss.xml", "w", encoding="utf-8") as f:
    f.write(rss)

print(f"完了：第{episode_number}話「{title}」（{len(body)}字）")
