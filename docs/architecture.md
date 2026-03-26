# アーキテクチャ詳細

## 処理フロー

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

## 排他制御（多重実行防止）

AI が実行中に新しいメッセージが届いても、同一メンバーの AI が同時に複数起動されることはありません。
3層の防御で保護されています。

| 防御層 | 仕組み | 対象 |
|--------|--------|------|
| **join 待ち** | 全スレッドの完了を待ってから次のポーリングに進む | サイクル間 |
| **メンバー排他ロック** | メンバーごとに `threading.Lock` を持ち、同一メンバーは1スレッドしか実行できない | スレッド間 |
| **バッチグループ化** | 同一サイクル内で同じメンバー宛のメッセージは1つにまとめて処理 | サイクル内 |

異なるメンバー（例: メンバーAとメンバーB）は**意図的に並列実行**されます。それぞれ別スレッド・別ロックで独立して動作します。

## プロセスクリーンアップ

ポーラー終了時に子プロセス（Claude Code CLI）がゾンビとして残らないよう、3層のクリーンアップを実装しています。

| 終了方法 | 捕捉する仕組み | 動作 |
|---------|---------------|------|
| Ctrl+C | `signal.SIGINT` | 子プロセスkill + PIDファイル削除 |
| ウィンドウ×ボタン | `SetConsoleCtrlHandler` (CTRL_CLOSE_EVENT) | 同上 |
| ログオフ / シャットダウン | `SetConsoleCtrlHandler` (CTRL_LOGOFF/SHUTDOWN) | 同上 |
| 正常終了 | `atexit` | 同上 |

`taskkill /F` で外部から強制 kill された場合のみクリーンアップが走りません。
その場合は `start_poller.bat` が起動時に自動で既存プロセスを kill して再起動します。

## ポーラー多重起動防止

`start_poller.bat` は起動時に `windows_poller.py` が既に実行中か確認します。
既存プロセスがある場合は確認なしで自動 kill → 再起動します（×ボタンで閉じたゾンビへの対応）。

## SQS ポーリング方式

`config.env` の `SQS_WAIT_TIME_SECONDS` で切り替えられます。

| 設定 | 方式 | 特徴 |
|------|------|------|
| `SQS_WAIT_TIME_SECONDS=0` | **ショートポーリング** | 即時応答。`POLL_INTERVAL` 秒間隔でループ。SQSリクエスト数が多くコスト高 |
| `SQS_WAIT_TIME_SECONDS=1〜20`（デフォルト: 1） | **ロングポーリング** | 指定秒数までSQS側で待機。メッセージが届いた時点で即返答。SQSコスト大幅削減 |

ロングポーリング時は `POLL_INTERVAL` は無効になります（SQS の Wait がインターバルの代わり）。

## AI 呼び出し方式

`config.env` の `USE_DIRECT_API` で切り替えられます。

| 設定 | 方式 | 特徴 |
|------|------|------|
| `USE_DIRECT_API=0`（デフォルト） | **Claude Code CLI** | `claude -p` コマンドを subprocess で実行。CLAUDE.md やツール連携が必要な場合に使用 |
| `USE_DIRECT_API=1` | **Anthropic API 直接呼び出し** | Python `anthropic` ライブラリで Messages API を直接呼び出し。高速だが従量課金（APIキー必要） |

全メンバーが同一の `CLAUDE_COMMAND` / `CLAUDE_MODEL` を使用します（メンバー別のモデル指定はありません）。

## Google Workspace URL 自動検出

メッセージ内に Google Workspace の URL が含まれている場合、API で内容を自動取得してプロンプトに挿入します。

| URL パターン | 取得方法 |
|-------------|---------|
| `docs.google.com/spreadsheets/d/{ID}` | Sheets API で全シート内容（最大100行/シート） |
| `docs.google.com/document/d/{ID}` | プレーンテキストでエクスポート（最大10000文字） |
| `docs.google.com/presentation/d/{ID}` | 各スライドのテキスト要素を抽出 |
| `drive.google.com/file/d/{ID}` | MIMEタイプに応じて自動判別 |

認証は `google_token.json` を使用（`check_gws.bat` で事前認証が必要）。
