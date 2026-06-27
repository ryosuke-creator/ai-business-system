# COSP Morning Research

Macの電源状態に依存せず、GitHub Actionsだけで毎朝のネタ収集、アフィリエイト投稿案作成、Markdown保存、LINE通知を実行する。自動投稿は行わない。

## 毎朝の処理

1. `github_data/Research.md`からカテゴリとキーワードを読む
2. Google News RSSから各カテゴリのネタを収集する
3. `github_data/Affiliate_Links.md`から関連リンクを選ぶ
4. 3〜5件の投稿文を作り、`github_data/Generated/YYYY-MM-DD_アフィリエイト投稿候補.md`へ保存する
5. LINE Messaging APIで、SNSへコピーできる投稿案を個人宛てに1通送る
6. 生成Markdownと実行ログをリポジトリへcommitする
7. 処理失敗時はLINEへ失敗通知を送る

投稿候補には、タイトル、投稿先、本文、ハッシュタグ、アフィリエイト導線、使用予定リンク、想定読者、投稿意図を含める。リンクがある本文には広告表示を入れる。

## 実行時刻

`.github/workflows/morning_line.yml`で次を設定している。

```yaml
schedule:
  - cron: "0 22 * * *"
workflow_dispatch:
```

`22:00 UTC`は翌日の`7:00 JST`。`workflow_dispatch`によりActions画面から手動実行もできる。

## GitHub Secrets

リポジトリの `Settings` → `Secrets and variables` → `Actions` →
`New repository secret` から次の2項目を登録する。

| Secret名 | 設定する値 |
|---|---|
| `LINE_CHANNEL_ACCESS_TOKEN` | LINE Developersで発行したチャネルアクセストークン |
| `LINE_TO_USER_ID` | 個人通知先の`U`から始まるuserId |

トークンとuserIdはファイル、`.env`、Markdownへ書かない。

## 手動テスト

1. GitHubリポジトリの`Actions`を開く
2. `Morning Research and LINE`を選ぶ
3. `Run workflow`を押す
4. もう一度`Run workflow`を押して開始する
5. 緑のチェック、LINE通知、`github_data/Generated`を確認する

ローカルでLINEを送らずに確認する場合:

```bash
python3 scripts/morning_line_content.py --dry-run
```

## 正常時のLINE

```text
【朝のResearch AI】
本日の投稿候補は3件です。

【候補1｜家づくり】
投稿タイトル: ...
投稿先おすすめ: Threads / X

本文:
...
※アフィリエイトリンクを含みます。
https://...

ハッシュタグ: ...
アフィリエイト導線: ...
使用予定リンク: ...
想定読者: ...
投稿意図: ...
```

## 失敗時のLINE

```text
【朝のAI秘書・処理失敗】
発生日時: YYYY-MM-DD HH:MM:SS JST
内容: GitHub Actionsの朝処理に失敗しました
確認先: GitHub Actionsの実行URL
```

Secrets自体が未設定の場合はLINE APIを呼べないため、Actionsのログで設定不足を確認する。
workflowはネタ収集前にLINE SecretsとAPI接続を検証する。`401`の場合は
`LINE_CHANNEL_ACCESS_TOKEN`をLINE Developersで再発行し、GitHub Secretを上書きする。

## 公開データの注意

`github_data/Research.md`には、SNSで公開してよいカテゴリだけを書く。
住所、契約、住宅ローン、支払情報、写真、トークンは登録しない。
