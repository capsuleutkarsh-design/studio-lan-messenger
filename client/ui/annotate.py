"""Draw on a picture before sending it: circle the soft edge, point an arrow at the flicker, write a short note -
the paint-over notes supervisors give every day."""

import math
import os
import tempfile
import time

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import (
    QButtonGroup, QDialog, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QPushButton, QSizePolicy, QToolButton,
    QVBoxLayout, QWidget,
)

from common import theme as T
from common.icons import icon

COLORS = ("#ff3b4f", "#ffcc33", "#3ddc84", "#4c9aff", "#ffffff")
TOOLS = (("pen", "pen", "Draw"), ("arrow", "arrow", "Arrow"), ("box", "rect", "Box"),
         ("circle", "circle", "Circle"), ("text", "type", "Text"))


class _Mark:
    def __init__(self, tool, color, width, points=None, text=""):
        self.tool, self.color, self.width = tool, color, width
        self.points = points or []
        self.text = text


def paint_marks(p, marks, scale=1.0):
    """Draws the marks; points are in picture pixels, scale turns them into widget pixels."""
    p.setRenderHint(QPainter.Antialiasing)
    for m in marks:
        pen = QPen(QColor(m.color), max(1.0, m.width * scale), Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        pts = [QPointF(x * scale, y * scale) for x, y in m.points]
        if not pts:
            continue
        if m.tool == "pen":
            path = QPainterPath(pts[0])
            for q in pts[1:]:
                path.lineTo(q)
            p.drawPath(path)
        elif m.tool in ("box", "circle") and len(pts) > 1:
            r = QRectF(pts[0], pts[-1]).normalized()
            p.drawRoundedRect(r, 6, 6) if m.tool == "box" else p.drawEllipse(r)
        elif m.tool == "arrow" and len(pts) > 1:
            a, b = pts[0], pts[-1]
            p.drawLine(a, b)
            ang = math.atan2(b.y() - a.y(), b.x() - a.x())
            head = max(12.0, m.width * scale * 4)
            wing = [QPointF(b.x() - head * math.cos(ang - s), b.y() - head * math.sin(ang - s)) for s in (0.45, -0.45)]
            p.setBrush(QColor(m.color))
            p.drawPolygon(QPolygonF([b, wing[0], wing[1]]))
        elif m.tool == "text" and m.text:
            f = QFont("Segoe UI", 1)
            f.setPixelSize(max(10, int(m.width * 6 * scale)))
            f.setBold(True)
            p.setFont(f)
            fm = p.fontMetrics()
            box = fm.boundingRect(m.text).adjusted(-6, -3, 6, 3)
            box.moveTopLeft(pts[0].toPoint())
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(0, 0, 0, 150))
            p.drawRoundedRect(QRectF(box), 6, 6)
            p.setPen(QColor(m.color))
            p.drawText(QRectF(box), Qt.AlignCenter, m.text)


class _DrawCanvas(QWidget):
    changed = Signal()

    def __init__(self, image: QImage):
        super().__init__()
        self.image = image
        self.marks = []
        self.tool, self.color, self.pen_width = "pen", COLORS[0], 4
        self.current = None
        self.setMinimumSize(320, 220)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setCursor(Qt.CrossCursor)

    def _geometry(self):
        """(scale, left, top) of the picture shown as large as fits."""
        s = min(self.width() / self.image.width(), self.height() / self.image.height(), 1.0)
        return s, (self.width() - self.image.width() * s) / 2, (self.height() - self.image.height() * s) / 2

    def _to_image(self, pos):
        s, x0, y0 = self._geometry()
        return ((pos.x() - x0) / s, (pos.y() - y0) / s)

    def sizeHint(self):
        return QSize(min(1100, self.image.width()), min(700, self.image.height()))

    def paintEvent(self, _):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(T.BG))
        s, x0, y0 = self._geometry()
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        p.drawImage(QRectF(x0, y0, self.image.width() * s, self.image.height() * s), self.image)
        p.translate(x0, y0)
        paint_marks(p, self.marks + ([self.current] if self.current else []), s)

    def mousePressEvent(self, e):
        if e.button() != Qt.LeftButton:
            return
        pt = self._to_image(e.position())
        if self.tool == "text":
            text, ok = QInputDialog.getText(self, "Note on the picture", "Text:")
            if ok and text.strip():
                self.marks.append(_Mark("text", self.color, self.pen_width, [pt], text.strip()[:120]))
                self.changed.emit()
                self.update()
            return
        self.current = _Mark(self.tool, self.color, self.pen_width, [pt])

    def mouseMoveEvent(self, e):
        if self.current:
            pt = self._to_image(e.position())
            if self.current.tool == "pen":
                self.current.points.append(pt)
            else:
                self.current.points[1:] = [pt]
            self.update()

    def mouseReleaseEvent(self, e):
        if self.current and len(self.current.points) > 1:
            self.marks.append(self.current)
            self.changed.emit()
        self.current = None
        self.update()

    def undo(self):
        if self.marks:
            self.marks.pop()
            self.changed.emit()
            self.update()

    def result(self) -> QImage:
        out = self.image.convertToFormat(QImage.Format_ARGB32)
        p = QPainter(out)
        paint_marks(p, self.marks, 1.0)
        p.end()
        return out


class AnnotateDialog(QDialog):
    """The picture, a row of tools and colours, a note, and Send."""

    def __init__(self, parent, image: QImage, where="", send_text="Send"):
        super().__init__(parent)
        self.setWindowTitle("Draw on the picture")
        self.resize(1100, 780)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(10)
        bar = QHBoxLayout()
        bar.setSpacing(4)
        self.canvas = _DrawCanvas(image)
        group = QButtonGroup(self)
        self.tool_buttons = {}
        for key, ic, tip in TOOLS:
            b = QToolButton()
            b.setIcon(icon(ic, T.TEXT, 18))
            b.setToolTip(tip)
            b.setCheckable(True)
            b.setFixedSize(38, 38)
            b.setStyleSheet(f"QToolButton {{ background: {T.SURFACE}; border: 1px solid {T.HAIR}; border-radius: 10px; }}"
                            f"QToolButton:checked {{ background: {T.ACCENT_SOFT}; border: 1px solid {T.ACCENT}; }}")
            b.clicked.connect(lambda _=False, k=key: self._tool(k))
            group.addButton(b)
            bar.addWidget(b)
            self.tool_buttons[key] = b
        bar.addSpacing(14)
        self.color_buttons = {}
        for c in COLORS:
            b = QPushButton()
            b.setFixedSize(26, 26)
            b.setCheckable(True)
            b.setToolTip("Colour")
            b.setStyleSheet(f"QPushButton {{ background: {c}; border-radius: 13px; border: 2px solid {T.PANEL};"
                            f" padding: 0; }} QPushButton:checked {{ border: 3px solid {T.TEXT}; }}")
            b.clicked.connect(lambda _=False, c=c: self._color(c))
            bar.addWidget(b)
            self.color_buttons[c] = b
        bar.addSpacing(14)
        for label, w in (("Thin", 2), ("Medium", 4), ("Thick", 8)):
            b = QPushButton(label)
            b.setCheckable(True)
            T.polish(b, chip=True)
            b.clicked.connect(lambda _=False, w=w: self._width(w))
            bar.addWidget(b)
            self.color_buttons[f"w{w}"] = b
        bar.addStretch(1)
        undo = QPushButton(" Undo")
        undo.setIcon(icon("undo", T.TEXT, 16))
        undo.setShortcut("Ctrl+Z")
        undo.clicked.connect(self.canvas.undo)
        bar.addWidget(undo)
        lay.addLayout(bar)
        lay.addWidget(self.canvas, 1)
        self.caption = QLineEdit()
        self.caption.setPlaceholderText("Add a message (optional)" + (f" to {where}" if where else ""))
        self.caption.setMinimumHeight(36)
        lay.addWidget(self.caption)
        row = QHBoxLayout()
        hint = QLabel("Drag on the picture to draw. Ctrl+Z undoes the last mark.")
        hint.setStyleSheet(f"color: {T.MUTED}; font-size: 8.5pt;")
        row.addWidget(hint, 1)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        send = QPushButton(send_text)
        T.polish(send, primary=True)
        send.clicked.connect(self.accept)
        row.addWidget(cancel)
        row.addWidget(send)
        lay.addLayout(row)
        self._tool("pen")
        self._color(COLORS[0])
        self._width(4)

    def showEvent(self, e):
        super().showEvent(e)
        T.dark_title_bar(self)

    def _tool(self, key):
        self.canvas.tool = key
        self.tool_buttons[key].setChecked(True)

    def _color(self, c):
        self.canvas.color = c
        for k, b in self.color_buttons.items():
            if not k.startswith("w"):
                b.setChecked(k == c)

    def _width(self, w):
        self.canvas.pen_width = w
        for k, b in self.color_buttons.items():
            if k.startswith("w"):
                b.setChecked(k == f"w{w}")

    def image(self) -> QImage:
        return self.canvas.result()

    def save(self, name="Notes"):
        """The drawn-on picture as a PNG ready to send."""
        safe = "".join(c for c in name if c.isalnum() or c in " -_.")[:60].strip() or "Notes"
        path = os.path.join(tempfile.gettempdir(), f"{safe} {time.strftime('%H.%M.%S')}.png")
        return path if self.image().save(path, "PNG") else None
