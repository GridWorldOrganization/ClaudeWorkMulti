"""
ChatWork API ヘルパー

ChatWork への投稿、送信者情報の取得、ルーム情報の収集を行う。
"""

import logging
import requests
from typing import Any

from poller.config import (
    CHATWORK_API_BASE,
    CHATWORK_API_TIMEOUT,
    DEBUG_NOTICE_ENABLED,
    DEBUG_NOTICE_CHATWORK_TOKEN,
    DEBUG_NOTICE_CHATWORK_ROOM_ID,
)

log = logging.getLogger(__name__)


def chatwork_post(token: str, room_id: int | str, message: str) -> None:
    """ChatWork にメッセージを投稿する"""
    try:
        res = requests.post(
            f"{CHATWORK_API_BASE}/rooms/{room_id}/messages",
            headers={"X-ChatWorkToken": token},
            data={"body": message},
            timeout=CHATWORK_API_TIMEOUT,
        )
        if res.status_code == 200:
            log.info(f"ChatWork投稿成功: room={room_id}")
        else:
            log.error(f"ChatWork投稿失敗: {res.status_code} {res.text}")
    except Exception as e:
        log.error(f"ChatWork投稿エラー: {e}")


def notify_error(title: str, detail: str) -> None:
    """デバッグ通知アカウントで通知を投稿する。無効時はログのみ"""
    if not DEBUG_NOTICE_ENABLED or not DEBUG_NOTICE_CHATWORK_TOKEN or not DEBUG_NOTICE_CHATWORK_ROOM_ID:
        log.warning(f"[通知スキップ] {title}: {detail[:200]}")
        return
    msg = f"[info][title]{title}[/title]{detail}[/info]"
    chatwork_post(DEBUG_NOTICE_CHATWORK_TOKEN, DEBUG_NOTICE_CHATWORK_ROOM_ID, msg)


def get_sender_name(token: str, room_id: str, sender_account_id: str) -> str | None:
    """ルームメンバー一覧から送信者の表示名を取得する"""
    try:
        res = requests.get(
            f"{CHATWORK_API_BASE}/rooms/{room_id}/members",
            headers={"X-ChatWorkToken": token},
            timeout=CHATWORK_API_TIMEOUT,
        )
        if res.status_code == 200:
            for m in res.json():
                if str(m["account_id"]) == str(sender_account_id):
                    return m["name"]
    except Exception as e:
        log.error(f"送信者名取得エラー: {e}")
    return None


def get_message_info(token: str, room_id: str, message_id: str) -> dict[str, str] | None:
    """ChatWork API でメッセージ情報を取得し、送信者の account_id と name を返す"""
    try:
        res = requests.get(
            f"{CHATWORK_API_BASE}/rooms/{room_id}/messages/{message_id}",
            headers={"X-ChatWorkToken": token},
            timeout=CHATWORK_API_TIMEOUT,
        )
        if res.status_code == 200:
            account = res.json().get("account", {})
            return {
                "account_id": str(account.get("account_id", "")),
                "name": account.get("name", ""),
            }
    except Exception as e:
        log.error(f"メッセージ情報取得エラー: {e}")
    return None


def gather_room_context(token: str, room_id: str) -> str:
    """ルームのメンバー一覧と直近メッセージを収集してテキストで返す"""
    parts: list[str] = []
    try:
        res = requests.get(
            f"{CHATWORK_API_BASE}/rooms/{room_id}/members",
            headers={"X-ChatWorkToken": token},
            timeout=CHATWORK_API_TIMEOUT,
        )
        if res.status_code == 200:
            member_list = ", ".join(f"{m['name']}(ID:{m['account_id']})" for m in res.json())
            parts.append(f"ルームメンバー: {member_list}")
    except Exception as e:
        log.error(f"メンバー取得エラー: {e}")
    try:
        res = requests.get(
            f"{CHATWORK_API_BASE}/rooms/{room_id}/messages",
            headers={"X-ChatWorkToken": token},
            params={"force": 1},
            timeout=CHATWORK_API_TIMEOUT,
        )
        if res.status_code == 200:
            recent = [
                f"[{m.get('account', {}).get('name', '不明')}] {m.get('body', '')[:200]}"
                for m in res.json()[-5:]
            ]
            parts.append("直近のメッセージ:\n" + "\n".join(recent))
    except Exception as e:
        log.error(f"メッセージ取得エラー: {e}")
    return "\n\n".join(parts)


def build_rp_header(token: str, room_id: str, sender: str, message_id: str) -> str | None:
    """ChatWork の返信タグ [rp aid=... to=...]名前さん を構築する。失敗時は None"""
    if not message_id or not sender:
        return None
    name = get_sender_name(token, room_id, sender)
    if not name:
        log.warning(f"送信者名が取得できませんでした: sender={sender}")
        return None
    return f"[rp aid={sender} to={room_id}-{message_id}]{name}さん"
