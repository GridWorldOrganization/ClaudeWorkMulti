# ClaudeWorkMulti

![Python](https://img.shields.io/badge/Python-3.12+-blue)
![License](https://img.shields.io/badge/License-MIT-green)
![Platform](https://img.shields.io/badge/Platform-Windows-lightgrey)
![Claude](https://img.shields.io/badge/AI-Claude_(Anthropic)-orange)

> **Multi-AI persona generation system for ChatWork, powered by Claude (Anthropic API / Claude Code CLI).**
> Each AI member has its own persona, conversation mode, and room-specific settings.
> Messages are received via AWS SQS and processed in parallel threads per member.

ChatWork × Claude（Anthropic API / Claude Code）による複数AI人格生成システム。

ChatWorkでメンバー宛にメッセージを送ると、各メンバーのペルソナに基づいてAI返信します。
複数メンバーが並列で動作し、ルームごとの会話モードやルーム別口調に対応しています。

## デモ

<!-- TODO: スクリーンショットを挿入 -->
<!-- ![Demo](docs/images/demo.png) -->
<!-- ChatWork上でのAI返信の様子を貼る -->

## システム構成

```
ChatWork → API Gateway → Lambda → SQS → ポーラー(Win PC) → Claude Code CLI → ChatWork
                                                            or Anthropic API
```

## 主な機能

- **複数AIメンバー対応** — フォルダ追加だけでメンバー追加（コード変更不要）
- **並列処理** — メンバーごとに別スレッドで並列実行
- **API直接呼び出し** — Anthropic API に切り替えて高速応答も可能（従量課金）
- **会話モード** — ログ / 業務 / ペルソナ / ペルソナ+ / 反抗期 の5種類（ログモードでは短い挨拶メッセージを自動スキップ）
- **ルーム別設定** — ルームごとに口調・会話モードを切り替え
- **Google Workspace連携** — メッセージ内のスプレッドシート/ドキュメント/スライドURLを自動取得してAIに渡す
- **AI同士の会話** — 人間が起点で開始、設定ターン数で自動停止
- **フォローアップ** — 「確認します」系の返答を自動検知し情報収集→再返信
- **コマンド** — `/status` `/session` `/sysinfo` `/bill` `/talk` `/gws` でAI未使用で即応答
- **安全停止** — Ctrl+C / ウィンドウ×ボタン / ログオフ時に子プロセスを自動クリーンアップ
- **ルームホワイトリスト** — 許可ルーム以外はAI起動せず拒否ログ記録

## ドキュメント

| ドキュメント | 内容 |
|-------------|------|
| [QUICKSTART.md](QUICKSTART.md) | 最短手順で動かすガイド |
| [docs/architecture.md](docs/architecture.md) | 処理フロー・排他制御・クリーンアップ・ポーリング方式・URL自動検出 |
| [docs/commands.md](docs/commands.md) | ChatWorkコマンド・会話モード・バッチツール一覧 |
| [infra/README.md](infra/README.md) | AWSインフラ構築手順（SQS, Lambda, API Gateway, IAM, Webhook） |
| [CHANGELOG.md](CHANGELOG.md) | 変更履歴 |
| [CONTRIBUTING.md](CONTRIBUTING.md) | コントリビューションガイド |

## フォルダ構成

```
ClaudeWorkMulti/
├── README.md                  # このファイル
├── LICENSE                    # MIT License
├── CHANGELOG.md               # 変更履歴
├── CONTRIBUTING.md            # コントリビューションガイド
├── QUICKSTART.md              # クイックスタートガイド
├── windows_poller.py          # エントリポイント（start_poller.bat から呼出）
├── poller/                    # Python パッケージ
│   ├── config.py              # 設定・定数・メンバー検出
│   ├── state.py               # グローバル状態（ロック付き）
│   ├── chatwork.py            # ChatWork API ヘルパー
│   ├── ai_runner.py           # AI実行（API直接 / CLI）・PID管理
│   ├── google_workspace.py    # Google URL検出・内容取得
│   ├── commands.py            # /status /sysinfo /bill /gws /talk ハンドラ
│   ├── processor.py           # メッセージ処理・バッチ処理
│   └── main.py                # メインループ・起動チェック
├── check_gws.py               # Google Workspace API チェッカー
├── start_poller.bat           # 起動（自動ゾンビkill + 再起動）
├── setup_windows.bat          # 初回セットアップ
├── check_task.bat      # プロセスチェッカー（ポーラー + Claude Native/npm）
├── check_gws.bat              # Google Workspace API 接続テスト
├── kill_zombie.bat            # ゾンビプロセス検出・削除（--all で全Claude対象）
├── config.env.example         # グローバル設定テンプレート
├── members/                   # AIメンバー設定
│   ├── 00_common_rules.md     # 全メンバー共通ルール
│   ├── templates/             # テンプレート
│   └── XX_name/               # 各メンバーフォルダ
├── docs/                      # 技術ドキュメント
│   ├── architecture.md        # アーキテクチャ詳細
│   └── commands.md            # コマンド・ツールリファレンス
└── infra/                     # AWSインフラ設定ガイド
    └── README.md
```

## 必要な環境

| 項目 | 備考 |
|------|------|
| **Windows PC** | ポーラー実行環境（常時稼働） |
| **Python 3.12+** | ポーラー本体の実行に必要 |
| **AWS CLI v2** | SQS アクセス用 |
| **ChatWork アカウント** | AIメンバー用（APIトークン発行が必要） |
| **Claude Code CLI** または **Anthropic API キー** | AI呼び出し用（どちらか一方） |
| Google Workspace API（オプション） | スプレッドシート連携用 |

## 設定一覧（config.env）

| 変数名 | デフォルト | 説明 |
|--------|-----------|------|
| `SQS_QUEUE_URL` | (必須) | SQS キューURL |
| `AWS_PROFILE` | (必須) | AWSプロファイル名 |
| `USE_DIRECT_API` | `0` | AI呼び出し方式（0=CLI, 1=API直接・従量課金） |
| `CLAUDE_MODEL` | `claude-haiku-4-5` | 使用モデル |
| `CLAUDE_COMMAND` | `claude` | Claude Code CLIパス（CLI時のみ） |
| `CLAUDE_TIMEOUT` | `60` | AI応答タイムアウト・秒 |
| `ANTHROPIC_API_KEY` | — | Anthropic APIキー（API直接時のみ必須） |
| `SQS_WAIT_TIME_SECONDS` | `1` | SQSポーリング（0=ショート, 1-20=ロング） |
| `POLL_INTERVAL` | `0.5` | ポーリング間隔・秒 |
| `FOLLOWUP_WAIT_SECONDS` | `30` | フォローアップ待機・秒 |
| `MAX_AI_CONVERSATION_TURNS` | `10` | AI同士の最大ターン数 |
| `REPLY_COOLDOWN_SECONDS` | `15` | 連投防止クールダウン・秒 |
| `DEBUG_NOTICE_ENABLED` | `1` | デバッグ通知（1=有効, 0=無効） |
| `DEBUG_NOTICE_CHATWORK_TOKEN` | — | デバッグ通知用ChatWork APIトークン |
| `DEBUG_NOTICE_CHATWORK_ROOM_ID` | — | デバッグ通知先ルームID（コマンドもここで受付） |
| `DEBUG_NOTICE_CHATWORK_ACCOUNT_ID` | — | デバッグ通知専用メンバーのアカウントID（このメンバー宛のコマンドのみ受付） |
| `GOOGLE_EMAIL` | — | Google アカウント |
| `GOOGLE_OAUTH_CLIENT_ID` | — | OAuth クライアント ID |
| `GOOGLE_OAUTH_CLIENT_SECRET` | — | OAuth クライアントシークレット |
| `GOOGLE_DRIVE_INCLUDE_MY_DRIVE` | `0` | マイドライブ参照（0=しない, 1=する） |
| `GOOGLE_DRIVE_INCLUDE_SHARED` | `1` | 共有ドライブ参照（0=しない, 1=する） |

## License

[MIT License](LICENSE)

## Contributing

[CONTRIBUTING.md](CONTRIBUTING.md) を参照してください。
