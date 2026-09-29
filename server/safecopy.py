"""A safe copy of the server's own data in the central folder, so a reinstall (or a new server PC) gets it back.

The live database stays on the server PC's own disk - SQLite over a network share is slow and can be damaged
when the connection drops mid-write. Every few minutes, when something changed, and when the server stops, a
consistent copy goes to <central folder>/Quillo server data:

    messenger.db    every account, chat, room, calendar item (a consistent snapshot, taken while running)
    config.json     the settings
    tls/            the server certificate - clients keep trusting the server after a restore
    avatars/        profile photos
    about.json      when, which version, how many people - written last, so it marks a complete copy

The setup finds this folder on a fresh install and offers to restore it (server/main.py --restore).
"""

import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import time

from common.files import replace_file

FOLDER_NAME = "Quillo server data"
LOCKABLE = (FOLDER_NAME, "Chat backup")          # the folders lock_folder may lock
INFO = "about.json"
FILES = ("messenger.db", "config.json")
FOLDERS = ("tls", "avatars")


def _inside(path, folder):
    path, folder = (os.path.normcase(os.path.abspath(p)).rstrip("\\/") for p in (path, folder))
    return path == folder or path.startswith(folder + os.sep)


inside = _inside


def is_network(path) -> bool:
    """A UNC path (two backslashes, a server, a share) or a mapped network drive."""
    path = os.path.abspath(path)
    if path.startswith("\\\\"):
        return True
    if sys.platform != "win32":
        return False
    import ctypes
    drive = os.path.splitdrive(path)[0]
    return bool(drive) and ctypes.windll.kernel32.GetDriveTypeW(drive + "\\") == 4     # DRIVE_REMOTE


def _my_sid():
    """The Windows account this server runs as (it must keep its own access to the folder)."""
    try:
        out = subprocess.run(["whoami", "/user", "/fo", "csv", "/nh"], capture_output=True, text=True,
                             timeout=20, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
        sid = out.strip().rsplit(",", 1)[-1].strip().strip('"')
        return sid if sid.startswith("S-1-") else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def lock_folder(path) -> dict:
    """Let only Administrators, SYSTEM (the Quillo service) and this server's own account open `path`.

    The central folder holds every chat, every account and the server's private key. Only a folder on this
    PC's own disks is locked: a network folder's permissions belong to the file server, and granting this
    PC's groups there could lock the server out of its own copy - the dashboard asks IT to do it instead."""
    result = {"folder": path, "ok": False, "network": False, "error": ""}
    if sys.platform != "win32":
        result["error"] = "only on Windows"
        return result
    if is_network(path):
        result["network"] = True
        return result
    if not os.path.isdir(path):
        result["missing"] = True
        return result
    # Only the folders Quillo makes and names itself. A folder an admin picked in an older version may be
    # shared with HR or other programs (or be a whole drive): its permissions are theirs to set.
    full = os.path.abspath(path)
    if os.path.basename(full.rstrip("\\/")) not in LOCKABLE or os.path.dirname(full) == full:
        result["error"] = "a folder you chose - set who may open it yourself"
        result["chosen"] = True
        return result
    grants = ["*S-1-5-18:(OI)(CI)F", "*S-1-5-32-544:(OI)(CI)F"]
    sid = _my_sid()
    if sid and sid != "S-1-5-18":
        grants.append(f"*{sid}:(OI)(CI)F")
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        # the folder gets its own list (no longer inherited from the share above it) ...
        done = subprocess.run(["icacls", path, "/inheritance:r", "/grant:r", *grants, "/C", "/Q"],
                              capture_output=True, text=True, timeout=120, creationflags=flags)
        if done.returncode == 0 and os.listdir(path):
            # ... and everything already inside takes it over (applying it to each file separately would
            # leave files with no permissions at all: the "this folder and below" flags mean nothing on a file)
            done = subprocess.run(["icacls", os.path.join(path, "*"), "/reset", "/T", "/C", "/Q"],
                                  capture_output=True, text=True, timeout=600, creationflags=flags)
        result["ok"] = done.returncode == 0
        result["error"] = "" if result["ok"] else (done.stderr or done.stdout).strip()[:300]
    except (OSError, subprocess.SubprocessError) as e:
        result["error"] = str(e)
    return result


def folder(cfg) -> str:
    """Where the copy goes: the folder set in Settings, else inside the shared-files folder when that is outside
    the data folder (a central path / another disk). '' = off."""
    if not cfg["safe_copy_enabled"]:
        return ""
    if cfg["safe_copy_dir"]:
        return cfg["safe_copy_dir"]
    if _inside(cfg.storage_dir, cfg.data_dir):
        return ""                     # the same disk as the data: a copy there would not survive losing it
    return os.path.join(cfg.storage_dir, FOLDER_NAME)


def _sync_folder(src, dst):
    """Copy new and changed files (size or time differ); remove ones that are gone (a removed photo)."""
    if not os.path.isdir(src):
        return
    os.makedirs(dst, exist_ok=True)
    names = set(os.listdir(src))
    for name in names:
        s, d = os.path.join(src, name), os.path.join(dst, name)
        if not os.path.isfile(s):
            continue
        st = os.stat(s)
        try:
            dt = os.stat(d)
            if dt.st_size == st.st_size and int(dt.st_mtime) == int(st.st_mtime):
                continue
        except OSError:
            pass
        shutil.copy2(s, d + ".tmp")
        replace_file(d + ".tmp", d)
    for name in set(os.listdir(dst)) - names:
        try:
            os.remove(os.path.join(dst, name))
        except OSError:
            pass


def write(db, cfg, target, info) -> dict:
    """The slow part (a worker thread): write the copy into `target`. Returns a status dict."""
    try:
        os.makedirs(target, exist_ok=True)
        about = os.path.join(target, INFO)
        try:                        # an unfinished copy must never look complete
            os.remove(about)
        except OSError:
            pass
        db_copy = os.path.join(target, "messenger.db")
        db.backup_to(db_copy + ".tmp")
        replace_file(db_copy + ".tmp", db_copy)
        shutil.copy2(cfg.path, os.path.join(target, "config.json.tmp"))
        replace_file(os.path.join(target, "config.json.tmp"), os.path.join(target, "config.json"))
        for name in FOLDERS:
            _sync_folder(os.path.join(cfg.data_dir, name), os.path.join(target, name))
        info = dict(info, time=time.time(), pc=socket.gethostname(), data_dir=cfg.data_dir,
                    size=os.path.getsize(db_copy))
        with open(about + ".tmp", "w", encoding="utf-8") as f:
            json.dump(info, f, indent=2, ensure_ascii=False)
        replace_file(about + ".tmp", about)
        return {"ok": True, "time": info["time"], "folder": target, "size": info["size"]}
    except (OSError, sqlite3.Error) as e:
        for name in ("messenger.db.tmp", "config.json.tmp", INFO + ".tmp"):
            try:
                os.remove(os.path.join(target, name))
            except OSError:
                pass
        return {"ok": False, "time": time.time(), "folder": target, "error": str(e)}


def read_info(source) -> dict | None:
    """about.json of a complete copy, or None."""
    try:
        with open(os.path.join(source, INFO), encoding="utf-8") as f:
            info = json.load(f)
        if isinstance(info, dict) and os.path.isfile(os.path.join(source, "messenger.db")):
            return info
    except (OSError, ValueError):
        pass
    return None


def restore(source, data_dir) -> dict:
    """Bring a safe copy back into an empty data folder. Never overwrites a live database."""
    info = read_info(source)
    if not info:
        raise ValueError(f"No complete Quillo safe copy in {source}")
    if os.path.exists(os.path.join(data_dir, "messenger.db")):
        raise ValueError(f"{data_dir} already holds a database - nothing was restored")
    check = sqlite3.connect(f"file:{os.path.join(source, 'messenger.db')}?mode=ro", uri=True)
    try:
        if check.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise ValueError("The safe copy's database is damaged - restore from a backup instead")
    finally:
        check.close()
    os.makedirs(data_dir, exist_ok=True)
    for name in FILES:
        src = os.path.join(source, name)
        if os.path.isfile(src):
            shutil.copy2(src, os.path.join(data_dir, name + ".tmp"))
            replace_file(os.path.join(data_dir, name + ".tmp"), os.path.join(data_dir, name))
    for name in FOLDERS:
        _sync_folder(os.path.join(source, name), os.path.join(data_dir, name))
    return info
