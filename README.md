# ClaudeWorkMulti

ChatWork × Claude Code による複数AIメンバー自動返信システム。

ChatWorkでメンバー宛にメッセージを送ると、Claude Code が各メンバーのペルソナに基づいてAI返信します。
複数メンバーが並列で動作し、ルームごとの会話モードやルーム別口調に対応しています。

## システム構成

```
ChatWork → API Gateway → Lambda → SQS → ポーラー(Win PC) → Claude Code → ChatWork
```

## フォルダ構成

```
ClaudeWorkMulti/
├── README.md                  # このファイル（目次）
├── poller_client/             # ポーラークライアント（Windows PC で実行）
│   ├── README.md              # ポーラーの詳細ドキュメント
│   ├── QUICKSTART.md          # クイックスタートガイド
│   ├── windows_poller.py      # メインスクリプト
│   ├── start_poller.bat       # 起動スクリプト
│   ├── setup_windows.bat      # 初回セットアップ
│   ├── check_claude_task.bat  # Claudeプロセスチェッカー
│   ├── config.env.example     # グローバル設定テンプレート
│   └── members/               # AIメンバー設定
│       ├── 00_common_rules.md # 全メンバー共通ルール
│       ├── templates/         # テンプレート
│       └── XX_name/           # 各メンバーフォルダ（member.env, persona, mode等）
└── infra/                     # AWSインフラ設定ガイド
    └── README.md              # SQS, Lambda, API Gateway, IAM, Webhook の構築手順
```

## ドキュメント

| ドキュメント | 内容 |
|-------------|------|
| [poller_client/QUICKSTART.md](poller_client/QUICKSTART.md) | 最短手順で動かすガイド（初回セットアップ + メンバー追加） |
| [poller_client/README.md](poller_client/README.md) | ポーラーの全機能・設定・トラブルシューティング |
| [infra/README.md](infra/README.md) | AWSインフラ構築手順（SQS, Lambda, API Gateway, IAM, Webhook） |

## 主な機能

- **複数AIメンバー対応** — フォルダ追加だけでメンバー追加（コード変更不要）
- **並列処理** — メンバーごとに別スレッドで Claude Code を実行
- **会話モード** — メンテナンス / 業務 / ペルソナ / ペルソナ+ の4種類
- **ルーム別設定** — ルームごとに口調・会話モードを切り替え
- **AI同士の会話** — 人間が起点で開始、設定ターン数で自動停止
- **フォローアップ** — 「確認します」系の返答を自動検知し情報収集→再返信
- **メンテナンスコマンド** — `/status` `/session` でClaude未使用で即応答
- **安全停止** — Ctrl+C で処理中のメッセージ完了後に終了、子プロセス自動kill
- **ルームホワイトリスト** — 許可ルーム以外はClaude起動せず拒否ログ記録
