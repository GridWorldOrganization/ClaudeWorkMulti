# クイックスタート

最短手順でポーラーを動かすためのガイドです。詳細は [README.md](README.md) を参照。

## 初回セットアップ（10分）

### 1. クローン

```
git clone https://github.com/GridWorldOrganization/ChatWorkWebHookClient
cd ChatWorkWebHookClient
```

### 2. config.env を作成

```
copy config.env.example config.env
```

以下を実際の値に書き換え（**全て必須。未設定だと起動失敗します**）：

```env
AWS_PROFILE=chatwork-webhook
SQS_QUEUE_URL=https://sqs.ap-northeast-1.amazonaws.com/XXXX/chatwork-webhook-queue
CHATWORK_API_TOKEN_ERROR_REPORTER=（エラー報告アカウントのChatWork APIトークン）
CHATWORK_ERROR_ROOM_ID=（エラー報告先のChatWorkルームID）
```

### 3. セットアップ実行

`setup_windows.bat` をダブルクリック。指示に従う。

### 4. メンバーを作成

```
cd members\templates
setup_member.bat
```

フォルダ名を入力（例: `01_yamada`）。

### 5. member.env を作成（**全項目必須。未設定だと起動失敗**）

`members\01_yamada\member.env` を作成：

```env
NAME=山田 太郎
ACCOUNT_ID=12345678
CHATWORK_API_TOKEN=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
ALLOWED_ROOMS=426936385
```

- `ALLOWED_ROOMS` が空だとそのメンバーは全送信不可になります

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
ALLOWED_ROOMS=426936385,427388771
```

### 3. 01_persona.md

キャラクター設定を記入。具体的に書くほど自然な会話になります。

### 4. mode.env（任意）

```env
TALK_MODE=2
TALK_MODE=426936385:3
```

| 値 | モード |
|---|---|
| 0 | メンテナンス（機械的・1行） |
| 1 | 業務（端的・丁寧語） |
| 2 | ペルソナ（キャラ準拠） |
| 3 | ペルソナ+（他メンバーにも話を振る） |

### 5. ポーラー再起動

`start_poller.bat` を再起動。ログに新メンバーが表示されればOK。

### 6. Webhook設定

新メンバーのChatWorkアカウントでWebhookを設定。既存と同じLambda構成。

---

## 動作確認

ChatWorkで新メンバー宛にメッセージを送信。数秒後にAIが返信すれば成功。

返信が来ない場合：
- `webhook_poller.log` を確認
- `Claude Code が見つかりません` → `config.env` に `CLAUDE_COMMAND=フルパス` を設定
- `Claude Code タイムアウト` → `CLAUDE_TIMEOUT` を増やす
- `許可されていないルーム` → `member.env` の `ALLOWED_ROOMS` を確認

---

## 停止

Ctrl+C で安全停止（処理中のメッセージ完了後に終了）。
