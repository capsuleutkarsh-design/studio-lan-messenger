"""Build the Quillo icons from the master logo (assets/brand/quillo.webp: the Q mark over the word).

    .venv\\Scripts\\python tools\\make_icon.py

Writes (needs Pillow + numpy - development only, not bundled):
    assets/app.ico                  app / installer / tray icon, 16-256 px: the mark on a soft white tile
    assets/logo.png                 the same tile, 512 px, for the logo in the apps
    assets/brand/quillo_mark.png    the Q mark alone on transparency
    assets/brand/quillo_word.png    the word "Quillo" alone on transparency
"""

import os

import numpy as np
from PIL import Image, ImageDraw

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BRAND = os.path.join(ROOT, "assets", "brand")
SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)


def cut_out(page, top, bottom, pad=6):
    """The part of the white page between rows top..bottom, with the white turned into transparency."""
    a = np.asarray(page).astype(float)[:, : page.width - 30]          # skip the scan line at the right edge
    ink = 255 - a.min(axis=2)
    ys, xs = np.nonzero(ink[top:bottom] > 40)
    x0, x1 = xs.min() - pad, xs.max() + 1 + pad
    y0, y1 = ys.min() + top - pad, ys.max() + 1 + top + pad
    c = a[y0:y1, x0:x1]
    alpha = np.clip((255 - c.min(axis=2) - 6) / 110.0, 0, 1)         # page texture -> 0, solid ink -> 1
    colour = np.clip((c - (1 - alpha[..., None]) * 255) / np.maximum(alpha, 1e-3)[..., None], 0, 255)
    return Image.fromarray(np.dstack([colour, alpha * 255]).astype(np.uint8), "RGBA")


def tile(mark, size=1024, fill=0.70):
    """The mark centred on a rounded white tile with a faint cool tint and a hairline edge."""
    big = size * 4
    mask = Image.new("L", (big, big), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, big - 1, big - 1), int(big * 0.225), fill=255)
    edge = Image.new("L", (big, big), 0)
    ImageDraw.Draw(edge).rounded_rectangle((0, 0, big - 1, big - 1), int(big * 0.225), outline=255,
                                           width=max(4, big // 256))
    mask, edge = mask.resize((size, size), Image.LANCZOS), edge.resize((size, size), Image.LANCZOS)
    face = Image.composite(Image.new("RGBA", (size, size), (232, 238, 250, 255)),
                           Image.new("RGBA", (size, size), (255, 255, 255, 255)),
                           Image.linear_gradient("L").resize((size, size)).point(lambda v: int(v * 0.9)))
    face = Image.composite(Image.new("RGBA", (size, size), (214, 220, 234, 255)), face, edge)
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.paste(face, (0, 0), mask)
    w = int(size * fill)
    m = mark.resize((w, int(mark.height * w / mark.width)), Image.LANCZOS)
    out.alpha_composite(m, ((size - w) // 2 + size // 100, (size - m.height) // 2))
    return out


def main():
    page = Image.open(os.path.join(BRAND, "quillo.webp")).convert("RGB")
    ink_rows = np.nonzero((255 - np.asarray(page)[:, : page.width - 30].min(axis=2) > 40).any(axis=1))[0]
    gap = np.argmax(np.diff(ink_rows[ink_rows > page.height * 0.3]) > 40)   # the space between Q and the word
    rows = ink_rows[ink_rows > page.height * 0.3]
    mark = cut_out(page, rows[0], rows[gap] + 1)
    word = cut_out(page, rows[gap + 1], rows[-1] + 1)
    mark.save(os.path.join(BRAND, "quillo_mark.png"))
    word.save(os.path.join(BRAND, "quillo_word.png"))
    t = tile(mark)
    t.resize((512, 512), Image.LANCZOS).save(os.path.join(ROOT, "assets", "logo.png"))
    t.save(os.path.join(ROOT, "assets", "app.ico"), sizes=[(s, s) for s in SIZES])
    print("mark", mark.size, "word", word.size, "-> app.ico, logo.png")


if __name__ == "__main__":
    main()
