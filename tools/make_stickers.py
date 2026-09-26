"""Cut sticker sheets (Freepik-style JPGs: many stickers on a plain background) into single stickers.

Run from the project folder (needs Pillow, numpy, scipy - development only, not bundled):

    .venv\\Scripts\\python tools\\make_stickers.py "C:\\...\\Sticker pack"

Add --only office,words to remake just those packs; the others are left untouched.

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
#   keep: sticker numbers (reading order) to keep, dropping repeats on busy sheets
#   box: (left, top, right, bottom) part of the sheet to use, as fractions - skips a frame line,
#        or takes one piece out of a single big illustration
#   small: smallest sticker side (fraction of the sheet) - lower it for sheets of little word labels
#   work: analysis size for big, busy sheets, so their small stickers still come out sharp
# A pack id listed twice gets the second sheet's stickers added after the first's.
PACKS = [
    ("vector-illustration-indian-themed-chat-stickers-set", "desi_chat", "Desi Chat", {}),
    ("cartoon-great-job-stickers-set", "great_job", "Great Job", {"group": 0.002}),
    ("cute-office-sticker-pack-with-funny-doodle-icons-vector-illustration-urgent-message-coffee-cup",
     "office", "Office Life", {"group": 0.0005, "work": 3000, "small": 0.035}),
    ("cartoon-illustration-man-cartoon-sticker-set", "desi_heroes", "Desi Heroes", {"grid": (3, 3)}),
    ("vector-cartoon-illustration-bald-man-sticker-set", "uncle_ji", "Uncle Ji", {"grid": (4, 4)}),
    ("vector-cartoon-illustration-haryanvi-couple-character-set", "haryanvi", "Haryanvi", {"grid": (4, 4)}),
    ("vector-cartoon-illustration-pirate-sticker-set", "pirate", "Pirate", {"grid": (3, 3)}),
    ("set-flat-style-emotion-expression-stickers", "moods", "Moods", {}),
    ("vintage-style-holi-sticker-collection-cosmic-latte-background", "holi", "Holi", {"thr": 14, "group": 0.003, "drop": [1]}),
    ("mangosteen", "diwali", "Diwali", {"group": 0.005}),
    ("hand-drawn-raksha-bandhan-labels-collection", "rakhi", "Rakhi & Janmashtami", {}),
    ("hand-drawn-janmashtami-illustration", "rakhi", "Rakhi & Janmashtami",          # little Krishna
     {"box": (0.1, 0.19, 0.92, 0.97), "group": 0.004}),
    ("hand-drawn-janmashtami-illustration", "rakhi", "Rakhi & Janmashtami",          # the title
     {"box": (0.34, 0.08, 0.66, 0.205), "group": 0.02, "small": 0.03}),
    ("labels-collection-diwali-hindu-festival-celebration", "diwali_labels", "Diwali Labels", {}),
    ("3d-cartoon-gradient-hindu-festival-icons-set", "festival", "Festival", {"group": 0.008}),
    ("india-independence-day-badge-collection", "independence", "Independence Day", {}),
    ("india-republic-day-celebration-labels-collection", "republic", "Republic Day", {}),
    ("india-icons-pack", "india", "India", {"group": 0.005}),
    ("indian-icons-set", "india_icons", "India Icons", {"group": 0.01}),
    ("collection-linear-style-thanksgiving-stickers", "food", "Food & Fun", {"group": 0.01}),
    ("man-holding-plate-food-with-name-vada-pav-it", "food", "Food & Fun", {"box": (0.02, 0.02, 0.98, 0.98), "group": 0.03}),
    ("collection-sticker-words-template-vector-flat-illustration-bundle-decoration-weekly-daily-planner-di",
     "words", "Words", {"group": 0.002, "small": 0.016, "work": 5000, "drop": [32, 36, 42, 44, 48, 59]}),
    ("diary-planner-flat-stickers-set", "planner", "Planner",                      # minus typos and repeats
     {"group": 0.002, "small": 0.02, "work": 4000, "drop": [14, 20, 28, 31, 33]}),
    ("collection-diary-stickers", "planner", "Planner",                            # only what the first sheet lacks
     {"group": 0.002, "small": 0.016, "work": 5000,
      "keep": [3, 4, 10, 12, 25, 32, 34, 38, 41, 42, 47, 48, 51, 52, 54, 55, 57, 67]}),
]


def load_sheet(zip_path, box=None):
    with zipfile.ZipFile(zip_path) as z:
        name = next(n for n in z.namelist() if n.lower().endswith(".jpg"))
        im = Image.open(io.BytesIO(z.read(name))).convert("RGB")
    if box:
        im = im.crop((round(im.width * box[0]), round(im.height * box[1]),
                      round(im.width * box[2]), round(im.height * box[3])))
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


def cut(sheet, thr=30, group=0.015, drop=(), grid=None, keep=None, small=0.07):
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
        if min(h, w) < small * WORK or (fg[sl] & (labels[sl] == k)).sum() < 0.4 * (small * WORK) ** 2:
            continue                                               # sparkles, "designed by freepik"
        found.append((sl, k))
    # reading order: rows (by centre, tolerance ~6% of the sheet), then left to right
    found.sort(key=lambda s: ((s[0][0].start + s[0][0].stop) // 2 // int(0.12 * WORK), s[0][1].start))
    stickers = []
    for i, (sl, k) in enumerate(found):
        if i in drop or (keep is not None and i not in keep):
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


def main(src, only=None, out=OUT):
    os.makedirs(out, exist_ok=True)
    made = {}                                      # pack id -> files written so far this run
    for zip_name, pack_id, title, opts in PACKS:
        if only and pack_id not in only:
            continue
        path = os.path.join(src, zip_name + ".zip")
        if not os.path.exists(path):
            print("missing", path)
            continue
        opts = dict(opts)
        global WORK
        WORK = opts.pop("work", 2000)
        stickers = cut(load_sheet(path, opts.pop("box", None)), **opts)
        folder = os.path.join(out, pack_id)
        if pack_id not in made:                    # first sheet of this pack: start it afresh
            os.makedirs(folder, exist_ok=True)
            for old in os.listdir(folder):
                os.remove(os.path.join(folder, old))
            made[pack_id] = []
        files = made[pack_id]
        for img in stickers:
            name = f"{len(files) + 1:02d}.webp"
            img.save(os.path.join(folder, name), "WEBP", quality=88, method=6)
            files.append(name)
        with open(os.path.join(folder, "pack.json"), "w", encoding="utf-8") as f:
            json.dump({"id": pack_id, "title": title, "stickers": files,
                       "credit": "Designed by Freepik"}, f, indent=1)
        print(f"{title:18s} {len(stickers):3d} stickers  ({zip_name[:40]})")
    index = []
    for _zip, pack_id, _title, _opts in PACKS:
        if pack_id not in index and os.path.exists(os.path.join(out, pack_id, "pack.json")):
            index.append(pack_id)
    with open(os.path.join(out, "index.json"), "w", encoding="utf-8") as f:
        json.dump(index, f, indent=1)


if __name__ == "__main__":
    args = sys.argv[1:]
    only = None
    if "--only" in args:
        i = args.index("--only")
        only = set(args[i + 1].split(","))
        del args[i:i + 2]
    main(args[0] if args else os.path.join(HERE, "..", "..", "..", "Sticker pack"), only)
