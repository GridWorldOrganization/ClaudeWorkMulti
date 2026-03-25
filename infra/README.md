# AWS インフラ設定ガイド

ChatWork Webhook Client が依存する AWS リソースのセットアップ手順です。
既に構築済みの場合、新しいメンバー追加時にAWS側の変更は不要です。

## 構成図

```
ChatWork         API Gateway          Lambda                SQS               Windows PC
[メンション] ──▶ /prod/webhook ──▶ chatwork-webhook ──▶ chatwork-webhook ──▶ windows_poller.py
                  (POST)            -handler              -queue              (ポーリング)
```

## 1. SQS キューの作成

### 設定

| 項目 | 値 |
|------|-----|
| キュー名 | `chatwork-webhook-queue` |
| キュータイプ | 標準（Standard） |
| 可視性タイムアウト | 300秒 |
| メッセージ保持期間 | 14日 |
| 最大メッセージサイズ | 256KB（デフォルト） |
| 暗号化 | SSE-SQS（デフォルト） |

### 手順

1. AWS Console → SQS → 「キューを作成」
2. 上記の値を設定
3. 作成後、キューURLをメモ（`config.env` の `SQS_QUEUE_URL` に設定）

## 2. Lambda 関数の作成

### 設定

| 項目 | 値 |
|------|-----|
| 関数名 | `chatwork-webhook-handler` |
| ランタイム | Python 3.12 |
| タイムアウト | 30秒 |
| メモリ | 128MB |

### 環境変数

| キー | 値 |
|------|-----|
| `SQS_QUEUE_URL` | SQSキューのURL |
| `CHATWORK_WEBHOOK_TOKEN` | 空（署名検証をスキップする場合） |

> `CHATWORK_WEBHOOK_TOKEN` を設定すると、ChatWork からのリクエストの署名を検証します。
> 空のままだと全リクエストを受け入れます。本番環境ではトークンを設定することを推奨。

### ソースコード

```python
import json
import os
import hashlib
import hmac
import boto3
from datetime import datetime, timezone

sqs = boto3.client('sqs')
QUEUE_URL = os.environ['SQS_QUEUE_URL']
CHATWORK_WEBHOOK_TOKEN = os.environ.get('CHATWORK_WEBHOOK_TOKEN', '')

def verify_signature(body, signature):
    if not CHATWORK_WEBHOOK_TOKEN:
        return True
    expected = hmac.new(
        CHATWORK_WEBHOOK_TOKEN.encode('utf-8'),
        body.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature)

def lambda_handler(event, context):
    http_method = event.get('httpMethod', '')

    # Chatwork Webhook の検証リクエスト（GET）
    if http_method == 'GET':
        return {
            'statusCode': 200,
            'body': json.dumps({'message': 'Webhook endpoint is active'})
        }

    # POST リクエスト処理
    body = event.get('body', '{}')

    # 署名検証
    signature = event.get('headers', {}).get('X-ChatWorkWebhookSignature', '')
    if CHATWORK_WEBHOOK_TOKEN and not verify_signature(body, signature):
        return {
            'statusCode': 401,
            'body': json.dumps({'error': 'Invalid signature'})
        }

    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return {
            'statusCode': 400,
            'body': json.dumps({'error': 'Invalid JSON'})
        }

    # SQS にメッセージ送信
    webhook_event = payload.get('webhook_event', {})
    sqs_message = {
        'source': 'chatwork',
        'webhook_event_type': payload.get('webhook_event_type', ''),
        'room_id': webhook_event.get('room_id', ''),
        'message_id': webhook_event.get('message_id', ''),
        'sender_account_id': webhook_event.get('account_id', ''),
        'body': webhook_event.get('body', ''),
        'send_time': webhook_event.get('send_time', ''),
        'timestamp': datetime.now(timezone.utc).isoformat()
    }

    sqs.send_message(
        QueueUrl=QUEUE_URL,
        MessageBody=json.dumps(sqs_message, ensure_ascii=False)
    )

    return {
        'statusCode': 200,
        'body': json.dumps({'message': 'Message queued successfully'})
    }
```

### IAM ロール

Lambda に以下の権限が必要：

```json
{
    "Effect": "Allow",
    "Action": [
        "sqs:SendMessage"
    ],
    "Resource": "arn:aws:sqs:ap-northeast-1:*:chatwork-webhook-queue"
}
```

加えて `AWSLambdaBasicExecutionRole`（CloudWatch Logs 書き込み用）。

## 3. API Gateway の作成

### 設定

| 項目 | 値 |
|------|-----|
| API名 | `chatwork-webhook-api` |
| タイプ | REST API |
| エンドポイント | リージョン |

### リソース・メソッド

```
/webhook
  ├── GET  → Lambda (chatwork-webhook-handler)  ※Webhook検証用
  └── POST → Lambda (chatwork-webhook-handler)  ※メッセージ受信用
```

### デプロイ

1. 「デプロイ」→ ステージ名 `prod`
2. 生成されるURL: `https://{api-id}.execute-api.ap-northeast-1.amazonaws.com/prod/webhook`
3. このURLを各メンバーのChatWork Webhook設定に登録

## 4. Windows PC 用 IAM ユーザー

ポーラー（`windows_poller.py`）がSQSにアクセスするためのIAMユーザー。

### 必要な権限

```json
{
    "Effect": "Allow",
    "Action": [
        "sqs:ReceiveMessage",
        "sqs:DeleteMessage",
        "sqs:GetQueueAttributes",
        "sqs:PurgeQueue"
    ],
    "Resource": "arn:aws:sqs:ap-northeast-1:*:chatwork-webhook-queue"
}
```

### 手順

1. AWS Console → IAM → ユーザー → 「ユーザーを作成」
2. 上記のSQS権限を持つポリシーをアタッチ
3. アクセスキーを発行
4. `config.env` の `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` に設定
   - または `setup_windows.bat` で AWS CLI プロファイルに設定

## 5. ChatWork Webhook 登録

各AIメンバーのChatWorkアカウントでWebhookを登録する。

### 手順

1. メンバーのChatWorkアカウントでログイン
2. 右上アイコン → 「サービス連携」 → 「Webhook」
3. 「Webhook新規作成」
4. 設定：
   - **Webhook名**: 任意（例: `ai_auto_reply`）
   - **Webhook URL**: API GatewayのURL（`https://{api-id}.execute-api.ap-northeast-1.amazonaws.com/prod/webhook`）
   - **イベント**: 「アカウントイベント」→「自分へのメンション（mention_to_me）」にチェック
5. 保存
6. ステータスが「有効」になっていることを確認

> **Webhook URLは全メンバー共通**です。Lambda は送信元を区別せず全メッセージをSQSに流します。
> メンバーの振り分けはポーラー側（`windows_poller.py`）がメッセージ内の `[To:アカウントID]` で判定します。

### 署名検証を有効にする場合

1. ChatWork Webhook作成時に表示される「トークン」をメモ
2. Lambda の環境変数 `CHATWORK_WEBHOOK_TOKEN` にそのトークンを設定
3. **注意**: メンバーごとにトークンが異なるため、複数メンバーの署名検証を同時に行うにはLambda側の改修が必要（現在は単一トークンのみ対応）

## トラブルシューティング

| 症状 | 原因・対処 |
|------|-----------|
| Webhook登録時に「URLが無効」 | API GatewayのGETメソッドが200を返すか確認 |
| メッセージがSQSに届かない | Lambda の CloudWatch Logs でエラーを確認 |
| ポーラーがSQSに接続できない | IAMユーザーの権限、config.envのキー設定を確認 |
| 「PurgeQueue」エラー | 前回パージから60秒以内。正常動作、無視してOK |
