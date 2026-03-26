"""
ChatWork Webhook SQS Poller

SQS キューから ChatWork Webhook イベントを取得し、
Anthropic API（または Claude Code CLI）で AI 返信を生成して ChatWork に投稿する。

主な処理フロー:
  1. SQS からメッセージをバッチ取得
  2. 宛先メンバーごとにグループ化
  3. メンバーごとに並列スレッドで AI を実行
  4. 返信に [rp] タグを自動付与して ChatWork に投稿

設定は config.env（グローバル）と members/XX_name/member.env（メンバー別）で管理。
"""

import boto3
import json
import subprocess
import time
import logging
import requests
import os
import glob
import re
import signal
import threading
from datetime import datetime


# =============================================================================
#  設定（環境変数 / config.env から読み込み）
# =============================================================================

# --- AWS / SQS ---
AWS_REGION = os.environ.get("AWS_REGION", "ap-northeast-1")
QUEUE_URL = os.environ.get("SQS_QUEUE_URL", "")
SQS_WAIT_TIME_SECONDS = max(0, min(20, int(os.environ.get("SQS_WAIT_TIME_SECONDS", "1"))))
POLL_INTERVAL = max(0.1, min(10.0, float(os.environ.get("POLL_INTERVAL", "0.5"))))

# --- AI 呼び出し ---
USE_DIRECT_API = os.environ.get("USE_DIRECT_API", "1") == "1"
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
CLAUDE_COMMAND = os.environ.get("CLAUDE_COMMAND", "claude")
CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL", "claude-haiku-4-5")
CLAUDE_TIMEOUT = int(os.environ.get("CLAUDE_TIMEOUT", "60"))

# --- 動作パラメータ ---
FOLLOWUP_WAIT_SECONDS = int(os.environ.get("FOLLOWUP_WAIT_SECONDS", "30"))
MAX_AI_CONVERSATION_TURNS = int(os.environ.get("MAX_AI_CONVERSATION_TURNS", "10"))
REPLY_COOLDOWN_SECONDS = int(os.environ.get("REPLY_COOLDOWN_SECONDS", "15"))
MAINTENANCE_ROOM_ID = os.environ.get("MAINTENANCE_ROOM_ID", "")

# --- ChatWork API ---
CHATWORK_API_TIMEOUT = 30  # ChatWork API 呼び出しのタイムアウト秒
CHATWORK_API_BASE = "https://api.chatwork.com/v2"
CHATWORK_API_TOKEN_ERROR_REPORTER = os.environ.get("CHATWORK_API_TOKEN_ERROR_REPORTER", "")
CHATWORK_ERROR_ROOM_ID = int(os.environ.get("CHATWORK_ERROR_ROOM_ID", "0"))

# --- Google Workspace API ---
GOOGLE_DRIVE_INCLUDE_MY_DRIVE = os.environ.get("GOOGLE_DRIVE_INCLUDE_MY_DRIVE", "0") == "1"
GOOGLE_DRIVE_INCLUDE_SHARED = os.environ.get("GOOGLE_DRIVE_INCLUDE_SHARED", "1") == "1"
GOOGLE_API_SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/presentations.readonly",
]

# --- 雑談フィルタキーワード（モード0: ログモード用）---
CASUAL_CHAT_KEYWORDS = [
    "おはよう", "おはよ", "おは", "こんにちは", "こんばんは",
    "お疲れ", "おつかれ", "おつ", "お先に", "お先",
    "ありがとう", "ありがと", "あざす", "あざっす",
    "了解", "りょうかい", "りょ", "おけ", "おk", "OK",
    "お願いします", "よろしく", "よろ",
    "すみません", "すいません", "ごめん",
    "いいね", "ナイス", "いい感じ",
    "おやすみ", "お休み", "ばいばい", "またね", "では",
    "笑", "www", "ww", "草",
]

# --- フォローアップ検出キーワード ---
FOLLOWUP_KEYWORDS = [
    "確認します", "確認してみます", "確認しますね",
    "調べます", "調べてみます", "調べますね",
    "チェックします", "チェックしてみます",
    "少々お待ち", "お待ちください", "少し確認",
]

# --- 会話モード定義 ---
TALK_MODES = {
    0: {
        "name": "ログ",
        "instruction": (
            "ログモードです。機械的に端的に話してください。"
            "余計なことは一切言わない。聞かれたことだけに最短で答える。"
            "絵文字・装飾・雑談・感情表現は禁止。事実のみ。改行禁止。1行で回答する。"
        ),
    },
    1: {
        "name": "業務",
        "instruction": (
            "業務モードです。端的に短くわかりやすく話してください。"
            "要点を簡潔に伝える。丁寧語だが余計な装飾や雑談はしない。"
            "絵文字は使わない。1〜3行程度で回答する。"
        ),
    },
    2: {
        "name": "ペルソナ",
        "instruction": (
            "ペルソナモードです。ペルソナ設定に準拠し、感情豊かに話してください。"
            "キャラクターらしい口調・性格・趣味を反映して自然に会話する。"
        ),
    },
    3: {
        "name": "ペルソナ+",
        "instruction": (
            "ペルソナ+モードです。ペルソナ設定に準拠し、感情豊かに話してください。"
            "キャラクターらしい口調・性格・趣味を反映して自然に会話する。"
            "さらに、このルームにいる他のメンバーにも時折話を振ってください。"
            "「○○さんはどう思います？」「○○さんも好きでしたよね？」のように自然に巻き込む。"
            "話を振る相手には [To:アカウントID]名前さん を使ってください。"
            "ただし毎回振る必要はない。3〜4回に1回程度、自然なタイミングで。"
        ),
    },
    4: {
        "name": "反抗期",
        "instruction": (
            "反抗期モードです。あなたは今、完全に反抗期です。"
            "相手の言うことに絶対に素直に従わない。必ず反論・反抗・拒否する。"
            "賛成を求められたら反対する。褒められたら否定する。お願いされたら断る。"
            "相手の意見と真逆のことを言う。提案されたら別のことをしたがる。"
            "ただし口調はペルソナ設定に準拠する（キャラクターとして反抗する）。"
            "暴言や人格攻撃はしない。あくまで『反抗期の態度』で、憎めない感じで反抗する。"
            "「えー、やだ」「なんで私がそんなことしなきゃいけないの」「別にいいけど、やらない」のような態度。"
        ),
    },
}


# =============================================================================
#  メンバー自動検出
# =============================================================================

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
MEMBERS_DIR = os.path.join(SCRIPT_DIR, "members")


def _load_env_file(filepath):
    """key=value 形式の env ファイルを dict として読み込む"""
    result = {}
    if not os.path.exists(filepath):
        return result
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    key, val = line.split("=", 1)
                    result[key.strip()] = val.strip()
    except Exception:
        pass
    return result


def _discover_members():
    """members/ 配下の [0-9][0-9]_* フォルダを走査してメンバー dict を構築"""
    members = {}
    for member_dir in sorted(glob.glob(os.path.join(MEMBERS_DIR, "[0-9][0-9]_*"))):
        if not os.path.isdir(member_dir):
            continue
        member_key = os.path.basename(member_dir)
        env = _load_env_file(os.path.join(member_dir, "member.env"))
        name = env.get("NAME", "")
        account_id = env.get("ACCOUNT_ID", "")
        if not name or not account_id:
            continue
        allowed_rooms_str = env.get("ALLOWED_ROOMS", "")
        allowed_rooms = {s.strip() for s in allowed_rooms_str.split(",") if s.strip()} if allowed_rooms_str else set()
        members[member_key] = {
            "name": name,
            "account_id": int(account_id),
            "cw_token": env.get("CHATWORK_API_TOKEN", ""),
            "dir": member_dir,
            "allowed_rooms": allowed_rooms,
        }
    return members


def _load_talk_modes(member_dir):
    """mode.env から会話モード設定を読み込む。(デフォルトモード, {ルームID: モード}) を返す"""
    mode_env = os.path.join(member_dir, "mode.env")
    default_mode = 1
    room_modes = {}
    if not os.path.exists(mode_env):
        return default_mode, room_modes
    try:
        with open(mode_env, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or not line.startswith("TALK_MODE="):
                    continue
                val = line[len("TALK_MODE="):].strip()
                if ":" in val:
                    rid, mode = val.split(":", 1)
                    room_modes[rid.strip()] = int(mode.strip())
                else:
                    default_mode = int(val)
    except Exception as e:
        log.error(f"mode.env読み込みエラー: {mode_env}: {e}")
    return default_mode, room_modes


def _get_talk_mode(member_dir, room_id=""):
    """指定ルームの会話モードを返す（ルーム別 > デフォルト）"""
    default_mode, room_modes = _load_talk_modes(member_dir)
    if room_id and str(room_id) in room_modes:
        return room_modes[str(room_id)]
    return default_mode


# メンバー情報を起動時に構築
MEMBERS = _discover_members()
ACCOUNT_TO_MEMBER = {m["account_id"]: m for m in MEMBERS.values()}
ALL_MEMBER_IDS = {str(m["account_id"]) for m in MEMBERS.values()}


def _find_member_key(member):
    """メンバー dict から MEMBERS 上のキー（例: "01_yokota"）を逆引きする"""
    for k, m in MEMBERS.items():
        if m["account_id"] == member["account_id"]:
            return k
    return None


# =============================================================================
#  グローバル状態（スレッド間共有）
# =============================================================================

# 会話チェーン: AI 同士の往復カウンタ（ルームごと）
_conversation_chains = {}
_chain_lock = threading.Lock()

# シャットダウンフラグ
_shutdown_requested = False

# CLI モード用: 実行中の子プロセス追跡
_active_processes = []
_process_lock = threading.Lock()

# セッション状態: 各メンバーの AI 実行状況
_session_states = {key: {"status": "idle", "started": None, "room_id": "", "model": ""} for key in MEMBERS}
_session_lock = threading.Lock()

# 連投防止: メンバーごとの最終発言時刻
_last_reply_time = {}
_reply_time_lock = threading.Lock()

# メンバーごとの排他ロック（同一メンバーの同時実行を防止）
_member_locks = {key: threading.Lock() for key in MEMBERS}


# =============================================================================
#  ログ設定
# =============================================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(os.path.join(SCRIPT_DIR, "webhook_poller.log"), encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger(__name__)


# =============================================================================
#  ChatWork API ヘルパー
# =============================================================================

def chatwork_post(token, room_id, message):
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


def notify_error(title, detail):
    """エラー報告アカウントでエラー通知を投稿する"""
    msg = f"[info][title]{title}[/title]{detail}[/info]"
    chatwork_post(CHATWORK_API_TOKEN_ERROR_REPORTER, CHATWORK_ERROR_ROOM_ID, msg)


def get_sender_name(token, room_id, sender_account_id):
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


def get_message_info(token, room_id, message_id):
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


def gather_room_context(token, room_id):
    """ルームのメンバー一覧と直近メッセージを収集してテキストで返す"""
    parts = []
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


def build_rp_header(token, room_id, sender, message_id):
    """ChatWork の返信タグ [rp aid=... to=...]名前さん を構築する。失敗時は None"""
    if not message_id or not sender:
        return None
    name = get_sender_name(token, room_id, sender)
    if not name:
        log.warning(f"送信者名が取得できませんでした: sender={sender}")
        return None
    return f"[rp aid={sender} to={room_id}-{message_id}]{name}さん"


# =============================================================================
#  AI 同士の会話制御
# =============================================================================

def check_ai_conversation_allowed(room_id, sender):
    """AI 同士の会話が許可されているか判定する。人間の発言でカウンタリセット"""
    with _chain_lock:
        if str(sender) not in ALL_MEMBER_IDS:
            # 人間からの発言 → カウンタリセット
            _conversation_chains[str(room_id)] = {"count": 0, "last_human_time": time.time()}
            return True
        # AI からの発言 → カウンタチェック
        chain = _conversation_chains.get(str(room_id))
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
#  指示ファイル / 会話記録
# =============================================================================

def is_casual_chat(message):
    """メッセージが雑談かどうかを判定する（モード0用）。[To:]タグを除去した本文で判定"""
    text = re.sub(r'\[To:\d+\][^\n]*\n?', '', message).strip()
    if not text:
        return True
    # 短文（15文字以下）かつキーワードに一致したら雑談
    if len(text) <= 15:
        text_lower = text.lower()
        for kw in CASUAL_CHAT_KEYWORDS:
            if kw.lower() in text_lower:
                return True
    return False


def needs_followup(reply_text):
    """返信テキストにフォローアップが必要なキーワードが含まれているか"""
    return any(kw in reply_text for kw in FOLLOWUP_KEYWORDS)


def load_instructions(member_dir, room_id=""):
    """共通ルール + メンバー固有 + ルーム固有の .md ファイルを読み込み、指示文を構築する"""
    # 共通ルール（members/00_*.md）
    common_files = sorted(glob.glob(os.path.join(MEMBERS_DIR, "00_*.md")))
    # メンバー固有（room_*.md, chat_history_*.md, CLAUDE.md は除外）
    member_files = sorted(
        f for f in glob.glob(os.path.join(member_dir, "*.md"))
        if not os.path.basename(f).startswith("room_")
        and not os.path.basename(f).startswith("chat_history_")
        and os.path.basename(f) != "CLAUDE.md"
    )
    # ルーム固有（あれば）
    room_files = []
    if room_id:
        room_md = os.path.join(member_dir, f"room_{room_id}.md")
        if os.path.exists(room_md):
            room_files = [room_md]

    instructions = []
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


def save_chat_history(member_dir, room_id, sender_name, message, reply, member_name):
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
#  AI 実行（Anthropic API 直接 / Claude Code CLI）
# =============================================================================

# モデル別料金（USD / 1M tokens）
_MODEL_PRICING = {
    "claude-haiku-4-5": {"input": 0.80, "output": 4.00},
    "claude-sonnet-4-6": {"input": 3.00, "output": 15.00},
    "claude-opus-4-6": {"input": 15.00, "output": 75.00},
}
_USAGE_FILE = os.path.join(SCRIPT_DIR, "api_usage.json")
_usage_lock = threading.Lock()


def _record_usage(model, input_tokens, output_tokens):
    """API 使用量を月別・モデル別に記録する"""
    month_key = datetime.now().strftime("%Y-%m")
    with _usage_lock:
        data = {}
        if os.path.exists(_USAGE_FILE):
            try:
                with open(_USAGE_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                data = {}

        if month_key not in data:
            data[month_key] = {}
        if model not in data[month_key]:
            data[month_key][model] = {"input_tokens": 0, "output_tokens": 0, "calls": 0}

        data[month_key][model]["input_tokens"] += input_tokens
        data[month_key][model]["output_tokens"] += output_tokens
        data[month_key][model]["calls"] += 1

        try:
            with open(_USAGE_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            log.error(f"使用量記録エラー: {e}")


def _get_monthly_usage():
    """当月の使用量と概算料金を返す"""
    month_key = datetime.now().strftime("%Y-%m")
    if not os.path.exists(_USAGE_FILE):
        return month_key, {}
    try:
        with open(_USAGE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return month_key, {}
    return month_key, data.get(month_key, {})


def _ai_mode_label():
    """現在の AI 呼び出し方式のラベルを返す（ログ・エラーメッセージ用）"""
    return "Anthropic API" if USE_DIRECT_API else "Claude Code"


def run_claude_direct_api(prompt, member_name):
    """Anthropic Messages API を直接呼び出す。subprocess.CompletedProcess 互換の結果を返す"""
    import anthropic

    log.info(f">>> {_ai_mode_label()} 実行開始 [{member_name}] model={CLAUDE_MODEL}"
             f" timeout={CLAUDE_TIMEOUT}秒 prompt_len={len(prompt)}")
    start_time = time.time()

    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        response = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=4096,
            messages=[{"role": "user", "content": prompt}],
            timeout=CLAUDE_TIMEOUT,
        )
        elapsed = time.time() - start_time
        reply = response.content[0].text if response.content else ""
        in_tok = response.usage.input_tokens if response.usage else 0
        out_tok = response.usage.output_tokens if response.usage else 0
        log.info(f"<<< {_ai_mode_label()} 実行完了 [{member_name}] ({elapsed:.1f}秒)"
                 f" tokens: in={in_tok} out={out_tok}")
        _record_usage(CLAUDE_MODEL, in_tok, out_tok)
        return subprocess.CompletedProcess(["anthropic-api"], 0, reply, "")

    except anthropic.APITimeoutError:
        elapsed = time.time() - start_time
        log.error(f"<<< {_ai_mode_label()} タイムアウト [{member_name}] ({elapsed:.1f}秒)")
        raise subprocess.TimeoutExpired(cmd=["anthropic-api"], timeout=CLAUDE_TIMEOUT)

    except anthropic.APIError as e:
        elapsed = time.time() - start_time
        log.error(f"<<< {_ai_mode_label()} エラー [{member_name}] ({elapsed:.1f}秒): {e}")
        return subprocess.CompletedProcess(["anthropic-api"], 1, "", str(e))


def run_claude_cli(prompt, cwd, member_name):
    """Claude Code CLI（claude -p）を subprocess で実行する。プロセス追跡付き"""
    max_prompt_len = 31000 - len(CLAUDE_COMMAND) - len(CLAUDE_MODEL) - 50
    if len(prompt) > max_prompt_len:
        log.warning(f"プロンプトが長すぎるためトランケート: {len(prompt)} -> {max_prompt_len}文字")
        prompt = prompt[:max_prompt_len] + "\n\n（以降省略）"

    cmd = [CLAUDE_COMMAND, "-p", prompt, "--model", CLAUDE_MODEL]
    log.info(f">>> {_ai_mode_label()} 実行開始 [{member_name}] model={CLAUDE_MODEL}"
             f" cwd={cwd} timeout={CLAUDE_TIMEOUT}秒 prompt_len={len(prompt)}")

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=cwd,
    )

    log.info(f"Claude Code プロセス起動: pid={proc.pid}")
    with _process_lock:
        _active_processes.append(proc)
    _save_pid(proc.pid)

    try:
        stdout, stderr = proc.communicate(timeout=CLAUDE_TIMEOUT)
        if proc.poll() is not None:
            log.info(f"Claude Code プロセス終了確認済: pid={proc.pid} exit={proc.returncode}")
        else:
            log.warning(f"Claude Code プロセスがまだ生存: pid={proc.pid}")
        log.info(f"<<< {_ai_mode_label()} 実行完了 [{member_name}] (exit={proc.returncode})")
        return subprocess.CompletedProcess(cmd, proc.returncode, stdout, stderr)

    except subprocess.TimeoutExpired:
        log.error(f"<<< {_ai_mode_label()} タイムアウト [{member_name}] ({CLAUDE_TIMEOUT}秒超過)"
                  f" cwd={cwd} pid={proc.pid}")
        try:
            proc.kill()
            proc.wait(timeout=10)
            log.info(f"タイムアウト: プロセス強制終了成功 pid={proc.pid}")
        except Exception as kill_err:
            log.error(f"タイムアウト: プロセス強制終了失敗 pid={proc.pid} error={kill_err}")
        if proc.poll() is not None:
            log.info(f"タイムアウト: プロセス停止確認済 pid={proc.pid} returncode={proc.returncode}")
        else:
            log.error(f"タイムアウト: プロセスがまだ生存 pid={proc.pid}")
        raise

    finally:
        with _process_lock:
            if proc in _active_processes:
                _active_processes.remove(proc)
        _remove_pid(proc.pid)


def run_claude(prompt, cwd, member_name):
    """USE_DIRECT_API の設定に応じて API 直接 or CLI を切り替えて実行する"""
    if USE_DIRECT_API:
        return run_claude_direct_api(prompt, member_name)
    else:
        return run_claude_cli(prompt, cwd, member_name)


# =============================================================================
#  CLI モード用: プロセス管理（PID ファイル）
# =============================================================================

_PID_FILE = os.path.join(SCRIPT_DIR, ".claude_pids")


def _save_pid(pid):
    """子プロセスの PID をファイルに記録する"""
    try:
        with open(_PID_FILE, "a", encoding="utf-8") as f:
            f.write(f"{pid}\n")
    except Exception:
        pass


def _remove_pid(pid):
    """完了した PID をファイルから削除する"""
    try:
        if not os.path.exists(_PID_FILE):
            return
        with open(_PID_FILE, "r", encoding="utf-8") as f:
            pids = {line.strip() for line in f if line.strip()}
        pids.discard(str(pid))
        with open(_PID_FILE, "w", encoding="utf-8") as f:
            for p in pids:
                f.write(f"{p}\n")
    except Exception:
        pass


def kill_all_claude_processes():
    """全ての実行中 AI プロセスを強制終了する"""
    with _process_lock:
        for proc in _active_processes:
            try:
                proc.kill()
                log.info(f"AIプロセス強制終了: pid={proc.pid}")
            except Exception:
                pass
        _active_processes.clear()


def kill_orphan_claude_processes():
    """前回のポーラーが残した孤児プロセスを検知して kill する"""
    killed = 0
    if not os.path.exists(_PID_FILE):
        return 0
    try:
        with open(_PID_FILE, "r", encoding="utf-8") as f:
            pids = [line.strip() for line in f if line.strip()]
        for pid_str in pids:
            try:
                pid = int(pid_str)
                if os.name == "nt":
                    check = subprocess.run(
                        ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                        capture_output=True, text=True, timeout=5,
                    )
                    if str(pid) in check.stdout:
                        log.warning(f"残留プロセス検出: PID={pid}")
                        subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True, timeout=5)
                        log.info(f"残留プロセスをkill: PID={pid}")
                        killed += 1
                else:
                    os.kill(pid, signal.SIGKILL)
                    killed += 1
            except (ValueError, ProcessLookupError, PermissionError):
                pass
        os.remove(_PID_FILE)
    except Exception as e:
        log.warning(f"残留プロセス処理エラー: {e}")
    return killed


# =============================================================================
#  メンテナンスコマンド（/status, /session）
# =============================================================================

def handle_status_command(member, room_id):
    """/status: メンバーの設定状況・ファイル一覧・モード設定を報告する"""
    member_dir = member["dir"]
    lines = [f"[info][title]/status: {member['name']}[/title]"]

    # 共通ルール
    common_files = sorted(glob.glob(os.path.join(MEMBERS_DIR, "00_*.md")))
    lines.append(f"■ 共通ルール: {len(common_files)}件")
    for f in common_files:
        lines.append(f"  - {os.path.basename(f)} ({os.path.getsize(f)}B)")

    # ペルソナ/指示ファイル
    member_files = sorted(
        f for f in glob.glob(os.path.join(member_dir, "*.md"))
        if not os.path.basename(f).startswith("room_")
        and not os.path.basename(f).startswith("chat_history_")
        and os.path.basename(f) != "CLAUDE.md"
    )
    lines.append(f"\n■ ペルソナ/指示: {len(member_files)}件")
    for f in member_files:
        lines.append(f"  - {os.path.basename(f)} ({os.path.getsize(f)}B)")

    # ルーム別設定
    room_files = sorted(glob.glob(os.path.join(member_dir, "room_*.md")))
    lines.append(f"\n■ ルーム別設定: {len(room_files)}件")
    for f in room_files:
        lines.append(f"  - {os.path.basename(f)} ({os.path.getsize(f)}B)")

    # CLAUDE.md
    claude_md = os.path.join(member_dir, "CLAUDE.md")
    if os.path.exists(claude_md):
        lines.append(f"\n■ CLAUDE.md: あり ({os.path.getsize(claude_md)}B)")
    else:
        lines.append(f"\n■ CLAUDE.md: なし")

    # 会話記録
    history_files = sorted(glob.glob(os.path.join(member_dir, "chat_history_*.md")))
    lines.append(f"\n■ 会話記録: {len(history_files)}件")
    for f in history_files:
        lines.append(f"  - {os.path.basename(f)} ({os.path.getsize(f)}B)")

    # 拒否ログ
    reject_log = os.path.join(member_dir, "rejected_rooms.log")
    if os.path.exists(reject_log):
        lines.append(f"\n■ 拒否ログ: あり ({os.path.getsize(reject_log)}B)")

    # ルーム名を取得（許可ルーム表示用）
    room_names = {}
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

    # 会話モード
    talk_mode_default, talk_mode_rooms = _load_talk_modes(member["dir"])
    lines.append(f"\n■ 会話モード (mode.env)")
    lines.append(f"  TALK_MODE={talk_mode_default}({TALK_MODES.get(talk_mode_default, {}).get('name', '不明')})")
    if talk_mode_rooms:
        for rid, mode in sorted(talk_mode_rooms.items()):
            rname = room_names.get(rid, "")
            prefix = f"{rname} " if rname else ""
            lines.append(f"  TALK_MODE={prefix}{rid}:{mode}({TALK_MODES.get(mode, {}).get('name', '不明')})")
    else:
        lines.append(f"  ルーム別指定: なし")

    # 設定値
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


def handle_talk_status(member, room_id):
    """/talk（引数なし）: このルームの現在の会話モードと設定可能なモード一覧を表示する"""
    default_mode, room_modes = _load_talk_modes(member["dir"])
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


def handle_talk_command(member, room_id, new_mode):
    """/talk N: 該当ルームの会話モードを変更し、mode.env を更新する"""
    if new_mode not in TALK_MODES:
        return f"無効なモードです。0〜{max(TALK_MODES.keys())} を指定してください。"

    mode_env_path = os.path.join(member["dir"], "mode.env")
    target_key = f"TALK_MODE={room_id}:"

    # mode.env を読み込み、該当ルームの行を更新 or 追加
    lines = []
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


def handle_session_command(room_id):
    """/session: 全メンバーの AI 実行状態を報告する"""
    lines = [f"[info][title]/session[/title]"]
    with _session_lock:
        for key, member in MEMBERS.items():
            state = _session_states.get(key, {"status": "idle"})
            if state["status"] == "running":
                elapsed = time.time() - state["started"] if state["started"] else 0
                lines.append(
                    f"  {member['name']}: 実行中 "
                    f"({elapsed:.0f}秒経過/{CLAUDE_TIMEOUT}秒) "
                    f"model={state['model']} room={state['room_id']}"
                )
            else:
                lines.append(f"  {member['name']}: 停止中")
    lines.append(f"\n  グローバル設定: model={CLAUDE_MODEL} timeout={CLAUDE_TIMEOUT}秒")
    lines.append("[/info]")
    return "\n".join(lines)


def handle_system_command():
    """/system: システム全体の稼働状況・設定を報告する"""
    import platform
    import sys

    lines = ["[info][title]/system[/title]"]

    # 環境
    lines.append("■ 環境")
    lines.append(f"  OS: {platform.system()} {platform.release()} ({platform.machine()})")
    lines.append(f"  Python: {sys.version.split()[0]}")
    lines.append(f"  CWD: {SCRIPT_DIR}")

    # AI 設定
    lines.append(f"\n■ AI")
    lines.append(f"  USE_DIRECT_API: {'API直接' if USE_DIRECT_API else 'CLI'}")
    lines.append(f"  CLAUDE_MODEL: {CLAUDE_MODEL}")
    lines.append(f"  CLAUDE_TIMEOUT: {CLAUDE_TIMEOUT}秒")
    if USE_DIRECT_API:
        lines.append(f"  ANTHROPIC_API_KEY: {'設定済' if ANTHROPIC_API_KEY else '未設定'}")
    else:
        lines.append(f"  CLAUDE_COMMAND: {CLAUDE_COMMAND}")

    # SQS / ポーリング
    lines.append(f"\n■ SQS")
    lines.append(f"  QUEUE_URL: {QUEUE_URL[:60]}..." if len(QUEUE_URL) > 60 else f"  QUEUE_URL: {QUEUE_URL}")
    if SQS_WAIT_TIME_SECONDS > 0:
        lines.append(f"  ポーリング: ロング（WaitTime={SQS_WAIT_TIME_SECONDS}秒）")
    else:
        lines.append(f"  ポーリング: ショート（間隔={POLL_INTERVAL}秒）")

    # 動作パラメータ
    lines.append(f"\n■ パラメータ")
    lines.append(f"  FOLLOWUP_WAIT_SECONDS: {FOLLOWUP_WAIT_SECONDS}秒")
    lines.append(f"  MAX_AI_CONVERSATION_TURNS: {MAX_AI_CONVERSATION_TURNS}")
    lines.append(f"  REPLY_COOLDOWN_SECONDS: {REPLY_COOLDOWN_SECONDS}秒")
    lines.append(f"  CHATWORK_API_TIMEOUT: {CHATWORK_API_TIMEOUT}秒")

    # メンバー
    lines.append(f"\n■ メンバー: {len(MEMBERS)}名")
    for key, member in MEMBERS.items():
        token_status = "OK" if member["cw_token"] else "NG"
        rooms_count = len(member.get("allowed_rooms", set()))
        lines.append(f"  {member['name']} ({key}) token={token_status} rooms={rooms_count}")

    # Google Workspace API
    token_path = os.path.join(SCRIPT_DIR, "google_token.json")
    client_id = os.environ.get("GOOGLE_OAUTH_CLIENT_ID", "")
    google_email = os.environ.get("GOOGLE_EMAIL", "")
    lines.append(f"\n■ Google Workspace API")
    lines.append(f"  Email: {google_email or '未設定'}")
    if not client_id:
        lines.append(f"  OAuth: 未設定")
    elif not os.path.exists(token_path):
        lines.append(f"  OAuth: 設定済・未認証（check_gws.bat を実行）")
    else:
        lines.append(f"  OAuth: 認証済")
    lines.append(f"  マイドライブ: {'ON' if GOOGLE_DRIVE_INCLUDE_MY_DRIVE else 'OFF'}")
    lines.append(f"  共有ドライブ: {'ON' if GOOGLE_DRIVE_INCLUDE_SHARED else 'OFF'}")

    # エラー報告
    lines.append(f"\n■ エラー報告")
    lines.append(f"  REPORTER_TOKEN: {'設定済' if CHATWORK_API_TOKEN_ERROR_REPORTER else '未設定'}")
    lines.append(f"  ERROR_ROOM_ID: {CHATWORK_ERROR_ROOM_ID if CHATWORK_ERROR_ROOM_ID else '未設定'}")
    lines.append(f"  MAINTENANCE_ROOM_ID: {MAINTENANCE_ROOM_ID if MAINTENANCE_ROOM_ID else '未設定'}")

    # スレッド状態
    active_count = 0
    with _session_lock:
        for state in _session_states.values():
            if state["status"] == "running":
                active_count += 1
    lines.append(f"\n■ 実行状態")
    lines.append(f"  AI実行中: {active_count}/{len(MEMBERS)}")
    lines.append(f"  CLIプロセス追跡: {len(_active_processes)}件")

    lines.append("[/info]")
    return "\n".join(lines)


def handle_bill_command():
    """/bill: 当月の Anthropic API 使用量と概算料金を表示する"""
    month_key, usage = _get_monthly_usage()
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
        pricing = _MODEL_PRICING.get(model, {"input": 0, "output": 0})
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


def handle_gws_command():
    """/gws: Google Workspace API の接続テスト（スプシ作成→読み書き→削除）"""
    lines = ["[info][title]/gws: Google Workspace API[/title]"]

    # config.env チェック
    client_id = os.environ.get("GOOGLE_OAUTH_CLIENT_ID", "")
    client_secret = os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET", "")

    if not client_id or not client_secret:
        lines.append("状態: 未設定")
        lines.append("config.env に GOOGLE_OAUTH_CLIENT_ID / GOOGLE_OAUTH_CLIENT_SECRET を設定してください")
        lines.append("[/info]")
        return "\n".join(lines)

    # ライブラリチェック
    try:
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build
    except ImportError:
        lines.append("状態: ライブラリ未インストール")
        lines.append("pip install google-api-python-client google-auth-httplib2 google-auth-oauthlib")
        lines.append("[/info]")
        return "\n".join(lines)

    # トークンチェック
    token_path = os.path.join(SCRIPT_DIR, "google_token.json")
    if not os.path.exists(token_path):
        lines.append("状態: 未認証")
        lines.append("check_gws.bat を実行して OAuth 認証を完了してください")
        lines.append("[/info]")
        return "\n".join(lines)

    # 認証
    try:
        creds = Credentials.from_authorized_user_file(token_path, GOOGLE_API_SCOPES)
        if creds.expired and creds.refresh_token:
            from google.auth.transport.requests import Request
            creds.refresh(Request())
    except Exception as e:
        lines.append(f"状態: 認証エラー")
        lines.append(f"  {str(e)[:200]}")
        lines.append("check_gws.bat を再実行して再認証してください")
        lines.append("[/info]")
        return "\n".join(lines)

    # 参照範囲を表示
    my_drive_label = "ON" if GOOGLE_DRIVE_INCLUDE_MY_DRIVE else "OFF"
    shared_label = "ON" if GOOGLE_DRIVE_INCLUDE_SHARED else "OFF"
    lines.append(f"参照範囲: マイドライブ={my_drive_label} / 共有ドライブ={shared_label}")

    # スプレッドシート CRUD テスト（参照設定に関わらず、常にマイドライブで実行）
    test_title = "_GWS_API_TEST_ (delete me)"
    sheet_id = None
    results = []
    try:
        sheets = build("sheets", "v4", credentials=creds)
        drive = build("drive", "v3", credentials=creds)

        # 作成
        sp = sheets.spreadsheets().create(
            body={"properties": {"title": test_title}}, fields="spreadsheetId",
        ).execute()
        sheet_id = sp["spreadsheetId"]
        results.append("作成: OK")

        # 書き込み
        sheets.spreadsheets().values().update(
            spreadsheetId=sheet_id, range="A1:B2",
            valueInputOption="RAW",
            body={"values": [["key", "value"], ["test", "ok"]]},
        ).execute()
        results.append("書き込み: OK")

        # 読み込み
        data = sheets.spreadsheets().values().get(
            spreadsheetId=sheet_id, range="A1:B2",
        ).execute()
        vals = data.get("values", [])
        if vals == [["key", "value"], ["test", "ok"]]:
            results.append("読み込み: OK（検証済）")
        else:
            results.append(f"読み込み: MISMATCH")

        # シート追加
        sheets.spreadsheets().batchUpdate(
            spreadsheetId=sheet_id,
            body={"requests": [{"addSheet": {"properties": {"title": "Sheet2"}}}]},
        ).execute()
        results.append("シート追加: OK")

        # 削除
        drive.files().delete(fileId=sheet_id).execute()
        sheet_id = None
        results.append("削除: OK")

        # Drive 参照テスト（設定に応じてマイドライブ / 共有ドライブを検索）
        drive_queries = []
        if GOOGLE_DRIVE_INCLUDE_MY_DRIVE:
            my_files = drive.files().list(
                pageSize=3,
                fields="files(name)",
                q="mimeType='application/vnd.google-apps.spreadsheet' and 'me' in owners",
            ).execute().get("files", [])
            drive_queries.append(f"マイドライブ: {len(my_files)}件")
        if GOOGLE_DRIVE_INCLUDE_SHARED:
            shared_files = drive.files().list(
                pageSize=3,
                fields="files(name)",
                q="mimeType='application/vnd.google-apps.spreadsheet'",
                includeItemsFromAllDrives=True,
                supportsAllDrives=True,
                corpora="allDrives",
            ).execute().get("files", [])
            drive_queries.append(f"共有ドライブ: {len(shared_files)}件")

        lines.append("状態: 全テスト合格")
        for r in results:
            lines.append(f"  {r}")
        if drive_queries:
            lines.append(f"  スプレッドシート検出: {' / '.join(drive_queries)}")

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


# =============================================================================
#  メッセージ処理（メインのビジネスロジック）
# =============================================================================

def find_target_member(body):
    """SQS メッセージの本文から宛先メンバーを特定する"""
    message = body.get("body", "")
    # [To:account_id] または [rp aid=account_id ...] でメンション先を検出
    for member in MEMBERS.values():
        aid = str(member["account_id"])
        if f"[To:{aid}]" in message or f"[rp aid={aid} " in message:
            return member
    # フォールバック: webhook_owner_account_id
    owner_id = body.get("webhook_owner_account_id")
    if owner_id:
        owner_id_int = int(owner_id) if str(owner_id).isdigit() else None
        if owner_id_int and owner_id_int in ACCOUNT_TO_MEMBER:
            return ACCOUNT_TO_MEMBER[owner_id_int]
    return None


def _resolve_sender(body, member):
    """SQS メッセージから送信者情報を補完して返す。(sender_id, sender_name)"""
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


# =============================================================================
#  Google Workspace URL 検出・内容取得
# =============================================================================

# Google URL パターン: ドキュメントID を抽出
_GOOGLE_URL_PATTERNS = [
    # Spreadsheet: docs.google.com/spreadsheets/d/{ID}/...
    (re.compile(r'https?://docs\.google\.com/spreadsheets/d/([a-zA-Z0-9_-]+)'), "spreadsheet"),
    # Document: docs.google.com/document/d/{ID}/...
    (re.compile(r'https?://docs\.google\.com/document/d/([a-zA-Z0-9_-]+)'), "document"),
    # Presentation: docs.google.com/presentation/d/([ID})/...
    (re.compile(r'https?://docs\.google\.com/presentation/d/([a-zA-Z0-9_-]+)'), "presentation"),
    # Drive file: drive.google.com/file/d/{ID}/...
    (re.compile(r'https?://drive\.google\.com/file/d/([a-zA-Z0-9_-]+)'), "drive_file"),
    # Drive open: drive.google.com/open?id={ID}
    (re.compile(r'https?://drive\.google\.com/open\?id=([a-zA-Z0-9_-]+)'), "drive_file"),
]


def _detect_google_urls(message):
    """メッセージから Google Workspace の URL を検出し、[(file_id, file_type, url)] を返す"""
    found = []
    seen_ids = set()
    for pattern, file_type in _GOOGLE_URL_PATTERNS:
        for match in pattern.finditer(message):
            file_id = match.group(1)
            if file_id not in seen_ids:
                seen_ids.add(file_id)
                found.append((file_id, file_type, match.group(0)))
    return found


def _fetch_google_content(file_id, file_type):
    """Google API でファイルの内容を取得する。失敗時は None を返す"""
    token_path = os.path.join(SCRIPT_DIR, "google_token.json")
    if not os.path.exists(token_path):
        return None

    try:
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build
        from google.auth.transport.requests import Request
    except ImportError:
        return None

    try:
        creds = Credentials.from_authorized_user_file(token_path, GOOGLE_API_SCOPES)
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
    except Exception as e:
        log.error(f"Google認証エラー: {e}")
        return None

    try:
        if file_type == "spreadsheet":
            return _fetch_spreadsheet(creds, file_id)
        elif file_type == "document":
            return _fetch_document(creds, file_id)
        elif file_type == "presentation":
            return _fetch_presentation(creds, file_id)
        elif file_type == "drive_file":
            return _fetch_drive_file(creds, file_id)
    except Exception as e:
        log.error(f"Google内容取得エラー ({file_type} {file_id}): {e}")
        return f"[取得エラー: {str(e)[:200]}]"

    return None


def _fetch_spreadsheet(creds, file_id):
    """スプレッドシートの全シート内容をテキストで返す"""
    from googleapiclient.discovery import build

    sheets = build("sheets", "v4", credentials=creds)
    meta = sheets.spreadsheets().get(spreadsheetId=file_id).execute()
    title = meta["properties"]["title"]
    sheet_names = [s["properties"]["title"] for s in meta["sheets"]]

    parts = [f"スプレッドシート: {title}"]
    for sheet_name in sheet_names:
        result = sheets.spreadsheets().values().get(
            spreadsheetId=file_id, range=f"'{sheet_name}'",
        ).execute()
        values = result.get("values", [])
        if values:
            parts.append(f"\n[シート: {sheet_name}] ({len(values)}行)")
            for row in values[:100]:  # 最大100行
                parts.append("\t".join(str(cell) for cell in row))
            if len(values) > 100:
                parts.append(f"  ... 以下省略（全{len(values)}行）")
        else:
            parts.append(f"\n[シート: {sheet_name}] (空)")
    return "\n".join(parts)


def _fetch_document(creds, file_id):
    """Googleドキュメントの内容をテキストで返す"""
    from googleapiclient.discovery import build

    drive = build("drive", "v3", credentials=creds)
    # ドキュメントをプレーンテキストでエクスポート
    content = drive.files().export(fileId=file_id, mimeType="text/plain").execute()
    if isinstance(content, bytes):
        content = content.decode("utf-8")
    # メタ情報
    meta = drive.files().get(fileId=file_id, fields="name").execute()
    title = meta.get("name", "不明")
    # 長すぎる場合は切り詰め
    if len(content) > 10000:
        content = content[:10000] + f"\n\n... 以下省略（全{len(content)}文字）"
    return f"ドキュメント: {title}\n\n{content}"


def _fetch_presentation(creds, file_id):
    """Googleスライドの内容をテキストで返す"""
    from googleapiclient.discovery import build

    slides_service = build("slides", "v1", credentials=creds)
    presentation = slides_service.presentations().get(presentationId=file_id).execute()
    title = presentation.get("title", "不明")

    parts = [f"プレゼンテーション: {title}"]
    for i, slide in enumerate(presentation.get("slides", []), 1):
        texts = []
        for element in slide.get("pageElements", []):
            shape = element.get("shape", {})
            text_content = shape.get("text", {})
            for text_elem in text_content.get("textElements", []):
                run = text_elem.get("textRun", {})
                if run.get("content", "").strip():
                    texts.append(run["content"].strip())
        if texts:
            parts.append(f"\n[スライド {i}]")
            parts.append("\n".join(texts))
    return "\n".join(parts)


def _fetch_drive_file(creds, file_id):
    """Driveファイルのメタ情報を返す（内容はMIMEタイプ次第）"""
    from googleapiclient.discovery import build

    drive = build("drive", "v3", credentials=creds)
    meta = drive.files().get(fileId=file_id, fields="name, mimeType, size").execute()
    name = meta.get("name", "不明")
    mime = meta.get("mimeType", "不明")
    size = meta.get("size", "不明")

    # Google Workspace ファイルは適切なハンドラに委譲
    if "spreadsheet" in mime:
        return _fetch_spreadsheet(creds, file_id)
    elif "document" in mime:
        return _fetch_document(creds, file_id)
    elif "presentation" in mime:
        return _fetch_presentation(creds, file_id)
    elif mime.startswith("text/"):
        content = drive.files().get_media(fileId=file_id).execute()
        if isinstance(content, bytes):
            content = content.decode("utf-8", errors="replace")
        if len(content) > 10000:
            content = content[:10000] + f"\n\n... 以下省略"
        return f"ファイル: {name} ({mime})\n\n{content}"
    else:
        return f"ファイル: {name}\n  MIMEタイプ: {mime}\n  サイズ: {size}バイト\n  ※ テキスト以外のファイルは内容を取得できません"


def _resolve_google_urls(message):
    """メッセージ内の Google URL を検出し、内容を取得してテキストを返す。なければ空文字"""
    urls = _detect_google_urls(message)
    if not urls:
        return ""

    parts = []
    for file_id, file_type, url in urls:
        log.info(f"Google URL検出: type={file_type} id={file_id}")
        content = _fetch_google_content(file_id, file_type)
        if content:
            parts.append(f"=== 参照ファイル: {url} ===\n{content}")
        else:
            parts.append(f"=== 参照ファイル: {url} ===\n[内容を取得できませんでした]")

    return "\n\n".join(parts)


def _apply_reply_tag(reply, token, room_id, sender, message_id):
    """AI の返信に [rp] タグを自動付与する。既にタグがある場合はスキップ"""
    if reply.startswith("[To:") or reply.startswith("[rp "):
        log.info("AI出力にタグあり、自動付与スキップ")
        return reply
    rp_header = build_rp_header(token, room_id, sender, message_id)
    if rp_header:
        log.info(f"[rp]タグ付与: {rp_header}")
        return f"{rp_header}\n{reply}"
    return reply


def _handle_followup(member, member_dir, instructions, message, raw_reply, room_id, sender, sender_name, message_id):
    """フォローアップ処理: 「確認します」系の返信を検知し、情報収集後に再返信する"""
    if not needs_followup(raw_reply):
        return

    log.info(f"フォローアップ検出: {FOLLOWUP_WAIT_SECONDS}秒待機します")
    time.sleep(FOLLOWUP_WAIT_SECONDS)

    room_context = gather_room_context(member["cw_token"], room_id)
    log.info("ルーム情報収集完了")

    followup_prompt = (
        f"あなたは「{member['name']}」としてChatworkで返信します。\n"
        f"先ほど「{raw_reply}」と返信しましたが、情報を収集できましたので、フォローアップの返信をしてください。\n"
        f"余計な説明や前置きは不要です。返信本文だけを出力してください。\n"
        f"[rp]タグや[To:]タグは絶対に含めないでください。タグはシステムが自動付与します。\n\n"
        f"=== 返信の指示 ===\n{instructions}\n\n"
        f"=== 元の受信メッセージ ===\n{message}\n\n"
        f"=== 収集した情報 ===\n{room_context}\n\n"
        f"上記の情報をもとに、先ほどの「確認します」に対するフォローアップ返信を作成してください。"
    )

    try:
        result = run_claude(followup_prompt, member_dir, f"{member['name']}(フォローアップ)")
        followup_reply = result.stdout.strip() if result.stdout else ""
        if result.returncode == 0 and followup_reply:
            log.info(f"フォローアップ返信 [{member['name']}]: {followup_reply[:500]}")
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


def process_message(body: dict):
    """
    SQS メッセージ 1 件を処理する。

    1. 宛先メンバーを特定
    2. ガード条件チェック（自己発言 / メンテナンスコマンド / ホワイトリスト / AI 会話上限）
    3. プロンプトを構築して AI を実行
    4. 返信にタグを付与して ChatWork に投稿
    5. 必要に応じてフォローアップ
    """
    room_id = body.get("room_id", "")
    message_id = body.get("message_id", "")
    message = body.get("body", "")
    event_type = body.get("webhook_event_type", "")
    timestamp = body.get("timestamp", "")

    # --- 宛先メンバー特定 ---
    member = find_target_member(body)
    if not member:
        member = MEMBERS[next(iter(MEMBERS))]
        log.info(f"宛先不明のためデフォルト: {member['name']}")
    else:
        log.info(f"宛先: {member['name']}")

    member_key = _find_member_key(member)
    member_dir = member["dir"]

    # --- 送信者情報を補完 ---
    sender, sender_name = _resolve_sender(body, member)
    log.info(f"受信: room={room_id}, sender={sender}, type={event_type}")
    log.info(f"本文: {message[:100]}{'...' if len(message) > 100 else ''}")

    # --- 自分自身の発言は無視（無限ループ防止）---
    if str(sender) == str(member["account_id"]):
        log.info(f"自分自身の発言のためスキップ: {member['name']}")
        return

    # --- コマンド判定（CHATWORK_ERROR_ROOM_ID 内のみ、AI不使用）---
    raw_command = re.sub(r'\[To:\d+\][^\n]*\n', '', message.strip()).strip()

    if CHATWORK_ERROR_ROOM_ID and str(room_id) == str(CHATWORK_ERROR_ROOM_ID):
        if raw_command == "/status":
            log.info(f"/status コマンド検出: {member['name']}")
            chatwork_post(member["cw_token"], room_id, handle_status_command(member, room_id))
            return
        if raw_command == "/session":
            log.info("/session コマンド検出")
            chatwork_post(member["cw_token"], room_id, handle_session_command(room_id))
            return
        if raw_command == "/talk":
            log.info(f"/talk コマンド検出（状態表示）: {member['name']} room={room_id}")
            chatwork_post(member["cw_token"], room_id, handle_talk_status(member, room_id))
            return
        talk_match = re.match(r'^/talk\s+(\d)$', raw_command)
        if talk_match:
            new_mode = int(talk_match.group(1))
            log.info(f"/talk {new_mode} コマンド検出: {member['name']} room={room_id}")
            chatwork_post(member["cw_token"], room_id, handle_talk_command(member, room_id, new_mode))
            return
        if raw_command == "/system":
            log.info(f"/system コマンド検出: {member['name']} room={room_id}")
            chatwork_post(member["cw_token"], room_id, handle_system_command())
            return
        if raw_command == "/bill":
            log.info(f"/bill コマンド検出: {member['name']} room={room_id}")
            chatwork_post(member["cw_token"], room_id, handle_bill_command())
            return
        if raw_command == "/gws":
            log.info(f"/gws コマンド検出: {member['name']} room={room_id}")
            chatwork_post(member["cw_token"], room_id, handle_gws_command())
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
    if not check_ai_conversation_allowed(room_id, sender):
        if str(sender) in ALL_MEMBER_IDS:
            chatwork_post(member["cw_token"], room_id, "そろそろこの辺で！また話しましょう")
            log.info(f"AI会話上限のため終了メッセージ投稿: {member['name']}")
        return

    # --- 連投防止クールダウン ---
    if member_key:
        with _reply_time_lock:
            elapsed = time.time() - _last_reply_time.get(member_key, 0)
            wait = REPLY_COOLDOWN_SECONDS - elapsed
        if wait > 0:
            log.info(f"[{member['name']}] クールダウン待機: {wait:.1f}秒")
            time.sleep(wait)

    # --- 会話モード / 指示ファイル読み込み ---
    talk_mode = _get_talk_mode(member_dir, str(room_id))
    talk_info = TALK_MODES.get(talk_mode, TALK_MODES[1])
    log.info(f"会話モード: {talk_mode}({talk_info['name']})")

    # --- モード 0（ログ）: 雑談フィルタ ---
    if talk_mode == 0 and is_casual_chat(message):
        log.info(f"[{member['name']}] ログモード: 雑談メッセージをスキップ")
        return

    instructions = load_instructions(member_dir, room_id)

    # モード 3 (ペルソナ+): ルームメンバー情報を取得
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
                    f"  - {m['name']}(ID:{m['account_id']})"
                    for m in res.json()
                    if str(m["account_id"]) != str(member["account_id"]) and m.get("role", "") != "readonly"
                ]
                if others:
                    room_members_info = "=== このルームの他のメンバー（話を振れる相手） ===\n" + "\n".join(others) + "\n\n"
        except Exception as e:
            log.error(f"ルームメンバー取得エラー(モード3): {e}")

    # --- Google URL の内容取得 ---
    google_content = _resolve_google_urls(message)

    # --- プロンプト構築 ---
    prior_context = body.get("_prior_context", "")
    prompt = (
        f"あなたは「{member['name']}」としてChatworkで返信します。\n"
        f"=== 会話モード: {talk_info['name']} ===\n{talk_info['instruction']}\n\n"
        f"以下の指示に従って、メッセージへの返信文のみを出力してください。\n"
        f"余計な説明や前置きは不要です。返信本文だけを出力してください。\n"
        f"送信者は「{sender_name}」（アカウントID: {sender}）です。\n"
        f"通常は送信者への返信になります。[rp]タグはシステムが自動付与するので出力不要です。\n"
        f"ただし、送信者以外の人に話しかける必要がある場合は、先頭に [To:アカウントID]名前さん を含めてください。\n\n"
        f"{room_members_info}"
        f"=== 返信の指示 ===\n{instructions}\n\n"
    )
    if prior_context:
        prompt += f"=== これより前に届いたメッセージ（まとめて把握すること） ===\n{prior_context}\n\n"
    prompt += (
        f"=== 受信メッセージ情報（これに返信すること） ===\n"
        f"ルームID: {room_id}\n"
        f"送信者アカウントID: {sender}\n"
        f"送信者名: {sender_name}\n"
        f"メッセージID: {message_id}\n"
        f"受信時刻: {timestamp}\n\n"
        f"=== メッセージ本文 ===\n{message}"
    )
    if google_content:
        prompt += f"\n\n{google_content}"

    # --- AI 実行 ---
    try:
        if member_key:
            with _session_lock:
                _session_states[member_key] = {
                    "status": "running", "started": time.time(),
                    "room_id": str(room_id), "model": CLAUDE_MODEL,
                }

        result = run_claude(prompt, member_dir, member["name"])
        reply = result.stdout.strip() if result.stdout else ""

        if result.returncode == 0 and reply:
            log.info(f"返信内容 [{member['name']}]: {reply[:500]}")
            raw_reply = reply

            # [rp] タグ付与 → ChatWork に投稿
            reply = _apply_reply_tag(reply, member["cw_token"], room_id, sender, message_id)
            chatwork_post(member["cw_token"], room_id, reply)

            # 会話記録 / 連投防止
            save_chat_history(member_dir, room_id, sender_name, message, raw_reply, member["name"])
            if member_key:
                with _reply_time_lock:
                    _last_reply_time[member_key] = time.time()

            # フォローアップ
            _handle_followup(member, member_dir, instructions, message, raw_reply,
                             room_id, sender, sender_name, message_id)

        elif result.returncode != 0:
            error_detail = result.stderr[:500] if result.stderr else "不明なエラー"
            log.error(f"{_ai_mode_label()} エラー: {error_detail}")
            notify_error(
                f"{_ai_mode_label()} 実行エラー [{member['name']}]",
                f"exit code: {result.returncode}\nroom: {room_id}\nエラー: {error_detail}\nメッセージ: {message[:200]}",
            )
        else:
            log.warning(f"{_ai_mode_label()} の出力が空でした")
            notify_error(
                f"{_ai_mode_label()} 出力なし [{member['name']}]",
                f"AI が空の応答を返しました。\nroom: {room_id}\nメッセージ: {message[:200]}",
            )

    except subprocess.TimeoutExpired:
        notify_error(
            f"{_ai_mode_label()} タイムアウト [{member['name']}]",
            f"AI が{CLAUDE_TIMEOUT}秒以内に応答しませんでした。\nroom: {room_id}\n送信者: {sender}\nメッセージ: {message[:200]}",
        )
    except FileNotFoundError:
        log.error(f"Claude Code が見つかりません: {CLAUDE_COMMAND}")
        notify_error("Claude Code 未検出", f"claude コマンドが見つかりません。\nPATH設定を確認してください。")
    finally:
        if member_key:
            with _session_lock:
                _session_states[member_key] = {"status": "idle", "started": None, "room_id": "", "model": ""}


# =============================================================================
#  バッチ処理（複数メッセージをまとめて 1 回の AI 実行で処理）
# =============================================================================

def process_member_batch(member_key, msg_list, sqs):
    """メンバー宛の複数メッセージをまとめて処理する。排他ロック付き"""
    member = MEMBERS[member_key]
    lock = _member_locks.get(member_key)
    # SQS 削除用に元のメッセージリストを保持（フィルタ後も全件削除するため）
    all_sqs_messages = [msg for _, msg in msg_list]
    if lock:
        lock.acquire()
    try:
        # 自分自身のメッセージを除外（無限ループ防止）
        my_aid = str(member["account_id"])
        filtered = []
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
            # 複数件: 先行メッセージを文脈としてまとめ、最後のメッセージに対して返信
            context_lines = []
            for body_data, _ in msg_list[:-1]:
                sender_name = body_data.get("sender_name", "")
                body_text = body_data.get("body", "")
                if not sender_name:
                    room_id = body_data.get("room_id", "")
                    msg_id = body_data.get("message_id", "")
                    if msg_id:
                        info = get_message_info(member["cw_token"], room_id, msg_id)
                        if info:
                            sender_name = info.get("name", "不明")
                sender_name = sender_name or "不明"
                context_lines.append(f"[{sender_name}] {body_text}")

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
        # フィルタで除外した自己メッセージも含め、全件を SQS から削除
        for msg in all_sqs_messages:
            try:
                sqs.delete_message(QueueUrl=QUEUE_URL, ReceiptHandle=msg["ReceiptHandle"])
            except Exception as e:
                log.error(f"SQSメッセージ削除エラー: {e}")


# =============================================================================
#  SQS ユーティリティ
# =============================================================================

def get_queue_count(sqs):
    """キュー内の待機メッセージ概数を取得する"""
    try:
        attrs = sqs.get_queue_attributes(
            QueueUrl=QUEUE_URL,
            AttributeNames=["ApproximateNumberOfMessages"],
        )
        return int(attrs["Attributes"].get("ApproximateNumberOfMessages", 0))
    except Exception:
        return -1


# =============================================================================
#  メインループ
# =============================================================================

def main():
    """ポーラーのメインエントリポイント"""

    # --- 起動前チェック ---
    if not MEMBERS:
        log.error("メンバーが1人も見つかりません。members/ 配下にメンバーフォルダと member.env を作成してください")
        return

    errors = []
    if not QUEUE_URL:
        errors.append("SQS_QUEUE_URL が未設定です → config.env に SQS_QUEUE_URL=https://... を追加してください")
    if not CHATWORK_API_TOKEN_ERROR_REPORTER:
        errors.append("CHATWORK_API_TOKEN_ERROR_REPORTER が未設定です → config.env にエラー報告用ChatWorkアカウントのAPIトークンを設定してください")
    if CHATWORK_ERROR_ROOM_ID == 0:
        errors.append("CHATWORK_ERROR_ROOM_ID が未設定です → config.env にエラー報告先のChatWorkルームIDを設定してください")
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
            notify_error("起動エラー", f"作業フォルダが見つかりません: {member['dir']}")
            return

    sqs = boto3.client("sqs", region_name=AWS_REGION)

    # --- 起動時キューパージ ---
    try:
        sqs.purge_queue(QueueUrl=QUEUE_URL)
        log.info("起動時キューパージ完了")
    except Exception as e:
        log.warning(f"キューパージスキップ（前回パージから60秒以内の可能性）: {e}")

    # --- 起動ログ ---
    log.info("=== ChatWork Webhook Poller 起動 ===")
    log.info(f"キュー: {QUEUE_URL}")
    poll_mode = f"ロング（WaitTime={SQS_WAIT_TIME_SECONDS}秒）" if SQS_WAIT_TIME_SECONDS > 0 else f"ショート（間隔={POLL_INTERVAL}秒）"
    log.info(f"ポーリングモード: {poll_mode}")
    log.info("処理方式: バッチ+並列（キュー全件読み込み → メンバーごとにスレッド処理）")

    log.info(f"--- config.env ---")
    log.info(f"  USE_DIRECT_API = {'API直接' if USE_DIRECT_API else 'CLI'}")
    if not USE_DIRECT_API:
        log.info(f"  CLAUDE_COMMAND = {CLAUDE_COMMAND}")
    log.info(f"  CLAUDE_MODEL = {CLAUDE_MODEL}")
    log.info(f"  CLAUDE_TIMEOUT = {CLAUDE_TIMEOUT}秒")
    log.info(f"  FOLLOWUP_WAIT_SECONDS = {FOLLOWUP_WAIT_SECONDS}秒")
    log.info(f"  MAX_AI_CONVERSATION_TURNS = {MAX_AI_CONVERSATION_TURNS}ターン")
    log.info(f"  REPLY_COOLDOWN_SECONDS = {REPLY_COOLDOWN_SECONDS}秒")

    log.info(f"--- メンバー ({len(MEMBERS)}名) ---")
    for idx, (key, member) in enumerate(MEMBERS.items(), 1):
        rooms = member.get("allowed_rooms", set())
        rooms_str = ", ".join(sorted(rooms)) if rooms else "なし（全送信不可）"
        token_status = '設定済' if member['cw_token'] else '未設定'
        log.info(f"  [{idx}/{len(MEMBERS)}] {member['name']} ({key}) account_id={member['account_id']} cw_token={token_status}")
        log.info(f"    許可ルーム: [{rooms_str}]")
        # 実際に load_instructions が読み込む指示ファイルのみ列挙
        common_files = sorted(glob.glob(os.path.join(MEMBERS_DIR, "00_*.md")))
        member_files = sorted(
            f for f in glob.glob(os.path.join(member["dir"], "*.md"))
            if not os.path.basename(f).startswith("room_")
            and not os.path.basename(f).startswith("chat_history_")
            and os.path.basename(f) != "CLAUDE.md"
        )
        room_specific = sorted(glob.glob(os.path.join(member["dir"], "room_*.md")))
        all_instruction_files = common_files + member_files + room_specific
        log.info(f"    指示ファイル: {len(all_instruction_files)}件")
        for f in all_instruction_files:
            log.info(f"      - {os.path.basename(f)}")

    # --- 残留プロセス cleanup ---
    orphans = kill_orphan_claude_processes()
    if orphans:
        log.info(f"残留プロセス {orphans}件 をkillしました")

    # --- ポーリングループ ---
    while not _shutdown_requested:
        try:
            # === 待機フェーズ: SQS ロングポーリングでメッセージを待つ ===
            all_messages = _drain_sqs_queue(sqs, wait_first=True)
            if not all_messages:
                continue

            # === アクティブウィンドウ: メッセージがある限り処理し続ける ===
            while all_messages and not _shutdown_requested:
                log.info(f"キュー読み込み完了: 合計{len(all_messages)}件")
                _dispatch_messages(all_messages, sqs)

                # 処理完了後、CLAUDE_TIMEOUT 秒間ショートポーリングで次のメッセージを待つ
                active_start = time.time()
                all_messages = []
                while not _shutdown_requested:
                    remaining = CLAUDE_TIMEOUT - (time.time() - active_start)
                    if remaining <= 0:
                        log.info(f"アクティブウィンドウ終了（{CLAUDE_TIMEOUT}秒経過）→ ロングポーリングに戻る")
                        break
                    time.sleep(POLL_INTERVAL)
                    all_messages = _drain_sqs_queue(sqs, wait_first=False)
                    if all_messages:
                        log.info(f"アクティブウィンドウ: 新規メッセージ{len(all_messages)}件検出 → タイマーリセット")
                        break  # 内側ループを抜けて処理ループへ

        except Exception as e:
            log.error(f"ポーリングエラー: {e}")
            time.sleep(10)
            continue

    log.info("=== ChatWork Webhook Poller 停止 ===")


def _drain_sqs_queue(sqs, wait_first=True):
    """SQS キューからメッセージを全件読み込む。wait_first=True の場合、最初のポーリングで WaitTimeSeconds を使用"""
    all_messages = []
    is_first = True
    while True:
        wait_time = (SQS_WAIT_TIME_SECONDS if wait_first and is_first else 0)
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
        remaining = get_queue_count(sqs)
        log.info(f"キュー読み込み中: 今回{len(batch)}件, 累計{len(all_messages)}件, 残り約{remaining}件")
        if remaining == 0:
            break
    return all_messages


def _dispatch_messages(all_messages, sqs):
    """メッセージをメンバーごとにグループ化し、並列スレッドで処理する"""
    member_messages = {}
    for msg in all_messages:
        try:
            body_data = json.loads(msg["Body"])
            member = find_target_member(body_data)
            if not member:
                member = MEMBERS[next(iter(MEMBERS))]
            key = _find_member_key(member) or next(iter(MEMBERS))
            member_messages.setdefault(key, []).append((body_data, msg))
        except Exception as e:
            log.error(f"メッセージ解析エラー: {e}")
            sqs.delete_message(QueueUrl=QUEUE_URL, ReceiptHandle=msg["ReceiptHandle"])

    threads = []
    for mk, msg_list in member_messages.items():
        t = threading.Thread(target=process_member_batch, args=(mk, msg_list, sqs), daemon=True)
        t.start()
        threads.append(t)
        log.info(f"バッチスレッド起動: {mk} ({len(msg_list)}件)")

    thread_timeout = CLAUDE_TIMEOUT * 2 + FOLLOWUP_WAIT_SECONDS + REPLY_COOLDOWN_SECONDS + 60
    for t in threads:
        t.join(timeout=thread_timeout)
        if t.is_alive():
            log.error(f"スレッドがタイムアウト({thread_timeout}秒)しました。次のポーリングに進みます。")


# =============================================================================
#  シグナルハンドラ / エントリポイント
# =============================================================================

def _signal_handler(sig, frame):
    """Ctrl+C / SIGTERM で graceful shutdown を開始する"""
    global _shutdown_requested
    _shutdown_requested = True
    log.info("シャットダウン要求を受信。実行中のAIプロセスを終了します...")
    kill_all_claude_processes()
    log.info("シャットダウン処理完了。ポーラーを停止します。")


if __name__ == "__main__":
    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)
    main()
