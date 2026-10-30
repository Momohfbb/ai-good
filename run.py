"""Start ULTRA AI GOD MODE: the brain, the dashboard and the Telegram bot.

    python run.py                 start and open the dashboard
    python run.py --no-browser    start in the background (autostart, server)
    python run.py --host 0.0.0.0  allow the phone on the same Wi-Fi to connect
"""
from __future__ import annotations

import argparse
import os
import sys
import threading
import time
import webbrowser
from pathlib import Path

if getattr(sys, "frozen", False):  # PyInstaller EXE: keep data next to the user, not in the temp folder
    os.environ.setdefault("ULTRA_DATA_DIR", str(Path(os.environ.get("LOCALAPPDATA", Path.home())) / "UltraAI"))

from ultra import __version__, config  # noqa: E402

if sys.stdout is None or sys.stderr is None:  # windowed EXE has no console: write output to a log file
    config.LOG_DIR.mkdir(parents=True, exist_ok=True)
    _log = open(config.LOG_DIR / "ultra.log", "a", encoding="utf-8", buffering=1)
    sys.stdout = sys.stdout or _log
    sys.stderr = sys.stderr or _log


def main() -> None:
    ap = argparse.ArgumentParser(description="ULTRA AI GOD MODE")
    ap.add_argument("--host")
    ap.add_argument("--port", type=int)
    ap.add_argument("--no-browser", action="store_true", help="run in the background without opening a window")
    ap.add_argument("--browser", action="store_true", help="open the dashboard in the web browser instead of the app window")
    args = ap.parse_args()

    config.ensure_dirs()
    from ultra.db import Database
    db = Database(config.DB_PATH)
    for k, v in config.DEFAULT_SETTINGS.items():
        if db.get_setting(k) is None:
            db.set_setting(k, v)
    host = args.host or db.get_setting("host", "127.0.0.1")
    port = args.port or int(db.get_setting("port", 8765))
    has_account = bool(db.one("SELECT id FROM users LIMIT 1"))
    db.close()

    from ultra.security import Access
    import ultra.db
    access = Access(ultra.db.Database(config.DB_PATH))
    url = f"http://127.0.0.1:{port}/"
    print(f"\n  ULTRA AI GOD MODE v{__version__}")
    print(f"  لوحة التحكم / Dashboard: {url}")
    print(f"  البيانات / Data: {config.DATA_DIR}")
    if not has_account:
        print(f"\n  مفتاح الإعداد الأول / Setup key:  {access.setup_key}")
        print(f"  (محفوظ أيضاً في {config.ADMIN_KEY_PATH})\n")
    access.db.close()

    import uvicorn
    from ultra.server import create_app
    server = uvicorn.Server(uvicorn.Config(create_app(config.DATA_DIR), host=host, port=port, log_level="warning"))

    if args.no_browser:
        server.run()
        return
    # Windows app: the dashboard in its own window (Edge WebView2); the brain keeps running
    # while the window is open. Falls back to the normal browser when no window toolkit exists.
    if getattr(sys, "frozen", False) and not args.browser:
        try:
            import webview
            t = threading.Thread(target=server.run, daemon=True)
            t.start()
            for _ in range(100):  # wait until the brain answers
                if server.started:
                    break
                time.sleep(0.1)
            webview.create_window("ULTRA AI GOD MODE", url, width=1300, height=850, min_size=(380, 600))
            webview.start(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) UltraAI-Windows/" + __version__,
                          private_mode=False)
            server.should_exit = True
            t.join(timeout=10)
            return
        except Exception as e:  # no WebView2 runtime or toolkit: use the browser
            print("app window unavailable, opening the browser:", e)
    threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    server.run()


if __name__ == "__main__":
    main()
