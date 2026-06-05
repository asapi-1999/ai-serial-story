import urllib.request
import json, os, datetime, re

today = datetime.datetime.now(
    datetime.timezone(datetime.timedelta(hours=9))
).strftime("%Y年%m月%d日")

try:
    with open("stories.json", "r", encoding="utf-8") as f:
        stories = json.load(f)
except:
    stories = []

episode_number = len(stories) + 1

context = ""
if len(stories) == 0:
    prompt = (
        "あなたはプロの小説家です。\n"
        "これから長編小説の連載をします。\n"
        "以下の条件で第1話を書いてください：\n"
        "・独自の世界観とメインキャラクターを設定すること\n"
        "・続きが気になる終わり方にすること\n"
        "・日本語で800字程度\n"
        "・更新日：" + today + "\n\n"
        "以下の形式で出力してください：\n"
        "【タイトル】ここにタイトル\n"
        "【本文】\nここに本文"
    )
else:
    recent = stories[-3:]
    for s in recent:
        context += f"第{s['episode']}話「{s['title']}」\n{s['body']}\n\n"

    prompt = (
        "あなたはプロの小説家です。\n"
        "以下はこれまでの連載内容です：\n\n"
        + context +
        "---\n"
        "上記の続きとなる第" + str(episode_number) + "話を書いてください。\n"
        "以下の条件を守ってください：\n"
        "・登場人物・世界観は前話から引き継ぐこと\n"
        "・前話の終わりから自然につながること\n"
        "・続きが気になる終わり方にすること\n"
        "・日本語で800字程度\n"
        "・更新日：" + today + "\n\n"
        "以下の形式で出力してください：\n"
        "【タイトル】ここにタイトル\n"
        "【本文】\nここに本文"
    )

data = json.dumps({
    "contents": [{"parts": [{"text": prompt}]}],
    "generationConfig": {"maxOutputTokens": 2000}
}).encode()

req = urllib.request.Request(
    "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key="
    + os.environ["GEMINI_API_KEY"],
    data=data,
    headers={"Content-Type": "application/json"}
)
res = json.loads(urllib.request.urlopen(req).read())
story = res["candidates"][0]["content"]["parts"][0]["text"]

title_match = re.search(r"【タイトル】(.+)", story)
body_match  = re.search(r"【本文】([\s\S]+)", story)
title = title_match.group(1).strip() if title_match else f"第{episode_number}話"
body  = body_match.group(1).strip()  if body_match  else story

stories.append({
    "episode": episode_number,
    "title": title,
    "date": today,
    "body": body
})

with open("stories.json", "w", encoding="utf-8") as f:
    json.dump(stories, f, ensure_ascii=False, indent=2)

archive_html = ""
for s in reversed(stories):
    archive_html += f"""
    <details>
      <summary>第{s['episode']}話｜{s['title']}（{s['date']}）</summary>
      <div class="past-body">{s['body']}</div>
    </details>"""

html = """<GITHUB_TOKEN = os.environ.get("GH_PAT", "")

html = """<!DOCTYPE html>
<html lang="ja">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>AI連載物語</title>
  <style>
    body {
      font-family: 'Hiragino Sans', 'Noto Sans JP', sans-serif;
      max-width: 680px;
      margin: 0 auto;
      padding: 30px 20px;
      line-height: 2.0;
      color: #2c2c2c;
      background: #fdf9f3;
    }
    h1 { font-size: 1.4em; border-bottom: 2px solid #c8a96e; padding-bottom: 10px; color: #5a3e1b; }
    .episode { color: #c8a96e; font-size: 0.9em; margin-bottom: 4px; }
    .date { color: #999; font-size: 0.85em; margin-bottom: 24px; }
    .story { white-space: pre-wrap; font-size: 1.05em; background: #fff; padding: 24px; border-radius: 8px; box-shadow: 0 2px 8px rgba(0,0,0,0.06); }
    .archive { margin-top: 40px; }
    details { margin-top: 12px; background: #fff; border-radius: 6px; padding: 12px 16px; box-shadow: 0 1px 4px rgba(0,0,0,0.05); }
    summary { cursor: pointer; font-weight: bold; color: #5a3e1b; }
    .past-body { white-space: pre-wrap; margin-top: 12px; font-size: 0.95em; }
    .btn {
      display: inline-block;
      margin-top: 24px;
      padding: 12px 28px;
      background: #c8a96e;
      color: #fff;
      border: none;
      border-radius: 8px;
      font-size: 1em;
      cursor: pointer;
    }
    .btn:hover { background: #a8893e; }
    .btn:disabled { background: #ccc; cursor: not-allowed; }
    #status { margin-top: 12px; font-size: 0.9em; color: #666; }
  </style>
</head>
<body>
  <p class="episode">第""" + str(episode_number) + """話</p>
  <h1>📖 """ + title + """</h1>
  <p class="date">📅 更新日：""" + today + """</p>
  <div class="story">""" + body + """</div>

  <button class="btn" id="genBtn" onclick="generateNext()">📝 次の話を生成する</button>
  <p id="status"></p>

  <div class="archive">
    <h2 style="font-size:1.1em; color:#5a3e1b;">📚 バックナンバー</h2>""" + archive_html + """
  </div>

  <script>
    function generateNext() {
      const btn = document.getElementById('genBtn');
      const status = document.getElementById('status');
      btn.disabled = true;
      btn.textContent = '⏳ 生成中...';
      status.textContent = '生成をリクエストしました。約2分後にページを自動更新します...';

      fetch('https://api.github.com/repos/asapi-1999/ai-serial-story/actions/workflows/generate_story.yml/dispatches', {
        method: 'POST',
        headers: {
          'Authorization': 'Bearer """ + GITHUB_TOKEN + """',
          'Content-Type': 'application/json'
        },
        body: JSON.stringify({ ref: 'main' })
      }).then(res => {
        if (res.status === 204) {
          status.textContent = '✅ リクエスト成功！2分後に自動更新します...';
          setTimeout(() => location.reload(), 120000);
        } else {
          status.textContent = '❌ エラーが発生しました。もう一度試してください。';
          btn.disabled = false;
          btn.textContent = '📝 次の話を生成する';
        }
      }).catch(() => {
        status.textContent = '❌ エラーが発生しました。';
        btn.disabled = false;
        btn.textContent = '📝 次の話を生成する';
      });
    }
  </script>
</body>
</html>"""
```

---

## GH_PATをSecretsに登録する

1. リポジトリの「**Settings**」→「**Secrets and variables**」→「**Actions**」
2. 「**New repository secret**」をクリック
3. 以下を入力：

| 項目 | 値 |
|---|---|
| Name | `GH_PAT` |
| Secret | さっきメモした `ghp_xxxx` |

4. 「**Add secret**」をクリック

---

## 完成後のイメージ

```
┌─────────────────────────────┐
│ 第2話                        │
│ 📖 タイトル                  │
│ 本文...                      │
│                              │
│ [📝 次の話を生成する]  ← ボタン│
│                              │
│ 📚 バックナンバー            │
└─────────────────────────────┘>
<html lang="ja">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>AI連載物語</title>
  <style>
    body {
      font-family: 'Hiragino Sans', 'Noto Sans JP', sans-serif;
      max-width: 680px;
      margin: 0 auto;
      padding: 30px 20px;
      line-height: 2.0;
      color: #2c2c2c;
      background: #fdf9f3;
    }
    h1 { font-size: 1.4em; border-bottom: 2px solid #c8a96e; padding-bottom: 10px; color: #5a3e1b; }
    .episode { color: #c8a96e; font-size: 0.9em; margin-bottom: 4px; }
    .date { color: #999; font-size: 0.85em; margin-bottom: 24px; }
    .story { white-space: pre-wrap; font-size: 1.05em; background: #fff; padding: 24px; border-radius: 8px; box-shadow: 0 2px 8px rgba(0,0,0,0.06); }
    .archive { margin-top: 40px; }
    details { margin-top: 12px; background: #fff; border-radius: 6px; padding: 12px 16px; box-shadow: 0 1px 4px rgba(0,0,0,0.05); }
    summary { cursor: pointer; font-weight: bold; color: #5a3e1b; }
    .past-body { white-space: pre-wrap; margin-top: 12px; font-size: 0.95em; }
  </style>
</head>
<body>
  <p class="episode">第""" + str(episode_number) + """話</p>
  <h1>📖 """ + title + """</h1>
  <p class="date">📅 更新日：""" + today + """</p>
  <div class="story">""" + body + """</div>
  <div class="archive">
    <h2 style="font-size:1.1em; color:#5a3e1b;">📚 バックナンバー</h2>""" + archive_html + """
  </div>
</body>
</html>"""

with open("index.html", "w", encoding="utf-8") as f:
    f.write(html)

print(f"完了：第{episode_number}話「{title}」")
