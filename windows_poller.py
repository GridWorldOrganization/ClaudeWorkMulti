"""
ChatWork Webhook SQS Poller - エントリポイント

実装は poller/ パッケージに分割されています。
このファイルは start_poller.bat から呼び出される薄いラッパーです。
"""

import logging
import os
import signal
import sys

# ログ設定（パッケージ読み込み前に設定）
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(os.path.join(SCRIPT_DIR, "webhook_poller.log"), encoding="utf-8"),
        logging.StreamHandler(),
    ],
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
