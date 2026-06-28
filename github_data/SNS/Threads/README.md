# Threads Performance AI

Threadsの投稿実績を手入力し、次回の改善案をGitHub ActionsとLINEで確認する。
初期版ではスクリーンショットのOCRは行わない。

## フォルダ

```text
github_data/SNS/Threads/
├── Templates/thread_post_result_template.md
├── Results/
├── Screenshots/
└── Analysis/
```

## 入力手順

1. [[github_data/SNS/Threads/Results/今日のThreads投稿結果|今日のThreads投稿結果]]を開く
2. Threadsの数値、本文、自己メモを入力する
3. Threadsのインサイト画面を`Screenshots/`へ保存する
4. 入力後に`status: draft`を`status: ready`へ変更する
5. ResultsとScreenshotsをGitHubの`main`へ反映する
6. Actionsの`Threads Performance AI`を手動実行する
7. LINEと`Analysis/YYYY-MM-DD_threads_analysis.md`を確認する

## 注意

- iCloud Driveへ保存しただけではGitHub Actionsから読めない
- GitHubへcommitまたはWebアップロードしてから実行する
- このリポジトリは公開設定のため、スクリーンショットの個人情報を必ず隠す
- 投稿URLや画像に住所、氏名、通知、アカウント情報が映っていないか確認する
- `分析済み: false`のファイルだけが対象
- LINE送信成功後に`分析済み: true`へ自動更新される

## 自動実行

- 毎日21:30 JST
- GitHub Actionsのcronは`30 12 * * *`（UTC）
- `workflow_dispatch`で手動実行可能

## 分析内容

- 表示回数と反応率
- 本文文字数とハッシュタグ数
- 投稿カテゴリと投稿時間
- アフィリエイト導線の有無
- 良かった点と改善点
- 次回投稿案と次に試す切り口

反応率は`(いいね数 + 返信数 + リポスト数 + 保存数) ÷ 表示回数 × 100`で計算する。
同じ日に手動実行と自動実行を行い、後の実行に未分析投稿がない場合は、先に作成した分析結果を上書きしない。
