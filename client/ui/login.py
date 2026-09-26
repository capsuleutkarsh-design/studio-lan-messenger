"""Login window with automatic server discovery."""

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget,
)

from common import protocol as P
from common import theme as T
from common.icons import add_show_password, asset, icon, license_label, logo_widget, pixmap
from client.network import Discovery
from client.ui.widgets import IconButton, plain


class LoginWindow(QWidget):
    login_requested = Signal(str, int, str, str, bool)

    def __init__(self, config):
        super().__init__()
        self.config = config
        self.setWindowTitle("Quillo")
        self.setWindowIcon(QIcon(asset("app.ico")))
        self.setFixedSize(860, 580)
        self.setObjectName("root")

        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # left: brand panel
        hero = QFrame()
        hero.setObjectName("hero")
        hero.setFixedWidth(360)
        deep = T.mix(T.ACCENT, T.RAIL, 0.30)
        if T.FESTIVAL and T.FESTIVAL["art"] == "republic":    # navy parade sky
            stops = "stop:0 #1b3fa0, stop:1 #0a1540"
        elif T.FESTIVAL and T.FESTIVAL["art"] == "chakra":    # tricolour
            stops = "stop:0 #e8801f, stop:0.45 #c9a26a, stop:0.55 #3c7f4a, stop:1 #0c5a0a"
        elif T.FESTIVAL:
            stops = "stop:0 #b3262b, stop:1 #14512f"
        else:
            stops = f"stop:0 {T.ACCENT}, stop:1 {deep}"
        hero.setStyleSheet(f"#hero {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:1, {stops}); }}")
        ink = T.ACCENT_TEXT
        hl = QVBoxLayout(hero)
        hl.setContentsMargins(40, 44, 36, 36)
        hl.setSpacing(10)
        hl.addWidget(logo_widget(64))
        hl.addSpacing(10)
        name = QLabel("Quillo")
        name.setStyleSheet(f"color: {ink}; font-size: 22pt; font-weight: 800;")
        hl.addWidget(name)
        if T.FESTIVAL:
            fest = QLabel(f"{T.FESTIVAL['greeting']} {T.FESTIVAL['emoji']}".strip())
            fest.setStyleSheet("color: #ffffff; font-size: 13pt; font-weight: 800;")
            hl.addWidget(fest)
        tag = QLabel("Chat, files and screen sharing for the whole studio.")
        tag.setWordWrap(True)
        tag.setStyleSheet(f"color: {ink}; font-size: 11pt;")
        hl.addWidget(tag)
        hl.addSpacing(18)
        for ic, text in (("chat", "Direct chats, rooms and announcements"),
                         ("folder", "Send files and whole folders — any size"),
                         ("sticker", "Stickers, @mentions, replies and pins"),
                         ("key", "Encrypted — nothing leaves your network")):
            row = QHBoxLayout()
            row.setSpacing(10)
            i = QLabel()
            i.setPixmap(pixmap(ic, ink, 16))
            row.addWidget(i)
            t = QLabel(text)
            t.setStyleSheet(f"color: {ink}; font-size: 9.5pt;")
            row.addWidget(t, 1)
            hl.addLayout(row)
        hl.addStretch(1)
        from common.version import APP_VERSION
        ver = QLabel(f"Version {APP_VERSION}")
        ver.setStyleSheet(f"color: {ink}; font-size: 8pt;")
        hl.addWidget(ver)
        outer.addWidget(hero)

        # right: the form
        form = QWidget()
        lay = QVBoxLayout(form)
        lay.setContentsMargins(56, 50, 56, 30)
        lay.setSpacing(8)
        title = QLabel("Sign in")
        title.setStyleSheet("font-size: 20pt; font-weight: 800;")
        lay.addWidget(title)
        sub = QLabel("Use the account your admin created for you.")
        T.polish(sub, muted=True)
        lay.addWidget(sub)
        lay.addSpacing(18)

        # the server: normally just its name; the address field only after "Change" (or when none is found)
        self.server_line = QWidget()
        sl = QHBoxLayout(self.server_line)
        sl.setContentsMargins(2, 0, 0, 0)
        sl.setSpacing(8)
        ic = QLabel()
        ic.setPixmap(pixmap("server", T.MUTED, 16))
        sl.addWidget(ic)
        self.server_name = plain(QLabel("Looking for the server..."))
        self.server_name.setStyleSheet(f"color: {T.MUTED};")
        sl.addWidget(self.server_name, 1)
        change = QPushButton("Change")
        change.setCursor(Qt.PointingHandCursor)
        change.setStyleSheet(f"QPushButton {{ background: transparent; border: none; color: {T.ACCENT};"
                             f" font-weight: 600; padding: 2px 4px; }} QPushButton:hover {{ text-decoration: underline; }}")
        change.clicked.connect(lambda: self.show_server_box(True))
        sl.addWidget(change)
        lay.addWidget(self.server_line)
        self.server_box = QWidget()
        sbl = QVBoxLayout(self.server_box)
        sbl.setContentsMargins(0, 0, 0, 0)
        sbl.setSpacing(8)
        sbl.addWidget(self._caption("Server"))
        srow = QHBoxLayout()
        srow.setSpacing(8)
        self.server = QComboBox()
        self.server.setEditable(True)
        self.server.lineEdit().setPlaceholderText("searching the network...")
        self.server.setMinimumHeight(42)
        srow.addWidget(self.server, 1)
        self.b_find = IconButton("refresh", "Search the network for servers", 42, 18, round_=False)
        self.b_find.clicked.connect(self.discover)
        srow.addWidget(self.b_find)
        sbl.addLayout(srow)
        lay.addWidget(self.server_box)
        self.server_box.hide()
        lay.addSpacing(6)

        lay.addWidget(self._caption("Username"))
        self.username = QLineEdit(config["username"])
        self.username.setMinimumHeight(42)
        self.username.addAction(icon("user", T.FAINT, 16), QLineEdit.LeadingPosition)
        lay.addWidget(self.username)
        lay.addSpacing(6)
        lay.addWidget(self._caption("Password"))
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.Password)
        self.password.setMinimumHeight(42)
        self.password.addAction(icon("key", T.FAINT, 16), QLineEdit.LeadingPosition)
        add_show_password(self.password)
        lay.addWidget(self.password)
        lay.addSpacing(4)
        self.remember = QCheckBox("Keep me signed in")
        self.remember.setChecked(config["remember"])
        lay.addWidget(self.remember)
        lay.addSpacing(12)
        self.b_login = QPushButton("Sign in")
        self.b_login.setMinimumHeight(46)
        self.b_login.setCursor(Qt.PointingHandCursor)
        self.b_login.setStyleSheet("font-size: 10.5pt;")
        T.polish(self.b_login, primary=True)
        self.b_login.clicked.connect(self.submit)
        lay.addWidget(self.b_login)
        self.status = plain(QLabel())
        self.status.setAlignment(Qt.AlignCenter)
        self.status.setWordWrap(True)
        self.status.setMinimumHeight(36)
        lay.addWidget(self.status)
        lay.addStretch(1)
        help_ = QLabel("Forgot your password? Ask IT or HR to reset it.")
        help_.setAlignment(Qt.AlignCenter)
        help_.setStyleSheet(f"color: {T.FAINT}; font-size: 8.5pt;")
        lay.addWidget(help_)
        lay.addWidget(license_label(align=Qt.AlignCenter))
        outer.addWidget(form, 1)

        for w in (self.username, self.password):
            w.returnPressed.connect(self.submit)
        self.server.lineEdit().returnPressed.connect(self.submit)

        self.known_name = config.get("server_name", "") or ""
        if config["server_host"]:
            self.server.setEditText(self._fmt(config["server_host"], config["server_port"]))
            self.server_name.setText(f"Server: {self.known_name or 'the one used last time'}")
            self.server_name.setStyleSheet(f"color: {T.TEXT};")
        self.discovery = Discovery(self)
        self.discovery.found.connect(self._found)
        self.discovery.finished.connect(self._discovery_done)
        QTimer.singleShot(100, self.discover)

    @staticmethod
    def _caption(text):
        lbl = QLabel(text)
        lbl.setStyleSheet(f"color: {T.MUTED}; font-size: 8.5pt; font-weight: 700; padding-left: 2px;")
        return lbl

    @staticmethod
    def _fmt(host, port):
        return host if int(port) == P.TCP_PORT else f"{host}:{port}"

    def showEvent(self, e):
        super().showEvent(e)
        T.dark_title_bar(self)
        if not self.username.text():
            self.username.setFocus()
        else:
            self.password.setFocus()

    # ----------------------------------------------------------- discovery
    def discover(self):
        self.b_find.setEnabled(False)
        if not self.server.currentText():
            self.server.lineEdit().setPlaceholderText("searching the network...")
        self.discovery.start()

    def show_server_box(self, show):
        self.server_box.setVisible(show)
        self.server_line.setVisible(not show)
        if show:
            self.server.setFocus()

    def _found(self, host, port, name):
        text = self._fmt(host, port)
        if self.server.findText(text) < 0:
            self.server.addItem(text)
            self.server.setItemData(self.server.count() - 1, name, Qt.ToolTipRole)
        if not self.server.currentText().strip():
            self.server.setEditText(text)
        if self.server.currentText().strip() == text:
            self.server_name.setText(f"Server: {name}")
            self.server_name.setStyleSheet(f"color: {T.TEXT};")

    def _discovery_done(self):
        self.b_find.setEnabled(True)
        self.server.lineEdit().setPlaceholderText("server name or IP address")
        if not self.server.currentText().strip():
            self.show_server_box(True)
            self.set_error("No server found automatically. Type the server's IP address.")

    # --------------------------------------------------------------- login
    def parse_server(self):
        text = self.server.currentText().strip()
        host, _, port = text.rpartition(":") if text.count(":") == 1 else (text, "", "")
        if not host:
            host, port = text, ""
        try:
            port = int(port) if port else P.TCP_PORT
        except ValueError:
            port = P.TCP_PORT
        return host, port

    def submit(self):
        host, port = self.parse_server()
        if not host:
            self.show_server_box(True)
            self.set_error("Enter the server address.")
            return
        if not self.username.text().strip() or not self.password.text():
            self.set_error("Enter your username and password.")
            return
        self.set_busy(True)
        self.login_requested.emit(host, port, self.username.text().strip(), self.password.text(),
                                  self.remember.isChecked())

    def set_busy(self, busy, text="Connecting..."):
        self.b_login.setEnabled(not busy)
        self.b_login.setText(text if busy else "Sign in")
        if busy:
            self.set_info("")

    def set_error(self, text):
        self.set_busy(False)
        if "reach server" in text:              # the saved or found address did not answer: show it
            self.show_server_box(True)
        self.status.setStyleSheet(f"color: {T.DANGER};")
        self.status.setText(text)

    def set_info(self, text):
        self.status.setStyleSheet(f"color: {T.MUTED};")
        self.status.setText(text)
