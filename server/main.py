"""LAN Messenger server entry point.

    python -m server.main               # console window (runs in the tray)
    python -m server.main --minimized   # start hidden in the tray (for Windows startup)
    python -m server.main --headless    # no GUI, e.g. as a service; stop with Ctrl+C
    python -m server.main --data D:\\msg # use another data folder
"""

import argparse
import logging
import logging.handlers
import os
import signal
import sys
import time

if __package__ in (None, ""):          # allow "python server/main.py" and the frozen exe
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.config import default_data_dir  # noqa: E402
from server.core import ServerCore  # noqa: E402


def setup_logging(path):
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    fh = logging.handlers.RotatingFileHandler(path, maxBytes=5_000_000, backupCount=3, encoding="utf-8")
    fh.setFormatter(fmt)
    root.addHandler(fh)
    if sys.stderr:
        sh = logging.StreamHandler()
        sh.setFormatter(fmt)
        root.addHandler(sh)


def configured_port(data_dir) -> int:
    """TCP port from the server's config.json, without creating or rewriting anything."""
    import json
    from common.protocol import TCP_PORT
    try:
        with open(os.path.join(data_dir, "config.json"), encoding="utf-8") as f:
            return int(json.load(f).get("tcp_port", TCP_PORT))
    except (OSError, ValueError, TypeError):
        return TCP_PORT


def run_remote_console(host, port, note=""):
    """Console for a server that already runs (the Windows service, or on another PC)."""
    from PySide6.QtWidgets import QApplication
    from common import theme
    from server.admin_gui import ConsoleLoginDialog, ServerWindow
    app = QApplication(sys.argv)
    app.setApplicationName("LAN Messenger Server console")
    app.setQuitOnLastWindowClosed(False)
    app.setStyleSheet(theme.STYLESHEET)
    dlg = ConsoleLoginDialog(host, port, note)
    if not dlg.exec():
        return 0
    ServerWindow(dlg.api)
    return app.exec()


def main():
    ap = argparse.ArgumentParser(description="LAN Messenger server")
    ap.add_argument("--data", default=default_data_dir(), help="data folder (database, files, config)")
    ap.add_argument("--headless", action="store_true", help="run without a window (Windows service mode)")
    ap.add_argument("--minimized", action="store_true", help="start hidden in the system tray")
    ap.add_argument("--console", nargs="?", const="127.0.0.1", metavar="HOST[:PORT]",
                    help="only open the console and connect to a running server (default: this PC)")
    args = ap.parse_args()
    data_dir = os.path.abspath(args.data)

    if args.console:
        host, _, port = args.console.partition(":")
        sys.exit(run_remote_console(host, int(port) if port else configured_port(data_dir)))

    from common.version import SERVER_MUTEX, hold_mutex
    # one server per PC, also across Windows sessions (Global\); the installer checks these names too
    _local = hold_mutex(SERVER_MUTEX)  # noqa: F841
    _global, already_running = hold_mutex("Global\\" + SERVER_MUTEX)
    if already_running:
        if args.minimized or args.headless:
            return
        # the server already runs (usually as the background service): open the console for it
        sys.exit(run_remote_console("127.0.0.1", configured_port(data_dir),
                                    "The server is running in the background on this PC. "
                                    "Sign in with an administrator account to manage it."))

    core = ServerCore(data_dir)
    setup_logging(core.config.log_path)

    if args.headless:
        core.start()
        signal.signal(signal.SIGINT, lambda *_: (core.stop(), sys.exit(0)))
        while True:
            time.sleep(1)

    from PySide6.QtWidgets import QApplication, QMessageBox
    from common import theme
    from server.admin_gui import ServerWindow
    from server.console_api import LocalApi

    app = QApplication(sys.argv)
    app.setApplicationName("LAN Messenger Server")
    app.setQuitOnLastWindowClosed(False)
    app.setStyleSheet(theme.STYLESHEET)
    win = ServerWindow(LocalApi(core), start_minimized=args.minimized)
    try:
        core.start()
    except Exception as e:  # noqa: BLE001
        from server.core import startup_error_text
        QMessageBox.critical(win, "Server could not start", startup_error_text(e, core.config))
    win.update_state()
    win.refresh_current()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
