"""Screenshot: drag over any part of any screen (or click for the whole screen), then send, copy or cancel.

One transparent overlay per screen shows a dimmed picture of that screen taken just before; the selected area
is shown bright with its size. Esc or a right-click cancels.
"""

import os
import tempfile
import time

from PySide6.QtCore import QPoint, QRect, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QGuiApplication, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget,
)

from common import theme as T


class _Overlay(QWidget):
    """Covers one screen; emits the chosen area of that screen's picture."""
    picked = Signal(QImage)
    cancelled = Signal()

    def __init__(self, screen, shot: QPixmap):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setCursor(Qt.CrossCursor)
        self.setMouseTracking(True)
        self.shot = shot
        self.start = self.end = None
        self.setGeometry(screen.geometry())

    def selection(self):
        if self.start is None or self.end is None:
            return QRect()
        return QRect(min(self.start.x(), self.end.x()), min(self.start.y(), self.end.y()),
                     abs(self.end.x() - self.start.x()), abs(self.end.y() - self.start.y()))

    def paintEvent(self, _):
        p = QPainter(self)
        p.drawPixmap(self.rect(), self.shot)
        p.fillRect(self.rect(), QColor(0, 0, 0, 110))
        sel = self.selection()
        if sel.width() > 1 and sel.height() > 1:
            ratio = self.shot.devicePixelRatio()
            src = QRect(int(sel.x() * ratio), int(sel.y() * ratio), int(sel.width() * ratio), int(sel.height() * ratio))
            p.drawPixmap(sel, self.shot, src)
            p.setPen(QPen(QColor(T.ACCENT), 2))
            p.drawRect(sel.adjusted(0, 0, -1, -1))
            label = f"{src.width()} × {src.height()}"
            f = QFont("Segoe UI", 9)
            f.setBold(True)
            p.setFont(f)
            box = p.fontMetrics().boundingRect(label).adjusted(-8, -4, 8, 4)
            box.moveTopLeft(QPoint(sel.left(), max(0, sel.top() - box.height() - 6)))
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(0, 0, 0, 170))
            p.drawRoundedRect(box, 8, 8)
            p.setPen(QColor("#ffffff"))
            p.drawText(box, Qt.AlignCenter, label)
        else:
            hint = "Drag to choose an area   ·   click for the whole screen   ·   Esc to cancel"
            f = QFont("Segoe UI", 11)
            p.setFont(f)
            box = p.fontMetrics().boundingRect(hint).adjusted(-18, -10, 18, 10)
            box.moveCenter(QPoint(self.width() // 2, 60))
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(0, 0, 0, 170))
            p.drawRoundedRect(box, 14, 14)
            p.setPen(QColor("#ffffff"))
            p.drawText(box, Qt.AlignCenter, hint)
        p.end()

    def mousePressEvent(self, e):
        if e.button() == Qt.RightButton:
            self.cancelled.emit()
            return
        self.start = self.end = e.position().toPoint()
        self.update()

    def mouseMoveEvent(self, e):
        if self.start is not None:
            self.end = e.position().toPoint()
            self.update()

    def mouseReleaseEvent(self, e):
        if e.button() != Qt.LeftButton or self.start is None:
            return
        self.end = e.position().toPoint()
        sel = self.selection()
        ratio = self.shot.devicePixelRatio()
        if sel.width() < 6 or sel.height() < 6:            # a click: the whole screen
            self.picked.emit(self.shot.toImage())
        else:
            src = QRect(int(sel.x() * ratio), int(sel.y() * ratio), int(sel.width() * ratio), int(sel.height() * ratio))
            self.picked.emit(self.shot.copy(src).toImage())

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            self.cancelled.emit()


class Snipper(QWidget):
    """Runs one screenshot: hides the app window, shows the overlays, then brings the window back."""
    done = Signal(QImage)          # a null image when cancelled

    def __init__(self, window):
        super().__init__()
        self.window_ = window
        self.overlays = []
        self.was_visible = window.isVisible() and not window.isMinimized()

    def start(self):
        if self.was_visible:
            self.window_.hide()                       # out of the picture
        QTimer.singleShot(260 if self.was_visible else 30, self._open)

    def _open(self):
        for screen in QGuiApplication.screens():
            shot = screen.grabWindow(0)
            o = _Overlay(screen, shot)
            o.picked.connect(self._finish)
            o.cancelled.connect(lambda: self._finish(QImage()))
            self.overlays.append(o)
            o.show()
            o.raise_()
            o.activateWindow()
        under = QGuiApplication.screenAt(QPoint(*self._cursor()))
        for o in self.overlays:
            if under and o.geometry() == under.geometry():
                o.activateWindow()
                o.setFocus()

    @staticmethod
    def _cursor():
        from PySide6.QtGui import QCursor
        pos = QCursor.pos()
        return pos.x(), pos.y()

    def _finish(self, image):
        for o in self.overlays:
            o.close()
        self.overlays = []
        if self.was_visible:
            self.window_.show_normal() if hasattr(self.window_, "show_normal") else self.window_.show()
        self.done.emit(image)


class ScreenshotDialog(QDialog):
    """Preview of the screenshot with an optional caption: Send, Copy or Cancel."""

    def __init__(self, parent, image: QImage, where: str):
        super().__init__(parent)
        self.setWindowTitle("Screenshot")
        self.image = image
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 18, 20, 16)
        lay.setSpacing(10)
        head = QLabel(f"Send to {where}")
        head.setTextFormat(Qt.PlainText)
        head.setStyleSheet("font-weight: 700; font-size: 11pt;")
        lay.addWidget(head)
        preview = QLabel()
        preview.setAlignment(Qt.AlignCenter)
        preview.setStyleSheet(f"background: {T.BG}; border-radius: 12px; padding: 6px;")
        pm = QPixmap.fromImage(image)
        preview.setPixmap(pm.scaled(620, 400, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                          if pm.width() > 620 or pm.height() > 400 else pm)
        lay.addWidget(preview, 1)
        size = QLabel(f"{image.width()} × {image.height()} pixels")
        size.setStyleSheet(f"color: {T.MUTED}; font-size: 8.5pt;")
        lay.addWidget(size)
        self.caption = QLineEdit()
        self.caption.setPlaceholderText("Add a message (optional)")
        self.caption.setMinimumHeight(36)
        lay.addWidget(self.caption)
        row = QHBoxLayout()
        copy = QPushButton("Copy")
        copy.setToolTip("Copy to the clipboard (paste it anywhere)")
        copy.clicked.connect(self._copy)
        row.addWidget(copy)
        row.addStretch(1)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        send = QPushButton("Send")
        T.polish(send, primary=True)
        send.setDefault(True)
        send.clicked.connect(self.accept)
        row.addWidget(cancel)
        row.addWidget(send)
        lay.addLayout(row)
        self.caption.setFocus()

    def showEvent(self, e):
        super().showEvent(e)
        T.dark_title_bar(self)

    def _copy(self):
        QGuiApplication.clipboard().setImage(self.image)
        self.reject()

    def save(self):
        """The screenshot as a PNG file ready to send."""
        path = os.path.join(tempfile.gettempdir(), f"Screenshot {time.strftime('%Y-%m-%d %H.%M.%S')}.png")
        return path if self.image.save(path, "PNG") else None
