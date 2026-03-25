"""
Chatwork Webhook SQS Poller for Windows PC
SQSキューからメッセージをバッチ取得し、メンバーごとに並列でClaude Codeを実行する
※ メンバーごとに別スレッド・別cwdで並列実行（同一メンバーは排他制御）
※ members/00_common_rules.md + メンバー個別の .md でAIの振る舞いを制御
※ Claude Code の出力を担当者として Chatwork に返信（[rp]タグ自動付与）
※ エラー時はエラー報告アカウントで報告ルームに通知
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

# ===== 設定 =====
AWS_REGION = os.environ.get("AWS_REGION", "ap-northeast-1")
QUEUE_URL = os.environ.get("SQS_QUEUE_URL", "")
POLL_INTERVAL = max(0.1, min(10.0, float(os.environ.get("POLL_INTERVAL", "0.5"))))
CLAUDE_COMMAND = os.environ.get("CLAUDE_COMMAND", "claude")
CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL", "claude-haiku-4-5")
MAINTENANCE_ROOM_ID = os.environ.get("MAINTENANCE_ROOM_ID", "")
FOLLOWUP_WAIT_SECONDS = int(os.environ.get("FOLLOWUP_WAIT_SECONDS", "30"))
MAX_AI_CONVERSATION_TURNS = int(os.environ.get("MAX_AI_CONVERSATION_TURNS", "10"))
REPLY_COOLDOWN_SECONDS = int(os.environ.get("REPLY_COOLDOWN_SECONDS", "15"))
CLAUDE_TIMEOUT = int(os.environ.get("CLAUDE_TIMEOUT", "60"))

# フォローアップ検出キーワード
FOLLOWUP_KEYWORDS = [
    "確認します", "確認してみます", "確認しますね",
    "調べます", "調べてみます", "調べますね",
    "チェックします", "チェックしてみます",
    "少々お待ち", "お待ちください", "少し確認",
]

# Chatwork API
CHATWORK_API_BASE = "https://api.chatwork.com/v2"
CHATWORK_API_TOKEN_ERROR_REPORTER = os.environ.get("CHATWORK_API_TOKEN_ERROR_REPORTER", "")
CHATWORK_ERROR_ROOM_ID = int(os.environ.get("CHATWORK_ERROR_ROOM_ID", "0"))

# 担当者設定（フォルダを自動スキャン）
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
MEMBERS_DIR = os.path.join(SCRIPT_DIR, "members")

def _load_env_file(filepath):
    """envファイルを読み込んでdictで返す。なければ空dict"""
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
    """members/ 配下のメンバーフォルダを自動スキャンしてMEMBERS dictを構築"""
    members = {}
    pattern = os.path.join(MEMBERS_DIR, "[0-9][0-9]_*")
    for member_dir in sorted(glob.glob(pattern)):
        if not os.path.isdir(member_dir):
            continue
        member_key = os.path.basename(member_dir)
        env = _load_env_file(os.path.join(member_dir, "member.env"))
        name = env.get("NAME", "")
        account_id = env.get("ACCOUNT_ID", "")
        cw_token = env.get("CHATWORK_API_TOKEN", "")
        allowed_rooms_str = env.get("ALLOWED_ROOMS", "")
        if not name or not account_id:
            continue  # NAME と ACCOUNT_ID は必須
        allowed_rooms = set()
        if allowed_rooms_str:
            allowed_rooms = {s.strip() for s in allowed_rooms_str.split(",") if s.strip()}
        members[member_key] = {
            "name": name,
            "account_id": int(account_id),
            "cw_token": cw_token,
            "dir": member_dir,
            "allowed_rooms": allowed_rooms,
        }
    return members

def _load_talk_modes(member_dir):
    """mode.envからTALK_MODE設定を読み込む。デフォルトとルーム別を返す。
    形式:
      TALK_MODE=2            → デフォルト
      TALK_MODE=426936385:3  → ルーム別
      TALK_MODE=427388771:1  → ルーム別
    """
    mode_env = os.path.join(member_dir, "mode.env")
    default_mode = 1
    room_modes = {}
    if not os.path.exists(mode_env):
        return default_mode, room_modes
    try:
        with open(mode_env, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if not line.startswith("TALK_MODE="):
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
    """該当ルームの会話モードを返す"""
    default_mode, room_modes = _load_talk_modes(member_dir)
    if room_id and str(room_id) in room_modes:
        return room_modes[str(room_id)]
    return default_mode

# 会話モード定義
TALK_MODES = {
    0: {
        "name": "メンテナンス",
        "instruction": (
            "メンテナンスモードです。機械的に端的に話してください。"
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
}

MEMBERS = _discover_members()

# account_id → メンバー設定の逆引き
ACCOUNT_TO_MEMBER = {m["account_id"]: m for m in MEMBERS.values()}
ALL_MEMBER_IDS = {str(m["account_id"]) for m in MEMBERS.values()}

# 会話チェーン管理（ルームごとのAI同士往復カウンタ）
# key: room_id, value: {"count": int, "last_human_time": float}
_conversation_chains = {}
_chain_lock = threading.Lock()

# シャットダウンフラグ
_shutdown_requested = False

# 実行中のClaudeプロセスを追跡（強制終了用）
_active_processes = []
_process_lock = threading.Lock()

# メンバーごとのセッション状態
# key: member_key, value: {"status": "idle"|"running", "started": timestamp, "room_id": str, "model": str}
_session_states = {key: {"status": "idle", "started": None, "room_id": "", "model": ""} for key in MEMBERS}
_session_lock = threading.Lock()

# メンバーごとの最終発言時刻（連投防止）
# key: member_key, value: timestamp
_last_reply_time = {}
_reply_time_lock = threading.Lock()


# ===== ログ設定 =====
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(os.path.join(SCRIPT_DIR, "webhook_poller.log"), encoding="utf-8"),
        logging.StreamHandler()
    ]
)
log = logging.getLogger(__name__)

def chatwork_post(token, room_id, message):
    """Chatwork にメッセージを投稿"""
    try:
        res = requests.post(
            f"{CHATWORK_API_BASE}/rooms/{room_id}/messages",
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
    """エラー報告アカウントでエラー投稿"""
    msg = f"[info][title]{title}[/title]{detail}[/info]"
    chatwork_post(CHATWORK_API_TOKEN_ERROR_REPORTER, CHATWORK_ERROR_ROOM_ID, msg)

def check_ai_conversation_allowed(room_id, sender):
    """AI同士の会話が許可されているか判定。人間の発言でカウンタリセット"""
    with _chain_lock:
        sender_is_ai = str(sender) in ALL_MEMBER_IDS
        if not sender_is_ai:
            # 人間からの発言: カウンタリセット
            _conversation_chains[str(room_id)] = {"count": 0, "last_human_time": time.time()}
            return True
        # AIからの発言: カウンタチェック
        chain = _conversation_chains.get(str(room_id))
        if not chain:
            # 人間が起点の会話チェーンがない → 拒否
            log.info(f"AI発言だが会話チェーンなし: room={room_id}, sender={sender}")
            return False
        chain["count"] += 1
        if chain["count"] > MAX_AI_CONVERSATION_TURNS:
            log.info(f"AI会話上限到達: room={room_id}, count={chain['count']}/{MAX_AI_CONVERSATION_TURNS}")
            return False
        log.info(f"AI会話許可: room={room_id}, count={chain['count']}/{MAX_AI_CONVERSATION_TURNS}")
        return True

def get_sender_name(token, room_id, sender_account_id):
    """送信者の表示名をルームメンバーから取得"""
    try:
        res = requests.get(
            f"{CHATWORK_API_BASE}/rooms/{room_id}/members",
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
            f"{CHATWORK_API_BASE}/rooms/{room_id}/messages/{message_id}",
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

def needs_followup(reply_text):
    """返信テキストにフォローアップが必要なキーワードが含まれているか判定"""
    for kw in FOLLOWUP_KEYWORDS:
        if kw in reply_text:
            return True
    return False

def gather_room_context(token, room_id):
    """ルームの追加情報（メンバー一覧、直近メッセージ）を収集"""
    context_parts = []
    # メンバー一覧
    try:
        res = requests.get(
            f"{CHATWORK_API_BASE}/rooms/{room_id}/members",
            headers={"X-ChatWorkToken": token}
        )
        if res.status_code == 200:
            members = res.json()
            member_list = ", ".join([f"{m['name']}(ID:{m['account_id']})" for m in members])
            context_parts.append(f"ルームメンバー: {member_list}")
    except Exception as e:
        log.error(f"メンバー取得エラー: {e}")
    # 直近メッセージ（最新5件）
    try:
        res = requests.get(
            f"{CHATWORK_API_BASE}/rooms/{room_id}/messages",
            headers={"X-ChatWorkToken": token},
            params={"force": 1}
        )
        if res.status_code == 200:
            msgs = res.json()[-5:]
            recent = []
            for m in msgs:
                name = m.get("account", {}).get("name", "不明")
                body = m.get("body", "")[:200]
                recent.append(f"[{name}] {body}")
            context_parts.append("直近のメッセージ:\n" + "\n".join(recent))
    except Exception as e:
        log.error(f"メッセージ取得エラー: {e}")
    return "\n\n".join(context_parts)

def load_instructions(member_dir, room_id=""):
    """共通指示 + メンバー固有指示 + ルーム固有指示を読み込んで指示文を構築"""
    instructions = []
    # 1. 共通ルール（members直下の 00_ で始まる .md のみ）を読み込む
    common_md_files = sorted(glob.glob(os.path.join(MEMBERS_DIR, "00_*.md")))
    # 2. メンバー固有の .md を読み込む（room_*.md と chat_history_*.md は除外）
    member_md_files = sorted([
        f for f in glob.glob(os.path.join(member_dir, "*.md"))
        if not os.path.basename(f).startswith("room_")
        and not os.path.basename(f).startswith("chat_history_")
        and os.path.basename(f) != "CLAUDE.md"
    ])
    # 3. ルーム固有の .md（あれば）
    room_md_files = []
    if room_id:
        room_md = os.path.join(member_dir, f"room_{room_id}.md")
        if os.path.exists(room_md):
            room_md_files = [room_md]
    all_md_files = common_md_files + member_md_files + room_md_files
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

def save_chat_history(member_dir, room_id, sender_name, message, reply, member_name):
    """会話記録をメンバーフォルダに保存"""
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

def run_claude(prompt, cwd, member_name):
    """Claude Codeを実行。プロセス追跡付き。"""
    MAX_PROMPT_LEN = 31000 - len(CLAUDE_COMMAND) - len(CLAUDE_MODEL) - 50
    if len(prompt) > MAX_PROMPT_LEN:
        log.warning(f"プロンプトが長すぎるためトランケート: {len(prompt)} -> {MAX_PROMPT_LEN}文字")
        prompt = prompt[:MAX_PROMPT_LEN] + "\n\n（以降省略）"

    cmd = [CLAUDE_COMMAND, "-p", prompt, "--model", CLAUDE_MODEL]

    log.info(f">>> Claude Code 実行開始 [{member_name}] model={CLAUDE_MODEL} cwd={cwd} timeout={CLAUDE_TIMEOUT}秒"
             f" prompt_len={len(prompt)}")

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=cwd
    )

    # プロセスを追跡リストに登録 + PIDファイルに記録
    with _process_lock:
        _active_processes.append(proc)
    _save_pid(proc.pid)

    try:
        stdout, stderr = proc.communicate(timeout=CLAUDE_TIMEOUT)
        log.info(f"<<< Claude Code 実行完了 [{member_name}] (exit={proc.returncode})")
        result = subprocess.CompletedProcess(cmd, proc.returncode, stdout, stderr)
        return result

    except subprocess.TimeoutExpired:
        log.error(f"<<< Claude Code タイムアウト [{member_name}] ({CLAUDE_TIMEOUT}秒超過) cwd={cwd} pid={proc.pid}")
        try:
            proc.kill()
            proc.wait(timeout=10)
            log.info(f"タイムアウト: プロセス強制終了成功 pid={proc.pid}")
        except Exception as kill_err:
            log.error(f"タイムアウト: プロセス強制終了失敗 pid={proc.pid} error={kill_err}")
        # kill後のプロセス状態を確認
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

def kill_all_claude_processes():
    """全ての実行中Claudeプロセスを強制終了"""
    with _process_lock:
        for proc in _active_processes:
            try:
                proc.kill()
                log.info(f"Claudeプロセス強制終了: pid={proc.pid}")
            except Exception:
                pass
        _active_processes.clear()

_PID_FILE = os.path.join(SCRIPT_DIR, ".claude_pids")

def _save_pid(pid):
    """子プロセスのPIDをファイルに記録"""
    try:
        with open(_PID_FILE, "a", encoding="utf-8") as f:
            f.write(f"{pid}\n")
    except Exception:
        pass

def _remove_pid(pid):
    """完了したPIDをファイルから削除"""
    try:
        if not os.path.exists(_PID_FILE):
            return
        with open(_PID_FILE, "r", encoding="utf-8") as f:
            pids = {l.strip() for l in f if l.strip()}
        pids.discard(str(pid))
        with open(_PID_FILE, "w", encoding="utf-8") as f:
            for p in pids:
                f.write(f"{p}\n")
    except Exception:
        pass

def kill_orphan_claude_processes():
    """起動時にポーラーが起動した残留プロセスを検知しkill（手動claudeは対象外）"""
    killed = 0
    if not os.path.exists(_PID_FILE):
        return 0
    try:
        with open(_PID_FILE, "r", encoding="utf-8") as f:
            pids = [l.strip() for l in f if l.strip()]
        for pid_str in pids:
            try:
                pid = int(pid_str)
                if os.name == 'nt':
                    # プロセスが存在するか確認
                    check = subprocess.run(
                        ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                        capture_output=True, text=True, timeout=5
                    )
                    if str(pid) in check.stdout:
                        log.warning(f"残留プロセス検出: PID={pid}")
                        subprocess.run(["taskkill", "/F", "/PID", str(pid)],
                                     capture_output=True, timeout=5)
                        log.info(f"残留プロセスをkill: PID={pid}")
                        killed += 1
                else:
                    os.kill(pid, signal.SIGKILL)
                    killed += 1
            except (ValueError, ProcessLookupError, PermissionError):
                pass
        # PIDファイルをクリア
        os.remove(_PID_FILE)
    except Exception as e:
        log.warning(f"残留プロセス処理エラー: {e}")
    return killed

def handle_status_command(member, room_id):
    """メンテナンスコマンド /status: メンバーの設定状況を報告"""
    member_dir = member["dir"]
    lines = []
    lines.append(f"[info][title]/status: {member['name']}[/title]")

    # 共通ルール
    common_files = sorted(glob.glob(os.path.join(MEMBERS_DIR, "00_*.md")))
    lines.append(f"■ 共通ルール: {len(common_files)}件")
    for f in common_files:
        name = os.path.basename(f)
        size = os.path.getsize(f)
        lines.append(f"  - {name} ({size}B)")

    # メンバー固有 .md
    member_files = sorted([
        f for f in glob.glob(os.path.join(member_dir, "*.md"))
        if not os.path.basename(f).startswith("room_")
        and not os.path.basename(f).startswith("chat_history_")
        and os.path.basename(f) != "CLAUDE.md"
    ])
    lines.append(f"\n■ ペルソナ/指示: {len(member_files)}件")
    for f in member_files:
        name = os.path.basename(f)
        size = os.path.getsize(f)
        lines.append(f"  - {name} ({size}B)")

    # ルーム固有 .md
    room_files = sorted(glob.glob(os.path.join(member_dir, "room_*.md")))
    lines.append(f"\n■ ルーム別設定: {len(room_files)}件")
    for f in room_files:
        name = os.path.basename(f)
        size = os.path.getsize(f)
        lines.append(f"  - {name} ({size}B)")

    # CLAUDE.md
    claude_md = os.path.join(member_dir, "CLAUDE.md")
    if os.path.exists(claude_md):
        size = os.path.getsize(claude_md)
        lines.append(f"\n■ CLAUDE.md: あり ({size}B)")
    else:
        lines.append(f"\n■ CLAUDE.md: なし")

    # 会話記録
    history_files = sorted(glob.glob(os.path.join(member_dir, "chat_history_*.md")))
    lines.append(f"\n■ 会話記録: {len(history_files)}件")
    for f in history_files:
        name = os.path.basename(f)
        size = os.path.getsize(f)
        lines.append(f"  - {name} ({size}B)")

    # 拒否ログ
    reject_log = os.path.join(member_dir, "rejected_rooms.log")
    if os.path.exists(reject_log):
        size = os.path.getsize(reject_log)
        lines.append(f"\n■ 拒否ログ: あり ({size}B)")

    # config.envパラメータ
    allowed = member.get("allowed_rooms", set())
    rooms_str = ", ".join(sorted(allowed)) if allowed else "全ルーム"
    # ルーム名のキャッシュ取得
    room_names = {}
    if member.get("cw_token"):
        try:
            res = requests.get(
                f"{CHATWORK_API_BASE}/rooms",
                headers={"X-ChatWorkToken": member["cw_token"]}
            )
            if res.status_code == 200:
                for r in res.json():
                    room_names[str(r["room_id"])] = r.get("name", "")
        except Exception:
            pass

    # モード設定
    talk_mode_default, talk_mode_rooms = _load_talk_modes(member["dir"])
    lines.append(f"\n■ 会話モード (mode.env)")
    lines.append(f"  TALK_MODE={talk_mode_default}({TALK_MODES.get(talk_mode_default, {}).get('name', '不明')})")
    if talk_mode_rooms:
        for rid, mode in sorted(talk_mode_rooms.items()):
            rname = room_names.get(rid, "")
            rname_str = f"{rname} " if rname else ""
            lines.append(f"  TALK_MODE={rname_str}{rid}:{mode}({TALK_MODES.get(mode, {}).get('name', '不明')})")
    else:
        lines.append(f"  ルーム別指定: なし")

    lines.append(f"\n■ 設定値")
    lines.append(f"  CLAUDE_COMMAND={CLAUDE_COMMAND}")
    lines.append(f"  CLAUDE_MODEL={CLAUDE_MODEL}")
    lines.append(f"  CLAUDE_TIMEOUT={CLAUDE_TIMEOUT}秒")
    lines.append(f"  FOLLOWUP_WAIT_SECONDS={FOLLOWUP_WAIT_SECONDS}秒")
    lines.append(f"  MAX_AI_CONVERSATION_TURNS={MAX_AI_CONVERSATION_TURNS}")
    lines.append(f"  REPLY_COOLDOWN_SECONDS={REPLY_COOLDOWN_SECONDS}秒")
    if allowed:
        allowed_with_names = []
        for rid in sorted(allowed):
            rname = room_names.get(rid, "")
            allowed_with_names.append(f"{rname}({rid})" if rname else rid)
        lines.append(f"  許可ルーム=[{', '.join(allowed_with_names)}]")
    else:
        lines.append(f"  許可ルーム=なし（全送信不可）")
    lines.append(f"  cwd={member_dir}")

    lines.append("[/info]")
    return "\n".join(lines)

def handle_session_command(room_id):
    """メンテナンスコマンド /session: 全メンバーのClaude実行状態を報告"""
    lines = []
    lines.append(f"[info][title]/session[/title]")
    with _session_lock:
        for key, member in MEMBERS.items():
            state = _session_states.get(key, {"status": "idle"})
            status = state["status"]
            if status == "running":
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
    if owner_id:
        owner_id_int = int(owner_id) if str(owner_id).isdigit() else None
        if owner_id_int and owner_id_int in ACCOUNT_TO_MEMBER:
            return ACCOUNT_TO_MEMBER[owner_id_int]
    return None

def process_message(body: dict):
    """SQSメッセージを処理してClaude Codeを実行し、Chatworkで返信"""
    log.debug(f"SQS body: {body}")
    room_id = body.get("room_id", "")
    sender = body.get("sender_account_id", "")
    message_id = body.get("message_id", "")
    sender_name = body.get("sender_name", "")
    message = body.get("body", "")

    # sender が空の場合、Chatwork API から補完
    if message_id and (not sender or not sender_name):
        member_tmp = find_target_member(body) or MEMBERS[next(iter(MEMBERS))]
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
    log.info(f"本文: {message[:100]}{'...' if len(message) > 100 else ''}")

    # 宛先メンバーを特定
    member = find_target_member(body)
    if not member:
        # デフォルトは最初のメンバー
        first_key = next(iter(MEMBERS))
        member = MEMBERS[first_key]
        log.info(f"宛先不明のためデフォルト: {member['name']}")
    else:
        log.info(f"宛先: {member['name']}")

    # 自分自身の発言は無視（無限ループ防止）
    if str(sender) == str(member["account_id"]):
        log.info(f"自分自身の発言のためスキップ: {member['name']}")
        return

    # メンテナンスコマンド判定: メッセージ本文から [To:xxx]名前さん\n を全て除去して比較
    raw_command = message.strip()
    raw_command = re.sub(r'\[To:\d+\][^\n]*\n', '', raw_command).strip()
    if MAINTENANCE_ROOM_ID and str(room_id) == MAINTENANCE_ROOM_ID:
        if raw_command == "/status":
            log.info(f"/status コマンド検出: {member['name']}")
            status_msg = handle_status_command(member, room_id)
            chatwork_post(member["cw_token"], room_id, status_msg)
            return
        if raw_command == "/session":
            log.info(f"/session コマンド検出")
            session_msg = handle_session_command(room_id)
            chatwork_post(member["cw_token"], room_id, session_msg)
            return

    # ルームIDホワイトリスト判定（空=全送信不可）
    allowed = member.get("allowed_rooms", set())
    if not allowed or str(room_id) not in allowed:
        log.warning(
            f"[許可されていないルーム] "
            f"メンバー={member['name']}, "
            f"ルームID={room_id}, "
            f"送信者={sender_name}(ID:{sender}), "
            f"メッセージID={message_id}, "
            f"本文={message[:200]}, "
            f"許可ルーム={allowed}"
        )
        # 拒否ログをメンバーフォルダに記録
        try:
            reject_log = os.path.join(member["dir"], "rejected_rooms.log")
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            with open(reject_log, "a", encoding="utf-8") as f:
                f.write(f"[{now}] room={room_id} sender={sender_name}(ID:{sender}) msg={message[:200]}\n")
        except Exception as e:
            log.error(f"拒否ログ書き込みエラー: {e}")
        return

    # 会話チェーン管理（AI同士の会話回数制御）
    if not check_ai_conversation_allowed(room_id, sender):
        # 上限到達時は終了メッセージを投稿
        if str(sender) in ALL_MEMBER_IDS:
            chatwork_post(member["cw_token"], room_id, "そろそろこの辺で！また話しましょう😊")
            log.info(f"AI会話上限のため終了メッセージ投稿: {member['name']}")
        return

    member_dir = member["dir"]

    # 連投防止クールダウン
    member_key = None
    for k, m in MEMBERS.items():
        if m["account_id"] == member["account_id"]:
            member_key = k
            break
    if member_key:
        wait = 0
        with _reply_time_lock:
            last_time = _last_reply_time.get(member_key, 0)
            elapsed = time.time() - last_time
            if elapsed < REPLY_COOLDOWN_SECONDS:
                wait = REPLY_COOLDOWN_SECONDS - elapsed
        if wait > 0:
            log.info(f"[{member['name']}] クールダウン待機: {wait:.1f}秒")
            time.sleep(wait)

    # 会話モード決定（TALK_MODE=ルームID:モード > TALK_MODE=デフォルト > 1）
    talk_mode = _get_talk_mode(member_dir, str(room_id))
    talk_info = TALK_MODES.get(talk_mode, TALK_MODES[1])
    log.info(f"会話モード: {talk_mode}({talk_info['name']})")

    # 指示ファイル読み込み
    instructions = load_instructions(member_dir, room_id)

    # モード3(ペルソナ+)の場合、ルームメンバー情報を取得
    room_members_info = ""
    if talk_mode == 3:
        try:
            res = requests.get(
                f"{CHATWORK_API_BASE}/rooms/{room_id}/members",
                headers={"X-ChatWorkToken": member["cw_token"]}
            )
            if res.status_code == 200:
                others = [
                    f"  - {m['name']}(ID:{m['account_id']})"
                    for m in res.json()
                    if str(m["account_id"]) != str(member["account_id"])
                    and m.get("role", "") != "readonly"
                ]
                if others:
                    room_members_info = "=== このルームの他のメンバー（話を振れる相手） ===\n" + "\n".join(others) + "\n\n"
        except Exception as e:
            log.error(f"ルームメンバー取得エラー(モード3): {e}")

    # 事前に届いたメッセージの文脈
    prior_context = body.get("_prior_context", "")

    # Claude Code に渡すプロンプト
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
        prompt += (
            f"=== これより前に届いたメッセージ（まとめて把握すること） ===\n"
            f"{prior_context}\n\n"
        )
    prompt += (
        f"=== 受信メッセージ情報（これに返信すること） ===\n"
        f"ルームID: {room_id}\n"
        f"送信者アカウントID: {sender}\n"
        f"送信者名: {sender_name}\n"
        f"メッセージID: {message_id}\n"
        f"受信時刻: {timestamp}\n\n"
        f"=== メッセージ本文 ===\n{message}"
    )

    try:
        # セッション状態: running
        if member_key:
            with _session_lock:
                _session_states[member_key] = {
                    "status": "running", "started": time.time(),
                    "room_id": str(room_id), "model": CLAUDE_MODEL
                }
        result = run_claude(prompt, member_dir, member["name"])

        reply = result.stdout.strip() if result.stdout else ""

        if result.returncode == 0 and reply:
            log.info(f"返信内容 [{member['name']}]: {reply[:500]}")
            # [rp]タグを自動構築（AIが[To:]や[rp]を出力していない場合のみ）
            if reply.startswith("[To:") or reply.startswith("[rp "):
                log.info(f"AI出力にタグあり、自動付与スキップ")
            elif message_id and sender:
                sender_name_resolved = get_sender_name(member["cw_token"], room_id, sender)
                if sender_name_resolved:
                    rp_header = f"[rp aid={sender} to={room_id}-{message_id}]{sender_name_resolved}さん"
                    reply = f"{rp_header}\n{reply}"
                    log.info(f"[rp]タグ付与: {rp_header}")
                else:
                    log.warning(f"送信者名が取得できませんでした: sender={sender}")
            else:
                log.warning(f"message_idまたはsenderが不足: message_id={message_id}, sender={sender}")
            # 担当者として Chatwork に返信
            chatwork_post(member["cw_token"], room_id, reply)
            # 会話記録を保存
            raw_reply = result.stdout.strip()
            save_chat_history(member_dir, room_id, sender_name, message, raw_reply, member["name"])
            # 最終発言時刻を記録（連投防止）
            if member_key:
                with _reply_time_lock:
                    _last_reply_time[member_key] = time.time()

            # フォローアップ判定（元のAI出力で判定、[rp]タグ付与前のテキスト）
            if needs_followup(raw_reply):
                log.info(f"フォローアップ検出: {FOLLOWUP_WAIT_SECONDS}秒待機します")
                time.sleep(FOLLOWUP_WAIT_SECONDS)
                # ルーム情報を収集
                room_context = gather_room_context(member["cw_token"], room_id)
                log.info(f"ルーム情報収集完了")
                # フォローアップ用プロンプト
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
                    followup_result = run_claude(followup_prompt, member_dir, f"{member['name']}(フォローアップ)")
                    followup_reply = followup_result.stdout.strip() if followup_result.stdout else ""
                    if followup_result.returncode == 0 and followup_reply:
                        log.info(f"フォローアップ返信 [{member['name']}]: {followup_reply[:500]}")
                        # [rp]タグ付与（元メッセージへの返信）
                        followup_sender_name = get_sender_name(member["cw_token"], room_id, sender) or sender_name
                        if message_id and sender and followup_sender_name:
                            rp_header = f"[rp aid={sender} to={room_id}-{message_id}]{followup_sender_name}さん"
                            followup_reply = f"{rp_header}\n{followup_reply}"
                        chatwork_post(member["cw_token"], room_id, followup_reply)
                    else:
                        log.warning(f"フォローアップ返信が空またはエラー: exit={followup_result.returncode}")
                    # フォローアップ成功時のみ「おやすみなさい」
                    if followup_result.returncode == 0 and followup_reply:
                        log.info(f"フォローアップ完了、おやすみ発言を投稿")
                        chatwork_post(member["cw_token"], room_id, "おやすみなさい")
                except Exception as e:
                    log.error(f"フォローアップ実行エラー: {e}")

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
        notify_error(
            f"Claude Code タイムアウト [{member['name']}]",
            f"Claude Code が{CLAUDE_TIMEOUT}秒以内に応答しませんでした。\nroom: {room_id}\n送信者: {sender}\nメッセージ: {message[:200]}"
        )
    except FileNotFoundError:
        log.error(f"Claude Code が見つかりません: {CLAUDE_COMMAND}")
        notify_error(
            "Claude Code 未検出",
            f"claude コマンドが見つかりません。\nPATH設定を確認してください。"
        )
    finally:
        # どんな例外でもセッション状態を確実にidle に戻す
        if member_key:
            with _session_lock:
                _session_states[member_key] = {"status": "idle", "started": None, "room_id": "", "model": ""}

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

# メンバーごとの排他ロック（同一メンバーの同時実行を防止）
_member_locks = {key: threading.Lock() for key in MEMBERS}

def process_member_batch(member_key, msg_list, sqs):
    """メンバー宛の複数メッセージをまとめて処理し、1回のClaude実行で返信"""
    member = MEMBERS[member_key]
    lock = _member_locks.get(member_key)
    if lock:
        lock.acquire()
    try:
        # 自分自身のメッセージをバッチから除外（無限ループ防止）
        my_aid = str(member["account_id"])
        filtered = []
        for body_data, msg in msg_list:
            # sender_account_idが空の場合はAPIで補完して判定
            s = body_data.get("sender_account_id", "")
            if not s:
                room_id = body_data.get("room_id", "")
                msg_id = body_data.get("message_id", "")
                if msg_id:
                    info = get_message_info(member["cw_token"], room_id, msg_id)
                    if info:
                        s = info.get("account_id", "")
            if str(s) == my_aid:
                log.info(f"[{member['name']}] バッチ: 自分自身のメッセージをスキップ")
                continue
            filtered.append((body_data, msg))
        msg_list = filtered

        if not msg_list:
            log.info(f"[{member['name']}] バッチ: 処理対象メッセージなし")
        elif len(msg_list) == 1:
            # 1件だけなら従来通り
            process_message(msg_list[0][0])
        else:
            # 複数件: 全メッセージを文脈としてまとめ、最後のメッセージに対して返信
            context_lines = []
            for i, (body_data, _) in enumerate(msg_list[:-1]):
                sender_name = body_data.get("sender_name", "")
                body_text = body_data.get("body", "")
                if not sender_name:
                    room_id = body_data.get("room_id", "")
                    msg_id = body_data.get("message_id", "")
                    if msg_id:
                        info = get_message_info(member["cw_token"], room_id, msg_id)
                        if info:
                            sender_name = info.get("name", "不明")
                if not sender_name:
                    sender_name = "不明"
                context_lines.append(f"[{sender_name}] {body_text}")

            last_body = dict(msg_list[-1][0])  # コピーして元のdictを汚染しない
            last_body["_prior_context"] = "\n".join(context_lines)
            log.info(f"[{member['name']}] バッチ処理: {len(msg_list)}件まとめ（{len(msg_list)-1}件を文脈、1件を処理対象）")
            process_message(last_body)
    except Exception as e:
        log.error(f"バッチ処理エラー [{member['name']}]: {e}")
        notify_error(f"バッチ処理エラー [{member['name']}]", f"{e}")
    finally:
        if lock:
            lock.release()
        # 全メッセージをSQSから削除
        for _, msg in msg_list:
            try:
                sqs.delete_message(QueueUrl=QUEUE_URL, ReceiptHandle=msg["ReceiptHandle"])
            except Exception as e:
                log.error(f"SQSメッセージ削除エラー: {e}")

def main():
    # メンバー検出チェック
    if not MEMBERS:
        log.error("メンバーが1人も見つかりません。members/ 配下にメンバーフォルダと member.env を作成してください")
        return

    # 必須設定チェック
    errors = []
    if not QUEUE_URL:
        errors.append("SQS_QUEUE_URL が未設定です → config.env に SQS_QUEUE_URL=https://... を追加してください")
    if not CHATWORK_API_TOKEN_ERROR_REPORTER:
        errors.append("CHATWORK_API_TOKEN_ERROR_REPORTER が未設定です → config.env にエラー報告用ChatWorkアカウントのAPIトークンを設定してください")
    if CHATWORK_ERROR_ROOM_ID == 0:
        errors.append("CHATWORK_ERROR_ROOM_ID が未設定です → config.env にエラー報告先のChatWorkルームIDを設定してください")
    for key, member in MEMBERS.items():
        if not member["cw_token"]:
            errors.append(f"{member['name']} の CHATWORK_API_TOKEN が未設定です → members/{key}/member.env にChatWork APIトークンを設定してください")
        if not member.get("allowed_rooms"):
            log.warning(f"[{member['name']}] ALLOWED_ROOMS が空のため全ルーム送信不可です → members/{key}/member.env に許可ルームIDを設定してください")
    if errors:
        log.error("=== 起動失敗 ===")
        for e in errors:
            log.error(f"  {e}")
        return

    sqs = boto3.client("sqs", region_name=AWS_REGION)

    # メンバーフォルダ存在確認
    for key, member in MEMBERS.items():
        if not os.path.isdir(member["dir"]):
            log.error(f"作業フォルダが見つかりません: {member['dir']}")
            notify_error("起動エラー", f"作業フォルダが見つかりません: {member['dir']}")
            return

    # 起動時にキューをパージ（前回の残留メッセージを削除）
    try:
        sqs.purge_queue(QueueUrl=QUEUE_URL)
        log.info("起動時キューパージ完了")
    except Exception as e:
        log.warning(f"キューパージスキップ（前回パージから60秒以内の可能性）: {e}")

    log.info("=== Chatwork Webhook Poller 起動 ===")
    log.info(f"キュー: {QUEUE_URL}")
    log.info(f"ポーリング間隔: {POLL_INTERVAL}秒")
    log.info("モード: バッチ+並列処理（キュー全件読み込み→メンバーごとにまとめて処理）")
    log.info(f"=== config.env パラメータ ===")
    log.info(f"  CLAUDE_COMMAND={CLAUDE_COMMAND}")
    log.info(f"  CLAUDE_MODEL={CLAUDE_MODEL}")
    log.info(f"  CLAUDE_TIMEOUT={CLAUDE_TIMEOUT}秒")
    log.info(f"  FOLLOWUP_WAIT_SECONDS={FOLLOWUP_WAIT_SECONDS}秒")
    log.info(f"  MAX_AI_CONVERSATION_TURNS={MAX_AI_CONVERSATION_TURNS}ターン")
    log.info(f"  REPLY_COOLDOWN_SECONDS={REPLY_COOLDOWN_SECONDS}秒")
    log.info(f"=== メンバー読み込み ===")
    log.info(f"スキャン対象: {MEMBERS_DIR}")
    for idx, (key, member) in enumerate(MEMBERS.items(), 1):
        md_files = glob.glob(os.path.join(member["dir"], "*.md"))
        rooms = member.get("allowed_rooms", set())
        rooms_str = ", ".join(sorted(rooms)) if rooms else "なし（全送信不可）"
        log.info(f"  [{idx}/{len(MEMBERS)}] {member['name']} ({key})")
        log.info(f"    cwd: {member['dir']}")
        log.info(f"    account_id: {member['account_id']}")
        log.info(f"    cw_token: {'設定済' if member['cw_token'] else '未設定'}")
        log.info(f"    許可ルーム: [{rooms_str}]")
        log.info(f"    指示ファイル: {len(md_files)}件")
        for f in sorted(md_files):
            log.info(f"      - {os.path.basename(f)}")
    log.info(f"メンバー合計: {len(MEMBERS)}名")

    # 残留Claudeプロセス検知・kill（ポーラーが起動したもののみ）
    orphans = kill_orphan_claude_processes()
    if orphans:
        log.info(f"残留プロセス {orphans}件 をkillしました")

    while not _shutdown_requested:
        try:
            # ===== フェーズ1: キューを空になるまで全件読み込み =====
            all_messages = []
            while True:
                res = sqs.receive_message(
                    QueueUrl=QUEUE_URL,
                    MaxNumberOfMessages=10,
                    WaitTimeSeconds=0
                )
                batch = res.get("Messages", [])
                if not batch:
                    break
                all_messages.extend(batch)
                # まだ残りがあるか確認
                remaining = get_queue_count(sqs)
                log.info(f"キュー読み込み中: 今回{len(batch)}件, 累計{len(all_messages)}件, 残り約{remaining}件")
                if remaining == 0:
                    break

            if not all_messages:
                continue

            log.info(f"キュー読み込み完了: 合計{len(all_messages)}件")

            # ===== フェーズ2: メンバーごとにメッセージをグループ化 =====
            member_messages = {}  # key: member_key, value: list of (body_data, sqs_msg)

            for msg in all_messages:
                try:
                    body_data = json.loads(msg["Body"])
                    member = find_target_member(body_data)
                    first_key = next(iter(MEMBERS))
                    if not member:
                        member = MEMBERS[first_key]
                    member_key = None
                    for k, m in MEMBERS.items():
                        if m["account_id"] == member["account_id"]:
                            member_key = k
                            break
                    if not member_key:
                        member_key = first_key
                    if member_key not in member_messages:
                        member_messages[member_key] = []
                    member_messages[member_key].append((body_data, msg))
                except Exception as e:
                    log.error(f"メッセージ解析エラー: {e}")
                    sqs.delete_message(QueueUrl=QUEUE_URL, ReceiptHandle=msg["ReceiptHandle"])

            # ===== フェーズ3: メンバーごとに並列処理 =====
            threads = []
            for member_key, msg_list in member_messages.items():
                t = threading.Thread(
                    target=process_member_batch,
                    args=(member_key, msg_list, sqs),
                    daemon=True
                )
                t.start()
                threads.append(t)
                log.info(f"バッチスレッド起動: {member_key} ({len(msg_list)}件)")

            # 全スレッド完了待ち（タイムアウト: CLAUDE_TIMEOUT + フォローアップ待機 + 余裕60秒）
            thread_timeout = CLAUDE_TIMEOUT * 2 + FOLLOWUP_WAIT_SECONDS + REPLY_COOLDOWN_SECONDS + 60
            for t in threads:
                t.join(timeout=thread_timeout)
                if t.is_alive():
                    log.error(f"スレッドがタイムアウト({thread_timeout}秒)しました。次のポーリングに進みます。")

        except Exception as e:
            log.error(f"ポーリングエラー: {e}")
            time.sleep(10)
            continue

        time.sleep(POLL_INTERVAL)

    log.info("=== Chatwork Webhook Poller 停止 ===")

def _signal_handler(sig, frame):
    """Ctrl+C / SIGTERM でgraceful shutdownを開始し、子プロセスをkill"""
    global _shutdown_requested
    _shutdown_requested = True
    log.info("シャットダウン要求を受信。実行中のClaudeプロセスを終了します...")
    kill_all_claude_processes()
    log.info("シャットダウン処理完了。ポーラーを停止します。")

if __name__ == "__main__":
    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)
    main()
