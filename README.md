# ChatWork Webhook Client

ChatWork のメッセージを SQS 経由で受信し、Claude Code を使って AI が自動返信するシステムです。
Windows PC 上で動作します。

> **注意: ポーラーは必ず1台のPCでのみ起動してください。** 2台同時に動くと同じメッセージを二重処理します。
> **注意: 起動時にSQSキューが自動パージされます。** 起動前にキューに溜まっていたメッセージは破棄されます。

## システム全体構成

```
ChatWork                    AWS                              Windows PC
┌──────────┐   Webhook    ┌──────────────┐   POST    ┌──────────────────┐
│ ユーザー  │──[To:メンバー]──▶│ API Gateway  │─────────▶│ Lambda           │
│ が発言    │              │ (prod)       │          │ chatwork-webhook │
└──────────┘              └──────────────┘          │ -handler         │
                                                     └────────┬─────────┘
                                                              │ SQS送信
                                                     ┌────────▼─────────┐
                                                     │ SQS Queue        │
                                                     │ chatwork-webhook │
                                                     │ -queue           │
                                                     └────────┬─────────┘
                                                              │ ポーリング
┌──────────┐   API返信    ┌──────────────────────────────────▼─────────┐
│ ChatWork  │◀────────────│ windows_poller.py                         │
│ ルーム    │              │   → Claude Code (claude -p --model)       │
└──────────┘              │   → Chatwork API で返信投稿               │
                           └──────────────────────────────────────────┘
```

### AWSリソース

| リソース | 名前 | 備考 |
|---------|------|------|
| API Gateway | `chatwork-webhook-api` (ID: `284bdrbap0`) | ステージ: `prod` |
| Lambda | `chatwork-webhook-handler` | Python 3.12 |
| SQS | `chatwork-webhook-queue` | 標準キュー。保持期間14日 |

### Webhook URL

```
https://284bdrbap0.execute-api.ap-northeast-1.amazonaws.com/prod
```

各メンバーのChatWorkアカウントで、このURLをWebhookとして登録する。

## ファイル構成

```
ChatWorkWebHookClient/
├── windows_poller.py          # メイン
├── start_poller.bat           # 起動スクリプト
├── setup_windows.bat          # 初回セットアップ
├── config.env                 # グローバル設定（※Gitに含まれない）
├── config.env.example         # ↑のテンプレート
├── webhook_poller.log         # 実行ログ（自動生成）
├── members/
│   ├── 00_common_rules.md     # 全メンバー共通ルール（Gitに含まれる）
│   ├── templates/             # テンプレート（Gitに含まれる）
│   │   ├── 01_persona.md.example
│   │   ├── member.env.example
│   │   ├── mode.env.example
│   │   └── setup_member.bat
│   ├── 01_yamada/             # メンバーフォルダ例（※Gitに含まれない）
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

## 前提条件

- Windows 10/11
- Python 3.x（pip含む。boto3, requests を自動インストール）
- Claude Code（`claude` コマンド。npm版: `npm install -g @anthropic-ai/claude-code`、またはネイティブ版: `claude install`）
- AWS CLI v2（`setup_windows.bat` でインストール案内あり。直接キー認証なら不要）
- Node.js / npm（Claude Code のnpm版インストールに必要。ネイティブ版なら不要）

## セットアップ手順

### 1. リポジトリを取得

```
git clone https://github.com/GridWorldOrganization/ChatWorkWebHookClient
cd ChatWorkWebHookClient
```

### 2. config.env を作成

```
copy config.env.example config.env
```

`config.env` にはグローバル設定のみ記載。メンバー固有設定はここには書きません。

> **config.env にはAPIトークン等の機密情報が入ります。Gitにコミットしないでください。**（.gitignore済み）

### 3. 初回セットアップ

`setup_windows.bat` をダブルクリック。以下を自動チェック：

1. Python 確認
2. pip パッケージインストール（boto3, requests）
3. Claude Code 確認（PATHが通っているか）
4. AWS CLI 確認（未インストールならダウンロードURL表示）
5. AWS プロファイル設定（config.envのAWSキーを使用）

### 4. メンバーフォルダを作成

```
cd members\templates
setup_member.bat
```

フォルダ名（例: `01_yamada`）を入力。命名規則: `番号_名前`（番号2桁）。

### 5. member.env を設定

各メンバーフォルダに `member.env` を作成（テンプレート: `members/templates/member.env.example`）。

```env
NAME=山田 太郎
ACCOUNT_ID=12345678
CW_TOKEN=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
ALLOWED_ROOMS=426936385,427388771
```

| キー | 必須 | 説明 |
|------|------|------|
| `NAME` | 必須 | メンバーの表示名 |
| `ACCOUNT_ID` | 必須 | ChatWork アカウントID |
| `CW_TOKEN` | 必須 | ChatWork APIトークン |
| `ALLOWED_ROOMS` | 任意 | 許可ルームID（カンマ区切り。空=全ルーム許可） |

### 6. ペルソナを設定

`01_persona.md` にキャラクター設定を記入。テンプレート: `members/templates/01_persona.md.example`。

### 7. 会話モードを設定

`mode.env` を作成。テンプレート: `members/templates/mode.env.example`。詳細は「会話モード」セクション。

### 8. 起動

`start_poller.bat` をダブルクリック。

起動ログ例：
```
=== Chatwork Webhook Poller 起動 ===
=== config.env パラメータ ===
  CLAUDE_COMMAND=claude
  CLAUDE_MODEL=claude-haiku-4-5
  CLAUDE_TIMEOUT=60秒
  FOLLOWUP_WAIT_SECONDS=30秒
  MAX_AI_CONVERSATION_TURNS=10ターン
  REPLY_COOLDOWN_SECONDS=15秒
  山田 太郎 (01_yamada): 指示ファイル 1件, cwd=...\members\01_yamada, 許可ルーム=[...]
```

**停止方法:** Ctrl+C または SIGTERM で安全停止（処理中のメッセージ完了後に終了）。

## 動作の仕組み

### 基本フロー

1. SQS キューを**空になるまで全件読み込み**
2. 宛先メンバーごとにメッセージをグループ化
   - 自分自身のメッセージは自動除外
   - 宛先不明のメッセージは最初のメンバーにフォールバック
3. メンバーごとに**並列**で処理（同一メンバーは排他ロックで直列化）
   - 複数メッセージが溜まっていた場合、先行分を文脈として含め最後の1件に返信
   - `members/00_common_rules.md` + `01_persona.md` + `room_{ルームID}.md`（あれば）をプロンプトに組み込み
   - Claude Code（`claude -p --model {CLAUDE_MODEL}`）を実行
   - プロンプトが約31,000文字を超える場合は自動トランケート（Windows CreateProcess制限）
4. Chatwork API 経由で返信（`[rp]` タグ自動付与）
   - `sender_account_id` が空の場合、Chatwork APIから自動取得して補完
5. 会話記録を `chat_history_{ルームID}.md` に保存

### 特殊処理

| 機能 | 動作 |
|------|------|
| **[rp]タグ自動付与** | AI出力が`[To:]`や`[rp]`で始まらなければ自動付与 |
| **[To:]自発発言** | AIが送信者以外に話しかける場合 `[To:]` を出力可能 |
| **フォローアップ** | 「確認します」等のキーワード検出 → `FOLLOWUP_WAIT_SECONDS` 秒待機 → ルーム情報収集 → 再返信 → 成功時「おやすみなさい」を投稿 |
| **AI会話チェーン** | 人間の発言でカウンタリセット。AI同士は `MAX_AI_CONVERSATION_TURNS` 到達で「そろそろこの辺で！」と投稿して停止 |
| **連投防止** | 同一メンバーは `REPLY_COOLDOWN_SECONDS` 秒待機（ロック外で待機） |
| **ルーム別口調** | `room_{ルームID}.md` があればそのルーム専用の指示を追加読み込み |
| **CLAUDE.md** | メンバーフォルダに置くとClaude Codeが自動読み込み（記憶・指示用。プロンプトには含めない） |
| **許可外ルーム拒否** | ホワイトリスト外はClaude起動せず `rejected_rooms.log` に記録 |

## グローバル設定（config.env）

| パラメータ | デフォルト | 説明 |
|-----------|-----------|------|
| `SQS_QUEUE_URL` | (必須) | SQSキューURL |
| `CW_TOKEN_ERROR` | (必須) | エラー報告アカウントのトークン |
| `CW_ERROR_ROOM_ID` | (必須) | エラー報告先ルームID |
| `AWS_PROFILE` | (なし) | AWSプロファイル名（`setup_windows.bat`で`chatwork-webhook`を作成） |
| `AWS_ACCESS_KEY_ID` | (なし) | AWSアクセスキー（プロファイル未設定時のフォールバック） |
| `AWS_SECRET_ACCESS_KEY` | (なし) | AWSシークレットキー（同上） |
| `CLAUDE_MODEL` | claude-haiku-4-5 | モデル（`claude-haiku-4-5` / `claude-sonnet-4-6` / `claude-opus-4-6`） |
| `CLAUDE_COMMAND` | claude | Claude Codeのパス（PATHで見つからない場合にフルパス指定） |
| `CLAUDE_TIMEOUT` | 60 | Claude Code実行タイムアウト（秒） |
| `FOLLOWUP_WAIT_SECONDS` | 30 | フォローアップ待機（秒） |
| `MAX_AI_CONVERSATION_TURNS` | 10 | AI同士の会話上限メッセージ数 |
| `REPLY_COOLDOWN_SECONDS` | 15 | 同一メンバーの連投防止（秒） |
| `MAINTENANCE_ROOM_ID` | (空) | メンテナンスコマンド受付ルームID |

※ ポーリング間隔は5秒固定（変更不可）

## 会話モード

| モード | 名前 | 説明 |
|--------|------|------|
| 0 | メンテナンス | 機械的。改行禁止。1行回答。絵文字禁止 |
| 1 | 業務 | 端的に短く。丁寧語。1〜3行 |
| 2 | ペルソナ | ペルソナ準拠。感情豊か |
| 3 | ペルソナ+ | ペルソナ＋ルーム内の他メンバーに話を振る（3〜4回に1回） |

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
| `/status` | メンバーの設定状況（.mdファイル一覧、会話モード、パラメータ値） |
| `/session` | 全メンバーのClaude実行状態（実行中/停止中、経過秒数、モデル名） |

## メンバーの追加ガイド

新しいメンバーを追加する手順。**コードの変更は不要です。**

### Step 1: フォルダ作成

```
cd members\templates
setup_member.bat
```

フォルダ名を入力（例: `03_tanaka`）。命名規則: `番号_名前`（番号は2桁、既存と重複しないこと）。

### Step 2: member.env を作成

`members\03_tanaka\member.env` をテキストエディタで作成：

```env
NAME=田中 一郎
ACCOUNT_ID=99999999
CW_TOKEN=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
ALLOWED_ROOMS=426936385
```

- `NAME`: ChatWork上の表示名と一致させる
- `ACCOUNT_ID`: ChatWorkのアカウント設定画面で確認
- `CW_TOKEN`: ChatWorkのAPI設定画面で発行
- `ALLOWED_ROOMS`: 応答を許可するルームIDをカンマ区切り。空なら全ルーム

### Step 3: ペルソナ設定

`members\03_tanaka\01_persona.md` にキャラクター設定を記入。テンプレートからコピー：

```
copy members\templates\01_persona.md.example members\03_tanaka\01_persona.md
```

性格・話し方・趣味・口癖・苦手なものなどを具体的に書くほど自然な会話になります。

### Step 4: 会話モード設定

`members\03_tanaka\mode.env` を作成：

```env
TALK_MODE=2
TALK_MODE=426936385:3
```

### Step 5: ポーラー再起動

`start_poller.bat` を再起動。起動ログに新メンバーが表示されればOK：

```
  田中 一郎 (03_tanaka): 指示ファイル 1件, cwd=...\members\03_tanaka, 許可ルーム=[426936385]
```

### Step 6: Chatwork Webhook設定

新メンバーのChatWorkアカウントでWebhookを登録する。

1. 新メンバーのChatWorkアカウントでログイン
2. 管理画面 → 「サービス連携」 → 「Webhook」
3. 「Webhook新規作成」をクリック
4. 以下を設定：
   - **Webhook名**: 任意（例: `AI自動返信`）
   - **Webhook URL**: `https://284bdrbap0.execute-api.ap-northeast-1.amazonaws.com/prod`
   - **イベント**: `メンションされた時（mention_to_me）` にチェック
5. 保存

> **注意**: Webhook URLは全メンバー共通です。Lambda → SQS → ポーラーの経路は1つ。メンバーの振り分けはポーラー側で行います。

### オプション: ルーム別口調

特定のルームで口調を変えたい場合、`room_{ルームID}.md` を作成：

```
members\03_tanaka\room_426936385.md
```

中身にそのルーム専用の指示を記入。

### オプション: CLAUDE.md

Claude Codeが自動読み込みする「記憶ファイル」。会話の文脈や過去の情報を保持：

```
members\03_tanaka\CLAUDE.md
```

## トラブルシューティング

| 症状 | 対処 |
|------|------|
| 「メンバーが1人も見つかりません」 | メンバーフォルダに `member.env` が作成されていない |
| 「CW_TOKEN in ...」 | `member.env` にCW_TOKENが未設定 |
| 返信が来ない | ログで `Claude Code タイムアウト` を確認。`CLAUDE_TIMEOUT` を増やす |
| 二重返信 | 1台のみで起動しているか確認 |
| AI同士が止まらない | `MAX_AI_CONVERSATION_TURNS` を下げる |
| `Claude Code が見つかりません` | `config.env` に `CLAUDE_COMMAND=フルパス` を設定 |
| Ctrl+Cで停止しない | 処理完了を待っています。しばらく待機 |
| 起動時「キューパージスキップ」 | 前回起動から60秒以内の再起動。正常動作、無視してOK |
| 起動時「ProfileNotFound」 | `setup_windows.bat` 未実行、またはconfig.envに `AWS_ACCESS_KEY_ID` を直接設定 |

### PATHの設定

```powershell
where.exe claude
[Environment]::SetEnvironmentVariable("Path", [Environment]::GetEnvironmentVariable("Path", "User") + ";C:\Users\ユーザー名\AppData\Roaming\npm", "User")
```

ウィンドウを開き直して再実行。または `config.env` に `CLAUDE_COMMAND=フルパス` で指定。
