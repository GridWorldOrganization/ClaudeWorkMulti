"""
AI 実行（Anthropic API 直接 / Claude Code CLI）

USE_DIRECT_API に応じて実行方式を切り替える。
結果は AIResult で統一的に返す。
"""

import json
import logging
import os
import signal
import subprocess
import time
import threading
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from poller.config import (
    ANTHROPIC_API_KEY,
    CLAUDE_COMMAND,
    CLAUDE_MODEL,
    CLAUDE_TIMEOUT,
    MAX_PROMPT_LEN_BASE,
    MAX_TOKENS,
    MODEL_PRICING,
    PID_FILE,
    USAGE_FILE,
    USE_DIRECT_API,
)
from poller import state

log = logging.getLogger(__name__)


# =============================================================================
#  AI 実行結果
# =============================================================================

@dataclass
class AIResult:
    """AI 実行の結果を表す"""
    returncode: int
    output: str
    error: str


# =============================================================================
#  使用量トラッキング
# =============================================================================

_usage_lock = threading.Lock()


def record_usage(model: str, input_tokens: int, output_tokens: int) -> None:
    """API 使用量を月別・モデル別に記録する"""
    month_key = datetime.now().strftime("%Y-%m")
    with _usage_lock:
        data: dict[str, Any] = {}
        if os.path.exists(USAGE_FILE):
            try:
                with open(USAGE_FILE, "r", encoding="utf-8") as f:
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
            with open(USAGE_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            log.error(f"使用量記録エラー: {e}")


def get_monthly_usage() -> tuple[str, dict[str, Any]]:
    """当月の使用量を返す"""
    month_key = datetime.now().strftime("%Y-%m")
    if not os.path.exists(USAGE_FILE):
        return month_key, {}
    try:
        with open(USAGE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return month_key, {}
    return month_key, data.get(month_key, {})


# =============================================================================
#  AI モードラベル
# =============================================================================

def ai_mode_label() -> str:
    """現在の AI 呼び出し方式のラベルを返す"""
    return "Anthropic API" if USE_DIRECT_API else "Claude Code"


# =============================================================================
#  Anthropic API 直接呼び出し
# =============================================================================

def run_direct_api(prompt: str, member_name: str) -> AIResult:
    """Anthropic Messages API を直接呼び出す"""
    import anthropic

    log.info(f">>> {ai_mode_label()} 実行開始 [{member_name}] model={CLAUDE_MODEL}"
             f" timeout={CLAUDE_TIMEOUT}秒 prompt_len={len(prompt)}")
    start_time = time.time()

    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        response = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=MAX_TOKENS,
            messages=[{"role": "user", "content": prompt}],
            timeout=CLAUDE_TIMEOUT,
        )
        elapsed = time.time() - start_time
        reply = response.content[0].text if response.content else ""
        in_tok = response.usage.input_tokens if response.usage else 0
        out_tok = response.usage.output_tokens if response.usage else 0
        log.info(f"<<< {ai_mode_label()} 実行完了 [{member_name}] ({elapsed:.1f}秒)"
                 f" tokens: in={in_tok} out={out_tok}")
        record_usage(CLAUDE_MODEL, in_tok, out_tok)
        return AIResult(returncode=0, output=reply, error="")

    except anthropic.APITimeoutError:
        elapsed = time.time() - start_time
        log.error(f"<<< {ai_mode_label()} タイムアウト [{member_name}] ({elapsed:.1f}秒)")
        raise subprocess.TimeoutExpired(cmd=["anthropic-api"], timeout=CLAUDE_TIMEOUT)

    except anthropic.APIError as e:
        elapsed = time.time() - start_time
        log.error(f"<<< {ai_mode_label()} エラー [{member_name}] ({elapsed:.1f}秒): {e}")
        return AIResult(returncode=1, output="", error=str(e))


# =============================================================================
#  Claude Code CLI
# =============================================================================

def run_cli(prompt: str, cwd: str, member_name: str) -> AIResult:
    """Claude Code CLI（claude -p）を subprocess で実行する"""
    max_prompt_len = MAX_PROMPT_LEN_BASE - len(CLAUDE_COMMAND) - len(CLAUDE_MODEL) - 50
    if len(prompt) > max_prompt_len:
        log.warning(f"プロンプトが長すぎるためトランケート: {len(prompt)} -> {max_prompt_len}文字")
        prompt = prompt[:max_prompt_len] + "\n\n（以降省略）"

    # cwd にメンバーフォルダを使うと CLAUDE.md や chat_history を CLI が読み込み
    # タイムアウトの原因になるため、一時ディレクトリで実行する
    import tempfile
    cli_cwd = tempfile.mkdtemp(prefix="claude_poll_")

    cmd = [CLAUDE_COMMAND, "-p", prompt, "--model", CLAUDE_MODEL]
    log.info(f">>> {ai_mode_label()} 実行開始 [{member_name}] model={CLAUDE_MODEL}"
             f" cwd={cli_cwd} timeout={CLAUDE_TIMEOUT}秒 prompt_len={len(prompt)}")

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=cli_cwd,
    )

    log.info(f"Claude Code プロセス起動: pid={proc.pid}")
    with state.process_lock:
        state.active_processes.append(proc)
    _save_pid(proc.pid)

    try:
        stdout, stderr = proc.communicate(timeout=CLAUDE_TIMEOUT)
        if proc.poll() is not None:
            log.info(f"Claude Code プロセス終了確認済: pid={proc.pid} exit={proc.returncode}")
        else:
            log.warning(f"Claude Code プロセスがまだ生存: pid={proc.pid}")
        log.info(f"<<< {ai_mode_label()} 実行完了 [{member_name}] (exit={proc.returncode})")
        return AIResult(returncode=proc.returncode, output=stdout or "", error=stderr or "")

    except subprocess.TimeoutExpired:
        log.error(f"<<< {ai_mode_label()} タイムアウト [{member_name}] ({CLAUDE_TIMEOUT}秒超過)"
                  f" cwd={cli_cwd} pid={proc.pid}")
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
        with state.process_lock:
            if proc in state.active_processes:
                state.active_processes.remove(proc)
        _remove_pid(proc.pid)
        # 一時ディレクトリのクリーンアップ
        try:
            import shutil
            shutil.rmtree(cli_cwd, ignore_errors=True)
        except Exception:
            pass


# =============================================================================
#  統合エントリポイント
# =============================================================================

def run_ai(prompt: str, cwd: str, member_name: str) -> AIResult:
    """USE_DIRECT_API に応じて API 直接 or CLI を切り替えて実行する"""
    if USE_DIRECT_API:
        return run_direct_api(prompt, member_name)
    else:
        return run_cli(prompt, cwd, member_name)


# =============================================================================
#  PID ファイル管理
# =============================================================================

def _save_pid(pid: int) -> None:
    """子プロセスの PID をファイルに記録する"""
    try:
        with open(PID_FILE, "a", encoding="utf-8") as f:
            f.write(f"{pid}\n")
    except Exception:
        pass


def _remove_pid(pid: int) -> None:
    """完了した PID をファイルから削除する"""
    try:
        if not os.path.exists(PID_FILE):
            return
        with open(PID_FILE, "r", encoding="utf-8") as f:
            pids = {line.strip() for line in f if line.strip()}
        pids.discard(str(pid))
        with open(PID_FILE, "w", encoding="utf-8") as f:
            for p in pids:
                f.write(f"{p}\n")
    except Exception:
        pass


def kill_all_processes() -> None:
    """全ての実行中 AI プロセスを強制終了する"""
    with state.process_lock:
        for proc in state.active_processes:
            try:
                proc.kill()
                log.info(f"AIプロセス強制終了: pid={proc.pid}")
            except Exception:
                pass
        state.active_processes.clear()


def kill_orphan_processes() -> int:
    """前回のポーラーが残した孤児プロセスを検知して kill する"""
    killed = 0
    if not os.path.exists(PID_FILE):
        return 0
    try:
        with open(PID_FILE, "r", encoding="utf-8") as f:
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
        os.remove(PID_FILE)
    except Exception as e:
        log.warning(f"残留プロセス処理エラー: {e}")
    return killed


def cleanup() -> None:
    """子プロセスの kill + PID ファイル削除"""
    kill_all_processes()
    if os.path.exists(PID_FILE):
        try:
            os.remove(PID_FILE)
        except Exception:
            pass
