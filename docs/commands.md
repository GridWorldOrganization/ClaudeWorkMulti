# コマンドリファレンス

## ChatWork コマンド

デバッグ通知ルーム（`DEBUG_NOTICE_CHATWORK_ROOM_ID`）内で、
デバッグ専用メンバー（`DEBUG_NOTICE_CHATWORK_ACCOUNT_ID`）宛に送信した場合のみ動作します。
他のルームや他のメンバー宛ではコマンドとして認識されず、無視されます。
全コマンドは AI を呼び出さず即応答します。

### /status — メンバー情報

| コマンド | 説明 |
|---------|------|
| `/status` | メンバー番号一覧を表示 |
| `/status N` | メンバー N の設定詳細を表示 |

### /talk — 会話モード

| コマンド | 説明 |
|---------|------|
| `/talk` | 全メンバーのデフォルトモード一覧 |
| `/talk N` | メンバー N のモード詳細（デフォルト + ルーム別） |
| `/talk N M` | メンバー N のデフォルトモードを M に変更 |
| `/talk N URL M` | メンバー N の特定ルームのモードを M に変更 |

URL は `https://www.chatwork.com/#!ridXXXXXXXXX` 形式またはルーム ID 直接指定。

### その他

| コマンド | 説明 |
|---------|------|
| `/session` | 全メンバーの AI 実行状態 |
| `/sysinfo` | システム全体の稼働状況・設定 |
| `/bill` | 当月の Anthropic API 使用量と概算料金（API直接モードのみ） |
| `/gws` | Google Workspace API 接続テスト（スプシ CRUD テスト） |

## 会話モード一覧

| モード | 名前 | 説明 |
|--------|------|------|
| 0 | ログ | 機械的・1行回答。雑談メッセージは AI を呼ばずスキップ |
| 1 | 業務 | 端的・丁寧語。1〜3行 |
| 2 | ペルソナ | ペルソナ設定に準拠。感情豊かに会話 |
| 3 | ペルソナ+ | ペルソナ + 他メンバーにも話を振る |
| 4 | 反抗期 | ペルソナの口調で反抗する。憎めない態度 |

## バッチツール

| バッチ | 説明 |
|--------|------|
| `start_poller.bat` | ポーラー起動。既存プロセスがあれば自動 kill して再起動 |
| `setup_windows.bat` | 初回セットアップ（依存パッケージ・AWS・Google API） |
| `check_task.bat` | プロセスチェッカー（ポーラー + Claude Native/npm） |
| `check_gws.bat` | Google Workspace API 接続テスト（OAuth認証 + CRUD 検証） |
| `kill_zombie.bat` | ゾンビプロセス検出・削除（ポーラーが起動したプロセスのみ） |
| `kill_zombie.bat --all` | 上記 + 全 Claude プロセス（Native/npm）も表示・削除対象に含める |
