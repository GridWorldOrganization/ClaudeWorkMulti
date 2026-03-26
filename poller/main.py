"""
メインループ・起動処理・シグナルハンドラ
"""

import glob
import json
import logging
import os
import signal
import subprocess
import threading
import time
from typing import Any

import boto3
import requests

from poller.config import (
    AWS_REGION,
    CHATWORK_API_BASE,
    CHATWORK_API_TIMEOUT,
    CLAUDE_COMMAND,
    CLAUDE_MODEL,
    CLAUDE_TIMEOUT,
    DEBUG_NOTICE_CHATWORK_ACCOUNT_ID,
    DEBUG_NOTICE_CHATWORK_ROOM_ID,
    DEBUG_NOTICE_CHATWORK_TOKEN,
    DEBUG_NOTICE_ENABLED,
    FOLLOWUP_WAIT_SECONDS,
    MAX_AI_CONVERSATION_TURNS,
    MEMBERS,
    MEMBERS_DIR,
    POLL_INTERVAL,
    QUEUE_URL,
    REPLY_COOLDOWN_SECONDS,
    SCRIPT_DIR,
    SQS_WAIT_TIME_SECONDS,
    USE_DIRECT_API,
    ANTHROPIC_API_KEY,
    find_member_key,
)
from poller import state
from poller.ai_runner import cleanup, kill_orphan_processes
from poller.processor import find_target_member, process_member_batch

log = logging.getLogger(__name__)


# =============================================================================
#  SQS ユーティリティ
# =============================================================================

def _get_queue_count(sqs: Any) -> int:
    """キュー内の待機メッセージ概数を取得する"""
    try:
        attrs = sqs.get_queue_attributes(
            QueueUrl=QUEUE_URL,
            AttributeNames=["ApproximateNumberOfMessages"],
        )
        return int(attrs["Attributes"].get("ApproximateNumberOfMessages", 0))
    except Exception:
        return -1


def _drain_sqs_queue(sqs: Any) -> list[dict[str, Any]]:
    """SQS キューからメッセージを全件読み込む"""
    all_messages: list[dict[str, Any]] = []
    is_first = True
    while True:
        wait_time = SQS_WAIT_TIME_SECONDS if is_first else 0
        res = sqs.receive_message(
            QueueUrl=QUEUE_URL,
            MaxNumberOfMessages=10,
            WaitTimeSeconds=wait_time,
        )
        is_first = False
        batch = res.get("Messages", [])
        if not batch:
            break
        all_messages.extend(batch)
        remaining = _get_queue_count(sqs)
        log.info(f"キュー読み込み中: 今回{len(batch)}件, 累計{len(all_messages)}件, 残り約{remaining}件")
        if remaining == 0:
            break
    return all_messages


def _is_debug_room_message(body_data: dict[str, Any]) -> bool:
    """デバッグルーム宛のメッセージか判定する"""
    if not DEBUG_NOTICE_CHATWORK_ACCOUNT_ID or not DEBUG_NOTICE_CHATWORK_ROOM_ID:
        return False
    room_id = body_data.get("room_id", "")
    message = body_data.get("body", "")
    return (
        str(room_id) == str(DEBUG_NOTICE_CHATWORK_ROOM_ID)
        and (f"[To:{DEBUG_NOTICE_CHATWORK_ACCOUNT_ID}]" in message
             or f"[rp aid={DEBUG_NOTICE_CHATWORK_ACCOUNT_ID} " in message)
    )


def _process_debug_message(body_data: dict[str, Any], msg: dict[str, Any], sqs: Any) -> None:
    """デバッグルームのメッセージをロックなしで即時処理する"""
    try:
        from poller.processor import process_message
        process_message(body_data)
    except Exception as e:
        log.error(f"デバッグメッセージ処理エラー: {e}")
    finally:
        try:
            sqs.delete_message(QueueUrl=QUEUE_URL, ReceiptHandle=msg["ReceiptHandle"])
        except Exception as e:
            log.error(f"SQSメッセージ削除エラー: {e}")


def _dispatch_messages(all_messages: list[dict[str, Any]], sqs: Any) -> None:
    """メッセージをメンバーごとにグループ化し、並列スレッドで処理する（ノンブロッキング）"""
    member_messages: dict[str, list[tuple[dict, dict]]] = {}

    for msg in all_messages:
        try:
            body_data = json.loads(msg["Body"])

            # デバッグルーム宛 → メンバーロックを経由せず即時処理（別スレッド）
            if _is_debug_room_message(body_data):
                t = threading.Thread(
                    target=_process_debug_message, args=(body_data, msg, sqs), daemon=True,
                )
                t.start()
                log.info(f"デバッグメッセージ即時処理スレッド起動")
                continue

            member = find_target_member(body_data)
            if not member:
                member = MEMBERS[next(iter(MEMBERS))]
            key = find_member_key(member) or next(iter(MEMBERS))
            member_messages.setdefault(key, []).append((body_data, msg))
        except Exception as e:
            log.error(f"メッセージ解析エラー: {e}")
            sqs.delete_message(QueueUrl=QUEUE_URL, ReceiptHandle=msg["ReceiptHandle"])

    for mk, msg_list in member_messages.items():
        t = threading.Thread(target=process_member_batch, args=(mk, msg_list, sqs), daemon=True)
        t.start()
        log.info(f"バッチスレッド起動: {mk} ({len(msg_list)}件)")


# =============================================================================
#  メインエントリポイント
# =============================================================================

def main() -> None:
    """ポーラーのメインエントリポイント"""

    # --- 起動前チェック ---
    if not MEMBERS:
        log.error("メンバーが1人も見つかりません。members/ 配下にメンバーフォルダと member.env を作成してください")
        return

    errors: list[str] = []
    if not QUEUE_URL:
        errors.append("SQS_QUEUE_URL が未設定です → config.env に SQS_QUEUE_URL=https://... を追加してください")
    if USE_DIRECT_API and not ANTHROPIC_API_KEY:
        errors.append("ANTHROPIC_API_KEY が未設定です → config.env に Anthropic API キーを設定してください（USE_DIRECT_API=1 の場合は必須）")
    for key, member in MEMBERS.items():
        if not member["cw_token"]:
            errors.append(f"{member['name']} の CHATWORK_API_TOKEN が未設定です → members/{key}/member.env にChatWork APIトークンを設定してください")
        if not member.get("allowed_rooms"):
            log.warning(f"[{member['name']}] ALLOWED_ROOMS が空のため全ルーム送信不可です")
    if errors:
        log.error("=== 起動失敗 ===")
        for e in errors:
            log.error(f"  {e}")
        return

    for key, member in MEMBERS.items():
        if not os.path.isdir(member["dir"]):
            log.error(f"作業フォルダが見つかりません: {member['dir']}")
            return

    # --- デバッグ通知チェック ---
    if DEBUG_NOTICE_ENABLED:
        if not DEBUG_NOTICE_CHATWORK_TOKEN or DEBUG_NOTICE_CHATWORK_ROOM_ID == 0:
            log.warning("=== デバッグ通知: 設定不完全 ===")
            if not DEBUG_NOTICE_CHATWORK_TOKEN:
                log.warning("  DEBUG_NOTICE_CHATWORK_TOKEN が未設定です")
            if DEBUG_NOTICE_CHATWORK_ROOM_ID == 0:
                log.warning("  DEBUG_NOTICE_CHATWORK_ROOM_ID が未設定です")
            log.warning("  デバッグ通知は無効になります（ポーラーは起動を継続します）")
        else:
            try:
                res = requests.get(
                    f"{CHATWORK_API_BASE}/rooms/{DEBUG_NOTICE_CHATWORK_ROOM_ID}",
                    headers={"X-ChatWorkToken": DEBUG_NOTICE_CHATWORK_TOKEN},
                    timeout=CHATWORK_API_TIMEOUT,
                )
                if res.status_code == 200:
                    room_name = res.json().get("name", "")
                    log.info(f"デバッグ通知: 接続OK (room={room_name})")
                else:
                    log.warning(f"デバッグ通知: API応答エラー (status={res.status_code})")
            except Exception as e:
                log.warning(f"デバッグ通知: API接続失敗 ({e})")
    else:
        log.info("デバッグ通知: 無効（DEBUG_NOTICE_ENABLED=0）")

    sqs = boto3.client("sqs", region_name=AWS_REGION)

    # --- 起動時キューパージ ---
    try:
        sqs.purge_queue(QueueUrl=QUEUE_URL)
        log.info("起動時キューパージ完了")
    except Exception as e:
        log.warning(f"キューパージスキップ（前回パージから60秒以内の可能性）: {e}")

    # --- 起動ログ ---
    from poller.config import VERSION
    log.info(f"=== ChatWork Webhook Poller v{VERSION} 起動 ===")
    log.info(f"キュー: {QUEUE_URL}")
    poll_mode = f"ロング（WaitTime={SQS_WAIT_TIME_SECONDS}秒）" if SQS_WAIT_TIME_SECONDS > 0 else f"ショート（間隔={POLL_INTERVAL}秒）"
    log.info(f"ポーリングモード: {poll_mode}")
    log.info("処理方式: バッチ+並列（キュー全件読み込み → メンバーごとにスレッド処理）")

    log.info(f"--- config.env ---")
    log.info(f"  USE_DIRECT_API = {'API直接' if USE_DIRECT_API else 'CLI'}")
    if not USE_DIRECT_API:
        log.info(f"  CLAUDE_COMMAND = {CLAUDE_COMMAND}")
        try:
            where_cmd = "where" if os.name == "nt" else "which"
            where_result = subprocess.run(
                [where_cmd, CLAUDE_COMMAND], capture_output=True, text=True, timeout=5,
            )
            if where_result.returncode == 0:
                cmd_path = where_result.stdout.strip().split("\n")[0]
                cli_type = "Native" if cmd_path.endswith(".exe") else ("npm" if cmd_path.endswith(".cmd") else "不明")
                log.info(f"  CLAUDE_PATH = {cmd_path} [{cli_type}]")
            else:
                log.warning(f"  CLAUDE_PATH = 検出失敗（'{CLAUDE_COMMAND}' が PATH に見つかりません）")
        except Exception as e:
            log.warning(f"  CLAUDE_PATH = 検出失敗（{e}）")
    log.info(f"  CLAUDE_MODEL = {CLAUDE_MODEL}")
    log.info(f"  CLAUDE_TIMEOUT = {CLAUDE_TIMEOUT}秒")
    log.info(f"  FOLLOWUP_WAIT_SECONDS = {FOLLOWUP_WAIT_SECONDS}秒")
    log.info(f"  MAX_AI_CONVERSATION_TURNS = {MAX_AI_CONVERSATION_TURNS}ターン")
    log.info(f"  REPLY_COOLDOWN_SECONDS = {REPLY_COOLDOWN_SECONDS}秒")

    log.info(f"--- メンバー ({len(MEMBERS)}名) ---")
    for idx, (key, member) in enumerate(MEMBERS.items(), 1):
        rooms = member.get("allowed_rooms", set())
        rooms_str = ", ".join(sorted(rooms)) if rooms else "なし（全送信不可）"
        token_status = "設定済" if member["cw_token"] else "未設定"
        log.info(f"  [{idx}/{len(MEMBERS)}] {member['name']} ({key}) account_id={member['account_id']} cw_token={token_status}")
        log.info(f"    許可ルーム: [{rooms_str}]")
        common_files = sorted(glob.glob(os.path.join(MEMBERS_DIR, "00_*.md")))
        member_files = sorted(
            f for f in glob.glob(os.path.join(member["dir"], "*.md"))
            if not os.path.basename(f).startswith("room_")
            and not os.path.basename(f).startswith("chat_history_")
            and os.path.basename(f) != "CLAUDE.md"
        )
        room_specific = sorted(glob.glob(os.path.join(member["dir"], "room_*.md")))
        all_instruction_files = common_files + member_files + room_specific
        file_names = ", ".join(os.path.basename(f) for f in all_instruction_files)
        log.info(f"    指示ファイル: {len(all_instruction_files)}件 [{file_names}]")

    # --- プロンプトチェッカー（指示ファイルにChatWork固有情報がないか検査）---
    _ng_keywords = ["chatwork", "チャットワーク", "[To:", "[rp ", "account_id", "アカウントID", "ルームID"]
    _prompt_warnings = []
    for key, member in MEMBERS.items():
        _common = sorted(glob.glob(os.path.join(MEMBERS_DIR, "00_*.md")))
        _member_md = sorted(
            f for f in glob.glob(os.path.join(member["dir"], "*.md"))
            if not os.path.basename(f).startswith("room_")
            and not os.path.basename(f).startswith("chat_history_")
            and os.path.basename(f) != "CLAUDE.md"
        )
        _room_md = sorted(glob.glob(os.path.join(member["dir"], "room_*.md")))
        for md_path in _common + _member_md + _room_md:
            try:
                with open(md_path, "r", encoding="utf-8") as f:
                    content = f.read().lower()
                for kw in _ng_keywords:
                    if kw.lower() in content:
                        _prompt_warnings.append(f"  [{member['name']}] {os.path.basename(md_path)} に '{kw}' を検出")
                        break
            except Exception:
                pass
    if _prompt_warnings:
        log.warning("--- プロンプトチェッカー: 警告 ---")
        log.warning("指示ファイルにChatWork固有情報が含まれています（Claudeの拒否原因になり得ます）:")
        for w in _prompt_warnings:
            log.warning(w)
    else:
        log.info("プロンプトチェッカー: OK（ChatWork固有情報なし）")

    # --- 残留プロセス cleanup ---
    orphans = kill_orphan_processes()
    if orphans:
        log.info(f"残留プロセス {orphans}件 をkillしました")

    # --- 起動通知（デバッグ通知ルームに投稿）---
    if DEBUG_NOTICE_ENABLED and DEBUG_NOTICE_CHATWORK_TOKEN and DEBUG_NOTICE_CHATWORK_ROOM_ID:
        from poller.chatwork import chatwork_post
        member_names = ", ".join(m["name"] for m in MEMBERS.values())
        ai_mode = "API直接" if USE_DIRECT_API else "CLI"
        from poller.config import VERSION
        poll_label = "ロング" if SQS_WAIT_TIME_SECONDS > 0 else "ショート"
        startup_msg = f"Poller v{VERSION} 起動 / {member_names}({len(MEMBERS)}名) / {ai_mode} {CLAUDE_MODEL} / {poll_label}"
        chatwork_post(DEBUG_NOTICE_CHATWORK_TOKEN, DEBUG_NOTICE_CHATWORK_ROOM_ID, startup_msg)

    # --- ポーリングループ ---
    while not state.shutdown_requested:
        try:
            all_messages = _drain_sqs_queue(sqs)
            if not all_messages:
                time.sleep(POLL_INTERVAL)
                continue

            log.info(f"キュー読み込み完了: 合計{len(all_messages)}件")
            _dispatch_messages(all_messages, sqs)

        except Exception as e:
            log.error(f"ポーリングエラー: {e}")
            time.sleep(10)
            continue

        time.sleep(POLL_INTERVAL)

    log.info("=== ChatWork Webhook Poller 停止 ===")


# =============================================================================
#  シグナルハンドラ
# =============================================================================

def signal_handler(sig: int, frame: Any) -> None:
    """Ctrl+C / SIGTERM で graceful shutdown を開始する"""
    state.shutdown_requested = True
    log.info("シャットダウン要求を受信。実行中のAIプロセスを終了します...")
    cleanup()
    log.info("シャットダウン処理完了。ポーラーを停止します。")
