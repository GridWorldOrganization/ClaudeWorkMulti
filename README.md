# ChatWork Webhook Client

ChatWork のメッセージを SQS 経由で受信し、Claude Code を使って AI が自動返信するシステムです。
Windows PC 上で動作します。

> **注意: ポーラーは必ず1台のPCでのみ起動してください。** 2台同時に動くと同じメッセージを二重処理します。

## 構成

```
ChatWorkWebHookClient/
├── windows_poller.py          # メイン: SQSポーリング + Claude Code 実行 + Chatwork返信
├── start_poller.bat           # 起動スクリプト（ダブルクリックで起動）
├── setup_windows.bat          # 初回セットアップ（AWS CLI設定等）
├── config.env                 # 環境変数（※自分で作成。Gitに含まれない）
├── config.env.example         # ↑のテンプレート
├── members/
│   ├── 00_common_rules.md     # 全メンバー共通ルール（Gitに含まれる）
│   ├── templates/             # テンプレート（Gitに含まれる）
│   │   ├── 01_persona.md.example
│   │   ├── mode.env.example
│   │   └── setup_member.bat
│   ├── 01_yokota/             # 横田百恵の設定（※Gitに含まれない）
│   │   ├── 01_persona.md
│   │   └── mode.env           # デフォルト会話モード
│   └── 02_fujino/             # 藤野楓の設定（※Gitに含まれない）
│       ├── 01_persona.md
│       └── mode.env
└── .gitignore
```

## セットアップ手順

### 1. リポジトリを取得

```
git clone https://github.com/GridWorldOrganization/ChatWorkWebHookClient
cd ChatWorkWebHookClient
```

### 2. config.env を作成

`config.env.example` をコピーして `config.env` を作成し、**実際の値** を設定します。

```
copy config.env.example config.env
```

`config.env` をテキストエディタで開き、以下を設定：

```env
# 必須: 接続情報
SQS_QUEUE_URL=https://sqs.ap-northeast-1.amazonaws.com/XXXX/chatwork-webhook-queue
CW_TOKEN_GURIKO=（実際のトークン）
CW_TOKEN_YOKOTA=（実際のトークン）
CW_TOKEN_FUJINO=（実際のトークン）
CW_ERROR_ROOM_ID=428354226

# オプション: 動作パラメータ（デフォルト値でOKなら省略可）
CLAUDE_TIMEOUT=60
FOLLOWUP_WAIT_SECONDS=30
MAX_AI_CONVERSATION_TURNS=10
REPLY_COOLDOWN_SECONDS=15
```

> **config.env にはAPIトークン等の機密情報が入ります。絶対にGitにコミットしないでください。**（.gitignore で除外済み）

### 3. AWS CLI セットアップ

`setup_windows.bat` を編集し、AWSアクセスキーを実際の値に書き換えてからダブルクリックで実行します。

### 4. メンバーフォルダを作成

#### 方法A: setup_member.bat を使う

```
cd members\templates
setup_member.bat
```

メンバーフォルダ名（例: `01_yokota`）を入力するとフォルダが作成され、テンプレートがコピーされます。

#### 方法B: 手動作成

```
mkdir members\01_yokota
copy members\templates\01_persona.md.example members\01_yokota\01_persona.md
```

### 5. ペルソナを設定

`members\01_yokota\01_persona.md` をテキストエディタで開き、キャラクター設定を記入します。

設定項目: 性格・話し方・趣味・最近の出来事・口癖・苦手なもの等。
詳細は `members/templates/01_persona.md.example` を参照。

> メンバーフォルダ（`01_yokota/` 等）は `.gitignore` 対象です。ペルソナにはトークンや個人的な設定が含まれるためGitには含まれません。

### 6. 起動

`start_poller.bat` をダブルクリックします。

起動時に以下がログに表示されれば正常です：
```
=== Chatwork Webhook Poller 起動 ===
=== config.env パラメータ ===
  CLAUDE_TIMEOUT=60秒
  FOLLOWUP_WAIT_SECONDS=30秒
  MAX_AI_CONVERSATION_TURNS=10ターン
  REPLY_COOLDOWN_SECONDS=15秒
  横田 百恵 (01_yokota): 指示ファイル 1件, cwd=...\members\01_yokota
  藤野 楓 (02_fujino): 指示ファイル 1件, cwd=...\members\02_fujino
```

## 動作の仕組み

### 基本フロー

1. SQS キューを**空になるまで全件読み込み**
2. 宛先メンバーごとにメッセージをグループ化
3. メンバーごとに**並列**で以下を実行：
   - 複数メッセージが溜まっていた場合、先行分を文脈として含め、最後の1件に対して返信
   - `members/00_common_rules.md` + メンバー個別の `.md` をプロンプトに組み込む
   - Claude Code（`claude -p`）を実行して返信文を生成
4. Chatwork API 経由でルームに返信（`[rp]` タグ自動付与）

### 特殊処理

| 機能 | 動作 |
|------|------|
| **[rp]タグ自動付与** | AI出力に`[To:]`や`[rp]`がなければ、コード側で送信者への`[rp]`を自動付与 |
| **[To:]自発発言** | AIが送信者以外に話しかける場合、`[To:アカウントID]名前さん` を出力可能 |
| **フォローアップ** | 「確認します」等のキーワード検出 → 待機 → 情報収集 → 再返信 → 「おやすみなさい」 |
| **AI会話チェーン** | 人間の発言で開始、AI同士は `MAX_AI_CONVERSATION_TURNS` で自動停止 |
| **連投防止** | 同一メンバーは前回発言から `REPLY_COOLDOWN_SECONDS` 秒待機 |
| **sender補完** | SQSのsender_account_idが空の場合、Chatwork APIから自動取得 |

## 設定パラメータ（config.env）

### 必須

| パラメータ | 説明 |
|-----------|------|
| `SQS_QUEUE_URL` | SQSキューのURL |
| `CW_TOKEN_GURIKO` | グリ姉のChatwork APIトークン（エラー報告用） |
| `CW_TOKEN_YOKOTA` | 横田百恵のChatwork APIトークン |
| `CW_TOKEN_FUJINO` | 藤野楓のChatwork APIトークン |
| `CW_ERROR_ROOM_ID` | エラー報告先のChatworkルームID |

### オプション

| パラメータ | デフォルト | 説明 |
|-----------|-----------|------|
| `CLAUDE_TIMEOUT` | 60 | Claude Code 実行タイムアウト（秒） |
| `FOLLOWUP_WAIT_SECONDS` | 30 | フォローアップ返信までの待機（秒） |
| `MAX_AI_CONVERSATION_TURNS` | 10 | AI同士の会話上限メッセージ数 |
| `REPLY_COOLDOWN_SECONDS` | 15 | 同一メンバーの連投防止クールダウン（秒） |
| `MAINTENANCE_ROOM_ID` | (空) | /status コマンドを受け付けるルームID |
| `CLAUDE_COMMAND` | claude | Claude Code のコマンドパス |

## 会話モード

4種類の会話モードがあり、メンバーごと・ルームごとに設定できます。

| モード | 名前 | 説明 |
|--------|------|------|
| 0 | メンテナンス | 機械的に端的。改行禁止。1行で回答。絵文字・雑談・感情表現禁止 |
| 1 | 業務 | 端的に短くわかりやすく。丁寧語だが装飾・雑談なし。1〜3行 |
| 2 | ペルソナ | ペルソナ設定に準拠し感情豊かに話す。キャラクターらしい口調 |
| 3 | ペルソナ+ | ペルソナに加え、ルーム内の他メンバーに時折話を振る（3〜4回に1回） |

### 設定方法

**メンバーのデフォルトモード（メンバーフォルダ内の `mode.env`）:**

```
members/01_yokota/mode.env
members/02_fujino/mode.env
```

中身の例：
```env
# デフォルト（0=メンテナンス/1=業務/2=ペルソナ/3=ペルソナ+）
TALK_MODE=2

# ルーム別（ルームID:モード）
TALK_MODE=426936385:3
TALK_MODE=427388771:1
```

上記例だと:
- ルーム426936385ではペルソナ+、427388771では業務、その他はTALK_MODE=2(ペルソナ)

`mode.env` がなければデフォルト1（業務）。テンプレートは `members/templates/mode.env.example`。

**優先順位:** ルーム別(ルームID:モード) > デフォルト(TALK_MODE) > 1(業務)

## メンバーの追加方法

1. `members/templates/setup_member.bat` でフォルダ作成
2. `01_persona.md` にキャラクター設定を記入
3. `windows_poller.py` の `MEMBERS` に新メンバーを追加
4. `config.env` に `CW_TOKEN_新メンバー=...` を追加
5. ポーラー再起動

## 前提条件

- Windows 10/11
- Python 3.x
- AWS CLI v2（`chatwork-webhook` プロファイルが設定済み）
- Claude Code（`claude` コマンドが使えること）
- AWS SQS キューおよび Chatwork Webhook（Lambda）の設定が完了していること

## トラブルシューティング

| 症状 | 原因・対処 |
|------|-----------|
| 起動時「必須環境変数が未設定」 | `config.env` が存在しないか、トークンが空 |
| 返信が来ない | ログで `Claude Code タイムアウト` を確認。`CLAUDE_TIMEOUT` を増やす |
| 二重返信が出る | 2台でポーラーが動いていないか確認。1台のみにする |
| AI同士が止まらない | `MAX_AI_CONVERSATION_TURNS` を下げる |
| 連続で同じ質問をぶつける | `REPLY_COOLDOWN_SECONDS` を上げる |
| `Claude Code が見つかりません: claude` | 下記「PATHの設定」を参照 |

### PATHの設定

`claude` コマンドが `start_poller.bat`（cmd.exe）から見つからない場合、Windows のシステム環境変数に PATH を追加する必要があります。

**1. claude のインストール先を確認（PowerShell で実行）**

```powershell
where.exe claude
```

出力例:
```
C:\Users\tobis\AppData\Roaming\npm\claude
C:\Users\tobis\AppData\Roaming\npm\claude.cmd
```

**2. PATH にフォルダを追加（PowerShell で実行）**

上記の例なら `C:\Users\tobis\AppData\Roaming\npm` を追加:

```powershell
[Environment]::SetEnvironmentVariable("Path", [Environment]::GetEnvironmentVariable("Path", "User") + ";C:\Users\tobis\AppData\Roaming\npm", "User")
```

**3. コマンドプロンプト/PowerShell を開き直す**

既存のウィンドウには PATH 変更が反映されません。新しいウィンドウで `start_poller.bat` を実行してください。

> **注意**: `claude install` でネイティブビルドに切り替えた場合、インストール先が異なる可能性があります。`where.exe claude` で実際のパスを確認してください。
