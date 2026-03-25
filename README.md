# ChatWork Webhook Client

ChatWork のメッセージを SQS 経由で受信し、Claude Code を使って AI が自動返信するシステムです。
Windows PC 上で動作します。

> **注意: ポーラーは必ず1台のPCでのみ起動してください。** 2台同時に動くと同じメッセージを二重処理します。

## 構成

```
ChatWorkWebHookClient/
├── windows_poller.py          # メイン
├── start_poller.bat           # 起動スクリプト
├── setup_windows.bat          # 初回セットアップ
├── config.env                 # グローバル設定（※Gitに含まれない）
├── config.env.example         # ↑のテンプレート
├── members/
│   ├── 00_common_rules.md     # 全メンバー共通ルール（Gitに含まれる）
│   ├── templates/             # テンプレート（Gitに含まれる）
│   │   ├── 01_persona.md.example
│   │   ├── member.env.example
│   │   ├── mode.env.example
│   │   └── setup_member.bat
│   ├── 01_yamada/             # メンバーフォルダ（※Gitに含まれない）
│   │   ├── member.env         # メンバー固有設定（名前・ID・トークン・許可ルーム）
│   │   ├── 01_persona.md      # ペルソナ設定
│   │   ├── mode.env           # 会話モード設定
│   │   ├── CLAUDE.md          # Claude自動読み込み記憶（任意）
│   │   ├── room_*.md          # ルーム別口調設定（任意）
│   │   ├── chat_history_*.md  # 会話記録（自動生成）
│   │   └── rejected_rooms.log # 拒否ログ（自動生成）
│   └── 02_suzuki/             # 別のメンバー（同構成）
└── .gitignore
```

## セットアップ手順

### 1. リポジトリを取得

```
git clone https://github.com/GridWorldOrganization/ChatWorkWebHookClient
cd ChatWorkWebHookClient
```

### 2. config.env を作成

`config.env.example` をコピーして `config.env` を作成。**グローバル設定のみ**を記載。

```
copy config.env.example config.env
```

メンバー固有の設定（トークン等）はここには書きません。各メンバーフォルダの `member.env` に書きます。

### 3. 初回セットアップ

`setup_windows.bat` をダブルクリック。Python / pip / Claude Code / AWS CLI を自動チェック。

### 4. メンバーフォルダを作成

```
cd members\templates
setup_member.bat
```

フォルダ名（例: `01_yamada`）を入力するとフォルダが作成されます。

### 5. member.env を設定

各メンバーフォルダに `member.env` を作成（テンプレート: `members/templates/member.env.example`）。

```env
NAME=山田 太郎
ACCOUNT_ID=12345678
CW_TOKEN=（ChatWork APIトークン）
ALLOWED_ROOMS=426936385,427388771
```

| キー | 必須 | 説明 |
|------|------|------|
| `NAME` | 必須 | メンバーの表示名 |
| `ACCOUNT_ID` | 必須 | ChatWork アカウントID |
| `CW_TOKEN` | 必須 | ChatWork APIトークン |
| `ALLOWED_ROOMS` | 任意 | 許可ルームID（カンマ区切り。空=全ルーム許可） |

### 6. ペルソナを設定

`01_persona.md` にキャラクター設定を記入。詳細は `members/templates/01_persona.md.example`。

### 7. 会話モードを設定

`mode.env` を作成。詳細は「会話モード」セクション参照。

### 8. 起動

`start_poller.bat` をダブルクリック。

**停止方法:** Ctrl+C（処理中のメッセージ完了後に安全停止）。

## 動作の仕組み

### 基本フロー

1. SQS キューを**空になるまで全件読み込み**
2. 宛先メンバーごとにメッセージをグループ化（自分自身のメッセージは自動除外）
3. メンバーごとに**並列**で Claude Code（`claude -p --model`）を実行して返信
4. Chatwork API 経由で返信（`[rp]` タグ自動付与）
5. 会話記録を `chat_history_{ルームID}.md` に保存

### 特殊処理

| 機能 | 動作 |
|------|------|
| **[rp]タグ自動付与** | AI出力に`[To:]`や`[rp]`がなければ自動付与 |
| **[To:]自発発言** | AIが送信者以外に話しかける場合 `[To:]` を出力可能 |
| **フォローアップ** | 「確認します」等のキーワード検出 → 待機 → 情報収集 → 再返信 |
| **AI会話チェーン** | 人間の発言で開始、`MAX_AI_CONVERSATION_TURNS` で自動停止 |
| **連投防止** | 同一メンバーは `REPLY_COOLDOWN_SECONDS` 秒待機 |
| **ルーム別口調** | `room_{ルームID}.md` で指示を追加読み込み |
| **CLAUDE.md** | メンバーフォルダに置くとClaude Codeが自動読み込み |
| **許可外ルーム拒否** | ホワイトリスト外はClaude起動せず `rejected_rooms.log` に記録 |

## グローバル設定（config.env）

| パラメータ | デフォルト | 説明 |
|-----------|-----------|------|
| `SQS_QUEUE_URL` | (必須) | SQSキューURL |
| `CW_TOKEN_GURIKO` | (必須) | グリ姉トークン（エラー報告用） |
| `CW_ERROR_ROOM_ID` | (必須) | エラー報告先ルームID |
| `AWS_PROFILE` | (なし) | AWSプロファイル名 |
| `CLAUDE_MODEL` | claude-haiku-4-5 | モデル（haiku/sonnet/opus） |
| `CLAUDE_COMMAND` | claude | Claude Codeのパス |
| `CLAUDE_TIMEOUT` | 60 | 実行タイムアウト（秒） |
| `FOLLOWUP_WAIT_SECONDS` | 30 | フォローアップ待機（秒） |
| `MAX_AI_CONVERSATION_TURNS` | 10 | AI同士の会話上限 |
| `REPLY_COOLDOWN_SECONDS` | 15 | 連投防止クールダウン（秒） |
| `MAINTENANCE_ROOM_ID` | (空) | メンテナンスコマンド受付ルーム |

## 会話モード

| モード | 名前 | 説明 |
|--------|------|------|
| 0 | メンテナンス | 機械的。改行禁止。1行回答。絵文字禁止 |
| 1 | 業務 | 端的に短く。丁寧語。1〜3行 |
| 2 | ペルソナ | ペルソナ準拠。感情豊か |
| 3 | ペルソナ+ | ペルソナ＋ルーム内の他メンバーに話を振る |

### 設定（メンバーフォルダの mode.env）

```env
TALK_MODE=2
TALK_MODE=426936385:3
TALK_MODE=427388771:1
```

**優先順位:** ルーム別(ルームID:モード) > デフォルト(TALK_MODE) > 1(業務)

## メンテナンスコマンド

`MAINTENANCE_ROOM_ID` で指定したルームで使用。Claudeは起動しません。

| コマンド | 説明 |
|---------|------|
| `/status` | メンバーの設定状況 |
| `/session` | 全メンバーのClaude実行状態（実行中/停止中、モデル名） |

## メンバーの追加方法

1. `members/templates/setup_member.bat` でフォルダ作成
2. `member.env` にNAME, ACCOUNT_ID, CW_TOKEN, ALLOWED_ROOMS を設定
3. `01_persona.md` にキャラクター設定を記入
4. `mode.env` に会話モードを設定
5. ポーラー再起動（コードの変更は不要。フォルダを自動検出します）

## 前提条件

- Windows 10/11
- Python 3.x
- Claude Code（`claude` コマンド）

## トラブルシューティング

| 症状 | 対処 |
|------|------|
| 「メンバーが1人も見つかりません」 | `member.env` が作成されていない |
| 「CW_TOKEN in ...」 | `member.env` にCW_TOKENが未設定 |
| 返信が来ない | `CLAUDE_TIMEOUT` を増やす |
| 二重返信 | 1台のみで起動しているか確認 |
| AI同士が止まらない | `MAX_AI_CONVERSATION_TURNS` を下げる |
| `Claude Code が見つかりません` | config.envに `CLAUDE_COMMAND=フルパス` を設定 |
| Ctrl+Cで停止しない | 処理完了を待っています。しばらく待機 |

### PATHの設定

```powershell
where.exe claude
[Environment]::SetEnvironmentVariable("Path", [Environment]::GetEnvironmentVariable("Path", "User") + ";C:\Users\ユーザー名\AppData\Roaming\npm", "User")
```

ウィンドウを開き直して再実行。または `config.env` に `CLAUDE_COMMAND=フルパス` で指定。
