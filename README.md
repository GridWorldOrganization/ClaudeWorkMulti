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
| `USE_DIRECT_API=0`（デフォルト） | **Claude Code CLI** | `claude -p` コマンドを subprocess で実行。CLAUDE.md やツール連携が必要な場合に使用 |
| `USE_DIRECT_API=1` | **Anthropic API 直接呼び出し** | Python `anthropic` ライブラリで Messages API を直接呼び出し。高速だが従量課金（APIキー必要） |

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

## 処理フローと排他制御

### 処理フロー

1回のポーリングサイクルは3フェーズで構成されます。

```
フェーズ1: SQSキューからメッセージを全件読み込み（空になるまでループ）
    ↓
フェーズ2: 宛先メンバーごとにメッセージをグループ化
    ↓
フェーズ3: メンバーごとにスレッドを起動し並列処理
    ↓
全スレッド完了待ち → 次のサイクルへ
```

### 同一メンバーの多重実行防止

AI が実行中に新しいメッセージが届いても、同一メンバーの AI が同時に複数起動されることはありません。
3層の防御で保護されています。

| 防御層 | 仕組み | 対象 |
|--------|--------|------|
| **join 待ち** | 全スレッドの完了を待ってから次のポーリングに進む | サイクル間 |
| **メンバー排他ロック** | メンバーごとに `threading.Lock` を持ち、同一メンバーは1スレッドしか実行できない | スレッド間 |
| **バッチグループ化** | 同一サイクル内で同じメンバー宛のメッセージは1つにまとめて処理 | サイクル内 |

### 異なるメンバー間

異なるメンバー（例: 横田と藤野）は**意図的に並列実行**されます。それぞれ別スレッド・別ロックで独立して動作します。

## フォルダ構成

```
ClaudeWorkMulti/
├── README.md                  # このファイル（目次）
├── QUICKSTART.md              # クイックスタートガイド
├── windows_poller.py          # ポーラーメインスクリプト（Windows PC で実行）
├── start_poller.bat           # 起動スクリプト
├── setup_windows.bat          # 初回セットアップ
├── check_claude_task.bat      # Claudeプロセスチェッカー（Native/npm判定）
├── check_gws.bat              # Google Workspace API 接続テスト
├── kill_zombie.bat            # ゾンビプロセス検出・削除ツール
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
| `DEBUG_NOTICE_ENABLED` | — | `1` | デバッグ通知の有効/無効（1=有効, 0=無効） |
| `DEBUG_NOTICE_CHATWORK_TOKEN` | — | — | デバッグ通知用ChatWork APIトークン |
| `DEBUG_NOTICE_CHATWORK_ROOM_ID` | — | — | デバッグ通知先ChatWorkルームID（コマンドもこのルームで受付） |
| `USE_DIRECT_API` | — | `0` | AI呼び出し方式（0=Claude Code CLI, 1=API直接・従量課金） |
| `ANTHROPIC_API_KEY` | ※ | — | Anthropic APIキー（※ `USE_DIRECT_API=1` 時は必須） |
| `CLAUDE_MODEL` | — | `claude-haiku-4-5` | 使用モデル |
| `CLAUDE_COMMAND` | — | `claude` | Claude Code CLIパス（`USE_DIRECT_API=0` 時のみ） |
| `SQS_WAIT_TIME_SECONDS` | — | `1` | SQSポーリング方式（0=ショート, 1-20=ロング） |
| `POLL_INTERVAL` | — | `0.5` | ポーリング間隔・秒（ショートポーリング時のみ有効） |
| `CLAUDE_TIMEOUT` | — | `60` | AI応答タイムアウト・秒 |
| `FOLLOWUP_WAIT_SECONDS` | — | `30` | AIが「確認します」等と返信した場合、この秒数待ってからルーム情報を収集し再返信する |
| `MAX_AI_CONVERSATION_TURNS` | — | `10` | AI同士の会話の最大ターン数 |
| `REPLY_COOLDOWN_SECONDS` | — | `15` | 連投防止クールダウン・秒。同一メンバーが前回返信してからこの秒数経過するまで次の返信を待機する |
| `CHATWORK_API_TIMEOUT` | — | `30` | ChatWork API 呼び出しのタイムアウト・秒（コード内定数） |
| `GOOGLE_EMAIL` | — | — | Google アカウントのメールアドレス |
| `GOOGLE_OAUTH_CLIENT_ID` | — | — | Google OAuth クライアント ID |
| `GOOGLE_OAUTH_CLIENT_SECRET` | — | — | Google OAuth クライアントシークレット |
| `GOOGLE_DRIVE_INCLUDE_MY_DRIVE` | — | `0` | マイドライブを参照対象にするか（0=しない, 1=する） |
| `GOOGLE_DRIVE_INCLUDE_SHARED` | — | `1` | 共有ドライブを参照対象にするか（0=しない, 1=する） |

## 主な機能

- **複数AIメンバー対応** — フォルダ追加だけでメンバー追加（コード変更不要）
- **並列処理** — メンバーごとに別スレッドで並列実行
- **API直接呼び出し** — Anthropic API に切り替えて高速応答も可能（従量課金）
- **会話モード** — メンテナンス / 業務 / ペルソナ / ペルソナ+ の4種類
- **ルーム別設定** — ルームごとに口調・会話モードを切り替え
- **AI同士の会話** — 人間が起点で開始、設定ターン数で自動停止
- **フォローアップ** — 「確認します」系の返答を自動検知し情報収集→再返信
- **メンテナンスコマンド** — `/status` `/session` でAI未使用で即応答
- **安全停止** — Ctrl+C で処理中のメッセージ完了後に終了、子プロセス自動kill
- **ルームホワイトリスト** — 許可ルーム以外はAI起動せず拒否ログ記録
- **SQSポーリング切り替え** — ショート/ロングポーリングを設定で切り替え（コスト最適化）

## ユーティリティツール

| バッチ | 説明 |
|--------|------|
| `start_poller.bat` | ポーラー起動（多重起動チェック付き） |
| `setup_windows.bat` | 初回セットアップ（依存パッケージ・AWS・Google API） |
| `check_claude_task.bat` | 実行中のClaudeプロセス一覧表示（Native/npm判定） |
| `check_gws.bat` | Google Workspace API 接続テスト（OAuth認証 + CRUD検証） |
| `kill_zombie.bat` | ゾンビプロセス検出・削除（ポーラーが起動したプロセスのみ） |
| `kill_zombie.bat --all` | 上記に加え、全Claudeプロセス（Native/npm）も表示・削除対象に含める |

### kill_zombie.bat の使い方

**通常モード**（ダブルクリック）: ポーラーが起動したプロセスのみ検出・削除

```
kill_zombie.bat
```

**--all モード**: ポーラー管轄外のClaude プロセス（手動起動のclaude.exe / node.exe 等）も一覧表示し、削除対象に含める

```
kill_zombie.bat --all
```

## 必要な環境

### 必須

| 項目 | 備考 |
|------|------|
| **Windows PC** | ポーラー実行環境（常時稼働） |
| **Python 3.12+** | ポーラー本体の実行に必要 |
| **AWS CLI v2** | SQS アクセス用（プロファイル設定） |
| **AWS SQS キュー** | ChatWork → Lambda → SQS の構成が必要（[infra/README.md](infra/README.md) 参照） |
| **ChatWork アカウント** | AIメンバー用アカウント（APIトークン発行が必要） |

### AI 呼び出し（どちらか一方）

| 方式 | 必要なもの |
|------|-----------|
| **Anthropic API 直接**（従量課金） | Anthropic API キー（[console.anthropic.com](https://console.anthropic.com/)） |
| **Claude Code CLI** | Node.js + Claude Code（`npm install -g @anthropic-ai/claude-code`） |

### オプション

| 項目 | 備考 |
|------|------|
| Google Workspace API | スプレッドシート連携用。OAuth クライアント ID/Secret が必要（[Google Cloud Console](https://console.cloud.google.com/)） |

### Python パッケージ

`setup_windows.bat` で自動インストールされます。

```
pip install boto3 requests anthropic google-api-python-client google-auth-httplib2 google-auth-oauthlib
```
