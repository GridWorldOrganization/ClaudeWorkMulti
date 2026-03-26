"""
ChatWork Webhook SQS Poller - エントリポイント

実装は poller/ パッケージに分割されています。
このファイルは start_poller.bat から呼び出される薄いラッパーです。
"""

import logging
import os
import signal
import sys
from datetime import datetime
from logging.handlers import BaseRotatingHandler

# ログ設定（パッケージ読み込み前に設定）
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(SCRIPT_DIR, "logs")
os.makedirs(LOG_DIR, exist_ok=True)


class DailyCsvHandler(BaseRotatingHandler):
    """日付が変わると自動で新ファイルに切り替わるCSVログハンドラ"""

    def __init__(self, log_dir: str):
        self.log_dir = log_dir
        self._current_date = ""
        path = self._get_path()
        super().__init__(path, mode="a", encoding="utf-8")
        self._ensure_header()

    def _get_path(self) -> str:
        self._current_date = datetime.now().strftime("%Y-%m-%d")
        return os.path.join(self.log_dir, f"poll_{self._current_date}.csv")

    def _ensure_header(self) -> None:
        if self.stream.tell() == 0:
            self.stream.write("timestamp,level,message\n")
            self.stream.flush()

    def shouldRollover(self, record) -> int:
        return 1 if datetime.now().strftime("%Y-%m-%d") != self._current_date else 0

    def doRollover(self) -> None:
        self.stream.close()
        self.baseFilename = self._get_path()
        self.stream = self._open()
        self._ensure_header()


class CsvFormatter(logging.Formatter):
    """CSV形式でログを出力するフォーマッター"""
    def format(self, record):
        ts = self.formatTime(record, "%Y-%m-%d %H:%M:%S")
        msg = record.getMessage().replace('"', '""')
        return f'{ts},{record.levelname},"{msg}"'


_csv_handler = DailyCsvHandler(LOG_DIR)
_csv_handler.setFormatter(CsvFormatter())

_console_handler = logging.StreamHandler()
_console_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))

logging.basicConfig(
    level=logging.INFO,
    handlers=[_csv_handler, _console_handler],
)

from poller.main import main, signal_handler
from poller.ai_runner import cleanup

if __name__ == "__main__":
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # Windows: コンソールウィンドウの×ボタン / ログオフ / シャットダウン時のクリーンアップ
    if os.name == "nt":
        try:
            import ctypes
            _CTRL_CLOSE_EVENT = 2
            _CTRL_LOGOFF_EVENT = 5
            _CTRL_SHUTDOWN_EVENT = 6

            @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_ulong)
            def _console_handler(event):
                if event in (_CTRL_CLOSE_EVENT, _CTRL_LOGOFF_EVENT, _CTRL_SHUTDOWN_EVENT):
                    cleanup()
                    return True
                return False

            ctypes.windll.kernel32.SetConsoleCtrlHandler(_console_handler, True)
        except Exception:
            pass

    # atexit: 正常終了時のフォールバック
    import atexit
    atexit.register(cleanup)

    main()
