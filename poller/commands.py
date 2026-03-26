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
    QUEUE_URL,
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
#  /help
# =============================================================================

def handle_help() -> str:
    """/help: コマンド一覧を表示する"""
    lines = [
        "[info][title]/help[/title]",
        "/help — コマンド一覧",
        "/status — メンバー一覧 / /status N — 詳細",
        "/talk — 会話モード設定（対話型）",
        "/session — AI実行状態",
        "/sysinfo — システム情報",
        "/bill — API使用量",
        "/gws — Google APIテスト",
        "[/info]",
    ]
    return "\n".join(lines)


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
                    f"({elapsed:.0f}秒経過/{CLAUDE_TIMEOUT}秒), "
                    f"model: {s['model']}, room: {s['room_id']}"
                )
            else:
                lines.append(f"  {member['name']}: 停止中")
    lines.append(f"\n  グローバル設定: model: {CLAUDE_MODEL}, timeout: {CLAUDE_TIMEOUT}秒")
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
    lines.append(f"  QUEUE_URL: {QUEUE_URL[:60]}..." if len(QUEUE_URL) > 60 else f"  QUEUE_URL: {QUEUE_URL}")
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
    if not client_id:
        oauth_status = "未設定"
    elif not os.path.exists(GOOGLE_TOKEN_PATH):
        oauth_status = "未認証"
    else:
        oauth_status = "認証済"
    my_drive = "ON" if GOOGLE_DRIVE_INCLUDE_MY_DRIVE else "OFF"
    shared_drive = "ON" if GOOGLE_DRIVE_INCLUDE_SHARED else "OFF"
    lines.append(f"\n■ Google Workspace API")
    lines.append(f"  {google_email or '未設定'} / OAuth:{oauth_status} / マイドライブ:{my_drive} / 共有ドライブ:{shared_drive}")

    from poller.config import DEBUG_NOTICE_CHATWORK_ACCOUNT_ID
    lines.append(f"\n■ デバッグ通知")
    lines.append(f"  DEBUG_NOTICE_ENABLED: {'ON' if DEBUG_NOTICE_ENABLED else 'OFF'}")
    lines.append(f"  CHATWORK_TOKEN: {'設定済' if DEBUG_NOTICE_CHATWORK_TOKEN else '未設定'}")
    lines.append(f"  CHATWORK_ROOM_ID: {DEBUG_NOTICE_CHATWORK_ROOM_ID if DEBUG_NOTICE_CHATWORK_ROOM_ID else '未設定'}")
    lines.append(f"  CHATWORK_ACCOUNT_ID: {DEBUG_NOTICE_CHATWORK_ACCOUNT_ID if DEBUG_NOTICE_CHATWORK_ACCOUNT_ID else '未設定（全メンバー受付）'}")

    active_count = 0
    with state.session_lock:
        for s in state.session_states.values():
            if s["status"] == "running":
                active_count += 1
    lines.append(f"\n■ 実行状態")
    lines.append(f"  AI実行中: {active_count}/{len(MEMBERS)} / CLIプロセス追跡: {len(state.active_processes)}件")

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
        lines.append(f"{model}: {calls}回 / 入力{in_tok:,} 出力{out_tok:,} tokens / ${cost:.4f}")

    lines.append(f"合計: {total_calls}回 / ${total_cost:.4f}（概算）")
    lines.append("[/info]")
    return "\n".join(lines)


# =============================================================================
#  /talk（対話型セッション）
# =============================================================================

def _write_mode_env(member_dir: str, room_id: str, new_mode: int) -> str | None:
    """mode.env にルーム別設定を書き込む。エラー時はメッセージを返す"""
    mode_env_path = os.path.join(member_dir, "mode.env")
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
    return None


def _delete_room_mode(member_dir: str, room_id: str) -> str | None:
    """mode.env からルーム別設定を削除する。エラー時はメッセージを返す"""
    mode_env_path = os.path.join(member_dir, "mode.env")
    target_key = f"TALK_MODE={room_id}:"
    if not os.path.exists(mode_env_path):
        return None
    try:
        lines: list[str] = []
        with open(mode_env_path, "r", encoding="utf-8") as f:
            for line in f:
                if not line.strip().startswith(target_key):
                    lines.append(line)
        with open(mode_env_path, "w", encoding="utf-8") as f:
            f.writelines(lines)
    except Exception as e:
        log.error(f"mode.env 削除エラー: {e}")
        return f"mode.env の操作に失敗しました: {e}"
    return None


def _mode_options_str() -> str:
    """会話モード選択肢の文字列を返す"""
    return ", ".join(f"{k}:{v['name']}" for k, v in sorted(TALK_MODES.items()))


def _get_room_names(cw_token: str) -> dict[str, str]:
    """ChatWork API でルーム名一覧を取得する。{ルームID: ルーム名}"""
    if not cw_token:
        return {}
    try:
        res = requests.get(
            f"{CHATWORK_API_BASE}/rooms",
            headers={"X-ChatWorkToken": cw_token},
            timeout=CHATWORK_API_TIMEOUT,
        )
        if res.status_code == 200:
            return {str(r["room_id"]): r.get("name", "") for r in res.json()}
    except Exception:
        pass
    return {}


def _room_display(rid: str, mode: int, room_names: dict[str, str]) -> str:
    """ルーム表示用の文字列を返す（ルーム名付き）"""
    rname = room_names.get(rid, "")
    mode_name = TALK_MODES.get(mode, {}).get("name", "不明")
    if rname:
        return f"  {rname}({rid}): {mode}（{mode_name}）"
    return f"  {rid}: {mode}（{mode_name}）"


def _extract_room_id(text: str) -> str:
    """テキストからルームIDを抽出する（ChatWork URL対応）"""
    url_match = re.match(r'https?://www\.chatwork\.com/#!rid(\d+)', text.strip())
    if url_match:
        return url_match.group(1)
    return text.strip()


def handle_talk_start() -> str:
    """/talk: 対話型セッションを開始する"""
    lines = ["誰の会話モードを確認しますか？"]
    for idx, (key, m) in enumerate(MEMBERS.items(), 1):
        default_mode, _ = load_talk_modes(m["dir"])
        mode_name = TALK_MODES.get(default_mode, {}).get("name", "不明")
        lines.append(f"  {idx}: {m['name']}（デフォルト会話モード: {default_mode} {mode_name}）")
    lines.append("番号を返信してください。")
    state.talk_session = {"state": "select_member"}
    return "\n".join(lines)


def handle_talk_session_reply(raw_input: str) -> str | None:
    """対話型 /talk セッションの応答を処理する。セッション外なら None を返す"""
    session = state.talk_session
    if not session:
        return None

    current_state = session.get("state", "")

    # --- メンバー選択 ---
    if current_state == "select_member":
        try:
            num = int(raw_input)
        except ValueError:
            state.talk_session = {}
            return "キャンセルしました。"

        member_list = list(MEMBERS.items())
        if num < 1 or num > len(member_list):
            return f"無効な番号です。1〜{len(member_list)} を入力してください。"

        member_key, member = member_list[num - 1]
        default_mode, room_modes = load_talk_modes(member["dir"])
        default_name = TALK_MODES.get(default_mode, {}).get("name", "不明")
        room_names = _get_room_names(member.get("cw_token", ""))

        lines = [f"[info][title]{num}:{member['name']}の会話モード[/title]"]
        lines.append(f"デフォルト: {default_mode}（{default_name}）")
        if room_modes:
            lines.append(f"ルーム別設定:")
            for rid, mode in sorted(room_modes.items()):
                lines.append(_room_display(rid, mode, room_names))
        else:
            lines.append(f"ルーム別設定: なし")
        lines.append(f"1: ルーム別会話設定追加")
        lines.append(f"2: ルーム別会話設定変更")
        lines.append(f"3: ルーム別会話設定削除")
        lines.append(f"番号を返信してください。[/info]")

        state.talk_session = {
            "state": "select_action",
            "member_key": member_key,
            "member_num": num,
        }
        return "\n".join(lines)

    # --- アクション選択 ---
    if current_state == "select_action":
        try:
            action = int(raw_input)
        except ValueError:
            state.talk_session = {}
            return "キャンセルしました。"

        member_key = session["member_key"]
        member = MEMBERS[member_key]

        if action == 1:
            state.talk_session["state"] = "add_room_id"
            return "追加するルームIDを返信してください。"

        if action == 2:
            _, room_modes = load_talk_modes(member["dir"])
            if not room_modes:
                state.talk_session = {}
                return "ルーム別設定がありません。先に追加してください。"
            room_names = _get_room_names(member.get("cw_token", ""))
            lines = ["変更するルームIDを返信してください。"]
            for rid, mode in sorted(room_modes.items()):
                lines.append(_room_display(rid, mode, room_names))
            state.talk_session["state"] = "change_room_id"
            return "\n".join(lines)

        if action == 3:
            _, room_modes = load_talk_modes(member["dir"])
            if not room_modes:
                state.talk_session = {}
                return "ルーム別設定がありません。"
            room_names = _get_room_names(member.get("cw_token", ""))
            lines = ["削除するルームIDを返信してください。"]
            for rid, mode in sorted(room_modes.items()):
                lines.append(_room_display(rid, mode, room_names))
            state.talk_session["state"] = "delete_room_id"
            return "\n".join(lines)

        return "1〜3の番号を入力してください。"

    # --- 追加: ルームID入力 ---
    if current_state == "add_room_id":
        room_id = _extract_room_id(raw_input)
        if not room_id.isdigit():
            return "ルームIDは数字で入力してください。"
        member = MEMBERS[session["member_key"]]
        room_names = _get_room_names(member.get("cw_token", ""))
        rname = room_names.get(room_id, room_id)
        state.talk_session["target_room_id"] = room_id
        state.talk_session["target_room_name"] = rname
        state.talk_session["state"] = "add_room_mode"
        return f"ルーム「{rname}」の会話モードを返信してください（{_mode_options_str()}）"

    # --- 追加: モード入力 ---
    if current_state == "add_room_mode":
        try:
            new_mode = int(raw_input)
        except ValueError:
            return f"0〜{max(TALK_MODES.keys())} の数字を入力してください。"
        if new_mode not in TALK_MODES:
            return f"無効なモードです。0〜{max(TALK_MODES.keys())} を指定してください。"

        member = MEMBERS[session["member_key"]]
        target_room = session["target_room_id"]
        rname = session.get("target_room_name", target_room)
        err = _write_mode_env(member["dir"], target_room, new_mode)
        state.talk_session = {}
        if err:
            return err
        member_num = session.get("member_num", "")
        mode_name = TALK_MODES[new_mode]["name"]
        log.info(f"/talk 対話: {member['name']} room={target_room} 追加 → {new_mode}({mode_name})")
        return f"{member_num}:{member['name']}のルーム別会話設定を追加しました。\nルーム「{rname}」→ {new_mode}（{mode_name}）"

    # --- 変更: ルームID入力 ---
    if current_state == "change_room_id":
        room_id = _extract_room_id(raw_input)
        member = MEMBERS[session["member_key"]]
        _, room_modes = load_talk_modes(member["dir"])
        if room_id not in room_modes:
            return f"ルームID {room_id} のルーム別設定はありません。正しいルームIDを入力してください。"
        current_mode = room_modes[room_id]
        current_name = TALK_MODES.get(current_mode, {}).get("name", "不明")
        room_names = _get_room_names(member.get("cw_token", ""))
        rname = room_names.get(room_id, room_id)
        state.talk_session["target_room_id"] = room_id
        state.talk_session["target_room_name"] = rname
        state.talk_session["state"] = "change_room_mode"
        return f"ルーム「{rname}」の会話モードは {current_mode}（{current_name}）です。\n変更する会話モードを返信してください（{_mode_options_str()}）"

    # --- 変更: モード入力 ---
    if current_state == "change_room_mode":
        try:
            new_mode = int(raw_input)
        except ValueError:
            return f"0〜{max(TALK_MODES.keys())} の数字を入力してください。"
        if new_mode not in TALK_MODES:
            return f"無効なモードです。0〜{max(TALK_MODES.keys())} を指定してください。"

        member = MEMBERS[session["member_key"]]
        target_room = session["target_room_id"]
        rname = session.get("target_room_name", target_room)
        err = _write_mode_env(member["dir"], target_room, new_mode)
        state.talk_session = {}
        if err:
            return err
        member_num = session.get("member_num", "")
        mode_name = TALK_MODES[new_mode]["name"]
        log.info(f"/talk 対話: {member['name']} room={target_room} 変更 → {new_mode}({mode_name})")
        return f"{member_num}:{member['name']}のルーム別会話設定を変更しました。\nルーム「{rname}」→ {new_mode}（{mode_name}）"

    # --- 削除: ルームID入力 ---
    if current_state == "delete_room_id":
        room_id = _extract_room_id(raw_input)
        member = MEMBERS[session["member_key"]]
        _, room_modes = load_talk_modes(member["dir"])
        if room_id not in room_modes:
            state.talk_session = {}
            return f"ルームID {room_id} のルーム別設定はありません。"
        current_mode = room_modes[room_id]
        current_name = TALK_MODES.get(current_mode, {}).get("name", "不明")
        room_names = _get_room_names(member.get("cw_token", ""))
        rname = room_names.get(room_id, room_id)
        state.talk_session["target_room_id"] = room_id
        state.talk_session["target_room_name"] = rname
        state.talk_session["state"] = "delete_confirm"
        return f"ルーム「{rname}」の会話モードは {current_mode}（{current_name}）です。削除しますか？（y/n）"

    # --- 削除: 確認 ---
    if current_state == "delete_confirm":
        if raw_input.strip().lower() in ("y", "yes"):
            member = MEMBERS[session["member_key"]]
            member_num = session.get("member_num", "")
            target_room = session["target_room_id"]
            rname = session.get("target_room_name", target_room)
            err = _delete_room_mode(member["dir"], target_room)
            state.talk_session = {}
            if err:
                return err
            log.info(f"/talk 対話: {member['name']} room={target_room} 削除")
            return f"{member_num}:{member['name']}のルーム「{rname}」の会話モード設定を削除しました。デフォルトとなります。"
        state.talk_session = {}
        return "キャンセルしました。"

    # 不明な状態 → リセット
    state.talk_session = {}
    return None


def handle_talk_change(member: dict[str, Any], room_id: str, new_mode: int) -> str:
    """/talk N URL M: ルーム別の会話モードを変更する（ショートカット用）"""
    if new_mode not in TALK_MODES:
        return f"無効なモードです。0〜{max(TALK_MODES.keys())} を指定してください。"
    err = _write_mode_env(member["dir"], room_id, new_mode)
    if err:
        return err
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

        lines.append(f"状態: 全テスト合格（{', '.join(results)}）")

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
