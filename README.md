# ai-serial-story

AI（Gemini）が**毎週月曜に1話ずつ**自動で連載していく小説サイトです。
1作品は**全5話で完結**し、完結すると**次の月曜から自動で新しい作品**が始まります。
ジャンルは作品ごとに自由（ファンタジー／SF／ミステリー／恋愛 など）。
GitHub Actions が定期実行で生成し、GitHub Pages で公開します。

## 仕組み

```
GitHub Actions (毎週月曜)
   └─ generate.py が Gemini API を呼び出して続きを生成
        ├─ stories.json … 連載中の作品の全話データ（話数・タイトル・日付・本文）
        ├─ bible.json   … 連載中の作品の設定（作品タイトル・世界観・登場人物・各話あらすじ）
        ├─ library.json … 完結した過去作品の書庫（作品ごとに全話を保存）
        └─ rss.xml      … RSSフィード（連載中の作品を配信）
   └─ 変更を自動コミット＆プッシュ

index.html … 本棚（作品一覧）。連載中・完結作品をカードで並べる
work.html  … 作品ビューア。?work=N で作品を1つ表示（全話＋ルビ描画）
```

### 5話完結と新作の自動開始

- 各話のプロンプトに「全5話構成・最終話で完結」を指示。第5話で伏線を回収して締めます。
- 第5話の翌週の実行で、完結作品を `library.json` へ退避し、`stories.json` / `bible.json` を
  リセットして**新しい作品の第1話**を生成します（書庫への退避は生成成功後に確定）。

### 文脈の渡し方

- 続きの生成時は **bible.json の設定（作品タイトル・世界観・登場人物・全話あらすじ）＋直近2話の全文**
  を文脈として渡します。全話の本文を毎回渡さないので、連載が長くてもコストと文脈上限を抑えられます。

### 構造化出力とデータ保護

- Geminiには第1話用／続話用のJSON Schemaを指定し、タイトル・本文・あらすじ・人物を構造化JSONで受け取ります。
- API応答は型・必須値・本文600文字以上を検証し、不正な場合は最大4回まで再試行します。
- 生成前に既存のJSONを話数、日付、人物、あらすじまで検証し、不整合があればAPIを呼ばず終了します。
- 更新するJSONとRSSはすべてメモリ上で組み立て、一時ファイルとバックアップを作成してから`os.replace()`で置換します。
  通常のI/O例外では置換済みファイルをロールバックし、生成・検証失敗時も既存ファイルを変更しません。
- Actionsログには実際に使用したモデルと、APIから取得できた場合は入出力・思考トークン数を記録します。

### 表示とルビ

- `index.html` / `work.html` は JSON を `fetch` し、`textContent` / `createTextNode` で描画するため
  本文に記号が含まれてもレイアウトが壊れず、**XSSに対して安全**です。
- 本文は青空文庫式ルビに対応：`漢字《よみ》` または `｜親文字《よみ》` が `<ruby>` に変換されます。

## セットアップ

1. **Gemini APIキーを取得**（[Google AI Studio](https://aistudio.google.com/)）。
2. リポジトリの **Settings → Secrets and variables → Actions** で
   `GEMINI_API_KEY` という名前のシークレットに登録。
3. **Settings → Pages** で公開を有効化（Source: `main` ブランチ）。
4. 公開URLが `https://<ユーザー名>.github.io/<リポジトリ名>/` と異なる場合は、
   `config.json` の `site_url` を実際のURLに合わせて修正（RSSのリンク用）。

## 実行

- **自動**: 毎週月曜 00:00 UTC（= 月曜 09:00 JST）に実行。
- **手動**: Actions タブ → 該当ワークフロー → **Run workflow**（`workflow_dispatch`）。
- スケジュールと手動が重なっても `concurrency` で直列化されます。
- 生成中に `main` が更新された場合、古い状態からの生成結果はコミットせず安全に終了します。
- **テスト**: `python3 -m unittest discover -s tests` と `node tests/check_static.js`

## 設定の調整箇所

| 項目 | 場所 |
|---|---|
| 完結話数 | `config.json` の `total_episodes`（既定 5） |
| 公開URL | `config.json` の `site_url` |
| 1話あたりの文字数 | プロンプト内の「1000字程度」 |
| 本文の最低文字数 | `MIN_BODY_LENGTH`（既定 600） |
| 生成モデル | `gemini-3.5-flash`（API障害時は `gemini-2.5-flash`） |
| 出力トークン上限 | `maxOutputTokens`（既定 12000） |
| 思考設定 | 3.5 Flashは `thinkingLevel: medium`、2.5 Flashは `thinkingBudget: 2048` |
| 文脈に渡す直近話数 | `stories[-2:]` |
| ルビの指示 | `RUBY_GUIDE` |
| 更新頻度 | `.github/workflows/generate_story.yml` の cron |

## 最初から全部やり直したいとき

連載中・書庫の両方を初期化する場合は、以下に戻します。

- `stories.json` → `[]`
- `bible.json`   → `{"work_title":"","world":"","characters":[],"synopsis":[]}`
- `library.json` → `[]`（過去作品も消したい場合のみ）
- `rss.xml`      → 記事なしの `<channel>` のみ

次回実行時に第1話から再スタートします。

## 補助ツール

- `edit.html` … 物語をローカルで手編集するためのエディタ（サイト本体では未使用・任意）。
