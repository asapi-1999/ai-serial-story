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


def fresh_bible():
    return {"work_title": "", "world": "", "characters": [], "synopsis": []}


stories = load_json("stories.json", [])
bible = load_json("bible.json", fresh_bible())
library = load_json("library.json", [])
episode_number = len(stories) + 1

# 全5話で完結する連載。完結済みなら、その作品を書庫(library.json)へ退避し、
# 新しい作品を第1話から始める。退避の確定（ファイル書き込み）は生成成功後に行う。
TOTAL_EPISODES = 5
starting_new_work = episode_number > TOTAL_EPISODES
completed_work = None
if starting_new_work:
    completed_work = {
        "work_title": bible.get("work_title") or "無題の物語",
        "completed": today_iso,
        "episodes": stories,
    }
    print(f"前作「{completed_work['work_title']}」が全{TOTAL_EPISODES}話で完結。新しい作品を開始します。")
    stories = []
    bible = fresh_bible()
    episode_number = 1


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
        "これから全" + str(TOTAL_EPISODES) + "話で完結するファンタジー小説の連載を始めます。\n"
        "以下の条件で第1話（全" + str(TOTAL_EPISODES) + "話）を書いてください：\n"
        "・独自の世界観とメインキャラクターを設定すること\n"
        "・全" + str(TOTAL_EPISODES) + "話で物語が完結する構成を念頭に、第1話では世界観と"
        "物語の核となる謎・目的を提示すること\n"
        "・続きが気になる終わり方にすること\n"
        "・日本語で1000字程度\n"
        "・「作品タイトル」は連載全体を通したタイトル、「タイトル」は今回の話のタイトルとし、"
        "どちらにも「第1話」などの話数は含めないこと\n"
        + RUBY_GUIDE +
        "・更新日：" + today + "\n\n"
        "以下の形式で、各セクションを必ず出力してください：\n"
        "【作品タイトル】連載全体のタイトル\n"
        "【タイトル】今回（第1話）のタイトル\n"
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

    work_title = bible.get("work_title") or "（未設定）"
    is_final = episode_number == TOTAL_EPISODES
    if is_final:
        ending_rule = (
            "・これは最終話（第" + str(TOTAL_EPISODES) + "話）です。"
            "これまでに張られた伏線や謎を回収し、物語をきれいに完結させること\n"
        )
    else:
        ending_rule = (
            "・全" + str(TOTAL_EPISODES) + "話構成のうちの第" + str(episode_number) +
            "話として、最終話（第" + str(TOTAL_EPISODES) + "話）での完結に向けて物語を前進させること\n"
            "・続きが気になる終わり方にすること\n"
        )

    prompt = (
        "あなたはプロの小説家です。全" + str(TOTAL_EPISODES) +
        "話で完結するファンタジー小説の続きを書きます。\n\n"
        "# 作品タイトル\n" + work_title + "\n\n"
        "# これまでの設定\n"
        "## 世界観\n" + (bible.get("world") or "（未設定）") + "\n\n"
        "## 登場人物\n" + chars + "\n\n"
        "## あらすじ（各話1行）\n" + synopsis + "\n\n"
        "# 直近のエピソード（全文）\n" + recent +
        "---\n"
        "上記の続きとなる第" + str(episode_number) + "話（全" + str(TOTAL_EPISODES) +
        "話）を書いてください。\n"
        "以下の条件を守ってください：\n"
        "・登場人物・世界観は引き継ぐこと\n"
        "・直近のエピソードの終わりから自然につながること\n"
        + ending_rule +
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
candidates = res.get("candidates")
if not candidates:
    # セーフティブロック等で候補が返らない場合は promptFeedback を添えて止める。
    feedback = res.get("promptFeedback", {})
    raise SystemExit(f"Geminiが候補を返しませんでした（promptFeedback={feedback}）: {res}")
cand = candidates[0]
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
    work_title = section_line("作品タイトル", story).strip("「」『』").strip()
    bible["work_title"] = work_title or "無題の物語"
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

# 新作を開始した回では、完結した前作を書庫へ確定保存する（生成成功後に行う）。
if starting_new_work and completed_work is not None:
    library.append(completed_work)
    with open("library.json", "w", encoding="utf-8") as f:
        json.dump(library, f, ensure_ascii=False, indent=2)

with open("stories.json", "w", encoding="utf-8") as f:
    json.dump(stories, f, ensure_ascii=False, indent=2)

with open("bible.json", "w", encoding="utf-8") as f:
    json.dump(bible, f, ensure_ascii=False, indent=2)

# ----- RSS生成 -----
# guid は作品をまたいで一意にする必要がある（新作はエピソード番号が1から振り直されるため、
# 単なる ep-N だと前作と衝突し、購読者に新作が「既読」扱いで届かない）。
# 完結済み作品数を現在作品のインデックスとして前置きする（連載中は値が変わらず安定）。
work_index = len(library)
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
      <guid isPermaLink="false">w{work_index}-ep-{s['episode']}</guid>
      <pubDate>{pub}</pubDate>
      <description>{html.escape(s['body'])}</description>
    </item>"""

channel_title = bible.get("work_title") or "AI連載物語"
rss = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>{html.escape(channel_title)}</title>
    <link>{SITE_URL}</link>
    <description>AIが毎週月曜に連載する全{TOTAL_EPISODES}話完結のファンタジー小説</description>
    <language>ja</language>{items}
  </channel>
</rss>"""

with open("rss.xml", "w", encoding="utf-8") as f:
    f.write(rss)

print(f"完了：第{episode_number}話「{title}」（{len(body)}字）")
