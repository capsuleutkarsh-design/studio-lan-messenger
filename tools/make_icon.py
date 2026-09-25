"""Render assets/app_icon.svg into assets/app.ico (16-256 px, PNG-compressed entries).

    .venv\\Scripts\\python tools\\make_icon.py [preview.png]
"""

import os
import struct
import sys

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QRectF, Qt
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter
from PySide6.QtSvg import QSvgRenderer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)


def render(renderer, size):
    img = QImage(size, size, QImage.Format_ARGB32)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    renderer.render(p, QRectF(0, 0, size, size))
    p.end()
    return img


def png_bytes(img):
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QIODevice.WriteOnly)
    img.save(buf, "PNG")
    return bytes(ba)


def main():
    QGuiApplication(sys.argv)
    renderer = QSvgRenderer(os.path.join(ROOT, "assets", "app_icon.svg"))
    small = QSvgRenderer(os.path.join(ROOT, "assets", "app_icon_small.svg"))
    images = {s: render(small if s <= 24 else renderer, s) for s in SIZES}

    entries = [png_bytes(images[s]) for s in SIZES]
    header = struct.pack("<HHH", 0, 1, len(SIZES))
    offset = 6 + 16 * len(SIZES)
    table = b""
    for s, data in zip(SIZES, entries):
        dim = 0 if s >= 256 else s
        table += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(data), offset)
        offset += len(data)
    with open(os.path.join(ROOT, "assets", "app.ico"), "wb") as f:
        f.write(header + table + b"".join(entries))
    print("wrote assets/app.ico")

    if len(sys.argv) > 1:          # preview sheet on light and dark backgrounds
        w = sum(SIZES) + 20 * (len(SIZES) + 1)
        sheet = QImage(w, 2 * 276, QImage.Format_ARGB32)
        p = QPainter(sheet)
        for row, bg in enumerate(("#f3f3f3", "#1c1c1c")):
            p.fillRect(0, row * 276, w, 276, QColor(bg))
            x = 20
            for s in SIZES:
                p.drawImage(x, row * 276 + 138 - s // 2, images[s])
                x += s + 20
        p.end()
        sheet.save(sys.argv[1])


if __name__ == "__main__":
    main()
