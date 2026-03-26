"""
メッセージ処理

SQS メッセージの解析・ガード判定・プロンプト構築・AI 実行・返信投稿を行う。
"""

import glob
import logging
import os
import re
import subprocess
import time
from datetime import datetime
from typing import Any

import requests

from poller.config import (
    AI_REFUSAL_KEYWORDS,
    ALL_MEMBER_IDS,
    CASUAL_CHAT_KEYWORDS,
    CASUAL_CHAT_MAX_LENGTH,
    CHATWORK_API_BASE,
    CHATWORK_API_TIMEOUT,
    CLAUDE_MODEL,
    CLAUDE_TIMEOUT,
    DEBUG_NOTICE_CHATWORK_ACCOUNT_ID,
    DEBUG_NOTICE_CHATWORK_ROOM_ID,
    DEBUG_NOTICE_CHATWORK_TOKEN,
    FOLLOWUP_KEYWORDS,
    FOLLOWUP_WAIT_SECONDS,
    MAX_AI_CONVERSATION_TURNS,
    MEMBERS,
    MEMBERS_DIR,
    REPLY_COOLDOWN_SECONDS,
    TALK_MODES,
    find_member_key,
    get_talk_mode,
)
from poller import state
from poller.chatwork import (
    build_rp_header,
    chatwork_post,
    gather_room_context,
    get_message_info,
    notify_error,
)
from poller.ai_runner import AIResult, ai_mode_label, run_ai
from poller.google_workspace import resolve_urls
from poller.commands import (
    handle_help,
    handle_status,
    handle_session,
    handle_system,
    handle_bill,
    handle_talk_start,
    handle_talk_session_reply,
    handle_talk_change,
    handle_gws,
)

log = logging.getLogger(__name__)


# =============================================================================
#  ユーティリティ
# =============================================================================

# =============================================================================
#  名前マスキング（ChatWork 固有情報の匿名化）
# =============================================================================

_PSEUDONYMS = [
    "AIアシスタントA子", "AIアシスタントB子", "AIアシスタントC子", "AIアシスタントD子", "AIアシスタントE子",
    "AIアシスタントF子", "AIアシスタントG子", "AIアシスタントH子", "AIアシスタントI子", "AIアシスタントJ子",
]


def _build_name_mapping(self_member: dict[str, Any]) -> dict[str, str]:
    """登録メンバー名 → 仮名の対応表を構築する。自分自身は除外"""
    mapping: dict[str, str] = {}
    pseudo_idx = 0
    self_aid = str(self_member["account_id"])
    for member in MEMBERS.values():
        if str(member["account_id"]) == self_aid:
            continue
        if pseudo_idx >= len(_PSEUDONYMS):
            break
        pseudo = _PSEUDONYMS[pseudo_idx]
        name = member["name"]
        # フルネーム（スペースあり/なし両方）
        mapping[name] = pseudo
        if " " in name or "　" in name:
            no_space = name.replace(" ", "").replace("　", "")
            mapping[no_space] = pseudo
            # 姓・名それぞれ（2文字以上のみ）
            parts = re.split(r'[\s　]+', name)
            for part in parts:
                if len(part) >= 2 and part not in mapping:
                    mapping[part] = pseudo
        pseudo_idx += 1
    return mapping


def _mask_names(text: str, mapping: dict[str, str]) -> str:
    """テキスト内の登録メンバー名を仮名に置換する（長い名前から優先）"""
    if not text or not mapping:
        return text
    for real_name in sorted(mapping.keys(), key=len, reverse=True):
        text = text.replace(real_name, mapping[real_name])
    return text


def _is_casual_chat(message: str) -> bool:
    """メッセージが雑談かどうかを判定する（モード0用）"""
    text = re.sub(r'\[To:\d+\][^\n]*\n?', '', message).strip()
    if not text:
        return True
    if len(text) <= CASUAL_CHAT_MAX_LENGTH:
        text_lower = text.lower()
        for kw in CASUAL_CHAT_KEYWORDS:
            if kw.lower() in text_lower:
                return True
    return False


def _is_ai_refusal(reply_text: str) -> bool:
    """AIの返信がセーフティフィルタによる拒否かどうかを判定する"""
    # キーワード部分一致
    for kw in AI_REFUSAL_KEYWORDS:
        if kw in reply_text:
            return True
    # 構造パターン: 拒否 + 理由列挙
    refusal_words = ["できません", "お断り", "対応できません", "サポートできません", "応じられません"]
    reason_words = ["理由", "以下の点", "以下の理由", "問題があります"]
    has_refusal = any(w in reply_text for w in refusal_words)
    has_reason = any(w in reply_text for w in reason_words)
    if has_refusal and has_reason:
        return True
    # 構造パターン: 拒否 + 代替提案
    alternative_words = ["代わりに", "お手伝いできます", "サポートできます", "以下のような"]
    if has_refusal and any(w in reply_text for w in alternative_words):
        return True
    # 構造パターン: なりすまし/不適切 関連
    impersonation_words = ["なりすまし", "不適切", "倫理的", "安全上", "ポリシー"]
    if any(w in reply_text for w in impersonation_words) and has_refusal:
        return True
    return False


def _needs_followup(reply_text: str) -> bool:
    """返信テキストにフォローアップが必要なキーワードが含まれているか"""
    return any(kw in reply_text for kw in FOLLOWUP_KEYWORDS)


def _load_instructions(member_dir: str, room_id: str = "") -> str:
    """共通ルール + メンバー固有 + ルーム固有の .md を読み込み、指示文を構築する"""
    common_files = sorted(glob.glob(os.path.join(MEMBERS_DIR, "00_*.md")))
    member_files = sorted(
        f for f in glob.glob(os.path.join(member_dir, "*.md"))
        if not os.path.basename(f).startswith("room_")
        and not os.path.basename(f).startswith("chat_history_")
        and os.path.basename(f) != "CLAUDE.md"
    )
    room_files: list[str] = []
    if room_id:
        room_md = os.path.join(member_dir, f"room_{room_id}.md")
        if os.path.exists(room_md):
            room_files = [room_md]

    instructions: list[str] = []
    for md_path in common_files + member_files + room_files:
        try:
            with open(md_path, "r", encoding="utf-8") as f:
                content = f.read().strip()
            if content:
                instructions.append(f"--- {os.path.basename(md_path)} ---\n{content}")
                log.info(f"指示ファイル読み込み: {os.path.basename(md_path)}")
        except Exception as e:
            log.error(f"指示ファイル読み込みエラー: {md_path}: {e}")

    return "\n\n".join(instructions) if instructions else "受信したメッセージに対して、丁寧に日本語で返信してください。"


def _save_chat_history(member_dir: str, room_id: str, sender_name: str,
                       message: str, reply: str, member_name: str) -> None:
    """会話記録をメンバーフォルダに追記する"""
    history_file = os.path.join(member_dir, f"chat_history_{room_id}.md")
    try:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(history_file, "a", encoding="utf-8") as f:
            f.write(f"\n---\n### {now}\n")
            f.write(f"**{sender_name}**: {message}\n\n")
            f.write(f"**{member_name}**: {reply}\n")
        log.info(f"会話記録保存: {history_file}")
    except Exception as e:
        log.error(f"会話記録保存エラー: {e}")


# =============================================================================
#  AI 同士の会話制御
# =============================================================================

def _check_ai_conversation_allowed(room_id: str, sender: str) -> bool:
    """AI 同士の会話が許可されているか判定する"""
    with state.chain_lock:
        if str(sender) not in ALL_MEMBER_IDS:
            state.conversation_chains[str(room_id)] = {"count": 0, "last_human_time": time.time()}
            return True
        chain = state.conversation_chains.get(str(room_id))
        if not chain:
            log.info(f"AI発言だが会話チェーンなし: room={room_id}, sender={sender}")
            return False
        chain["count"] += 1
        if chain["count"] > MAX_AI_CONVERSATION_TURNS:
            log.info(f"AI会話上限到達: room={room_id}, count={chain['count']}/{MAX_AI_CONVERSATION_TURNS}")
            return False
        log.info(f"AI会話許可: room={room_id}, count={chain['count']}/{MAX_AI_CONVERSATION_TURNS}")
        return True


# =============================================================================
#  送信者情報
# =============================================================================

def _resolve_sender(body: dict[str, Any], member: dict[str, Any]) -> tuple[str, str]:
    """SQS メッセージから送信者情報を補完して返す"""
    sender = body.get("sender_account_id", "")
    sender_name = body.get("sender_name", "")
    message_id = body.get("message_id", "")
    room_id = body.get("room_id", "")

    if message_id and (not sender or not sender_name):
        info = get_message_info(member["cw_token"], room_id, message_id)
        if info:
            if not sender:
                sender = info["account_id"]
                log.info(f"sender補完(API): {sender}")
            if not sender_name:
                sender_name = info["name"]
                log.info(f"sender_name補完(API): {sender_name}")
    return sender, sender_name


def _apply_reply_tag(reply: str, token: str, room_id: str, sender: str, message_id: str) -> str:
    """AI の返信に [rp] タグを自動付与する"""
    if reply.startswith("[To:") or reply.startswith("[rp "):
        log.info("AI出力にタグあり、自動付与スキップ")
        return reply
    rp_header = build_rp_header(token, room_id, sender, message_id)
    if rp_header:
        log.info(f"[rp]タグ付与: {rp_header}")
        return f"{rp_header}\n{reply}"
    return reply


# =============================================================================
#  フォローアップ
# =============================================================================

def _handle_followup(member: dict[str, Any], member_dir: str, instructions: str,
                     message: str, raw_reply: str, room_id: str,
                     sender: str, sender_name: str, message_id: str) -> None:
    """フォローアップ処理: 「確認します」系の返信を検知し、情報収集後に再返信する"""
    if not _needs_followup(raw_reply):
        return

    log.info(f"フォローアップ検出: {FOLLOWUP_WAIT_SECONDS}秒待機します")
    time.sleep(FOLLOWUP_WAIT_SECONDS)

    room_context = gather_room_context(member["cw_token"], room_id)
    log.info("ルーム情報収集完了")

    # フォローアップでも名前マスキングを適用
    fu_name_map = _build_name_mapping(member)
    masked_message = _mask_names(message, fu_name_map)
    masked_context = _mask_names(room_context, fu_name_map)

    followup_prompt = (
        f"あなたはAIアシスタントです。「{member['name']}」というキャラクター設定で会話してください。\n"
        f"先ほど「{raw_reply}」と返信しましたが、情報を収集できましたので、フォローアップの返信をしてください。\n"
        f"余計な説明や前置きは不要です。返信本文だけを出力してください。\n"
        f"タグやメタ情報は一切含めないでください。\n\n"
        f"=== 返信の指示 ===\n{instructions}\n\n"
        f"=== 元の受信メッセージ ===\n{masked_message}\n\n"
        f"=== 収集した情報 ===\n{masked_context}\n\n"
        f"上記の情報をもとに、先ほどの「確認します」に対するフォローアップ返信を作成してください。"
    )

    try:
        result = run_ai(followup_prompt, member_dir, f"{member['name']}(フォローアップ)")
        followup_reply = result.output.strip() if result.output else ""
        if result.returncode == 0 and followup_reply:
            log.info(f"フォローアップ返信 [{member['name']}]: {followup_reply[:500]}")
            # 仮名 → 実名に復元
            fu_reverse = {v: k for k, v in fu_name_map.items() if " " not in k and "　" not in k}
            for real, pseudo in fu_name_map.items():
                if (" " in real or "　" in real) and pseudo not in fu_reverse:
                    fu_reverse[pseudo] = real
            followup_reply = _mask_names(followup_reply, fu_reverse)
            rp_header = build_rp_header(member["cw_token"], room_id, sender, message_id)
            if rp_header:
                followup_reply = f"{rp_header}\n{followup_reply}"
            chatwork_post(member["cw_token"], room_id, followup_reply)
            chatwork_post(member["cw_token"], room_id, "おやすみなさい")
            log.info("フォローアップ完了")
        else:
            log.warning(f"フォローアップ返信が空またはエラー: exit={result.returncode}")
    except Exception as e:
        log.error(f"フォローアップ実行エラー: {e}")


# =============================================================================
#  宛先メンバー特定
# =============================================================================

def find_target_member(body: dict[str, Any]) -> dict[str, Any] | None:
    """SQS メッセージの本文から宛先メンバーを特定する"""
    from poller.config import ACCOUNT_TO_MEMBER

    message = body.get("body", "")
    for member in MEMBERS.values():
        aid = str(member["account_id"])
        if f"[To:{aid}]" in message or f"[rp aid={aid} " in message:
            return member
    owner_id = body.get("webhook_owner_account_id")
    if owner_id:
        owner_id_int = int(owner_id) if str(owner_id).isdigit() else None
        if owner_id_int and owner_id_int in ACCOUNT_TO_MEMBER:
            return ACCOUNT_TO_MEMBER[owner_id_int]
    return None


# =============================================================================
#  メッセージ処理（メインのビジネスロジック）
# =============================================================================

def process_message(body: dict[str, Any]) -> None:
    """
    SQS メッセージ 1 件を処理する。

    1. 宛先メンバーを特定
    2. ガード条件チェック（自己発言 / コマンド / ホワイトリスト / AI 会話上限）
    3. プロンプトを構築して AI を実行
    4. 返信にタグを付与して ChatWork に投稿
    5. 必要に応じてフォローアップ
    """
    room_id = body.get("room_id", "")
    message_id = body.get("message_id", "")
    message = body.get("body", "")
    event_type = body.get("webhook_event_type", "")
    timestamp = body.get("timestamp", "")

    # --- コマンド判定（デバッグ専用アカウント宛 → MEMBERS 外でも処理）---
    raw_command = re.sub(r'\[To:\d+\][^\n]*\n', '', message.strip()).strip()
    _COMMAND_KEYWORDS = {"/help", "/status", "/session", "/talk", "/sysinfo", "/bill", "/gws"}
    is_command = (raw_command in _COMMAND_KEYWORDS
                  or re.match(r'^/talk\s+\d', raw_command)
                  or re.match(r'^/status\s+\d+$', raw_command))

    # /system コマンド検知 → 宛先に関わらずブロックし、デバッグルームに通知
    if raw_command == "/system":
        log.info(f"/system 検知: room={room_id} → 無視")
        if DEBUG_NOTICE_CHATWORK_TOKEN and DEBUG_NOTICE_CHATWORK_ROOM_ID:
            chatwork_post(DEBUG_NOTICE_CHATWORK_TOKEN, DEBUG_NOTICE_CHATWORK_ROOM_ID,
                          "/systemを検知しました。無視します。")
        return

    # デバッグルーム宛のメッセージかチェック（[To:] または [rp] タグ）
    is_debug_msg = (
        DEBUG_NOTICE_CHATWORK_ACCOUNT_ID
        and DEBUG_NOTICE_CHATWORK_ROOM_ID
        and str(room_id) == str(DEBUG_NOTICE_CHATWORK_ROOM_ID)
        and (f"[To:{DEBUG_NOTICE_CHATWORK_ACCOUNT_ID}]" in message
             or f"[rp aid={DEBUG_NOTICE_CHATWORK_ACCOUNT_ID} " in message)
    )

    # デバッグアカウント宛の非コマンドメッセージ → セッション応答 or 無視（AIには渡さない）
    if is_debug_msg and not is_command:
        session_input = re.sub(
            r'\[(To:\d+[^\]]*|rp aid=\d+ to=\d+-\d+)\][^\n]*\n?', '', message.strip()
        ).strip()
        if session_input:
            with state.talk_session_lock:
                reply = handle_talk_session_reply(session_input)
            if reply:
                log.info(f"/talk 対話セッション応答: input='{session_input}'")
                chatwork_post(DEBUG_NOTICE_CHATWORK_TOKEN, room_id, reply)
                return
        log.info(f"デバッグアカウント宛の非コマンドメッセージ: '{raw_command}' → 定型返信")
        chatwork_post(DEBUG_NOTICE_CHATWORK_TOKEN, room_id, "...。")
        return

    # デバッグ専用アカウント宛のコマンドを先に処理（MEMBERS に含まれなくても動作）
    if is_command and is_debug_msg:
        debug_token = DEBUG_NOTICE_CHATWORK_TOKEN
        log.info(f"デバッグコマンド '{raw_command}' 検出 (room={room_id})")

        if raw_command == "/help":
            chatwork_post(debug_token, room_id, handle_help())
            return
        status_match = re.match(r'^/status\s+(\d+)$', raw_command)
        if raw_command == "/status":
            # /status（引数なし）: メンバー番号一覧を返す
            lines = ["[info][title]/status: メンバー一覧[/title]"]
            for idx, (key, m) in enumerate(MEMBERS.items(), 1):
                lines.append(f"  {idx}. {m['name']} ({key})")
            lines.append(f"\n詳細: /status [番号]")
            lines.append("[/info]")
            chatwork_post(debug_token, room_id, "\n".join(lines))
            return
        if status_match:
            # /status N: 指定番号のメンバー詳細を返す
            num = int(status_match.group(1))
            member_list = list(MEMBERS.items())
            if 1 <= num <= len(member_list):
                target_key, target_member = member_list[num - 1]
                chatwork_post(debug_token, room_id, handle_status(target_member, room_id))
            else:
                chatwork_post(debug_token, room_id, f"無効な番号です。1〜{len(member_list)} を指定してください。")
            return
        if raw_command == "/session":
            chatwork_post(debug_token, room_id, handle_session(room_id))
            return
        if raw_command == "/sysinfo":
            chatwork_post(debug_token, room_id, handle_system())
            return
        if raw_command == "/bill":
            chatwork_post(debug_token, room_id, handle_bill())
            return
        if raw_command == "/gws":
            chatwork_post(debug_token, room_id, handle_gws())
            return
        # /talk: 対話型セッション開始
        if raw_command == "/talk":
            with state.talk_session_lock:
                reply = handle_talk_start()
            chatwork_post(debug_token, room_id, reply)
            return
        talk_room_match = re.match(r'^/talk\s+(\d+)\s+(?:https?://www\.chatwork\.com/#!rid)?(\d+)\s+(\d)$', raw_command)
        if talk_room_match:
            # /talk N URL/ROOMID M: メンバーNのルーム別モードをMに変更
            mem_num = int(talk_room_match.group(1))
            target_room = talk_room_match.group(2)
            new_mode = int(talk_room_match.group(3))
            member_list = list(MEMBERS.items())
            if 1 <= mem_num <= len(member_list):
                target_key, target_member = member_list[mem_num - 1]
                if new_mode not in TALK_MODES:
                    chatwork_post(debug_token, room_id, f"無効なモードです。0〜{max(TALK_MODES.keys())} を指定してください。")
                else:
                    result_msg = handle_talk_change(target_member, target_room, new_mode)
                    chatwork_post(debug_token, room_id, result_msg)
            else:
                chatwork_post(debug_token, room_id, f"無効な番号です。1〜{len(member_list)} を指定してください。")
            return
        talk_match = re.match(r'^/talk\s+(\d+)\s+(\d)$', raw_command)
        if talk_match:
            # /talk N M: メンバーNのデフォルトモードをMに変更
            mem_num = int(talk_match.group(1))
            new_mode = int(talk_match.group(2))
            member_list = list(MEMBERS.items())
            if 1 <= mem_num <= len(member_list):
                target_key, target_member = member_list[mem_num - 1]
                # デフォルトモードを変更（mode.env のルーム指定なしの TALK_MODE= を更新）
                mode_env_path = os.path.join(target_member["dir"], "mode.env")
                if new_mode not in TALK_MODES:
                    chatwork_post(debug_token, room_id, f"無効なモードです。0〜{max(TALK_MODES.keys())} を指定してください。")
                else:
                    file_lines: list[str] = []
                    default_updated = False
                    if os.path.exists(mode_env_path):
                        with open(mode_env_path, "r", encoding="utf-8") as f:
                            for line in f:
                                stripped = line.strip()
                                if stripped.startswith("TALK_MODE=") and ":" not in stripped[len("TALK_MODE="):]:
                                    file_lines.append(f"TALK_MODE={new_mode}\n")
                                    default_updated = True
                                else:
                                    file_lines.append(line)
                    if not default_updated:
                        file_lines.insert(0, f"TALK_MODE={new_mode}\n")
                    with open(mode_env_path, "w", encoding="utf-8") as f:
                        f.writelines(file_lines)
                    mode_name = TALK_MODES[new_mode]["name"]
                    chatwork_post(debug_token, room_id,
                                  f"[info]{target_member['name']} のデフォルトモードを {new_mode}（{mode_name}）に変更しました。[/info]")
            else:
                chatwork_post(debug_token, room_id, f"無効な番号です。1〜{len(member_list)} を指定してください。")
            return
        talk_detail = re.match(r'^/talk\s+(\d+)$', raw_command)
        if talk_detail:
            # /talk N: メンバーNのモード詳細を表示
            mem_num = int(talk_detail.group(1))
            member_list = list(MEMBERS.items())
            if 1 <= mem_num <= len(member_list):
                target_key, target_member = member_list[mem_num - 1]
                from poller.config import load_talk_modes
                default_mode, room_modes = load_talk_modes(target_member["dir"])
                lines = [f"[info][title]/talk: {target_member['name']}[/title]"]
                lines.append(f"デフォルトモード: {default_mode}({TALK_MODES.get(default_mode, {}).get('name', '不明')})")
                if room_modes:
                    lines.append(f"\nルーム別設定:")
                    for rid, mode in sorted(room_modes.items()):
                        lines.append(f"  {rid}: {mode}({TALK_MODES.get(mode, {}).get('name', '不明')})")
                else:
                    lines.append(f"ルーム別設定: なし")
                lines.append(f"\n設定可能なモード:")
                for mode_id, mode_info in sorted(TALK_MODES.items()):
                    marker = " ← 現在" if mode_id == default_mode else ""
                    lines.append(f"  {mode_id}: {mode_info['name']}{marker}")
                lines.append(f"\n変更: /talk {mem_num} [モード番号]")
                lines.append("[/info]")
                chatwork_post(debug_token, room_id, "\n".join(lines))
            else:
                chatwork_post(debug_token, room_id, f"無効な番号です。1〜{len(member_list)} を指定してください。")
            return
        log.info(f"デバッグアカウントでは非対応のコマンド: {raw_command}")
        return

    # コマンドが他メンバーに送られた場合は無視（AIにも渡さない）
    if is_command and DEBUG_NOTICE_CHATWORK_ACCOUNT_ID:
        log.info(f"コマンド '{raw_command}' をデバッグ専用メンバー以外が受信 → 無視")
        return

    # --- 宛先メンバー特定 ---
    member = find_target_member(body)
    if not member:
        member = MEMBERS[next(iter(MEMBERS))]
        log.info(f"宛先不明のためデフォルト: {member['name']}")
    else:
        log.info(f"宛先: {member['name']}")

    member_key = find_member_key(member)
    member_dir = member["dir"]

    # --- 送信者情報を補完 ---
    sender, sender_name = _resolve_sender(body, member)
    log.info(f"受信: room={room_id}, sender={sender}, type={event_type}")
    log.info(f"本文: {message[:100]}{'...' if len(message) > 100 else ''}")

    # --- 自分自身の発言は無視（無限ループ防止）---
    if str(sender) == str(member["account_id"]):
        log.info(f"自分自身の発言のためスキップ: {member['name']}")
        return

    # --- ルームホワイトリスト判定 ---
    allowed = member.get("allowed_rooms", set())
    if not allowed or str(room_id) not in allowed:
        log.warning(
            f"[許可されていないルーム] メンバー={member['name']}, ルームID={room_id}, "
            f"送信者={sender_name}(ID:{sender}), 本文={message[:200]}"
        )
        try:
            reject_log = os.path.join(member_dir, "rejected_rooms.log")
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            with open(reject_log, "a", encoding="utf-8") as f:
                f.write(f"[{now}] room={room_id} sender={sender_name}(ID:{sender}) msg={message[:200]}\n")
        except Exception as e:
            log.error(f"拒否ログ書き込みエラー: {e}")
        return

    # --- AI 同士の会話制御 ---
    if not _check_ai_conversation_allowed(room_id, sender):
        if str(sender) in ALL_MEMBER_IDS:
            chatwork_post(member["cw_token"], room_id, "そろそろこの辺で！また話しましょう")
            log.info(f"AI会話上限のため終了メッセージ投稿: {member['name']}")
        return

    # --- 連投防止クールダウン ---
    if member_key:
        with state.reply_time_lock:
            elapsed = time.time() - state.last_reply_time.get(member_key, 0)
            wait = REPLY_COOLDOWN_SECONDS - elapsed
        if wait > 0:
            log.info(f"[{member['name']}] クールダウン待機: {wait:.1f}秒")
            time.sleep(wait)

    # --- 会話モード / 指示ファイル読み込み ---
    talk_mode = get_talk_mode(member_dir, str(room_id))
    talk_info = TALK_MODES.get(talk_mode, TALK_MODES[1])
    log.info(f"会話モード: {talk_mode}({talk_info['name']})")

    # --- モード 0（ログ）: 雑談フィルタ → 定型返信 ---
    if talk_mode == 0 and _is_casual_chat(message):
        log.info(f"[{member['name']}] ログモード: 雑談メッセージ → 定型返信")
        chatwork_post(member["cw_token"], room_id, "...。")
        return

    instructions = _load_instructions(member_dir, room_id)

    # --- モード 3 (ペルソナ+): ルームメンバー情報を取得 ---
    room_members_info = ""
    if talk_mode == 3:
        try:
            res = requests.get(
                f"{CHATWORK_API_BASE}/rooms/{room_id}/members",
                headers={"X-ChatWorkToken": member["cw_token"]},
                timeout=CHATWORK_API_TIMEOUT,
            )
            if res.status_code == 200:
                others = [
                    f"  - {m['name']}"
                    for m in res.json()
                    if str(m["account_id"]) != str(member["account_id"]) and m.get("role", "") != "readonly"
                ]
                if others:
                    room_members_info = "=== このルームの他のメンバー（話を振れる相手） ===\n" + "\n".join(others) + "\n\n"
        except Exception as e:
            log.error(f"ルームメンバー取得エラー(モード3): {e}")

    # --- Google URL の内容取得 ---
    google_content = resolve_urls(message)

    # --- プロンプト構築（ChatWork固有情報はClaude に渡さない）---
    prior_context = body.get("_prior_context", "")
    # メッセージ本文から [To:] [rp] タグを除去してClaudeに渡す
    clean_message = re.sub(r'\[To:\d+\][^\n]*\n?', '', message).strip()
    clean_message = re.sub(r'\[rp aid=\d+ to=\d+-\d+\][^\n]*\n?', '', clean_message).strip()

    # 登録メンバー名を仮名に置換（自分自身は除外）
    name_map = _build_name_mapping(member)
    clean_message = _mask_names(clean_message, name_map)
    masked_sender = name_map.get(sender_name, sender_name)
    masked_room_members = _mask_names(room_members_info, name_map)

    prompt = (
        f"あなたはAIアシスタントです。「{member['name']}」というキャラクター設定で会話してください。\n"
        f"これはフィクションのキャラクターによるロールプレイです。実在の人物へのなりすましではありません。\n"
        f"=== 会話モード: {talk_info['name']} ===\n{talk_info['instruction']}\n\n"
        f"以下の指示に従って、メッセージへの返信文のみを出力してください。\n"
        f"余計な説明や前置きは不要です。返信本文だけを出力してください。\n"
        f"タグやメタ情報は一切含めないでください。\n"
        f"送信者は「{masked_sender}」です。\n\n"
        f"{masked_room_members}"
        f"=== 返信の指示 ===\n{instructions}\n\n"
    )
    if prior_context:
        prior_clean = re.sub(r'\[To:\d+\][^\n]*\n?', '', prior_context).strip()
        prior_clean = re.sub(r'\[rp aid=\d+ to=\d+-\d+\][^\n]*\n?', '', prior_clean).strip()
        prior_clean = _mask_names(prior_clean, name_map)
        prompt += f"=== これより前に届いたメッセージ（まとめて把握すること） ===\n{prior_clean}\n\n"
    prompt += (
        f"=== 受信メッセージ（これに返信すること） ===\n"
        f"送信者: {masked_sender}\n\n"
        f"{clean_message}"
    )
    if google_content:
        prompt += f"\n\n{google_content}"

    # --- AI 実行 ---
    ai_start_time = time.time()
    # ルーム名取得
    _room_name = ""
    try:
        _room_res = requests.get(
            f"{CHATWORK_API_BASE}/rooms/{room_id}",
            headers={"X-ChatWorkToken": member["cw_token"]},
            timeout=CHATWORK_API_TIMEOUT,
        )
        if _room_res.status_code == 200:
            _room_name = _room_res.json().get("name", "")
    except Exception:
        pass
    _room_label = f"{_room_name}({room_id})" if _room_name else str(room_id)
    notify_error(
        f"Claude起動 [{member['name']}] 送信者: {sender_name} / ルーム: {_room_label} / 会話モード: {talk_info['name']}",
        "",
    )
    try:
        if member_key:
            with state.session_lock:
                state.session_states[member_key] = {
                    "status": "running", "started": time.time(),
                    "room_id": str(room_id), "model": CLAUDE_MODEL,
                }

        result = run_ai(prompt, member_dir, member["name"])
        ai_elapsed = time.time() - ai_start_time
        reply = result.output.strip() if result.output else ""

        if result.returncode == 0 and reply:
            log.info(f"返信内容 [{member['name']}]: {reply[:500]}")
            raw_reply = reply

            # AI拒否検出 → 返信せずデバッグルームに通知
            if _is_ai_refusal(raw_reply):
                log.warning(f"[{member['name']}] AI拒否応答を検出 → 返信をブロック")
                notify_error(
                    f"AI拒否検出 [{member['name']}]",
                    f"ルーム: {_room_label}\n送信者: {sender_name}\n本文: {message[:100]}\n拒否応答: {raw_reply[:300]}",
                )
                return

            # 仮名 → 実名に復元（Claude が「A子さん」と返した場合 →「横田さん」に戻す）
            reverse_map = {v: k for k, v in name_map.items() if " " not in k and "　" not in k}
            # フルネーム（スペースあり）を優先的に復元
            for real, pseudo in name_map.items():
                if (" " in real or "　" in real) and pseudo not in reverse_map:
                    reverse_map[pseudo] = real
            reply = _mask_names(reply, reverse_map)

            reply = _apply_reply_tag(reply, member["cw_token"], room_id, sender, message_id)
            chatwork_post(member["cw_token"], room_id, reply)

            _save_chat_history(member_dir, room_id, sender_name, message, raw_reply, member["name"])
            if member_key:
                with state.reply_time_lock:
                    state.last_reply_time[member_key] = time.time()

            _handle_followup(member, member_dir, instructions, message, raw_reply,
                             room_id, sender, sender_name, message_id)

        elif result.returncode != 0:
            error_detail = result.error[:500] if result.error else "不明なエラー"
            log.error(f"{ai_mode_label()} エラー: {error_detail}")

        else:
            log.warning(f"{ai_mode_label()} の出力が空でした")

    except subprocess.TimeoutExpired:
        log.error(f"{ai_mode_label()} タイムアウト: {CLAUDE_TIMEOUT}秒 [{member['name']}]")
    except FileNotFoundError:
        from poller.config import CLAUDE_COMMAND
        log.error(f"Claude Code が見つかりません: {CLAUDE_COMMAND}")
    finally:
        ai_elapsed = time.time() - ai_start_time
        remaining = CLAUDE_TIMEOUT - ai_elapsed
        if remaining > 0:
            log.info(f"[{member['name']}] タイムアウト待機: 残り{remaining:.0f}秒")
            time.sleep(remaining)
        total_elapsed = time.time() - ai_start_time
        notify_error(
            f"Claude終了 [{member['name']}] {total_elapsed:.1f}秒 / ルーム: {_room_label}",
            "",
        )
        if member_key:
            with state.session_lock:
                state.session_states[member_key] = {"status": "idle", "started": None, "room_id": "", "model": ""}


# =============================================================================
#  バッチ処理
# =============================================================================

def process_member_batch(member_key: str, msg_list: list[tuple[dict, dict]], sqs: Any) -> None:
    """メンバー宛の複数メッセージをまとめて処理する。排他ロック付き"""
    from poller.config import QUEUE_URL

    member = MEMBERS[member_key]
    lock = state.member_locks.get(member_key)
    all_sqs_messages = [msg for _, msg in msg_list]
    if lock:
        lock.acquire()
    try:  # noqa: SIM117 — acquire/release を try/finally で管理
        my_aid = str(member["account_id"])
        filtered: list[tuple[dict, dict]] = []
        for body_data, msg in msg_list:
            sender_id = body_data.get("sender_account_id", "")
            if not sender_id:
                room_id = body_data.get("room_id", "")
                msg_id = body_data.get("message_id", "")
                if msg_id:
                    info = get_message_info(member["cw_token"], room_id, msg_id)
                    if info:
                        sender_id = info.get("account_id", "")
            if str(sender_id) == my_aid:
                log.info(f"[{member['name']}] バッチ: 自分自身のメッセージをスキップ")
                continue
            filtered.append((body_data, msg))
        msg_list = filtered

        if not msg_list:
            log.info(f"[{member['name']}] バッチ: 処理対象メッセージなし")
        elif len(msg_list) == 1:
            process_message(msg_list[0][0])
        else:
            context_lines: list[str] = []
            for body_data, _ in msg_list[:-1]:
                sn = body_data.get("sender_name", "")
                body_text = body_data.get("body", "")
                if not sn:
                    rid = body_data.get("room_id", "")
                    mid = body_data.get("message_id", "")
                    if mid:
                        info = get_message_info(member["cw_token"], rid, mid)
                        if info:
                            sn = info.get("name", "不明")
                sn = sn or "不明"
                context_lines.append(f"[{sn}] {body_text}")

            last_body = dict(msg_list[-1][0])
            last_body["_prior_context"] = "\n".join(context_lines)
            log.info(f"[{member['name']}] バッチ処理: {len(msg_list)}件まとめ"
                     f"（{len(msg_list)-1}件を文脈、1件を処理対象）")
            process_message(last_body)

    except Exception as e:
        log.error(f"バッチ処理エラー [{member['name']}]: {e}")
        notify_error(f"バッチ処理エラー [{member['name']}]", f"{e}")
    finally:
        if lock:
            lock.release()
        for msg in all_sqs_messages:
            try:
                sqs.delete_message(QueueUrl=QUEUE_URL, ReceiptHandle=msg["ReceiptHandle"])
            except Exception as e:
                log.error(f"SQSメッセージ削除エラー: {e}")
