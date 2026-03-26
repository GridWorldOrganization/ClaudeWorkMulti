# クイックスタート

最短手順でポーラーを動かすためのガイドです。
詳細は [README.md](README.md)、技術仕様は [docs/](docs/) を参照。

## 初回セットアップ

### 1. クローン

```
git clone https://github.com/GridWorldOrganization/ClaudeWorkMulti
cd ClaudeWorkMulti
```

### 2. config.env を作成

```
copy config.env.example config.env
```

以下を実際の値に書き換え：

```env
# 必須
AWS_PROFILE=chatwork-webhook
SQS_QUEUE_URL=https://sqs.ap-northeast-1.amazonaws.com/XXXX/chatwork-webhook-queue

# デバッグ通知（設定しなくても起動可能。設定するとエラー通知やコマンドが使える）
DEBUG_NOTICE_ENABLED=1
DEBUG_NOTICE_CHATWORK_TOKEN=（デバッグ通知用ChatWork APIトークン）
DEBUG_NOTICE_CHATWORK_ROOM_ID=（デバッグ通知先のChatWorkルームID）
```

### 3. セットアップ実行

`setup_windows.bat` をダブルクリック。以下が自動実行されます：

| Step | 内容 |
|------|------|
| 1 | Python の存在確認 |
| 2 | pip パッケージインストール（boto3, requests, anthropic, google-api-python-client 等） |
| 3 | Claude Code CLI の存在確認 |
| 4 | AWS CLI の存在確認 |
| 5 | AWS プロファイル設定（config.env に `AWS_ACCESS_KEY_ID` がある場合のみ） |
| 6 | Google Workspace API 接続テスト（config.env に OAuth 設定がある場合） |

エラーが出た場合は画面の指示に従ってください。

### 4. メンバーを作成

```
cd members\templates
setup_member.bat
```

フォルダ名を入力（例: `01_yamada`）。

### 5. member.env を作成

`members\01_yamada\member.env` を作成（**全項目必須**）：

```env
NAME=山田 太郎
ACCOUNT_ID=12345678
CHATWORK_API_TOKEN=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
ALLOWED_ROOMS=123456789
```

- `ALLOWED_ROOMS` が空だとそのメンバーは全送信不可になります
- 複数ルームはカンマ区切り: `123456789,987654321`

### 6. ペルソナを書く

`members\01_yamada\01_persona.md` をエディタで開いてキャラ設定を記入。

### 7. 起動

`start_poller.bat` をダブルクリック。ログに起動メッセージが出ればOK。

---

## メンバーを追加するには

**コードの変更は不要です。** フォルダとファイルを追加するだけ。

### 1. フォルダ作成

```
cd members\templates
setup_member.bat
```

フォルダ名を入力（例: `03_tanaka`）。番号は既存と重複しない2桁。

### 2. member.env

```env
NAME=田中 一郎
ACCOUNT_ID=99999999
CHATWORK_API_TOKEN=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
ALLOWED_ROOMS=123456789,987654321
```

### 3. 01_persona.md

キャラクター設定を記入。具体的に書くほど自然な会話になります。

### 4. mode.env（任意）

```env
TALK_MODE=2
TALK_MODE=123456789:3
```

| 値 | モード |
|---|---|
| 0 | ログ（機械的・1行。雑談スキップ） |
| 1 | 業務（端的・丁寧語） |
| 2 | ペルソナ（キャラ準拠） |
| 3 | ペルソナ+（他メンバーにも話を振る） |
| 4 | 反抗期（憎めない反抗態度） |

ChatWork で `/talk N` を送ればルーム単位で動的に変更可能。

### 5. ポーラー再起動

`start_poller.bat` をダブルクリック（既存プロセスは自動で停止されます）。ログに新メンバーが表示されればOK。

### 6. Webhook設定

新メンバーのChatWorkアカウントでWebhookを設定。既存と同じLambda構成。
詳細は [infra/README.md](infra/README.md) を参照。

---

## 動作確認

ChatWorkで新メンバー宛にメッセージを送信。数秒後にAIが返信すれば成功。

返信が来ない場合：
- `webhook_poller.log` を確認
- `Claude Code が見つかりません` → `config.env` に `CLAUDE_COMMAND=フルパス` を設定
- `AI応答タイムアウト` → `CLAUDE_TIMEOUT` を増やす
- `許可されていないルーム` → `member.env` の `ALLOWED_ROOMS` を確認

---

## 停止

- **Ctrl+C**: 処理中のメッセージ完了後に安全停止
- **ウィンドウ×ボタン**: 子プロセスを自動クリーンアップして終了
- **再起動**: `start_poller.bat` をダブルクリック（既存プロセスは自動 kill）
