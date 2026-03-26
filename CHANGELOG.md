# Changelog

## [0.2.0] - 2026-03-27

### Added
- AI拒否検出システム: セーフティフィルタによるペルソナ崩壊を検知し、返信をブロックしてデバッグルームに通知
  - 日本語・英語の拒否パターンに対応（キーワード + 構造パターンマッチ）
- デバッグコマンド並列処理: デバッグルームのコマンド(/session等)がClaude実行中でも即時応答
- 起動時プロンプトチェッカー: ペルソナファイル内のChatWork固有情報を検出し警告
- デバッグルームALLOWED_ROOMS警告: メンバーの許可ルームにデバッグルームが含まれる場合に起動時警告
- CSV日次ログ: `logs/poll_YYYY-MM-DD.csv` 形式の日次ローテーションログ
- `/help` コマンド: コマンド一覧表示
- ペルソナ崩壊対策ドキュメント: `docs/persona-collapse.md`

### Changed
- プロンプト設計を「社内AIマスコットキャラクター」に変更（虚偽フレーミングを排除）
- モード0(ログ)/1(業務)でペルソナファイル読み込みをスキップ（共通ルールのみ使用）
- CLIのcwdを一時ディレクトリに変更（CLAUDE.md/chat_historyの誤読み込み防止）
- デバッグルーム判定をルームIDのみに簡素化（並行処理の確実性向上）
- 反抗期モードの指示文を柔らかい表現に変更
- `/talk` を対話型セッションに改造
- UI簡素化: 起動通知・デバッグ通知・コマンド出力を1行化
- FOLLOWUP_WAIT_SECONDSデフォルトを30秒→120秒に変更
- ポーリングループをノンブロッキング化

### Fixed
- デバッグルーム通知がメンバー宛メッセージとして再処理される無限ループ
- グリ姉自身の発言による自己応答ループ（「...。」の連投）
- CLIがメンバーフォルダのCLAUDE.md/chat_historyを読み込みタイムアウトする問題
- デバッグアカウント宛の非コマンドメッセージが横田に振り分けられる問題
- プロンプト内のChatWork固有情報(account_id, room_id, [To:]タグ等)によるセーフティフィルタ拒否

### Removed
- 名前マスキング機能（ペルソナファイル側での対策に変更したため不要）

## [0.1.0] - 2026-03-26

### Added
- `/system` command: system-wide status display (OS, AI mode, SQS, Google API, members)
- `/bill` command: monthly Anthropic API usage and estimated cost tracking
- `/gws` command: Google Workspace API connection test (CRUD test with temp spreadsheet)
- `/talk` command: view/change conversation mode per room (mode 0-4)
- Conversation mode 4: "Rebellion mode" (contrarian persona responses)
- Google Workspace URL auto-detection: Sheets/Docs/Slides/Drive URLs in messages are fetched and included in AI prompts
- Google Workspace API integration via OAuth (replaces service account approach)
- `USE_DIRECT_API` option: switch between Claude Code CLI (default) and Anthropic API direct call
- API usage tracking with token count recording (`api_usage.json`)
- `SQS_WAIT_TIME_SECONDS` for short/long polling switch
- Window close (X button) cleanup via `SetConsoleCtrlHandler`
- `atexit` cleanup as fallback
- `kill_zombie.bat`: zombie process detection and cleanup tool (`--all` for extended mode)
- `check_gws.bat` / `check_gws.py`: Google Workspace API connection checker
- `check_claude_task.bat`: Native/npm Claude process detection
- Multi-instance prevention in `start_poller.bat` (auto-kill zombie + restart)
- ChatWork API timeout (30s) on all 7 API call sites
- Casual chat filter for mode 0 (skip greetings without AI call)
- `DEBUG_NOTICE_ENABLED` flag for debug notification on/off
- Startup API connectivity test for debug notification room
- CLI mode: PID startup/shutdown logging with `proc.poll()` verification
- Startup log: `where claude` result showing Native/npm and full path
- `docs/` folder: architecture.md, commands.md

### Changed
- Default `USE_DIRECT_API` changed from `1` to `0` (Claude Code CLI is now default)
- Renamed `CHATWORK_API_TOKEN_ERROR_REPORTER` → `DEBUG_NOTICE_CHATWORK_TOKEN`
- Renamed `CHATWORK_ERROR_ROOM_ID` → `DEBUG_NOTICE_CHATWORK_ROOM_ID`
- Removed `MAINTENANCE_ROOM_ID` (all commands now use `DEBUG_NOTICE_CHATWORK_ROOM_ID`)
- Conversation mode 0 renamed from "Maintenance" to "Log"
- All commands restricted to `DEBUG_NOTICE_CHATWORK_ROOM_ID` only
- README.md restructured: technical details moved to `docs/`
- QUICKSTART.md updated to reflect all changes
- Google API scopes unified into single constant (`GOOGLE_API_SCOPES`)
- Added `presentations.readonly` scope for Google Slides support

### Fixed
- Self-messages not deleted from SQS in `process_member_batch` (zombie message loop)
- `/talk` `/gws` commands executable from unauthorized rooms (moved after whitelist check)
- Sheets API range `Sheet1!A1:B2` fails in Japanese locale (changed to `A1:B2`)
- `setup_windows.bat` AWS key skip message causing batch syntax error (Japanese in if-block)
- Google API scope inconsistency between token creation and usage

## [0.1.0] - 2026-03-25

### Added
- Initial release
- SQS polling with batch processing
- Multi-member parallel execution
- Conversation modes (0-3)
- Room-specific settings
- AI-to-AI conversation with turn limit
- Follow-up auto-reply
- `/status` `/session` maintenance commands
- Graceful shutdown (Ctrl+C)
- Room whitelist
