# ai-serial-story

AI（Gemini）が**毎週月曜に1話ずつ**自動で連載していくファンタジー小説です。
GitHub Actions が定期実行で続きを生成し、GitHub Pages で公開します。

## 仕組み

```
GitHub Actions (毎週月曜)
   └─ generate.py が Gemini API を呼び出して続きを生成
        ├─ stories.json … 全話のデータ（話数・タイトル・日付・本文）
        ├─ bible.json   … 世界観・登場人物・各話あらすじ（整合性維持用の設定資料）
        └─ rss.xml      … RSSフィード
   └─ 変更を自動コミット＆プッシュ
index.html … stories.json を読み込んで表示する静的ページ（XSS安全）
```

- 続きの生成時は、**bible.json の設定（世界観・登場人物・全話あらすじ）＋直近2話の全文**を文脈として渡します。
  全話の本文を毎回渡さないので、連載が長くなってもコストと文脈上限を抑えられます。
- `index.html` は `stories.json` を `fetch` して `textContent` で描画するため、本文に記号が含まれてもレイアウトが壊れません。

## セットアップ

1. **Gemini APIキーを取得**（[Google AI Studio](https://aistudio.google.com/)）。
2. リポジトリの **Settings → Secrets and variables → Actions** で
   `GEMINI_API_KEY` という名前のシークレットに登録。
3. **Settings → Pages** で公開を有効化（Source: `main` ブランチ）。
4. 公開URLが `https://<ユーザー名>.github.io/<リポジトリ名>/` と異なる場合は、
   `generate.py` の `SITE_URL` を実際のURLに合わせて修正（RSSのリンク用）。

## 実行

- **自動**: 毎週月曜 00:00 UTC（= 月曜 09:00 JST）に実行。
- **手動**: Actions タブ → 該当ワークフロー → **Run workflow**（`workflow_dispatch`）。

## 設定の調整箇所（generate.py）

| 項目 | 場所 |
|---|---|
| 1話あたりの文字数 | プロンプト内の「1000字程度」 |
| 出力トークン上限 | `maxOutputTokens`（既定 8000） |
| 思考トークン | `thinkingConfig.thinkingBudget`（既定 2048／0で無効） |
| 文脈に渡す直近話数 | `stories[-2:]` |
| 更新頻度 | `.github/workflows/generate_story.yml` の cron |

## 最初からやり直したいとき

`stories.json` を `[]`、`bible.json` を `{"world":"","characters":[],"synopsis":[]}` に戻せば、
次回実行時に第1話から再スタートします。
