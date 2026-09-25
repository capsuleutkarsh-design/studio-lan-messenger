"""Festival decorations: a coloured stripe beside the rail and a banner on the home page.

Only shown while a festival theme is active (theme.FESTIVAL - see common/theme.py).
"""

import math
import random

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen, QRadialGradient
from PySide6.QtWidgets import QSizePolicy, QWidget

from common import theme as T

CHAKRA_BLUE = "#1b3fa0"


def draw_chakra(p, center, r, color=CHAKRA_BLUE, alpha=255):
    """The 24-spoke Ashoka Chakra."""
    c = QColor(color)
    c.setAlpha(alpha)
    p.save()
    p.setRenderHint(QPainter.Antialiasing)
    p.setBrush(Qt.NoBrush)
    p.setPen(QPen(c, max(1.5, r * 0.07)))
    p.drawEllipse(center, r, r)
    p.setPen(QPen(c, max(1.0, r * 0.028)))
    for i in range(24):
        a = math.radians(i * 15)
        p.drawLine(QPointF(center.x() + math.cos(a) * r * 0.16, center.y() + math.sin(a) * r * 0.16),
                   QPointF(center.x() + math.cos(a) * r * 0.93, center.y() + math.sin(a) * r * 0.93))
    p.setPen(Qt.NoPen)
    p.setBrush(c)
    p.drawEllipse(center, r * 0.14, r * 0.14)
    for i in range(24):                                   # small dots between the spokes on the rim
        a = math.radians(i * 15 + 7.5)
        p.drawEllipse(QPointF(center.x() + math.cos(a) * r * 0.9, center.y() + math.sin(a) * r * 0.9),
                      r * 0.035, r * 0.035)
    p.restore()


class FestiveStripe(QWidget):
    """Thin vertical band in the festival colours (tricolour, candy cane) at the far left of the window."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(4)

    def paintEvent(self, _):
        f = T.FESTIVAL
        if not f:
            return
        p = QPainter(self)
        colors = f["stripe"]
        if f["art"] == "snow":                            # candy cane: repeating diagonal bands
            band = 14
            for i, y in enumerate(range(-band, self.height() + band, band)):
                p.fillRect(0, y, self.width(), band, QColor(colors[i % 2 * 1]))
            return
        h = self.height() / len(colors)
        for i, c in enumerate(colors):
            p.fillRect(QRectF(0, i * h, self.width(), h + 1), QColor(c))


class FestiveBanner(QWidget):
    """Greeting banner at the top of the home page (Ashoka Chakra, or falling snow at Christmas)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(128)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.flakes = []
        self.angle = 0.0
        self.timer = QTimer(self, interval=40, timeout=self._tick)
        f = T.FESTIVAL
        if f and f["art"] == "snow":
            rnd = random.Random(7)
            self.flakes = [[rnd.random(), rnd.random(), 1.2 + rnd.random() * 2.6, 0.2 + rnd.random() * 0.6]
                           for _ in range(70)]
        self.timer.start()

    def _tick(self):
        self.angle = (self.angle + 0.4) % 360
        for fl in self.flakes:
            fl[1] += 0.004 * fl[3] + 0.002
            fl[0] += math.sin((fl[1] + fl[2]) * 6) * 0.0006
            if fl[1] > 1.05:
                fl[1], fl[0] = -0.05, random.random()
        self.update()

    def paintEvent(self, _):
        f = T.FESTIVAL
        if not f:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        clip = QPainterPath()
        clip.addRoundedRect(r, 20, 20)
        p.setClipPath(clip)
        base = QLinearGradient(0, 0, self.width(), 0)
        if f["art"] == "chakra":
            base.setColorAt(0, QColor("#ff9933"))
            base.setColorAt(0.33, QColor("#ffb266"))
            base.setColorAt(0.5, QColor("#fbfbf7"))
            base.setColorAt(0.67, QColor("#7bc27a"))
            base.setColorAt(1, QColor("#138808"))
        elif f["art"] == "republic":
            base = QLinearGradient(0, 0, self.width(), self.height())
            base.setColorAt(0, QColor("#1b3fa0"))
            base.setColorAt(0.6, QColor("#10225e"))
            base.setColorAt(1, QColor("#0a1540"))
        else:
            base.setColorAt(0, QColor("#7a1418"))
            base.setColorAt(0.55, QColor("#14301f"))
            base.setColorAt(1, QColor("#0d2016"))
        p.fillRect(r, base)
        # soft dark veil on the left so the greeting always reads well
        veil = QLinearGradient(0, 0, self.width() * 0.55, 0)
        veil.setColorAt(0, QColor(0, 0, 0, 70 if f["art"] == "chakra" else 150))
        veil.setColorAt(1, QColor(0, 0, 0, 0))
        p.fillRect(r, veil)
        if f["art"] == "republic":
            w, h = self.width(), self.height()
            rnd = random.Random(26)
            for i in range(46):                              # parade sparkles
                x, y = rnd.random() * w * 0.95, rnd.random() * h * 0.7
                tw = 0.5 + 0.5 * math.sin(self.angle / 8 + i)
                c = QColor(("#ff9933", "#ffffff", "#34c759")[i % 3])
                c.setAlpha(int(70 + 150 * tw))
                p.setBrush(c)
                p.setPen(Qt.NoPen)
                p.drawEllipse(QPointF(x, y), 1.2 + tw * 1.4, 1.2 + tw * 1.4)
            for i, col in enumerate(("#ff9933", "#ffffff", "#138808")):   # flowing tricolour ribbons
                path = QPainterPath()
                y0 = h - 30 + i * 9
                path.moveTo(0, y0)
                for x in range(0, w + 20, 20):
                    path.lineTo(x, y0 + math.sin(x / 70 + self.angle / 25) * 6)
                pen = QPen(QColor(col), 7)
                pen.setCapStyle(Qt.RoundCap)
                p.setPen(pen)
                p.setBrush(Qt.NoBrush)
                p.drawPath(path)
            cx = w * 0.5 if w < 520 else w - 110
            glow = QRadialGradient(QPointF(cx, h / 2 - 8), 64)
            glow.setColorAt(0, QColor(255, 255, 255, 90))
            glow.setColorAt(1, QColor(255, 255, 255, 0))
            p.setBrush(glow)
            p.setPen(Qt.NoPen)
            p.drawEllipse(QPointF(cx, h / 2 - 8), 64, 64)
            p.save()
            p.translate(cx, h / 2 - 8)
            p.rotate(self.angle)
            draw_chakra(p, QPointF(0, 0), 40, "#ffffff")
            p.restore()
        elif f["art"] == "chakra":
            cx = self.width() * 0.5 if self.width() < 520 else self.width() - 110
            glow = QRadialGradient(QPointF(cx, self.height() / 2), 70)
            glow.setColorAt(0, QColor(255, 255, 255, 170))
            glow.setColorAt(1, QColor(255, 255, 255, 0))
            p.setBrush(glow)
            p.setPen(Qt.NoPen)
            p.drawEllipse(QPointF(cx, self.height() / 2), 70, 70)
            p.save()
            p.translate(cx, self.height() / 2)
            p.rotate(self.angle)
            draw_chakra(p, QPointF(0, 0), 44)
            p.restore()
        else:
            gold = QColor(f.get("gold", "#f2c14e"))
            for fl in self.flakes:
                c = QColor(255, 255, 255, int(120 + 100 * fl[3]))
                p.setBrush(c)
                p.setPen(Qt.NoPen)
                p.drawEllipse(QPointF(fl[0] * self.width(), fl[1] * self.height()), fl[2], fl[2])
            # a simple tree of stacked triangles with a gold star
            tx, base_y = self.width() - 100, self.height() - 14
            p.setPen(Qt.NoPen)
            for i, (w, h) in enumerate(((78, 40), (62, 34), (44, 30))):
                y = base_y - 22 - i * 24
                p.setBrush(QColor("#1f8a4c") if i % 2 == 0 else QColor("#2aa35c"))
                path = QPainterPath()
                path.moveTo(tx - w / 2, y)
                path.lineTo(tx + w / 2, y)
                path.lineTo(tx, y - h)
                path.closeSubpath()
                p.drawPath(path)
            p.setBrush(QColor("#6b3e1e"))
            p.drawRect(QRectF(tx - 7, base_y - 22, 14, 16))
            p.setBrush(gold)
            star = QPainterPath()
            sy = base_y - 22 - 2 * 24 - 30
            for i in range(10):
                rad = 10 if i % 2 == 0 else 4.2
                a = math.radians(-90 + i * 36)
                pt = QPointF(tx + math.cos(a) * rad, sy + math.sin(a) * rad)
                star.moveTo(pt) if i == 0 else star.lineTo(pt)
            star.closeSubpath()
            p.drawPath(star)
            for i, (dx, dy, col) in enumerate(((-18, -34, "#e5484d"), (14, -52, "#f2c14e"), (-8, -70, "#5bc0ff"),
                                              (22, -30, "#f2c14e"), (4, -88, "#e5484d"))):
                p.setBrush(QColor(col))
                p.drawEllipse(QPointF(tx + dx, base_y + dy), 3.2, 3.2)
        p.setClipping(False)
        # greeting
        font = QFont("Segoe UI")
        font.setPixelSize(30)
        font.setWeight(QFont.ExtraBold)
        p.setFont(font)
        p.setPen(QColor("#ffffff"))
        p.drawText(QRectF(28, 22, self.width() - 56, 44), Qt.AlignLeft | Qt.AlignVCenter,
                   f"{f['greeting']} {f['emoji']}".strip())
        font.setPixelSize(15)
        font.setWeight(QFont.DemiBold)
        p.setFont(font)
        p.setPen(QColor(255, 255, 255, 220))
        p.drawText(QRectF(30, 68, self.width() - 60, 26), Qt.AlignLeft | Qt.AlignVCenter, f["sub"])
