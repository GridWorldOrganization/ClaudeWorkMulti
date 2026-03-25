# ChatWork Webhook Client

ChatWork のメッセージを SQS 経由で受信し、Claude Code を使って AI が自動返信するシステムです。

## 構成

```
ChatWorkWebHookClient/
├── windows_poller.py       # SQSポーリング + Claude Code 実行 + Chatwork返信
├── start_poller.bat        # 起動スクリプト（ダブルクリックで起動）
├── setup_windows.bat       # 初回セットアップスクリプト
├── clients/
│   ├── 00_common_rules.md  # 全メンバー共通ルール
│   ├── 01_yokota/          # メンバー個別フォルダ（.gitignore対象）
│   │   └── 01_persona.md
│   └── 02_fujino/          # メンバー個別フォルダ（.gitignore対象）
│       └── 01_persona.md
└── .gitignore
```

## セットアップ（新しいPCで使う場合）

### 1. リポジトリを取得

```
git clone https://github.com/GridWorldOrganization/ChatWorkWebHookClient
cd ChatWorkWebHookClient
```

### 2. setup_windows.bat を編集

`setup_windows.bat` を開き、AWSキーを実際の値に書き換えます。

```bat
aws configure set aws_access_key_id YOUR_AWS_ACCESS_KEY_ID ...
aws configure set aws_secret_access_key YOUR_AWS_SECRET_ACCESS_KEY ...
```

### 3. setup_windows.bat を実行

ダブルクリックで実行します。以下が自動でセットアップされます。

- boto3 インストール
- AWS プロファイル `chatwork-webhook` の設定
- 環境変数の設定

### 4. メンバーフォルダを作成

`clients/` 直下に各メンバーのフォルダを作成し、ペルソナファイルを配置します。

```
clients/
├── 01_yokota/
│   └── 01_persona.md   ← 横田百恵のキャラクター設定
└── 02_fujino/
    └── 01_persona.md   ← 藤野楓のキャラクター設定
```

> `clients/01_*/` と `clients/02_*/` は `.gitignore` 対象です。
> トークンや個人情報が含まれるため、Git には含まれません。

### 5. 起動

`start_poller.bat` をダブルクリックします。

## 動作の仕組み

1. SQS キューから Chatwork Webhook イベントを受信
2. 宛先メンバー（[To:ACCOUNT_ID] のメンション）を特定
3. `clients/00_common_rules.md` + メンバー個別の `.md` をプロンプトに組み込む
4. Claude Code を実行して返信文を生成
5. Chatwork API 経由でルームに返信（[rp] タグ自動付与）

## 前提条件

- Python 3.x
- AWS CLI v2
- Claude Code（`claude` コマンドが使えること）
- AWS SQS キューおよび Chatwork Webhook の設定が完了していること
