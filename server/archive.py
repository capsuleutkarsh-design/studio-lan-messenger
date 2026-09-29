"""Readable chat backups and message retention.

Every night the server appends new messages to plain text files: a folder per room and per pair of
people, one file per month:

    <chat backup>/Rooms/Falcon Comp/2026-09.txt
    <chat backup>/People/Alice Mathew + Bob Fernandes/2026-09.txt
    <chat backup>/People/Alice Mathew - My space/2026-09.txt

The chat backup lives in the central folder (see server/safecopy.py) when there is one, so it stays with
the studio when the server PC goes, and that folder is locked to administrators. A room keeps its folder
when it is renamed (folders.json remembers which folder is whose).

Messages older than ``message_retention_days`` (0 = never, the default) are then removed from the
database - but only once they are in those files, so nothing is lost.
"""

import json
import logging
import os
import re
import time

log = logging.getLogger("server")

# The Rooms / People layout starts its own count, so a server upgraded from 1.8 writes everything still in
# its database once into the new folders (the old month folders are copied to "Before 1.9.0").
META_LAST_ID = "chat_backup_last_id"
META_OLD_LAST_ID = "chat_log_last_id"
META_MIGRATED = "chat_backup_migrated"
INDEX = "folders.json"
META_LAST_RUN = "chat_log_last_run"
META_PREVIOUS_RUN = "chat_log_previous_run"
_BAD = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def log_dir(config) -> str:
    """The chat backup folder: set in Settings, else in the central folder, else next to the backups."""
    from server import safecopy
    if config.values.get("chat_log_dir"):
        return config.values["chat_log_dir"]
    central = safecopy.folder(config)
    if central:
        return os.path.join(central, "Chat backup")
    return os.path.join(config.backup_dir, "Chat backup")


def old_log_dir(config) -> str:
    """Where 1.8 and earlier wrote the month folders."""
    return config.values.get("chat_log_dir") or os.path.join(config.backup_dir, "Chat logs")


_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


def _safe(name: str) -> str:
    name = _BAD.sub("_", name).strip(" .")[:80] or "chat"
    if name.split(".")[0].strip().upper() in _RESERVED:
        name += "_"                  # Windows keeps these names for devices: "NUL" can't be a folder
    return name


class _Names:
    """User / room names, looked up once per export."""

    def __init__(self, db):
        self.db = db
        self.users, self.rooms = {}, {}

    def user(self, uid):
        if uid not in self.users:
            row = self.db.get_user(uid) if uid else None
            self.users[uid] = (row["display_name"] or row["username"]) if row else "Administrator" if uid == 0 \
                else f"user {uid}"
        return self.users[uid]

    def room(self, rid):
        if rid not in self.rooms:
            row = self.db.get_room(rid)
            self.rooms[rid] = row["name"] if row else f"room {rid}"
        return self.rooms[rid]

    def title(self, conv):
        """'Room · Falcon Comp' / 'Alice Mathew + Bob Fernandes' - the heading of a backup file."""
        if conv.startswith("r:"):
            return f"Room · {self.room(int(conv[2:]))}"
        return " + ".join(self.pair(conv))

    def pair(self, conv):
        _, a, b = conv.split(":")
        if a == b:
            return [f"{self.user(int(a))} - My space"]
        return sorted((self.user(int(a)), self.user(int(b))), key=str.lower)

    def folder(self, conv):
        """Rooms/<name> or People/<A + B> (not yet made unique - see _Index)."""
        if conv.startswith("r:"):
            return "Rooms", _safe(self.room(int(conv[2:])))
        return "People", _safe(" + ".join(self.pair(conv)))


class _Index:
    """Which folder belongs to which chat, kept in folders.json: a renamed room keeps its folder, and two
    rooms with the same name get two folders ('Comp' and 'Comp (room 12)')."""

    def __init__(self, root):
        self.path = os.path.join(root, INDEX)
        try:
            with open(self.path, encoding="utf-8") as f:
                self.by_conv = json.load(f)
        except (OSError, ValueError):
            self.by_conv = {}
        self.changed = False

    def folder(self, root, names, conv):
        if conv not in self.by_conv:
            group, name = names.folder(conv)
            taken = {v.lower() for v in self.by_conv.values()}
            rel = f"{group}/{name}"
            if rel.lower() in taken:
                ref = f"room {conv[2:]}" if conv.startswith("r:") else "users " + "-".join(conv.split(":")[1:])
                rel = f"{group}/{name} ({ref})"
            self.by_conv[conv] = rel
            self.changed = True
        return os.path.join(root, *self.by_conv[conv].split("/"))

    def save(self):
        if not self.changed:
            return
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path + ".tmp", "w", encoding="utf-8") as f:
            json.dump(self.by_conv, f, indent=1, ensure_ascii=False, sort_keys=True)
        os.replace(self.path + ".tmp", self.path)
        self.changed = False


def format_message(m, names, db) -> str:
    """One message as text, e.g. '2026-09-25 14:03  Bob Fernandes: FAL_020 is up'."""
    when = time.strftime("%Y-%m-%d %H:%M", time.localtime(m["created_at"]))
    who = names.user(m["sender_id"])
    if m["kind"] == "system":
        return f"{when}  -- {m['body']} --"
    if m["deleted"]:
        return f"{when}  {who}: [message deleted]"
    parts = []
    if m["forwarded"]:
        parts.append("[forwarded]")
    if "thread_root" in m.keys() and m["thread_root"]:
        root = db.get_message(m["thread_root"])
        if root:
            snippet = (root["body"] or "").replace("\n", " ")[:50]
            parts.append(f"[in the thread of {names.user(root['sender_id'])}: {snippet}]")
    if m["reply_to"]:
        q = db.get_message(m["reply_to"])
        if q:
            snippet = (q["body"] or "").replace("\n", " ")[:60]
            parts.append(f"[reply to {names.user(q['sender_id'])}: {snippet}]")
    kind = m["kind"]
    if kind == "sticker":
        parts.append(f"[sticker {m['body']}]")
    elif kind == "buzz":
        parts.append("[BUZZ]")
    elif kind == "poll":
        poll = db.poll_for_message(m["id"])
        options = json.loads(poll["options"]) if poll else []
        counts = [0] * len(options)
        for option, _uid in (db.poll_votes(poll["id"]) if poll else []):
            if 0 <= option < len(counts):
                counts[option] += 1
        parts.append(f"[poll] {m['body']}  —  " + ", ".join(f"{o} ({c})" for o, c in zip(options, counts)))
    else:
        if m["file_id"]:
            size = m["file_size"] or 0
            parts.append(f"[file: {m['file_name']} ({_size(size)})]")
        if m["body"]:
            body = m["body"].replace("\r\n", "\n")
            parts.append(body.replace("\n", "\n" + " " * 20))
    if m["edited_at"]:
        parts.append("(edited)")
    return f"{when}  {who}: " + " ".join(parts)


def _size(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n} B"


def _write(root, names, by_file, touched, index=None):
    """Append lines to the month file of each chat. by_file: {(month, conv): [lines]}"""
    index = index or _Index(root)
    for (month, conv), lines in by_file.items():
        folder = index.folder(root, names, conv)
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, month + ".txt")
        new = not os.path.exists(path)
        with open(path, "a", encoding="utf-8", newline="\n") as f:
            if new:
                f.write(f"Quillo chat backup  ·  {names.title(conv)}  ·  {month}\n{'=' * 72}\n")
            f.write("\n".join(lines) + "\n")
        touched.add(path)
    index.save()


def _copy_old_logs(db, config, root):
    """Once: 1.8 wrote <backups>/Chat logs/<month>/<chat>.txt. Keep a copy of it inside the new chat backup
    ('Before 1.9.0'), because messages older than the old 90-day limit exist only there."""
    import shutil
    if db.get_meta(META_MIGRATED, ""):
        return
    old = old_log_dir(config)
    if os.path.isdir(old) and os.path.normcase(os.path.abspath(old)) != os.path.normcase(os.path.abspath(root)):
        target = os.path.join(root, "Before 1.9.0")
        if not os.path.exists(target):
            shutil.copytree(old, target)
            log.info("Chat backup: the old chat logs were copied to %s", target)
    db.set_meta(META_MIGRATED, "1")


def export_chat_logs(db, config, batches=None) -> dict:
    """Append every message not exported yet. Returns {'messages', 'files', 'folder', 'more'}.

    batches: stop after that many 5000-message batches (a huge first export must not hold up the server)."""
    root = log_dir(config)
    os.makedirs(root, exist_ok=True)
    _copy_old_logs(db, config, root)
    last = int(db.get_meta(META_LAST_ID, 0))
    names = _Names(db)
    index = _Index(root)
    total, touched, done, more = 0, set(), 0, False
    while True:
        if batches is not None and done >= batches:
            more = bool(db.messages_after(last, 1))
            break
        rows = db.messages_after(last, 5000)
        if not rows:
            break
        by_file = {}
        for m in rows:
            month = time.strftime("%Y-%m", time.localtime(m["created_at"]))
            by_file.setdefault((month, m["conv"]), []).append(format_message(m, names, db))
        _write(root, names, by_file, touched, index)
        last = rows[-1]["id"]
        db.set_meta(META_LAST_ID, last)        # progress is saved per batch: a crash never duplicates much
        total += len(rows)
        done += 1
    if not more:
        mark_run(db)
    if total:
        log.info("Chat backup: %s new message(s) written to %s file(s) in %s", total, len(touched), root)
    return {"messages": total, "files": len(touched), "folder": root, "more": more}


def mark_run(db):
    """Today's run is over (also after a failure: it is tried again tomorrow, or with 'Back up now')."""
    db.set_meta(META_PREVIOUS_RUN, db.get_meta(META_LAST_RUN, 0) or 0)
    db.set_meta(META_LAST_RUN, time.time())


def apply_retention(db, config) -> int:
    """Remove messages older than the retention period that are already in the chat logs."""
    days = float(config.values.get("message_retention_days") or 0)
    if days <= 0:
        return 0
    exported = int(db.get_meta(META_LAST_ID, 0))
    if not exported:
        return 0
    # the clock jumped forward (or the server was off for weeks): skip one night rather than remove
    # messages that only look old. (The previous run's time is saved before this is called.)
    previous = float(db.get_meta(META_PREVIOUS_RUN, 0) or 0)
    if previous and time.time() - previous > 30 * 86400:
        log.warning("Message retention skipped tonight: the last run was %s days ago (clock changed?)",
                    int((time.time() - previous) / 86400))
        return 0
    cutoff = time.time() - days * 86400
    # messages that changed after they went into the log (edited, deleted, poll votes since then):
    # write their final state next to them first, so nothing is lost when they leave the database
    changed = db.changed_before_removal(cutoff, exported)
    if changed:
        names, by_file = _Names(db), {}
        stamp = time.strftime("%Y-%m-%d")
        for m in changed:
            month = time.strftime("%Y-%m", time.localtime(m["created_at"]))
            by_file.setdefault((month, m["conv"]), []).append(
                f"[final version, saved {stamp}]  " + format_message(m, names, db))
        _write(log_dir(config), names, by_file, set())
    removed = db.delete_old_messages(cutoff, exported)
    if removed:
        log.info("Moved %s message(s) older than %s days out of the live database (kept in the chat logs)",
                 removed, int(days))
    return removed


def due(db, config) -> bool:
    """Once a day, at the backup hour (or later that day)."""
    if not config.values.get("chat_log_enabled", True):
        return False
    if time.localtime().tm_hour < int(config["backup_hour"]):
        return False
    last = float(db.get_meta(META_LAST_RUN, 0) or 0)
    return time.strftime("%Y-%m-%d", time.localtime(last)) != time.strftime("%Y-%m-%d")
