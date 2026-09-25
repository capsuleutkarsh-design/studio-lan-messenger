"""Sending whole folders (e.g. image sequences) and unpacking received zips.

Folders are packed into a zip in a background thread (no compression: EXR/DPX/MOV are already
compressed, and "store" is as fast as copying), sent like any file, then the temp zip is removed.
"""

import os
import shutil
import tempfile
import threading
import time
import zipfile

from PySide6.QtCore import QObject, Signal


def folder_size(path):
    total, count = 0, 0
    for root, _, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
                count += 1
            except OSError:
                pass
    return total, count


def temp_dir():
    d = os.path.join(tempfile.gettempdir(), "LANMessenger")
    os.makedirs(d, exist_ok=True)
    return d


def clean_temp(max_age=86400):
    """Remove packed-folder zips left behind by a crash or a failed upload (called at start-up)."""
    d = os.path.join(tempfile.gettempdir(), "LANMessenger")
    try:
        names = os.listdir(d)
    except OSError:
        return
    for n in names:
        p = os.path.join(d, n)
        try:
            if os.path.isfile(p) and time.time() - os.path.getmtime(p) > max_age:
                os.remove(p)
        except OSError:
            pass


class PackJob(QObject):
    """Looks like an upload Transfer so the chat's upload strip can show its progress."""
    progress = Signal(object)
    finished = Signal(object)

    kind = "upload"
    hidden = True

    def __init__(self, folder, conv):
        super().__init__()
        self.folder = os.path.normpath(folder)
        self.conv = conv
        base = os.path.basename(self.folder) or "folder"
        self.name = f"{base}.zip (packing...)"
        self.size, self.files = folder_size(self.folder)
        self.done = 0
        self.speed = 0.0
        self.state = "running"
        self.error = ""
        self.zip_path = os.path.join(temp_dir(), f"{base}_{int(time.time())}.zip")
        self._cancel = False
        self._last = (time.time(), 0)

    @property
    def active(self):
        return self.state == "running"

    def start(self):
        threading.Thread(target=self._run, daemon=True).start()

    def cancel(self):
        self._cancel = True

    def _run(self):
        try:
            with zipfile.ZipFile(self.zip_path, "w", zipfile.ZIP_STORED, allowZip64=True) as z:
                parent = os.path.dirname(self.folder)
                for root, _, files in os.walk(self.folder):
                    for f in sorted(files):
                        if self._cancel:
                            raise InterruptedError("cancelled")
                        full = os.path.join(root, f)
                        z.write(full, os.path.relpath(full, parent))
                        self.done += os.path.getsize(full)
                        now = time.time()
                        if now - self._last[0] > 0.5:
                            self.speed = (self.done - self._last[1]) / (now - self._last[0])
                            self._last = (now, self.done)
                            self.progress.emit(self)
            self.state = "done"
        except InterruptedError:
            self.state = "cancelled"
        except OSError as e:
            self.state = "failed"
            self.error = str(e)
        if self.state != "done":
            try:
                os.remove(self.zip_path)
            except OSError:
                pass
        self.finished.emit(self)


def safe_extract(zip_path, dest_parent):
    """Unpack into a new folder next to the zip. Refuses entries that try to escape it."""
    base = os.path.splitext(os.path.basename(zip_path))[0]
    dest = os.path.join(dest_parent, base)
    n = 1
    while os.path.exists(dest):
        dest = os.path.join(dest_parent, f"{base} ({n})")
        n += 1
    root = os.path.realpath(dest)
    with zipfile.ZipFile(zip_path) as z:
        for info in z.infolist():
            target = os.path.realpath(os.path.join(dest, info.filename))
            if not (target == root or target.startswith(root + os.sep)):
                raise ValueError(f"Unsafe path inside the zip: {info.filename}")
        os.makedirs(dest, exist_ok=True)
        z.extractall(dest)
    return dest


class Extractor(QObject):
    done = Signal(str, str)        # zip path, result folder ('' on error)
    failed = Signal(str, str)      # zip path, error

    def extract(self, zip_path):
        def run():
            try:
                self.done.emit(zip_path, safe_extract(zip_path, os.path.dirname(zip_path)))
            except (OSError, ValueError, zipfile.BadZipFile) as e:
                self.failed.emit(zip_path, str(e))
        threading.Thread(target=run, daemon=True).start()


def free_space(path):
    try:
        return shutil.disk_usage(path).free
    except OSError:
        return None
