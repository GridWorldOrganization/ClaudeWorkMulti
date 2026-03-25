# ClaudeWorkMulti

ChatWork × Claude（Anthropic API / Claude Code）による複数AIメンバー自動返信システム。

ChatWorkでメンバー宛にメッセージを送ると、各メンバーのペルソナに基づいてAI返信します。
複数メンバーが並列で動作し、ルームごとの会話モードやルーム別口調に対応しています。

## システム構成

```
ChatWork → API Gateway → Lambda → SQS → ポーラー(Win PC) → Anthropic API → ChatWork
                                                            or Claude Code CLI
```

## AI呼び出し方式

`config.env` の `USE_DIRECT_API` で切り替えられます。

| 設定 | 方式 | 特徴 |
|------|------|------|
| `USE_DIRECT_API=1`（デフォルト・推奨） | **Anthropic API 直接呼び出し** | Python `anthropic` ライブラリで Messages API を直接呼び出し。Node.js の起動オーバーヘッド（2〜5秒/回）がなく**高速** |
| `USE_DIRECT_API=0` | **Claude Code CLI** | `claude -p` コマンドを subprocess で実行する従来方式。CLAUDE.md やツール連携が必要な場合に使用 |

### 必要な設定

**API 直接（`USE_DIRECT_API=1`）の場合：**

```env
USE_DIRECT_API=1
ANTHROPIC_API_KEY=sk-ant-api03-...   # https://console.anthropic.com/ で発行
CLAUDE_MODEL=claude-haiku-4-5        # 使用モデル
```

**Claude Code CLI（`USE_DIRECT_API=0`）の場合：**

```env
USE_DIRECT_API=0
CLAUDE_COMMAND=claude                 # claude コマンドのパス（省略可）
CLAUDE_MODEL=claude-haiku-4-5        # 使用モデル
```

## SQS ポーリング方式

`config.env` の `SQS_WAIT_TIME_SECONDS` で切り替えられます。

| 設定 | 方式 | 特徴 |
|------|------|------|
| `SQS_WAIT_TIME_SECONDS=0` | **ショートポーリング** | 即時応答。`POLL_INTERVAL` 秒間隔でループ。SQSリクエスト数が多くコスト高 |
| `SQS_WAIT_TIME_SECONDS=1〜20`（デフォルト: 1） | **ロングポーリング** | 指定秒数までSQS側で待機。メッセージが届いた時点で即返答。SQSコスト大幅削減 |

ロングポーリング時は `POLL_INTERVAL` は無効になります（SQS の Wait がインターバルの代わり）。

## フォルダ構成

```
ClaudeWorkMulti/
├── README.md                  # このファイル（目次）
├── QUICKSTART.md              # クイックスタートガイド
├── windows_poller.py          # ポーラーメインスクリプト（Windows PC で実行）
├── start_poller.bat           # 起動スクリプト
├── setup_windows.bat          # 初回セットアップ
├── check_claude_task.bat      # Claudeプロセスチェッカー
├── config.env.example         # グローバル設定テンプレート
├── members/                   # AIメンバー設定
│   ├── 00_common_rules.md     # 全メンバー共通ルール
│   ├── templates/             # テンプレート
│   └── XX_name/               # 各メンバーフォルダ（member.env, persona, mode等）
└── infra/                     # AWSインフラ設定ガイド
    └── README.md              # SQS, Lambda, API Gateway, IAM, Webhook の構築手順
```

## ドキュメント

| ドキュメント | 内容 |
|-------------|------|
| [QUICKSTART.md](QUICKSTART.md) | 最短手順で動かすガイド（初回セットアップ + メンバー追加） |
| [infra/README.md](infra/README.md) | AWSインフラ構築手順（SQS, Lambda, API Gateway, IAM, Webhook） |

## 設定一覧（config.env）

| 変数名 | 必須 | デフォルト | 説明 |
|--------|------|-----------|------|
| `AWS_PROFILE` | 必須 | — | AWSプロファイル名 |
| `SQS_QUEUE_URL` | 必須 | — | SQS キューURL |
| `CHATWORK_API_TOKEN_ERROR_REPORTER` | 必須 | — | エラー報告用ChatWork APIトークン |
| `CHATWORK_ERROR_ROOM_ID` | 必須 | — | エラー報告先ChatWorkルームID |
| `USE_DIRECT_API` | — | `1` | AI呼び出し方式（1=API直接, 0=CLI） |
| `ANTHROPIC_API_KEY` | ※ | — | Anthropic APIキー（※ `USE_DIRECT_API=1` 時は必須） |
| `CLAUDE_MODEL` | — | `claude-haiku-4-5` | 使用モデル |
| `CLAUDE_COMMAND` | — | `claude` | Claude Code CLIパス（`USE_DIRECT_API=0` 時のみ） |
| `SQS_WAIT_TIME_SECONDS` | — | `1` | SQSポーリング方式（0=ショート, 1-20=ロング） |
| `POLL_INTERVAL` | — | `0.5` | ポーリング間隔・秒（ショートポーリング時のみ有効） |
| `CLAUDE_TIMEOUT` | — | `60` | AI応答タイムアウト・秒 |
| `FOLLOWUP_WAIT_SECONDS` | — | `30` | フォローアップ待機時間・秒 |
| `MAX_AI_CONVERSATION_TURNS` | — | `10` | AI同士の会話の最大ターン数 |
| `REPLY_COOLDOWN_SECONDS` | — | `15` | 連投防止クールダウン・秒 |
| `MAINTENANCE_ROOM_ID` | — | — | メンテナンスルームID |

## 主な機能

- **複数AIメンバー対応** — フォルダ追加だけでメンバー追加（コード変更不要）
- **並列処理** — メンバーごとに別スレッドで並列実行
- **API直接呼び出し** — Anthropic API を直接利用し高速応答（CLI方式にも切り替え可能）
- **会話モード** — メンテナンス / 業務 / ペルソナ / ペルソナ+ の4種類
- **ルーム別設定** — ルームごとに口調・会話モードを切り替え
- **AI同士の会話** — 人間が起点で開始、設定ターン数で自動停止
- **フォローアップ** — 「確認します」系の返答を自動検知し情報収集→再返信
- **メンテナンスコマンド** — `/status` `/session` でAI未使用で即応答
- **安全停止** — Ctrl+C で処理中のメッセージ完了後に終了、子プロセス自動kill
- **ルームホワイトリスト** — 許可ルーム以外はAI起動せず拒否ログ記録
- **SQSポーリング切り替え** — ショート/ロングポーリングを設定で切り替え（コスト最適化）
