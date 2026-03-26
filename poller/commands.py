"""
ChatWork コマンドハンドラ

/status /session /system /bill /gws /talk の処理を行う。
全コマンドは DEBUG_NOTICE_CHATWORK_ROOM_ID 内でのみ動作し、AI を呼び出さず即応答する。
"""

import glob
import logging
import os
import platform
import re
import subprocess
import sys
import time
from typing import Any

import requests

from poller.config import (
    CHATWORK_API_BASE,
    CHATWORK_API_TIMEOUT,
    CLAUDE_COMMAND,
    CLAUDE_MODEL,
    CLAUDE_TIMEOUT,
    DEBUG_NOTICE_CHATWORK_TOKEN,
    DEBUG_NOTICE_CHATWORK_ROOM_ID,
    DEBUG_NOTICE_ENABLED,
    FOLLOWUP_WAIT_SECONDS,
    GOOGLE_API_SCOPES,
    GOOGLE_DRIVE_INCLUDE_MY_DRIVE,
    GOOGLE_DRIVE_INCLUDE_SHARED,
    GOOGLE_TOKEN_PATH,
    MAX_AI_CONVERSATION_TURNS,
    MEMBERS,
    MEMBERS_DIR,
    MODEL_PRICING,
    REPLY_COOLDOWN_SECONDS,
    SCRIPT_DIR,
    SQS_WAIT_TIME_SECONDS,
    POLL_INTERVAL,
    TALK_MODES,
    USE_DIRECT_API,
    ANTHROPIC_API_KEY,
    load_talk_modes,
)
from poller import state
from poller.ai_runner import get_monthly_usage, ai_mode_label

log = logging.getLogger(__name__)


# =============================================================================
#  /status
# =============================================================================

def handle_status(member: dict[str, Any], room_id: str) -> str:
    """/status: メンバーの設定状況・ファイル一覧・モード設定を報告する"""
    member_dir = member["dir"]
    lines = [f"[info][title]/status: {member['name']}[/title]"]

    common_files = sorted(glob.glob(os.path.join(MEMBERS_DIR, "00_*.md")))
    lines.append(f"■ 共通ルール: {len(common_files)}件")
    for f in common_files:
        lines.append(f"  - {os.path.basename(f)} ({os.path.getsize(f)}B)")

    member_files = sorted(
        f for f in glob.glob(os.path.join(member_dir, "*.md"))
        if not os.path.basename(f).startswith("room_")
        and not os.path.basename(f).startswith("chat_history_")
        and os.path.basename(f) != "CLAUDE.md"
    )
    lines.append(f"\n■ ペルソナ/指示: {len(member_files)}件")
    for f in member_files:
        lines.append(f"  - {os.path.basename(f)} ({os.path.getsize(f)}B)")

    room_files = sorted(glob.glob(os.path.join(member_dir, "room_*.md")))
    lines.append(f"\n■ ルーム別設定: {len(room_files)}件")
    for f in room_files:
        lines.append(f"  - {os.path.basename(f)} ({os.path.getsize(f)}B)")

    claude_md = os.path.join(member_dir, "CLAUDE.md")
    if os.path.exists(claude_md):
        lines.append(f"\n■ CLAUDE.md: あり ({os.path.getsize(claude_md)}B)")
    else:
        lines.append(f"\n■ CLAUDE.md: なし")

    history_files = sorted(glob.glob(os.path.join(member_dir, "chat_history_*.md")))
    lines.append(f"\n■ 会話記録: {len(history_files)}件")
    for f in history_files:
        lines.append(f"  - {os.path.basename(f)} ({os.path.getsize(f)}B)")

    reject_log = os.path.join(member_dir, "rejected_rooms.log")
    if os.path.exists(reject_log):
        lines.append(f"\n■ 拒否ログ: あり ({os.path.getsize(reject_log)}B)")

    room_names: dict[str, str] = {}
    if member.get("cw_token"):
        try:
            res = requests.get(
                f"{CHATWORK_API_BASE}/rooms",
                headers={"X-ChatWorkToken": member["cw_token"]},
                timeout=CHATWORK_API_TIMEOUT,
            )
            if res.status_code == 200:
                for r in res.json():
                    room_names[str(r["room_id"])] = r.get("name", "")
        except Exception:
            pass

    talk_mode_default, talk_mode_rooms = load_talk_modes(member["dir"])
    lines.append(f"\n■ 会話モード (mode.env)")
    lines.append(f"  TALK_MODE={talk_mode_default}({TALK_MODES.get(talk_mode_default, {}).get('name', '不明')})")
    if talk_mode_rooms:
        for rid, mode in sorted(talk_mode_rooms.items()):
            rname = room_names.get(rid, "")
            prefix = f"{rname} " if rname else ""
            lines.append(f"  TALK_MODE={prefix}{rid}:{mode}({TALK_MODES.get(mode, {}).get('name', '不明')})")
    else:
        lines.append(f"  ルーム別指定: なし")

    lines.append(f"\n■ 設定値")
    lines.append(f"  USE_DIRECT_API={'API直接' if USE_DIRECT_API else 'CLI'}")
    lines.append(f"  CLAUDE_MODEL={CLAUDE_MODEL}")
    lines.append(f"  CLAUDE_TIMEOUT={CLAUDE_TIMEOUT}秒")
    lines.append(f"  FOLLOWUP_WAIT_SECONDS={FOLLOWUP_WAIT_SECONDS}秒")
    lines.append(f"  MAX_AI_CONVERSATION_TURNS={MAX_AI_CONVERSATION_TURNS}")
    lines.append(f"  REPLY_COOLDOWN_SECONDS={REPLY_COOLDOWN_SECONDS}秒")

    allowed = member.get("allowed_rooms", set())
    if allowed:
        allowed_display = []
        for rid in sorted(allowed):
            rname = room_names.get(rid, "")
            allowed_display.append(f"{rname}({rid})" if rname else rid)
        lines.append(f"  許可ルーム=[{', '.join(allowed_display)}]")
    else:
        lines.append(f"  許可ルーム=なし（全送信不可）")
    lines.append(f"  cwd={member_dir}")

    lines.append("[/info]")
    return "\n".join(lines)


# =============================================================================
#  /session
# =============================================================================

def handle_session(room_id: str) -> str:
    """/session: 全メンバーの AI 実行状態を報告する"""
    lines = [f"[info][title]/session[/title]"]
    with state.session_lock:
        for key, member in MEMBERS.items():
            s = state.session_states.get(key, {"status": "idle"})
            if s["status"] == "running":
                elapsed = time.time() - s["started"] if s["started"] else 0
                lines.append(
                    f"  {member['name']}: 実行中 "
                    f"({elapsed:.0f}秒経過/{CLAUDE_TIMEOUT}秒) "
                    f"model={s['model']} room={s['room_id']}"
                )
            else:
                lines.append(f"  {member['name']}: 停止中")
    lines.append(f"\n  グローバル設定: model={CLAUDE_MODEL} timeout={CLAUDE_TIMEOUT}秒")
    lines.append("[/info]")
    return "\n".join(lines)


# =============================================================================
#  /system
# =============================================================================

def handle_system() -> str:
    """/system: システム全体の稼働状況・設定を報告する"""
    lines = ["[info][title]/system[/title]"]

    lines.append("■ 環境")
    lines.append(f"  OS: {platform.system()} {platform.release()} ({platform.machine()})")
    lines.append(f"  Python: {sys.version.split()[0]}")
    lines.append(f"  CWD: {SCRIPT_DIR}")

    lines.append(f"\n■ AI")
    lines.append(f"  USE_DIRECT_API: {'API直接' if USE_DIRECT_API else 'CLI'}")
    lines.append(f"  CLAUDE_MODEL: {CLAUDE_MODEL}")
    lines.append(f"  CLAUDE_TIMEOUT: {CLAUDE_TIMEOUT}秒")
    if USE_DIRECT_API:
        lines.append(f"  ANTHROPIC_API_KEY: {'設定済' if ANTHROPIC_API_KEY else '未設定'}")
    else:
        lines.append(f"  CLAUDE_COMMAND: {CLAUDE_COMMAND}")
        try:
            result = subprocess.run(
                [CLAUDE_COMMAND, "--version"],
                capture_output=True, text=True, timeout=10,
            )
            version_str = result.stdout.strip() if result.returncode == 0 else "取得失敗"
            if os.name == "nt":
                where_result = subprocess.run(
                    ["where", CLAUDE_COMMAND], capture_output=True, text=True, timeout=5,
                )
                cmd_path = where_result.stdout.strip().split("\n")[0] if where_result.returncode == 0 else ""
                if cmd_path.endswith(".exe"):
                    cli_type = "Native"
                elif cmd_path.endswith(".cmd"):
                    cli_type = "npm"
                else:
                    cli_type = "不明"
                lines.append(f"  CLI種別: {cli_type} ({version_str})")
                if cmd_path:
                    lines.append(f"  CLIパス: {cmd_path}")
            else:
                lines.append(f"  CLIバージョン: {version_str}")
        except Exception:
            lines.append(f"  CLI種別: 検出失敗")

    lines.append(f"\n■ SQS")
    queue_display = f"{SCRIPT_DIR}..." if not os.environ.get("SQS_QUEUE_URL") else os.environ.get("SQS_QUEUE_URL", "")
    lines.append(f"  QUEUE_URL: {queue_display[:60]}..." if len(queue_display) > 60 else f"  QUEUE_URL: {queue_display}")
    if SQS_WAIT_TIME_SECONDS > 0:
        lines.append(f"  ポーリング: ロング（WaitTime={SQS_WAIT_TIME_SECONDS}秒）")
    else:
        lines.append(f"  ポーリング: ショート（間隔={POLL_INTERVAL}秒）")

    lines.append(f"\n■ パラメータ")
    lines.append(f"  FOLLOWUP_WAIT_SECONDS: {FOLLOWUP_WAIT_SECONDS}秒")
    lines.append(f"  MAX_AI_CONVERSATION_TURNS: {MAX_AI_CONVERSATION_TURNS}")
    lines.append(f"  REPLY_COOLDOWN_SECONDS: {REPLY_COOLDOWN_SECONDS}秒")
    lines.append(f"  CHATWORK_API_TIMEOUT: {CHATWORK_API_TIMEOUT}秒")

    lines.append(f"\n■ メンバー: {len(MEMBERS)}名")
    for key, member in MEMBERS.items():
        token_status = "OK" if member["cw_token"] else "NG"
        rooms_count = len(member.get("allowed_rooms", set()))
        lines.append(f"  {member['name']} ({key}) token={token_status} rooms={rooms_count}")

    google_email = os.environ.get("GOOGLE_EMAIL", "")
    client_id = os.environ.get("GOOGLE_OAUTH_CLIENT_ID", "")
    lines.append(f"\n■ Google Workspace API")
    lines.append(f"  Email: {google_email or '未設定'}")
    if not client_id:
        lines.append(f"  OAuth: 未設定")
    elif not os.path.exists(GOOGLE_TOKEN_PATH):
        lines.append(f"  OAuth: 設定済・未認証（check_gws.bat を実行）")
    else:
        lines.append(f"  OAuth: 認証済")
    lines.append(f"  マイドライブ: {'ON' if GOOGLE_DRIVE_INCLUDE_MY_DRIVE else 'OFF'}")
    lines.append(f"  共有ドライブ: {'ON' if GOOGLE_DRIVE_INCLUDE_SHARED else 'OFF'}")

    lines.append(f"\n■ デバッグ通知")
    lines.append(f"  DEBUG_NOTICE_ENABLED: {'ON' if DEBUG_NOTICE_ENABLED else 'OFF'}")
    lines.append(f"  CHATWORK_TOKEN: {'設定済' if DEBUG_NOTICE_CHATWORK_TOKEN else '未設定'}")
    lines.append(f"  CHATWORK_ROOM_ID: {DEBUG_NOTICE_CHATWORK_ROOM_ID if DEBUG_NOTICE_CHATWORK_ROOM_ID else '未設定'}")

    active_count = 0
    with state.session_lock:
        for s in state.session_states.values():
            if s["status"] == "running":
                active_count += 1
    lines.append(f"\n■ 実行状態")
    lines.append(f"  AI実行中: {active_count}/{len(MEMBERS)}")
    lines.append(f"  CLIプロセス追跡: {len(state.active_processes)}件")

    lines.append("[/info]")
    return "\n".join(lines)


# =============================================================================
#  /bill
# =============================================================================

def handle_bill() -> str:
    """/bill: 当月の Anthropic API 使用量と概算料金を表示する"""
    month_key, usage = get_monthly_usage()
    lines = [f"[info][title]/bill: {month_key} API使用量[/title]"]

    if not usage:
        lines.append("当月のAPI使用実績はありません。")
        if not USE_DIRECT_API:
            lines.append("（CLI モードではトークン数を記録できません）")
        lines.append("[/info]")
        return "\n".join(lines)

    total_cost = 0.0
    total_calls = 0
    for model, stats in sorted(usage.items()):
        in_tok = stats["input_tokens"]
        out_tok = stats["output_tokens"]
        calls = stats["calls"]
        total_calls += calls
        pricing = MODEL_PRICING.get(model, {"input": 0, "output": 0})
        cost_in = in_tok / 1_000_000 * pricing["input"]
        cost_out = out_tok / 1_000_000 * pricing["output"]
        cost = cost_in + cost_out
        total_cost += cost
        lines.append(f"■ {model}")
        lines.append(f"  呼出回数: {calls}回")
        lines.append(f"  入力: {in_tok:,} tokens (${cost_in:.4f})")
        lines.append(f"  出力: {out_tok:,} tokens (${cost_out:.4f})")
        lines.append(f"  小計: ${cost:.4f}")
        lines.append("")

    lines.append(f"合計: {total_calls}回 / ${total_cost:.4f}")
    lines.append(f"（公開単価による概算。実際の請求額とは異なる場合があります）")
    lines.append("[/info]")
    return "\n".join(lines)


# =============================================================================
#  /talk
# =============================================================================

def handle_talk_status(member: dict[str, Any], room_id: str) -> str:
    """/talk: このルームの現在の会話モードと一覧を表示する"""
    default_mode, room_modes = load_talk_modes(member["dir"])
    current_mode = room_modes.get(str(room_id), default_mode)
    is_default = str(room_id) not in room_modes
    current_name = TALK_MODES.get(current_mode, {}).get("name", "不明")

    lines = [f"[info][title]/talk: {member['name']}[/title]"]
    source = "デフォルト" if is_default else "ルーム別設定"
    lines.append(f"このルームの会話モード: {current_mode}（{current_name}）[{source}]")
    lines.append(f"\n設定可能なモード:")
    for mode_id, mode_info in sorted(TALK_MODES.items()):
        marker = " ← 現在" if mode_id == current_mode else ""
        lines.append(f"  /talk {mode_id} : {mode_info['name']}{marker}")
    lines.append("[/info]")
    return "\n".join(lines)


def handle_talk_change(member: dict[str, Any], room_id: str, new_mode: int) -> str:
    """/talk N: 該当ルームの会話モードを変更し、mode.env を更新する"""
    if new_mode not in TALK_MODES:
        return f"無効なモードです。0〜{max(TALK_MODES.keys())} を指定してください。"

    mode_env_path = os.path.join(member["dir"], "mode.env")
    target_key = f"TALK_MODE={room_id}:"

    lines: list[str] = []
    updated = False
    if os.path.exists(mode_env_path):
        try:
            with open(mode_env_path, "r", encoding="utf-8") as f:
                for line in f:
                    if line.strip().startswith(target_key):
                        lines.append(f"TALK_MODE={room_id}:{new_mode}\n")
                        updated = True
                    else:
                        lines.append(line)
        except Exception as e:
            log.error(f"mode.env 読み込みエラー: {e}")
            return f"mode.env の読み込みに失敗しました: {e}"

    if not updated:
        lines.append(f"TALK_MODE={room_id}:{new_mode}\n")

    try:
        with open(mode_env_path, "w", encoding="utf-8") as f:
            f.writelines(lines)
    except Exception as e:
        log.error(f"mode.env 書き込みエラー: {e}")
        return f"mode.env の書き込みに失敗しました: {e}"

    mode_name = TALK_MODES[new_mode]["name"]
    log.info(f"/talk コマンド: {member['name']} room={room_id} → モード{new_mode}({mode_name})")
    return f"[info]会話モードを {new_mode}（{mode_name}）に変更しました。[/info]"


# =============================================================================
#  /gws
# =============================================================================

def handle_gws() -> str:
    """/gws: Google Workspace API の接続テスト"""
    lines = ["[info][title]/gws: Google Workspace API[/title]"]

    client_id = os.environ.get("GOOGLE_OAUTH_CLIENT_ID", "")
    client_secret = os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET", "")

    if not client_id or not client_secret:
        lines.append("状態: 未設定")
        lines.append("config.env に GOOGLE_OAUTH_CLIENT_ID / GOOGLE_OAUTH_CLIENT_SECRET を設定してください")
        lines.append("[/info]")
        return "\n".join(lines)

    try:
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build
    except ImportError:
        lines.append("状態: ライブラリ未インストール")
        lines.append("pip install google-api-python-client google-auth-httplib2 google-auth-oauthlib")
        lines.append("[/info]")
        return "\n".join(lines)

    if not os.path.exists(GOOGLE_TOKEN_PATH):
        lines.append("状態: 未認証")
        lines.append("check_gws.bat を実行して OAuth 認証を完了してください")
        lines.append("[/info]")
        return "\n".join(lines)

    try:
        creds = Credentials.from_authorized_user_file(GOOGLE_TOKEN_PATH, GOOGLE_API_SCOPES)
        if creds.expired and creds.refresh_token:
            from google.auth.transport.requests import Request
            creds.refresh(Request())
    except Exception as e:
        lines.append(f"状態: 認証エラー")
        lines.append(f"  {str(e)[:200]}")
        lines.append("check_gws.bat を再実行して再認証してください")
        lines.append("[/info]")
        return "\n".join(lines)

    my_drive_label = "ON" if GOOGLE_DRIVE_INCLUDE_MY_DRIVE else "OFF"
    shared_label = "ON" if GOOGLE_DRIVE_INCLUDE_SHARED else "OFF"
    lines.append(f"参照範囲: マイドライブ={my_drive_label} / 共有ドライブ={shared_label}")

    # スプレッドシート CRUD テスト（参照設定に関わらず、常にマイドライブで実行）
    test_title = "_GWS_API_TEST_ (delete me)"
    sheet_id = None
    results: list[str] = []
    try:
        sheets = build("sheets", "v4", credentials=creds)
        drive = build("drive", "v3", credentials=creds)

        sp = sheets.spreadsheets().create(
            body={"properties": {"title": test_title}}, fields="spreadsheetId",
        ).execute()
        sheet_id = sp["spreadsheetId"]
        results.append("作成: OK")

        sheets.spreadsheets().values().update(
            spreadsheetId=sheet_id, range="A1:B2",
            valueInputOption="RAW",
            body={"values": [["key", "value"], ["test", "ok"]]},
        ).execute()
        results.append("書き込み: OK")

        data = sheets.spreadsheets().values().get(
            spreadsheetId=sheet_id, range="A1:B2",
        ).execute()
        vals = data.get("values", [])
        if vals == [["key", "value"], ["test", "ok"]]:
            results.append("読み込み: OK（検証済）")
        else:
            results.append("読み込み: MISMATCH")

        sheets.spreadsheets().batchUpdate(
            spreadsheetId=sheet_id,
            body={"requests": [{"addSheet": {"properties": {"title": "Sheet2"}}}]},
        ).execute()
        results.append("シート追加: OK")

        drive.files().delete(fileId=sheet_id).execute()
        sheet_id = None
        results.append("削除: OK")

        if GOOGLE_DRIVE_INCLUDE_MY_DRIVE:
            my_files = drive.files().list(
                pageSize=3, fields="files(name)",
                q="mimeType='application/vnd.google-apps.spreadsheet' and 'me' in owners",
            ).execute().get("files", [])
            results.append(f"マイドライブ: {len(my_files)}件")
        if GOOGLE_DRIVE_INCLUDE_SHARED:
            shared_files = drive.files().list(
                pageSize=3, fields="files(name)",
                q="mimeType='application/vnd.google-apps.spreadsheet'",
                includeItemsFromAllDrives=True, supportsAllDrives=True, corpora="allDrives",
            ).execute().get("files", [])
            results.append(f"共有ドライブ: {len(shared_files)}件")

        lines.append("状態: 全テスト合格")
        for r in results:
            lines.append(f"  {r}")

    except Exception as e:
        lines.append("状態: テスト失敗")
        for r in results:
            lines.append(f"  {r}")
        lines.append(f"  エラー: {str(e)[:200]}")
    finally:
        if sheet_id:
            try:
                drive.files().delete(fileId=sheet_id).execute()
            except Exception:
                lines.append(f"  注意: テスト用スプシ '{test_title}' の手動削除が必要です")

    lines.append("[/info]")
    return "\n".join(lines)
