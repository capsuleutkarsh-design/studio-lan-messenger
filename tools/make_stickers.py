"""Cut sticker sheets (Freepik-style JPGs: many stickers on a plain background) into single stickers.

Run from the project folder (needs Pillow, numpy, scipy - development only, not bundled):

    .venv\\Scripts\\python tools\\make_stickers.py "C:\\...\\Sticker pack"

For every pack listed in PACKS it opens the zip, finds each sticker on the sheet, crops it,
gives it a white die-cut outline and writes assets/stickers/<pack>/NN.webp plus pack.json.
"""
import io
import json
import os
import sys
import zipfile

import numpy as np
from PIL import Image
from scipy import ndimage

Image.MAX_IMAGE_PIXELS = None

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "..", "assets", "stickers")
WORK = 2000          # sheets are scaled to this size before analysis
SIZE = 256           # longest side of a finished sticker
BORDER = 9           # white outline, in working pixels

# zip name -> (pack id, display name, options)
#   thr: colour distance from the background that counts as "sticker"
#   group: how close (fraction of sheet) two parts must be to belong to the same sticker
#   drop: sticker numbers (reading order) to leave out, e.g. a title card
#   grid: (rows, cols) for tightly packed sheets - every cell is one sticker
PACKS = [
    ("vector-illustration-indian-themed-chat-stickers-set", "desi_chat", "Desi Chat", {}),
    ("cartoon-illustration-man-cartoon-sticker-set", "desi_heroes", "Desi Heroes", {"grid": (3, 3)}),
    ("vector-cartoon-illustration-bald-man-sticker-set", "uncle_ji", "Uncle Ji", {"grid": (4, 4)}),
    ("vector-cartoon-illustration-haryanvi-couple-character-set", "haryanvi", "Haryanvi", {"grid": (4, 4)}),
    ("vector-cartoon-illustration-pirate-sticker-set", "pirate", "Pirate", {"grid": (3, 3)}),
    ("set-flat-style-emotion-expression-stickers", "moods", "Moods", {}),
    ("vintage-style-holi-sticker-collection-cosmic-latte-background", "holi", "Holi", {"thr": 14, "group": 0.003, "drop": [1]}),
    ("mangosteen", "diwali", "Diwali", {"group": 0.005}),
    ("labels-collection-diwali-hindu-festival-celebration", "diwali_labels", "Diwali Labels", {}),
    ("3d-cartoon-gradient-hindu-festival-icons-set", "festival", "Festival", {"group": 0.008}),
    ("india-independence-day-badge-collection", "independence", "Independence Day", {}),
    ("india-republic-day-celebration-labels-collection", "republic", "Republic Day", {}),
    ("india-icons-pack", "india", "India", {"group": 0.005}),
    ("indian-icons-set", "india_icons", "India Icons", {"group": 0.01}),
    ("collection-linear-style-thanksgiving-stickers", "food", "Food & Fun", {"group": 0.01}),
]


def load_sheet(zip_path):
    with zipfile.ZipFile(zip_path) as z:
        name = next(n for n in z.namelist() if n.lower().endswith(".jpg"))
        im = Image.open(io.BytesIO(z.read(name))).convert("RGB")
    scale = WORK / max(im.size)
    return im.resize((round(im.width * scale), round(im.height * scale)), Image.LANCZOS)


def background(a):
    edge = np.concatenate([a[:6].reshape(-1, 3), a[-6:].reshape(-1, 3),
                           a[:, :6].reshape(-1, 3), a[:, -6:].reshape(-1, 3)])
    return np.median(edge, axis=0)


def grid_labels(fg, rows, cols):
    """Label every foreground pixel with its grid cell (1..rows*cols)."""
    ys, xs = np.nonzero(fg)
    y0, y1, x0, x1 = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
    r = np.clip((np.arange(fg.shape[0]) - y0) * rows // (y1 - y0), 0, rows - 1)
    c = np.clip((np.arange(fg.shape[1]) - x0) * cols // (x1 - x0), 0, cols - 1)
    cells = r[:, None] * cols + c[None, :] + 1
    # whole parts (a word, a face) go to the cell their centre is in, so nothing is sliced at a cell line
    parts, n = ndimage.label(ndimage.distance_transform_edt(~fg) <= 0.003 * WORK)
    centres = ndimage.center_of_mass(fg, parts, range(1, n + 1))
    lut = np.zeros(n + 1, dtype=np.int32)
    for i, (cy, cx) in enumerate(centres, 1):
        lut[i] = cells[int(cy), int(cx)]
    return np.where(fg, lut[parts], 0), rows * cols


def cut(sheet, thr=30, group=0.015, drop=(), grid=None):
    a = np.asarray(sheet).astype(np.int16)
    bg = background(a)
    fg = np.sqrt(((a - bg) ** 2).sum(axis=2)) > thr
    fg = ndimage.binary_opening(fg, iterations=1)                 # JPEG speckles
    if grid:
        labels, n = grid_labels(fg, *grid)
    else:
        near = ndimage.distance_transform_edt(~fg)
        labels, n = ndimage.label(near <= group * WORK)           # parts close together = one sticker
    boxes = ndimage.find_objects(labels)
    found = []
    for k, sl in enumerate(boxes, 1):
        h, w = sl[0].stop - sl[0].start, sl[1].stop - sl[1].start
        if min(h, w) < 0.07 * WORK or (fg[sl] & (labels[sl] == k)).sum() < 0.002 * WORK * WORK:
            continue                                               # sparkles, "designed by freepik"
        found.append((sl, k))
    # reading order: rows (by centre, tolerance ~6% of the sheet), then left to right
    found.sort(key=lambda s: ((s[0][0].start + s[0][0].stop) // 2 // int(0.12 * WORK), s[0][1].start))
    stickers = []
    for i, (sl, k) in enumerate(found):
        if i in drop:
            continue
        pad = BORDER + 4
        y0, y1 = max(sl[0].start - pad, 0), min(sl[0].stop + pad, a.shape[0])
        x0, x1 = max(sl[1].start - pad, 0), min(sl[1].stop + pad, a.shape[1])
        own = fg[y0:y1, x0:x1] & (labels[y0:y1, x0:x1] == k)
        solid = ndimage.binary_fill_holes(own)
        dist = ndimage.distance_transform_edt(~solid)
        alpha = np.clip((BORDER + 1 - dist) * 255, 0, 255).astype(np.uint8)   # soft edge
        rgb = np.asarray(sheet)[y0:y1, x0:x1].copy()
        rgb[~own] = 255                                            # outline + gaps become white
        img = Image.fromarray(np.dstack([rgb, alpha]), "RGBA")
        img.thumbnail((SIZE, SIZE), Image.LANCZOS)
        stickers.append(img)
    return stickers


def main(src):
    os.makedirs(OUT, exist_ok=True)
    index = []
    for zip_name, pack_id, title, opts in PACKS:
        path = os.path.join(src, zip_name + ".zip")
        if not os.path.exists(path):
            print("missing", path)
            continue
        stickers = cut(load_sheet(path), **opts)
        folder = os.path.join(OUT, pack_id)
        os.makedirs(folder, exist_ok=True)
        for old in os.listdir(folder):
            os.remove(os.path.join(folder, old))
        files = []
        for i, img in enumerate(stickers, 1):
            name = f"{i:02d}.webp"
            img.save(os.path.join(folder, name), "WEBP", quality=88, method=6)
            files.append(name)
        with open(os.path.join(folder, "pack.json"), "w", encoding="utf-8") as f:
            json.dump({"id": pack_id, "title": title, "stickers": files,
                       "credit": "Designed by Freepik"}, f, indent=1)
        index.append(pack_id)
        print(f"{title:18s} {len(files):3d} stickers")
    with open(os.path.join(OUT, "index.json"), "w", encoding="utf-8") as f:
        json.dump(index, f, indent=1)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "..", "..", "..", "Sticker pack"))
