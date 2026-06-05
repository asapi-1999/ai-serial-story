import urllib.request
import json, os, datetime, re

today = datetime.datetime.now(
    datetime.timezone(datetime.timedelta(hours=9))
).strftime("%Y年%m月%d日")

prompt = (
    "あなたはプロの小説家です。"
    + today
    + "を更新日とした短編ファンタジー物語を日本語で800字程度書いてください。"
    + "毎回異なる登場人物・世界観にしてください。"
    + "以下の形式で出力してください：\n"
    + "【タイトル】ここにタイトル\n"
    + "【本文】\nここに本文"
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
title = title_match.group(1).strip() if title_match else "AI連載物語"
body  = body_match.group(1).strip()  if body_match  else story

archive_html = ""
try:
    with open("index.html", "r", encoding="utf-8") as f:
        content = f.read()
    past = re.findall(r"<details>[\s\S]*?</details>", content)
    archive_html = "\n".join(past[:200])
except:
    pass

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
    .date { color: #999; font-size: 0.85em; margin-bottom: 24px; }
    .story { white-space: pre-wrap; font-size: 1.05em; background: #fff; padding: 24px; border-radius: 8px; box-shadow: 0 2px 8px rgba(0,0,0,0.06); }
    .archive { margin-top: 40px; }
    details { margin-top: 12px; background: #fff; border-radius: 6px; padding: 12px 16px; box-shadow: 0 1px 4px rgba(0,0,0,0.05); }
    summary { cursor: pointer; font-weight: bold; color: #5a3e1b; }
    .past-body { white-space: pre-wrap; margin-top: 12px; font-size: 0.95em; }
  </style>
</head>
<body>
  <h1>📖 """ + title + """</h1>
  <p class="date">📅 更新日：""" + today + """</p>
  <div class="story">""" + body + """</div>
  <div class="archive">
    <h2 style="font-size:1.1em; color:#5a3e1b;">📚 バックナンバー</h2>
    <details>
      <summary>🗒 """ + today + "｜" + title + """</summary>
      <div class="past-body">""" + body + """</div>
    </details>
    """ + archive_html + """
  </div>
</body>
</html>"""

with open("index.html", "w", encoding="utf-8") as f:
    f.write(html)

print("完了：" + title)
