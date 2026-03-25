"""
Chatwork Webhook SQS Poller for Windows PC
SQSキューからメッセージを1件ずつ取得し、Claude Codeをバッチ実行する
※ Claude Code は常に1プロセスのみ実行（直列処理）
※ 各担当者フォルダ内の .md ファイルで返答方針を指示
※ Claude Code の出力を担当者として Chatwork に返信
※ エラー時はグリ姉でルーム 428354226 に報告
"""
import boto3
import json
import subprocess
import time
import logging
import requests
import os
import glob
from datetime import datetime

# ===== 設定 =====
AWS_REGION = "ap-northeast-1"
QUEUE_URL = "https://sqs.ap-northeast-1.amazonaws.com/REDACTED_AWS_ACCOUNT_ID/chatwork-webhook-queue"
POLL_INTERVAL = 5  # 秒
CLAUDE_COMMAND = "claude"

# Chatwork API
CW_API_BASE = "https://api.chatwork.com/v2"
CW_TOKEN_GURIKO = "REDACTED_CW_TOKEN_GURIKO"   # グリ姉（エラー報告用）
CW_ERROR_ROOM_ID = 428354226                             # エラー報告先ルーム

# 担当者設定（フォルダ名 → 設定）
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CLIENTS_DIR = os.path.join(SCRIPT_DIR, "clients")

MEMBERS = {
    "01_yokota": {
        "name": "横田 百恵",
        "account_id": 11202266,
        "cw_token": "REDACTED_CW_TOKEN_YOKOTA",
        "dir": os.path.join(CLIENTS_DIR, "01_yokota"),
    },
    "02_fujino": {
        "name": "藤野 楓",
        "account_id": 11204912,
        "cw_token": "REDACTED_CW_TOKEN_FUJINO",
        "dir": os.path.join(CLIENTS_DIR, "02_fujino"),
    },
}

# account_id → メンバー設定の逆引き
ACCOUNT_TO_MEMBER = {m["account_id"]: m for m in MEMBERS.values()}

# ===== ログ設定 =====
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("webhook_poller.log", encoding="utf-8"),
        logging.StreamHandler()
    ]
)
log = logging.getLogger(__name__)

def chatwork_post(token, room_id, message):
    """Chatwork にメッセージを投稿"""
    try:
        res = requests.post(
            f"{CW_API_BASE}/rooms/{room_id}/messages",
            headers={"X-ChatWorkToken": token},
            data={"body": message}
        )
        if res.status_code == 200:
            log.info(f"Chatwork投稿成功: room={room_id}")
        else:
            log.error(f"Chatwork投稿失敗: {res.status_code} {res.text}")
    except Exception as e:
        log.error(f"Chatwork投稿エラー: {e}")

def notify_error(title, detail):
    """グリ姉のアカウントでエラー報告"""
    msg = f"[info][title]{title}[/title]{detail}[/info]"
    chatwork_post(CW_TOKEN_GURIKO, CW_ERROR_ROOM_ID, msg)

def get_sender_name(token, room_id, sender_account_id):
    """送信者の表示名をルームメンバーから取得"""
    try:
        res = requests.get(
            f"{CW_API_BASE}/rooms/{room_id}/members",
            headers={"X-ChatWorkToken": token}
        )
        if res.status_code == 200:
            for m in res.json():
                if str(m["account_id"]) == str(sender_account_id):
                    return m["name"]
    except Exception as e:
        log.error(f"送信者名取得エラー: {e}")
    return None

def get_message_info(token, room_id, message_id):
    """Chatwork APIでメッセージ情報を取得し、送信者のaccount_idとnameを返す"""
    try:
        res = requests.get(
            f"{CW_API_BASE}/rooms/{room_id}/messages/{message_id}",
            headers={"X-ChatWorkToken": token}
        )
        if res.status_code == 200:
            data = res.json()
            account = data.get("account", {})
            return {
                "account_id": str(account.get("account_id", "")),
                "name": account.get("name", "")
            }
    except Exception as e:
        log.error(f"メッセージ情報取得エラー: {e}")
    return None

def load_instructions(member_dir):
    """共通指示 + メンバー固有指示を読み込んで指示文を構築"""
    instructions = []
    # 1. 共通ルール（clients直下の 00_ で始まる .md のみ）を読み込む
    common_md_files = sorted(glob.glob(os.path.join(CLIENTS_DIR, "00_*.md")))
    # 2. メンバー固有の .md を読み込む
    member_md_files = sorted(glob.glob(os.path.join(member_dir, "*.md")))
    all_md_files = common_md_files + member_md_files
    for md_path in all_md_files:
        try:
            with open(md_path, "r", encoding="utf-8") as f:
                content = f.read().strip()
            if content:
                filename = os.path.basename(md_path)
                instructions.append(f"--- {filename} ---\n{content}")
                log.info(f"指示ファイル読み込み: {filename}")
        except Exception as e:
            log.error(f"指示ファイル読み込みエラー: {md_path}: {e}")
    if not instructions:
        return "受信したメッセージに対して、丁寧に日本語で返信してください。"
    return "\n\n".join(instructions)

def find_target_member(body):
    """メッセージの宛先メンバーを特定する"""
    message = body.get("body", "")
    # [To:account_id] または [rp aid=account_id パターンでメンション先を検出
    for member in MEMBERS.values():
        aid = str(member["account_id"])
        if f"[To:{aid}]" in message or f"[rp aid={aid} " in message:
            return member
    # webhook_owner_account_id があれば使う
    owner_id = body.get("webhook_owner_account_id")
    if owner_id and owner_id in ACCOUNT_TO_MEMBER:
        return ACCOUNT_TO_MEMBER[owner_id]
    return None

def process_message(body: dict):
    """SQSメッセージを処理してClaude Codeを実行し、Chatworkで返信"""
    log.info(f"SQS body: {body}")
    room_id = body.get("room_id", "")
    sender = body.get("sender_account_id", "")
    message_id = body.get("message_id", "")
    sender_name = body.get("sender_name", "")
    message = body.get("body", "")

    # sender が空の場合、Chatwork API から補完
    if message_id and (not sender or not sender_name):
        member_tmp = find_target_member(body) or MEMBERS["01_yokota"]
        msg_info = get_message_info(member_tmp["cw_token"], room_id, message_id)
        if msg_info:
            if not sender:
                sender = msg_info["account_id"]
                log.info(f"sender補完(API): {sender}")
            if not sender_name:
                sender_name = msg_info["name"]
                log.info(f"sender_name補完(API): {sender_name}")
    event_type = body.get("webhook_event_type", "")
    timestamp = body.get("timestamp", "")

    log.info(f"受信: room={room_id}, sender={sender}, type={event_type}")
    log.info(f"本文: {message}")

    # 宛先メンバーを特定
    member = find_target_member(body)
    if not member:
        # デフォルトは横田
        member = MEMBERS["01_yokota"]
        log.info(f"宛先不明のためデフォルト: {member['name']}")
    else:
        log.info(f"宛先: {member['name']}")

    # 自分自身の発言は無視（無限ループ防止）
    if str(sender) == str(member["account_id"]):
        log.info(f"自分自身の発言のためスキップ: {member['name']}")
        return

    member_dir = member["dir"]

    # 指示ファイル読み込み
    instructions = load_instructions(member_dir)

    # Claude Code に渡すプロンプト
    prompt = (
        f"あなたは「{member['name']}」としてChatworkで返信します。\n"
        f"以下の指示に従って、メッセージへの返信文のみを出力してください。\n"
        f"余計な説明や前置きは不要です。返信本文だけを出力してください。\n"
        f"[rp]タグや[To:]タグは絶対に含めないでください。タグはシステムが自動付与します。\n\n"
        f"=== 返信の指示 ===\n{instructions}\n\n"
        f"=== 受信メッセージ情報 ===\n"
        f"ルームID: {room_id}\n"
        f"送信者アカウントID: {sender}\n"
        f"送信者名: {sender_name}\n"
        f"メッセージID: {message_id}\n"
        f"受信時刻: {timestamp}\n\n"
        f"=== メッセージ本文 ===\n{message}"
    )

    log.info(f">>> Claude Code 実行開始 [{member['name']}]（他のメッセージはキューで待機中）")
    try:
        result = subprocess.run(
            [CLAUDE_COMMAND, "-p", prompt],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=member_dir,
            timeout=300
        )
        log.info(f"<<< Claude Code 実行完了 (exit={result.returncode})")

        reply = result.stdout.strip() if result.stdout else ""

        if result.returncode == 0 and reply:
            log.info(f"返信内容 [{member['name']}]: {reply[:500]}")
            # [rp]タグを自動構築
            if message_id and sender:
                sender_name = get_sender_name(member["cw_token"], room_id, sender)
                if sender_name:
                    rp_header = f"[rp aid={sender} to={room_id}-{message_id}]{sender_name}さん"
                    reply = f"{rp_header}\n{reply}"
                    log.info(f"[rp]タグ付与: {rp_header}")
                else:
                    log.warning(f"送信者名が取得できませんでした: sender={sender}")
            else:
                log.warning(f"message_idまたはsenderが不足: message_id={message_id}, sender={sender}")
            # 担当者として Chatwork に返信
            chatwork_post(member["cw_token"], room_id, reply)
        elif result.returncode != 0:
            error_detail = result.stderr[:500] if result.stderr else "不明なエラー"
            log.error(f"Claude Code エラー: {error_detail}")
            notify_error(
                f"Claude Code 実行エラー [{member['name']}]",
                f"exit code: {result.returncode}\nroom: {room_id}\nエラー: {error_detail}\nメッセージ: {message[:200]}"
            )
        else:
            log.warning("Claude Code の出力が空でした")
            notify_error(
                f"Claude Code 出力なし [{member['name']}]",
                f"Claude Code が空の応答を返しました。\nroom: {room_id}\nメッセージ: {message[:200]}"
            )

    except subprocess.TimeoutExpired:
        log.error("Claude Code 実行タイムアウト (300秒)")
        notify_error(
            f"Claude Code タイムアウト [{member['name']}]",
            f"Claude Code が300秒以内に応答しませんでした。\nroom: {room_id}\n送信者: {sender}\nメッセージ: {message[:200]}"
        )
    except FileNotFoundError:
        log.error(f"Claude Code が見つかりません: {CLAUDE_COMMAND}")
        notify_error(
            "Claude Code 未検出",
            f"claude コマンドが見つかりません。\nPATH設定を確認してください。"
        )

def get_queue_count(sqs):
    """キュー内の待機メッセージ数を取得"""
    try:
        attrs = sqs.get_queue_attributes(
            QueueUrl=QUEUE_URL,
            AttributeNames=["ApproximateNumberOfMessages"]
        )
        return int(attrs["Attributes"].get("ApproximateNumberOfMessages", 0))
    except Exception:
        return -1

def main():
    sqs = boto3.client("sqs", region_name=AWS_REGION)

    # メンバーフォルダ存在確認
    for key, member in MEMBERS.items():
        if not os.path.isdir(member["dir"]):
            log.error(f"作業フォルダが見つかりません: {member['dir']}")
            notify_error("起動エラー", f"作業フォルダが見つかりません: {member['dir']}")
            return

    log.info("=== Chatwork Webhook Poller 起動 ===")
    log.info(f"キュー: {QUEUE_URL}")
    log.info(f"ポーリング間隔: {POLL_INTERVAL}秒")
    log.info("モード: 直列処理（Claude Code は常に1プロセスのみ）")
    log.info(f"登録メンバー数: {len(MEMBERS)}")
    for key, member in MEMBERS.items():
        md_files = glob.glob(os.path.join(member["dir"], "*.md"))
        log.info(f"  {member['name']} ({key}): 指示ファイル {len(md_files)}件")
        for f in sorted(md_files):
            log.info(f"    - {os.path.basename(f)}")

    while True:
        try:
            res = sqs.receive_message(
                QueueUrl=QUEUE_URL,
                MaxNumberOfMessages=1,
                WaitTimeSeconds=5
            )
            messages = res.get("Messages", [])

            if not messages:
                continue

            msg = messages[0]
            remaining = get_queue_count(sqs)
            if remaining > 0:
                log.info(f"キュー待機中: 約{remaining}件")

            try:
                body_data = json.loads(msg["Body"])
                process_message(body_data)
            except Exception as e:
                log.error(f"メッセージ処理エラー: {e}")
                notify_error("メッセージ処理エラー", f"{e}")

            sqs.delete_message(
                QueueUrl=QUEUE_URL,
                ReceiptHandle=msg["ReceiptHandle"]
            )
            log.info(f"メッセージ削除: {msg['MessageId']}")
            continue

        except Exception as e:
            log.error(f"ポーリングエラー: {e}")
            time.sleep(10)
            continue

        time.sleep(POLL_INTERVAL)

if __name__ == "__main__":
    main()
