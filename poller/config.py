"""
設定値・定数の一元管理

環境変数（config.env）から読み込むパラメータと、コード内定数を集約。
"""

import os
import re
import glob
from typing import Any

VERSION = "0.1.0"

# =============================================================================
#  パス
# =============================================================================

SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MEMBERS_DIR = os.path.join(SCRIPT_DIR, "members")

# =============================================================================
#  AWS / SQS
# =============================================================================

AWS_REGION: str = os.environ.get("AWS_REGION", "ap-northeast-1")
QUEUE_URL: str = os.environ.get("SQS_QUEUE_URL", "")
SQS_WAIT_TIME_SECONDS: int = max(0, min(20, int(os.environ.get("SQS_WAIT_TIME_SECONDS", "1"))))
POLL_INTERVAL: float = max(0.1, min(10.0, float(os.environ.get("POLL_INTERVAL", "0.5"))))

# =============================================================================
#  AI 呼び出し
# =============================================================================

USE_DIRECT_API: bool = os.environ.get("USE_DIRECT_API", "0") == "1"
ANTHROPIC_API_KEY: str = os.environ.get("ANTHROPIC_API_KEY", "")
CLAUDE_COMMAND: str = os.environ.get("CLAUDE_COMMAND", "claude")
CLAUDE_MODEL: str = os.environ.get("CLAUDE_MODEL", "claude-haiku-4-5")
CLAUDE_TIMEOUT: int = int(os.environ.get("CLAUDE_TIMEOUT", "60"))

# AI 実行結果の型互換用定数
MAX_TOKENS: int = 4096
MAX_PROMPT_LEN_BASE: int = 31000  # CLI モードのコマンドライン長制限

# =============================================================================
#  動作パラメータ
# =============================================================================

FOLLOWUP_WAIT_SECONDS: int = int(os.environ.get("FOLLOWUP_WAIT_SECONDS", "30"))
MAX_AI_CONVERSATION_TURNS: int = int(os.environ.get("MAX_AI_CONVERSATION_TURNS", "10"))
REPLY_COOLDOWN_SECONDS: int = int(os.environ.get("REPLY_COOLDOWN_SECONDS", "15"))

# =============================================================================
#  ChatWork API
# =============================================================================

CHATWORK_API_TIMEOUT: int = 30
CHATWORK_API_BASE: str = "https://api.chatwork.com/v2"

# =============================================================================
#  デバッグ通知
# =============================================================================

DEBUG_NOTICE_ENABLED: bool = os.environ.get("DEBUG_NOTICE_ENABLED", "1") == "1"
DEBUG_NOTICE_CHATWORK_TOKEN: str = os.environ.get("DEBUG_NOTICE_CHATWORK_TOKEN", "")
DEBUG_NOTICE_CHATWORK_ROOM_ID: int = int(os.environ.get("DEBUG_NOTICE_CHATWORK_ROOM_ID", "0"))
DEBUG_NOTICE_CHATWORK_ACCOUNT_ID: int = int(os.environ.get("DEBUG_NOTICE_CHATWORK_ACCOUNT_ID", "0"))

# =============================================================================
#  Google Workspace API
# =============================================================================

GOOGLE_DRIVE_INCLUDE_MY_DRIVE: bool = os.environ.get("GOOGLE_DRIVE_INCLUDE_MY_DRIVE", "0") == "1"
GOOGLE_DRIVE_INCLUDE_SHARED: bool = os.environ.get("GOOGLE_DRIVE_INCLUDE_SHARED", "1") == "1"
GOOGLE_API_SCOPES: list[str] = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/presentations.readonly",
]
GOOGLE_TOKEN_PATH: str = os.path.join(SCRIPT_DIR, "google_token.json")

# Google URL 自動取得の制限値
GOOGLE_SHEET_MAX_ROWS: int = 100
GOOGLE_DOC_MAX_CHARS: int = 10000

# =============================================================================
#  雑談フィルタ（モード0: ログモード用）
# =============================================================================

CASUAL_CHAT_MAX_LENGTH: int = 15
CASUAL_CHAT_KEYWORDS: list[str] = [
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

# =============================================================================
#  AI拒否検出（セーフティフィルタ応答のブロック）
# =============================================================================

AI_REFUSAL_KEYWORDS: list[str] = [
    "申し訳ありませんが、このリクエストにはお応えできません",
    "このリクエストにはお応えできません",
    "なりすまし行為",
    "利用規約違反",
    "お応えすることができません",
    "I can't help with",
    "I cannot assist with",
    "I'm not able to",
]

# =============================================================================
#  フォローアップ検出
# =============================================================================

FOLLOWUP_KEYWORDS: list[str] = [
    "確認します", "確認してみます", "確認しますね",
    "調べます", "調べてみます", "調べますね",
    "チェックします", "チェックしてみます",
    "少々お待ち", "お待ちください", "少し確認",
]

# =============================================================================
#  会話モード定義
# =============================================================================

TALK_MODES: dict[int, dict[str, str]] = {
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
#  API 料金（USD / 1M tokens）
# =============================================================================

MODEL_PRICING: dict[str, dict[str, float]] = {
    "claude-haiku-4-5": {"input": 0.80, "output": 4.00},
    "claude-sonnet-4-6": {"input": 3.00, "output": 15.00},
    "claude-opus-4-6": {"input": 15.00, "output": 75.00},
}

# =============================================================================
#  メンバー自動検出
# =============================================================================


def _load_env_file(filepath: str) -> dict[str, str]:
    """key=value 形式の env ファイルを dict として読み込む"""
    result: dict[str, str] = {}
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


def _discover_members() -> dict[str, dict[str, Any]]:
    """members/ 配下の [0-9][0-9]_* フォルダを走査してメンバー dict を構築"""
    members: dict[str, dict[str, Any]] = {}
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


def load_talk_modes(member_dir: str) -> tuple[int, dict[str, int]]:
    """mode.env から会話モード設定を読み込む。(デフォルトモード, {ルームID: モード}) を返す"""
    mode_env = os.path.join(member_dir, "mode.env")
    default_mode = 1
    room_modes: dict[str, int] = {}
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
        import logging
        logging.getLogger(__name__).error(f"mode.env読み込みエラー: {mode_env}: {e}")
    return default_mode, room_modes


def get_talk_mode(member_dir: str, room_id: str = "") -> int:
    """指定ルームの会話モードを返す（ルーム別 > デフォルト）"""
    default_mode, room_modes = load_talk_modes(member_dir)
    if room_id and room_id in room_modes:
        return room_modes[room_id]
    return default_mode


# メンバー情報を起動時に構築
MEMBERS: dict[str, dict[str, Any]] = _discover_members()
ACCOUNT_TO_MEMBER: dict[int, dict[str, Any]] = {m["account_id"]: m for m in MEMBERS.values()}
ALL_MEMBER_IDS: set[str] = {str(m["account_id"]) for m in MEMBERS.values()}


def find_member_key(member: dict[str, Any]) -> str | None:
    """メンバー dict から MEMBERS 上のキー（例: "01_yamada"）を逆引きする"""
    for k, m in MEMBERS.items():
        if m["account_id"] == member["account_id"]:
            return k
    return None


# =============================================================================
#  PID ファイルパス
# =============================================================================

PID_FILE: str = os.path.join(SCRIPT_DIR, ".claude_pids")
USAGE_FILE: str = os.path.join(SCRIPT_DIR, "api_usage.json")
