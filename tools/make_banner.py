"""Render docs/images/banner.png (the README header) from the app icon.

    .venv\\Scripts\\python tools\\make_banner.py
"""

import os
import sys

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QGuiApplication, QImage, QLinearGradient, QPainter, QPen, QRadialGradient

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
W, H = 1600, 520


def glow(p, x, y, r, color, alpha):
    g = QRadialGradient(QPointF(x, y), r)
    c = QColor(color)
    c.setAlpha(alpha)
    g.setColorAt(0, c)
    c.setAlpha(0)
    g.setColorAt(1, c)
    p.setBrush(g)
    p.setPen(Qt.NoPen)
    p.drawEllipse(QPointF(x, y), r, r)


def main():
    QGuiApplication(sys.argv)
    img = QImage(W, H, QImage.Format_ARGB32)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    p.setRenderHint(QPainter.TextAntialiasing)

    bg = QLinearGradient(0, 0, W, H)
    bg.setColorAt(0, QColor("#132456"))
    bg.setColorAt(0.55, QColor("#0c1638"))
    bg.setColorAt(1, QColor("#070c22"))
    p.setBrush(bg)
    p.setPen(Qt.NoPen)
    p.drawRoundedRect(QRectF(0, 0, W, H), 36, 36)
    glow(p, 250, 120, 420, "#3f6fd8", 110)
    glow(p, 520, 470, 360, "#3cc8b4", 60)
    glow(p, 1450, 80, 420, "#3cc8b4", 60)
    glow(p, 1250, 520, 380, "#3f6fd8", 60)
    p.setPen(QPen(QColor(255, 255, 255, 26), 2))
    p.setBrush(Qt.NoBrush)
    p.drawRoundedRect(QRectF(1, 1, W - 2, H - 2), 36, 36)

    icon = QImage(os.path.join(ROOT, "assets", "logo.png"))
    p.setRenderHint(QPainter.SmoothPixmapTransform)
    p.drawImage(QRectF(120, 130, 260, 260), icon)

    x = 440
    f = QFont("Segoe UI")
    f.setPixelSize(92)
    f.setWeight(QFont.ExtraBold)
    p.setFont(f)
    p.setPen(QColor("#ffffff"))
    p.drawText(QRectF(x, 118, 1100, 120), Qt.AlignLeft | Qt.AlignVCenter, "Quillo")
    f = QFont("Segoe UI")
    f.setPixelSize(34)
    p.setFont(f)
    p.setPen(QColor(230, 232, 245, 225))
    p.drawText(QRectF(x + 4, 238, 1100, 50), Qt.AlignLeft | Qt.AlignVCenter,
               "Chat, files and screen sharing for your studio.")
    p.setPen(QColor(200, 205, 230, 170))
    p.drawText(QRectF(x + 4, 284, 1100, 50), Qt.AlignLeft | Qt.AlignVCenter,
               "Runs entirely on your own network — nothing leaves the building.")

    f = QFont("Segoe UI")
    f.setPixelSize(22)
    f.setWeight(QFont.DemiBold)
    p.setFont(f)
    px = x + 4
    for text, color in (("Encrypted", "#5b8def"), ("20 GB files", "#3cc8b4"), ("Stickers & polls", "#8fa8ff"),
                        ("Org chart", "#5b8def"), ("Windows service", "#3cc8b4")):
        w = p.fontMetrics().horizontalAdvance(text) + 36
        c = QColor(color)
        c.setAlpha(40)
        p.setBrush(c)
        c.setAlpha(150)
        p.setPen(QPen(c, 1.5))
        p.drawRoundedRect(QRectF(px, 368, w, 44), 22, 22)
        p.setPen(QColor("#ffffff"))
        p.drawText(QRectF(px, 368, w, 44), Qt.AlignCenter, text)
        px += w + 12
    p.end()

    out = os.path.join(ROOT, "docs", "images")
    os.makedirs(out, exist_ok=True)
    img.save(os.path.join(out, "banner.png"))
    print("wrote docs/images/banner.png")


if __name__ == "__main__":
    main()
