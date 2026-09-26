"""Quillo client entry point.

    python -m client.main              # normal start
    python -m client.main --minimized  # start in the tray (used for Windows startup)
"""

import argparse
import getpass
import os
import sys

if __package__ in (None, ""):          # allow "python client/main.py" and the frozen exe
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import QObject, Qt, QTimer  # noqa: E402
from PySide6.QtGui import QIcon  # noqa: E402
from PySide6.QtNetwork import QLocalServer, QLocalSocket  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from common import theme  # noqa: E402
from common.icons import asset  # noqa: E402
from client.config import ClientConfig  # noqa: E402
from client.network import Connection  # noqa: E402
from client.store import Store  # noqa: E402
from client.transfers import TransferManager  # noqa: E402
from client.ui.login import LoginWindow  # noqa: E402
from client.ui.main_window import MainWindow  # noqa: E402

INSTANCE_KEY = f"LANMessengerClient-{getpass.getuser()}"


class App(QObject):
    def __init__(self, minimized=False):
        super().__init__()
        self.config = ClientConfig()
        self.conn = Connection(self)
        self.store = Store(self.conn)
        self.transfers = TransferManager(self.conn, self.config, self)
        self.main = None
        self.minimized = minimized
        self.pending_remember = False

        self.login = LoginWindow(self.config)
        self.login.login_requested.connect(self.do_login)
        self.conn.logged_in.connect(self.on_logged_in)
        self.conn.login_failed.connect(self.on_login_failed)
        self.conn.kicked.connect(self.on_kicked)
        self.conn.tls = bool(self.config.get("tls", True))
        self.conn.pins = self.config["pins"]
        self.conn.pins_changed.connect(self.config.save)
        self.conn.identity_changed.connect(self.on_identity_changed)

        password = self.config.get_password() if self.config["remember"] else ""
        if password and self.config["server_host"] and self.config["username"]:
            self.login.password.setText(password)
            if not minimized:
                self.login.show()
            self.login.set_busy(True, "Signing in...")
            QTimer.singleShot(300, lambda: self.do_login(self.config["server_host"], self.config["server_port"],
                                                         self.config["username"], password, True))
        else:
            self.login.show()

    def show_front(self):
        """Another launch of the client asked us to come to the front."""
        if self.main and self.conn.online:
            self.main.show_normal()
        else:
            self.login.show()
            self.login.raise_()
            self.login.activateWindow()

    def do_login(self, host, port, username, password, remember):
        self.config["server_host"] = host
        self.config["server_port"] = port
        self.config["username"] = username
        self.config["remember"] = remember
        self.pending_remember = remember
        self.config.save()
        self.conn.login(host, port, username, password, status="online")

    def on_logged_in(self, boot):
        first = self.main is None
        if first:
            self.main = MainWindow(self.conn, self.store, self.transfers, self.config)
            self.main.logout_requested.connect(self.logout)
        self.store.load(boot)
        if first:
            # the window connected to logged_in during this very signal, so Qt won't call it this time
            self.main._on_logged_in(boot)
        if self.pending_remember:
            self.config.set_password(self.conn.password)
            self.config.save()
            self.pending_remember = False
        if self.login.isVisible() or first:
            self.login.hide()
            self.login.set_busy(False)
            if not self.minimized or not first:
                self.main.show_normal()
        self.minimized = False

    def on_login_failed(self, error):
        if self.main and self.main.isVisible():
            self.main.hide()
        if "Invalid username or password" in error:
            self.config.set_password(None)
            self.config.save()
            self.login.password.clear()
        self.login.show()
        self.login.raise_()
        self.login.set_error(error)

    def on_identity_changed(self, server_key, old_fp, new_fp):
        """The server presents a different certificate than last time: possible impostor."""
        if self.main:
            self.main.hide()
        self.login.show()
        self.login.raise_()
        box = QMessageBox(self.login)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle("Security warning")
        box.setText(f"The server at {server_key} is not the one this PC talked to before.")
        box.setInformativeText(
            "This is expected ONLY if your administrator reinstalled or replaced the messenger server.\n"
            "Otherwise another computer may be pretending to be the server — do not sign in, "
            "and tell your administrator.\n\n"
            f"Remembered: {old_fp[:47]}...\nNow:        {new_fp[:47]}...")
        trust = box.addButton("The admin replaced the server — trust it", QMessageBox.AcceptRole)
        box.addButton("Don't connect", QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() is trust:
            self.conn.trust(server_key, new_fp)
            self.login.set_busy(True)
            # we are still inside the socket's own "encrypted" signal: reconnect once it has returned,
            # otherwise the new handshake on the same socket never finishes
            QTimer.singleShot(0, lambda: self.conn.login(self.conn.host, self.conn.port, self.conn.username,
                                                         self.conn.password, self.conn.status))
        else:
            self.login.set_error("Not connected: the server's identity could not be confirmed.")

    def on_kicked(self, reason):
        if self.main:
            self.main.hide()
        self.login.show()
        self.login.set_error(reason)

    def logout(self):
        self.conn.logout()
        self.config["remember"] = False
        self.config.set_password(None)
        self.config.save()
        for t in list(self.transfers.transfers):
            t.cancel()
        if self.main:
            self.main.hide()
            self.main.signed_out()
        self.login.password.clear()
        self.login.remember.setChecked(False)
        self.login.set_busy(False)
        self.login.set_info("Signed out.")
        self.login.show()


def setup_logging():
    """Errors go to %LOCALAPPDATA%\\LANMessenger\\client.log (small, rotating). Returns its path, or ''."""
    import logging
    import logging.handlers
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or os.path.expanduser("~")
    path = os.path.join(base, "LANMessenger", "client.log")
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        fh = logging.handlers.RotatingFileHandler(path, maxBytes=1_000_000, backupCount=2, encoding="utf-8")
    except OSError:
        return ""
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.WARNING)
    root.addHandler(fh)
    return path


def main():
    ap = argparse.ArgumentParser(description="Quillo client")
    ap.add_argument("--minimized", action="store_true", help="start in the system tray")
    args, _ = ap.parse_known_args()

    log_file = setup_logging()
    from common import crash
    crash.install("Quillo", log_file)

    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    app.setApplicationName("Quillo")
    app.setWindowIcon(QIcon(asset("app.ico")))
    app.setQuitOnLastWindowClosed(False)
    cfg = ClientConfig()
    from client.folders import clean_temp
    clean_temp()
    theme.apply(cfg["theme"], cfg["accent"], festivals=cfg["festival_themes"])
    app.setStyleSheet(theme.STYLESHEET)

    # single instance per Windows user: a second launch just brings the first one forward
    probe = QLocalSocket()
    probe.connectToServer(INSTANCE_KEY)
    if probe.waitForConnected(300):
        if not args.minimized:           # a second autostart entry must not pop the window open
            probe.write(b"show")
            probe.waitForBytesWritten(300)
        return 0
    from common.version import CLIENT_MUTEX, hold_mutex
    _mutex = hold_mutex(CLIENT_MUTEX)       # lets the installer see that the client is running  # noqa: F841
    _gmutex = hold_mutex("Global\\" + CLIENT_MUTEX)  # noqa: F841
    QLocalServer.removeServer(INSTANCE_KEY)
    server = QLocalServer()
    server.listen(INSTANCE_KEY)
    app.instance_server = server

    controller = App(minimized=args.minimized)
    app.aboutToQuit.connect(controller.conn.logout)
    if sys.platform == "win32":
        # lets the installer close us during an update and start us again afterwards
        try:
            import ctypes
            ctypes.windll.kernel32.RegisterApplicationRestart(None, 0)
        except (AttributeError, OSError):
            pass

    def session_ending(_manager):          # Windows logoff/shutdown or installer asking us to close
        if controller.main:
            controller.main.quitting = True
        app.quit()
    app.commitDataRequest.connect(session_ending)
    server.newConnection.connect(lambda: (server.nextPendingConnection(), controller.show_front()))

    # quitting from the login window (never logged in)
    def login_closed(event):
        if not (controller.main and controller.conn.online):
            app.quit()
        event.accept()
    controller.login.closeEvent = login_closed
    return app.exec()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:  # noqa: BLE001 - show fatal errors instead of dying silently (pythonw/exe)
        import traceback
        from client.config import config_dir
        with open(os.path.join(config_dir(), "crash.log"), "a", encoding="utf-8") as f:
            traceback.print_exc(file=f)
        if QApplication.instance():
            QMessageBox.critical(None, "Quillo", f"Unexpected error:\n{e}")
        raise
