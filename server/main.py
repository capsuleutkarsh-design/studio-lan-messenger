"""Quillo server entry point.

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


def setup_logging(path, fallback=""):
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        fh = logging.handlers.RotatingFileHandler(path, maxBytes=5_000_000, backupCount=3, encoding="utf-8")
    except OSError:
        if not fallback:
            raise
        fh = logging.handlers.RotatingFileHandler(fallback, maxBytes=5_000_000, backupCount=3, encoding="utf-8")
        root.warning("Log folder not available (%s); logging to %s", os.path.dirname(path), fallback)
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


def startup_failed(e, data_dir, headless) -> int:
    """The settings could not even be read: write a note to %TEMP% and tell the admin."""
    import tempfile
    import traceback
    text = (f"Quillo Server could not start.\n\nData folder: {data_dir}\n{type(e).__name__}: {e}\n\n"
            + ("The data folder can only be changed by administrators. If the server runs as the background "
               "service, the console connects to it - make sure the service is running (Start menu > "
               "Quillo Server). Otherwise start the console as administrator."
               if isinstance(e, PermissionError) else "Check that the data folder exists and is not full."))
    try:
        with open(os.path.join(tempfile.gettempdir(), "LANMessengerServer-startup.log"), "a",
                  encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + text + "\n" + traceback.format_exc() + "\n")
    except OSError:
        pass
    if not headless:
        from PySide6.QtWidgets import QApplication, QMessageBox
        QApplication.instance() or QApplication(sys.argv)
        QMessageBox.critical(None, "Quillo Server", text)
    return 1


def configure(data_dir, args) -> int:
    """Called by the setup: store the chosen folders (empty = default inside the data folder)."""
    from server.config import ServerConfig
    cfg = ServerConfig(data_dir)
    values = {}
    for key, value in (("storage_dir", args.storage), ("backup_dir", args.backups), ("log_dir", args.logs)):
        if value is not None:
            value = value.strip().rstrip("\\/")
            default = os.path.join(data_dir, {"storage_dir": "files", "backup_dir": "backups", "log_dir": ""}[key])
            values[key] = "" if not value or os.path.normcase(os.path.abspath(value)) == \
                os.path.normcase(os.path.abspath(default).rstrip("\\/")) else value
    cfg.update(**values)
    return 0


def run_remote_console(host, port, note=""):
    """Console for a server that already runs (the Windows service, or on another PC)."""
    from PySide6.QtWidgets import QApplication
    from common import theme
    from server.admin_gui import ConsoleLoginDialog, ServerWindow
    app = QApplication(sys.argv)
    app.setApplicationName("Quillo Server console")
    app.setQuitOnLastWindowClosed(False)
    app.setStyleSheet(theme.STYLESHEET)
    from common import crash
    log_file = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), "LANMessenger", "console.log")
    try:
        os.makedirs(os.path.dirname(log_file), exist_ok=True)
        setup_logging(log_file)
    except OSError:
        log_file = ""
    crash.install("Quillo Server console", log_file)
    dlg = ConsoleLoginDialog(host, port, note)
    if not dlg.exec():
        return 0
    win = ServerWindow(dlg.api)  # noqa: F841 - keep the window alive
    return app.exec()


def main():
    ap = argparse.ArgumentParser(description="Quillo server")
    ap.add_argument("--data", default=default_data_dir(), help="data folder (database, files, config)")
    ap.add_argument("--headless", action="store_true", help="run without a window (Windows service mode)")
    ap.add_argument("--minimized", action="store_true", help="start hidden in the system tray")
    ap.add_argument("--console", nargs="?", const="127.0.0.1", metavar="HOST[:PORT]",
                    help="only open the console and connect to a running server (default: this PC)")
    ap.add_argument("--configure", action="store_true",
                    help="(used by the setup) save the folders below into the settings and exit")
    ap.add_argument("--storage", help="with --configure: folder for shared files")
    ap.add_argument("--backups", help="with --configure: folder for database backups and chat logs")
    ap.add_argument("--logs", help="with --configure: folder for server.log")
    args = ap.parse_args()
    data_dir = os.path.abspath(args.data)

    if args.configure:
        sys.exit(configure(data_dir, args))

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

    try:
        core = ServerCore(data_dir)
    except Exception as e:  # noqa: BLE001 - nothing is logged yet: say it here
        return startup_failed(e, data_dir, args.headless)
    setup_logging(core.config.log_path, os.path.join(data_dir, "server.log"))
    from common import crash
    crash.install("Quillo Server", core.config.log_path)

    if args.headless:
        core.start()
        signal.signal(signal.SIGINT, lambda *_: (core.stop(), sys.exit(0)))
        while True:
            time.sleep(1)

    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication, QMessageBox
    from common import theme
    from server.admin_gui import ServerWindow
    from server.console_api import LocalApi

    app = QApplication(sys.argv)
    app.setApplicationName("Quillo Server")
    app.setQuitOnLastWindowClosed(False)
    app.setStyleSheet(theme.STYLESHEET)
    win = ServerWindow(LocalApi(core), start_minimized=args.minimized)
    try:
        core.start()
    except Exception as e:  # noqa: BLE001
        from server.core import startup_error_text
        QMessageBox.critical(win, "Server could not start", startup_error_text(e, core.config))
    QTimer.singleShot(0, lambda: (win.update_state(), win.refresh_current()))
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
