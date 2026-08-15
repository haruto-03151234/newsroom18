# NEWSROOM 18

毎日 **06:00 / 12:00 / 18:00（Asia/Tokyo）** に更新する、日本語ニュースダイジェストです。RSS/Atomフィードから候補を集め、過去の掲載履歴と照合して重複を抑え、静的なGitHub Pagesサイトとして公開します。

## 更新する時間帯

| 版 | 主な対象時間（半開区間） |
|---|---|
| 朝6時版 | 前日18:00以上〜当日06:00未満 |
| 昼12時版 | 当日06:00以上〜12:00未満 |
| 夕方6時版 | 当日12:00以上〜18:00未満 |

GitHub Actionsが遅れて起動しても、記事の対象時間は固定境界から計算します。前の実行が欠けた場合は `.state/news-state.json` を基準に直近の未処理版を補完します。

## 仕組み

1. `config/feeds.json` に登録した15本のフィードを取得
2. 公開時刻をAsia/Tokyoへ統一し、対象時間帯に絞り込み
3. 追跡パラメータを除いたURLと日本語タイトル類似度で重複を排除
4. OpenAI Responses APIのStructured Outputsで、最大12本を日本語編集
5. APIキーがない場合や一時障害時は、出典を明記した決定論的フォールバックで継続
6. 最新号・過去号・Markdown記事・RSS・サイトマップを生成
7. 同じワークフロー内でコミットし、GitHub Pagesへ公開

## ディレクトリ

```text
.github/workflows/news-pages.yml  定刻収集・検証・Pages公開
.state/news-state.json            重複排除と補完処理の状態
config/feeds.json                 取得元とカテゴリ・利用制約
config/site.json                  サイト名と公開URL
scripts/collect_news.py           更新処理の入口
scripts/news_pipeline/            収集・編集・重複排除・公開処理
templates/article.md.tmpl         保存用記事テンプレート
content/                          自動生成するMarkdown記事
site/                             GitHub Pagesへ公開する静的サイト
tests/                            時刻窓・安全性・重複排除・生成テスト
```

## GitHub側の設定

このリポジトリは公開リポジトリを前提とします。

1. **Settings → Pages → Build and deployment → Source** を `GitHub Actions` にする
2. 任意で **Settings → Secrets and variables → Actions → Secrets** に `OPENAI_API_KEY` を追加
3. 任意でActions variable `OPENAI_MODEL` を追加（未設定時は `gpt-5.6`）
4. **Actions → Collect news and publish Pages → Run workflow** で初回更新を実行

APIキーはリポジトリや生成ファイルへ書き込まれません。未設定でも日本語フィードを中心に更新は継続しますが、自然な統合要約と英語ソースの日本語化には設定を推奨します。

## ローカル確認

Python 3.12以降を使用します。

```bash
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
python scripts/collect_news.py --edition auto
python -m http.server --directory site 8000
```

収集を試すと実データと状態ファイルが更新されます。表示だけを確認する場合は、同梱の明示的なサンプルデータを使用してください。

## 編集・安全方針

- フィード本文や画像を転載せず、短い候補情報から独自の文章を生成します。
- 利用条件が厳しい配信元は `metadataOnly` とし、見出し・URL・時刻だけを照合に使います。
- 外部データは命令ではない未信頼入力として扱い、モデルにツールや秘密情報を渡しません。
- 出典URLはモデルに生成させず、候補IDからコードで再結合します。
- HTTP(S)以外、認証情報付きURL、ローカル・予約IPを拒否します。
- 重要度4以上は、原則として複数媒体または一次情報がある場合だけ表示します。
- 詳細や最新の訂正は、必ず各記事の出典リンクで確認してください。

配信元ごとの条件は変更されることがあります。商用化や大規模配信を行う場合は、各社の最新条件を改めて確認してください。

## ライセンス

このリポジトリのコードとサイトデザインは [MIT License](LICENSE) です。ニュースの見出し・商標・リンク先コンテンツの権利は各権利者に帰属します。
