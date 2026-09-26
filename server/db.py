"""SQLite storage for the messenger server.

All methods are synchronous and must be called from the server's event-loop
thread only (the admin GUI goes through ``ServerCore.call``).
"""

import hashlib
import json
import hmac
import os
import sqlite3
import time

PBKDF2_ROUNDS = 150_000

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE COLLATE NOCASE,
    display_name TEXT NOT NULL,
    pw_hash TEXT NOT NULL,
    pw_salt TEXT NOT NULL,
    department TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL DEFAULT '',
    is_admin INTEGER NOT NULL DEFAULT 0,
    can_broadcast INTEGER NOT NULL DEFAULT 0,
    disabled INTEGER NOT NULL DEFAULT 0,
    deleted INTEGER NOT NULL DEFAULT 0,
    status_msg TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL,
    last_seen REAL
);
CREATE TABLE IF NOT EXISTS rooms(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    topic TEXT NOT NULL DEFAULT '',
    owner_id INTEGER,
    created_at REAL NOT NULL,
    deleted INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS room_members(
    room_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    joined_at REAL NOT NULL,
    last_read INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY(room_id, user_id)
);
CREATE TABLE IF NOT EXISTS messages(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conv TEXT NOT NULL,
    sender_id INTEGER NOT NULL,
    recipient_id INTEGER,
    room_id INTEGER,
    kind TEXT NOT NULL DEFAULT 'text',
    body TEXT NOT NULL DEFAULT '',
    file_id TEXT,
    created_at REAL NOT NULL,
    delivered_at REAL,
    read_at REAL
);
CREATE INDEX IF NOT EXISTS idx_msg_conv ON messages(conv, id);
CREATE INDEX IF NOT EXISTS idx_msg_recipient ON messages(recipient_id, read_at);
CREATE INDEX IF NOT EXISTS idx_msg_file ON messages(file_id);
CREATE INDEX IF NOT EXISTS idx_members_user ON room_members(user_id);
CREATE INDEX IF NOT EXISTS idx_msg_room ON messages(room_id, id);
CREATE TABLE IF NOT EXISTS files(
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    size INTEGER NOT NULL,
    uploader_id INTEGER NOT NULL,
    path TEXT NOT NULL,
    created_at REAL NOT NULL,
    complete INTEGER NOT NULL DEFAULT 0,
    purged INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS announcements(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sender_id INTEGER NOT NULL,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    department TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS announcement_reads(
    announcement_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    read_at REAL NOT NULL,
    PRIMARY KEY(announcement_id, user_id)
);
CREATE TABLE IF NOT EXISTS file_downloads(
    file_id TEXT NOT NULL,
    user_id INTEGER NOT NULL,
    ts REAL NOT NULL,
    PRIMARY KEY(file_id, user_id)
);
CREATE TABLE IF NOT EXISTS pins(
    conv TEXT NOT NULL,
    message_id INTEGER NOT NULL,
    pinned_by INTEGER NOT NULL,
    ts REAL NOT NULL,
    PRIMARY KEY(conv, message_id)
);
CREATE TABLE IF NOT EXISTS mutes(
    user_id INTEGER NOT NULL,
    conv TEXT NOT NULL,
    PRIMARY KEY(user_id, conv)
);
CREATE TABLE IF NOT EXISTS audit(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    target TEXT NOT NULL DEFAULT '',
    details TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit(ts);
CREATE TABLE IF NOT EXISTS roles(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE COLLATE NOCASE,
    level INTEGER NOT NULL DEFAULT 0,
    announce TEXT NOT NULL DEFAULT 'none',
    create_rooms INTEGER NOT NULL DEFAULT 1,
    manage_users INTEGER NOT NULL DEFAULT 0,
    see_all INTEGER NOT NULL DEFAULT 1,
    always_visible INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS polls(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id INTEGER NOT NULL UNIQUE,
    creator_id INTEGER NOT NULL,
    question TEXT NOT NULL,
    options TEXT NOT NULL,
    multi INTEGER NOT NULL DEFAULT 0,
    anonymous INTEGER NOT NULL DEFAULT 0,
    closed INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS poll_votes(
    poll_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    option INTEGER NOT NULL,
    PRIMARY KEY(poll_id, user_id, option)
);
CREATE TABLE IF NOT EXISTS reminders(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    conv TEXT NOT NULL DEFAULT '',
    message_id INTEGER,
    text TEXT NOT NULL DEFAULT '',
    due_at REAL NOT NULL,
    state INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_reminders_due ON reminders(state, due_at);
CREATE TABLE IF NOT EXISTS scheduled(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    conv TEXT NOT NULL,
    text TEXT NOT NULL DEFAULT '',
    sticker TEXT NOT NULL DEFAULT '',
    due_at REAL NOT NULL,
    state TEXT NOT NULL DEFAULT 'pending',
    error TEXT NOT NULL DEFAULT '',
    message_id INTEGER,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_scheduled_due ON scheduled(state, due_at);
CREATE TABLE IF NOT EXISTS meta(
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS reactions(
    message_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    emoji TEXT NOT NULL,
    created_at REAL NOT NULL,
    PRIMARY KEY(message_id, user_id, emoji)
);
CREATE TABLE IF NOT EXISTS departments(
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    parent_id INTEGER NULL REFERENCES departments(id),
    has_room INTEGER NOT NULL DEFAULT 0,
    created_at REAL
);
"""

# Columns added after v1: (table, column, definition). Applied on startup if missing.
MIGRATIONS = [
    ("users", "section", "TEXT NOT NULL DEFAULT ''"),
    ("users", "role_id", "INTEGER"),
    ("users", "manager_id", "INTEGER"),
    ("rooms", "auto_key", "TEXT"),
    ("rooms", "file_retention_days", "REAL"),          # NULL = the server default, 0 = keep forever
    ("users", "birthday", "TEXT NOT NULL DEFAULT ''"),       # '--MM-DD' (ISO, no year) or 'YYYY-MM-DD'
    ("users", "joined_on", "TEXT NOT NULL DEFAULT ''"),      # 'YYYY-MM-DD' (work anniversaries)
    ("users", "employee_id", "TEXT NOT NULL DEFAULT ''"),
    ("announcements", "target_kind", "TEXT NOT NULL DEFAULT 'all'"),
    ("announcements", "target_value", "TEXT NOT NULL DEFAULT ''"),
    ("users", "must_change_pw", "INTEGER NOT NULL DEFAULT 0"),
    ("users", "pw_changed_at", "REAL"),
    ("messages", "reply_to", "INTEGER"),
    ("messages", "edited_at", "REAL"),
    ("messages", "deleted", "INTEGER NOT NULL DEFAULT 0"),
    ("messages", "forwarded", "INTEGER NOT NULL DEFAULT 0"),
    ("users", "avatar_ver", "INTEGER NOT NULL DEFAULT 0"),
    ("users", "status_emoji", "TEXT NOT NULL DEFAULT ''"),
    ("users", "status_until", "REAL"),
]

# Who can announce to whom, from least to most.
ANNOUNCE_LEVELS = ("none", "team", "section", "department", "all")

# name, level, announce, create_rooms, manage_users, see_all, always_visible
DEFAULT_ROLES = [
    ("Management", 100, "all", 1, 0, 1, 1),
    ("HOD", 90, "department", 1, 0, 1, 1),
    ("Supervisor", 80, "department", 1, 0, 1, 0),
    ("HR", 75, "all", 1, 1, 1, 1),
    ("IT", 75, "all", 1, 1, 1, 1),
    ("Production", 70, "all", 1, 0, 1, 1),
    ("Lead", 60, "section", 1, 0, 1, 0),
    ("Senior Artist", 40, "none", 1, 0, 1, 0),
    ("Artist", 30, "none", 1, 0, 1, 0),
    ("Junior Artist", 20, "none", 0, 0, 1, 0),
    ("Trainee", 10, "none", 0, 0, 1, 0),
]
# VFX studio designations, added once to every server (new and upgraded); an admin can rename or delete them.
# (name, level, announce, create_rooms, manage_users, see_all, always_visible)
VFX_ROLES = [
    ("Studio Head", 100, "all", 1, 0, 1, 1),
    ("Executive Producer", 98, "all", 1, 0, 1, 1),
    ("VFX Supervisor", 95, "all", 1, 0, 1, 1),
    ("VFX Producer", 94, "all", 1, 0, 1, 1),
    ("Associate VFX Supervisor", 92, "all", 1, 0, 1, 1),
    ("DFX Supervisor", 90, "all", 1, 0, 1, 1),
    ("CG Supervisor", 88, "department", 1, 0, 1, 1),
    ("Compositing Supervisor", 85, "department", 1, 0, 1, 1),
    ("FX Supervisor", 85, "department", 1, 0, 1, 1),
    ("Lighting Supervisor", 85, "department", 1, 0, 1, 1),
    ("Animation Supervisor", 85, "department", 1, 0, 1, 1),
    ("Pipeline Supervisor", 85, "all", 1, 0, 1, 1),
    ("Roto/Paint Supervisor", 82, "department", 1, 0, 1, 0),
    ("Matchmove Supervisor", 82, "department", 1, 0, 1, 0),
    ("DMP Supervisor", 82, "department", 1, 0, 1, 0),
    ("Assets Supervisor", 82, "department", 1, 0, 1, 0),
    ("Line Producer", 80, "all", 1, 0, 1, 1),
    ("Production Manager", 78, "all", 1, 0, 1, 1),
    ("Production Coordinator", 65, "department", 1, 0, 1, 1),
    ("Production Assistant", 35, "none", 1, 0, 1, 0),
    ("Compositing Lead", 60, "section", 1, 0, 1, 0),
    ("FX Lead", 60, "section", 1, 0, 1, 0),
    ("Lighting Lead", 60, "section", 1, 0, 1, 0),
    ("Animation Lead", 60, "section", 1, 0, 1, 0),
    ("Roto Lead", 60, "section", 1, 0, 1, 0),
    ("Paint Lead", 60, "section", 1, 0, 1, 0),
    ("Matchmove Lead", 60, "section", 1, 0, 1, 0),
    ("Modeling Lead", 60, "section", 1, 0, 1, 0),
    ("Texturing Lead", 60, "section", 1, 0, 1, 0),
    ("Rigging Lead", 60, "section", 1, 0, 1, 0),
    ("Layout Lead", 60, "section", 1, 0, 1, 0),
    ("DMP Lead", 60, "section", 1, 0, 1, 0),
    ("Pipeline TD", 50, "none", 1, 0, 1, 1),
    ("Senior Compositor", 45, "none", 1, 0, 1, 0),
    ("Compositor", 32, "none", 1, 0, 1, 0),
    ("FX Artist", 32, "none", 1, 0, 1, 0),
    ("FX TD", 35, "none", 1, 0, 1, 0),
    ("Lighting Artist", 32, "none", 1, 0, 1, 0),
    ("Lighting TD", 35, "none", 1, 0, 1, 0),
    ("Look Dev Artist", 32, "none", 1, 0, 1, 0),
    ("Animator", 32, "none", 1, 0, 1, 0),
    ("CFX Artist", 32, "none", 1, 0, 1, 0),
    ("Layout Artist", 32, "none", 1, 0, 1, 0),
    ("Modeler", 32, "none", 1, 0, 1, 0),
    ("Texture Artist", 32, "none", 1, 0, 1, 0),
    ("Rigger", 32, "none", 1, 0, 1, 0),
    ("Environment Artist", 32, "none", 1, 0, 1, 0),
    ("Matte Painter", 32, "none", 1, 0, 1, 0),
    ("Concept Artist", 32, "none", 1, 0, 1, 0),
    ("Matchmove Artist", 30, "none", 1, 0, 1, 0),
    ("Roto Artist", 28, "none", 0, 0, 1, 0),
    ("Paint Artist", 28, "none", 0, 0, 1, 0),
    ("Prep Artist", 28, "none", 0, 0, 1, 0),
    ("Render Wrangler", 30, "none", 1, 0, 1, 1),
    ("Data I/O", 30, "none", 1, 0, 1, 1),
    ("Editor", 35, "none", 1, 0, 1, 0),
    ("System Administrator", 75, "all", 1, 1, 1, 1),
    ("Intern", 5, "none", 0, 0, 1, 0),
]
ROLE_FIELDS = ("name", "level", "announce", "create_rooms", "manage_users", "see_all", "always_visible")

USER_FIELDS = ("username", "display_name", "department", "section", "title", "role_id", "manager_id",
               "is_admin", "can_broadcast", "disabled", "birthday", "joined_on", "employee_id")

MONTHS = {m: i for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct",
                                      "nov", "dec"), 1)}


def check_date(value, what, year_optional=False):
    """A date typed by a person or read from Excel -> 'YYYY-MM-DD' (or 'MM-DD' when the year may be left out).

    Accepts 2026-09-26, 26-09-2026, 26/09/2026, 26.09.2026, 26 Sep 2026, and without a year (birthdays)
    26-09, 26/09, 26 Sep. Empty stays empty."""
    import datetime
    import re
    text = str(value or "").strip()
    if not text:
        return ""
    text = text.split(" 00:00")[0].split("T")[0]
    iso_no_year = re.fullmatch(r"--(\d{2})-(\d{2})", text)          # what we store for a birthday without a year
    if iso_no_year:
        text = f"{iso_no_year.group(2)}-{iso_no_year.group(1)}"
    year = month = day = None
    m = re.fullmatch(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})", text)
    if m:
        year, month, day = (int(x) for x in m.groups())
    m = m or re.fullmatch(r"(\d{1,2})[-/.](\d{1,2})(?:[-/.](\d{2,4}))?", text)
    if m and day is None:
        day, month = int(m.group(1)), int(m.group(2))
        year = int(m.group(3)) if m.group(3) else None
    if day is None:
        m = re.fullmatch(r"(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]{3})[A-Za-z]*\.?(?:,?\s+(\d{4}))?", text)
        if m and m.group(2).lower() in MONTHS:
            day, month = int(m.group(1)), MONTHS[m.group(2).lower()]
            year = int(m.group(3)) if m.group(3) else None
    if day is None:
        raise ValueError(f"{what} '{text}' is not a date - use DD-MM-YYYY")
    if year is not None and year < 100:
        year += 2000 if year < 50 else 1900
    try:
        datetime.date(year or 2000, month, day)          # 2000 is a leap year: 29-02 birthdays are fine
    except ValueError:
        raise ValueError(f"{what} '{text}' is not a real date")
    if year is None:
        if not year_optional:
            raise ValueError(f"{what} needs a year - use DD-MM-YYYY")
        return f"--{month:02d}-{day:02d}"
    if not 1900 <= year <= 2100:
        raise ValueError(f"{what} '{text}' has an odd year")
    return f"{year:04d}-{month:02d}-{day:02d}"


def check_label(value: str, what: str) -> str:
    """Names shown in lists and headers: no markup characters or control characters."""
    value = (value or "").strip()
    if any(ch in value for ch in "<>") or any(ord(ch) < 32 for ch in value):
        raise ValueError(f"{what} can't contain < > or control characters")
    if len(value) > 80:
        raise ValueError(f"{what} is too long (max 80 characters)")
    return value


def clean_label(value: str) -> str:
    """Remove characters check_label() refuses (for names built from older data)."""
    return "".join(ch for ch in (value or "") if ch not in "<>" and ord(ch) >= 32).strip()[:80] or "?"


def hash_password(password: str, salt: str | None = None):
    salt = salt or os.urandom(16).hex()
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                                 bytes.fromhex(salt), PBKDF2_ROUNDS)
    return digest.hex(), salt


def direct_key(a: int, b: int) -> str:
    """Internal conversation key for a 1-to-1 chat."""
    lo, hi = sorted((a, b))
    return f"d:{lo}:{hi}"


class Database:
    def __init__(self, path: str):
        self.path = path
        self.con = sqlite3.connect(path, check_same_thread=False)
        self.con.row_factory = sqlite3.Row
        self.con.execute("PRAGMA journal_mode=WAL")
        self.con.execute("PRAGMA synchronous=NORMAL")
        new_departments = not self.con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='departments'").fetchone()
        self.con.executescript(SCHEMA)
        added = set()
        for table, column, definition in MIGRATIONS:
            cols = {r[1] for r in self.con.execute(f"PRAGMA table_info({table})")}
            if column not in cols:
                self.con.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
                added.add((table, column))
        if ("announcements", "target_kind") in added:      # v1 department announcements
            self.con.execute("UPDATE announcements SET target_kind='department',"
                             " target_value=department WHERE department<>''")
        if not self.con.execute("SELECT 1 FROM roles LIMIT 1").fetchone():
            self.con.executemany(f"INSERT INTO roles({', '.join(ROLE_FIELDS)}) VALUES(?,?,?,?,?,?,?)",
                                 DEFAULT_ROLES)
        if not self.con.execute("SELECT 1 FROM meta WHERE key='vfx_roles'").fetchone():
            # once per server: names that already exist (in any spelling) are left as the admin set them
            self.con.executemany(f"INSERT OR IGNORE INTO roles({', '.join(ROLE_FIELDS)}) VALUES(?,?,?,?,?,?,?)",
                                 VFX_ROLES)
            self.con.execute("INSERT OR REPLACE INTO meta(key, value) VALUES('vfx_roles', '1')")
        if new_departments:
            self._seed_departments()
        self.con.commit()

    def _seed_departments(self):
        """One-time move from free-text departments to the managed list.

        The departments and sections people already have become entries (without a chat room), users' text is
        set to one spelling per name, and the old automatic department/section rooms become normal rooms so
        their history stays. Ticking "Chat room" later picks the old room up again (see released_room)."""
        now = time.time()
        depts, sects = {}, {}            # lower name -> id ; (dept id, lower section) -> id
        rows = self.con.execute("SELECT id, department, section FROM users WHERE deleted=0 ORDER BY id").fetchall()
        for uid, dept, sect in rows:
            dept, sect = clean_label(dept) if dept.strip() else "", clean_label(sect) if sect.strip() else ""
            if not dept:
                continue
            if dept.lower() not in depts:
                depts[dept.lower()] = self.con.execute(
                    "INSERT INTO departments(name, parent_id, has_room, created_at) VALUES(?,NULL,0,?)",
                    (dept, now)).lastrowid
            dept_id = depts[dept.lower()]
            if sect and (dept_id, sect.lower()) not in sects:
                sects[(dept_id, sect.lower())] = self.con.execute(
                    "INSERT INTO departments(name, parent_id, has_room, created_at) VALUES(?,?,0,?)",
                    (sect, dept_id, now)).lastrowid
        names = {r[0]: r[1] for r in self.con.execute("SELECT id, name FROM departments")}
        for uid, dept, sect in rows:            # one spelling per name ("comp" and "Comp" become the same)
            dept_id = depts.get(clean_label(dept).lower()) if dept.strip() else None
            new_dept = names[dept_id] if dept_id else ""
            sect_id = sects.get((dept_id, clean_label(sect).lower())) if dept_id and sect.strip() else None
            new_sect = names[sect_id] if sect_id else ""
            if (new_dept, new_sect) != (dept, sect):
                self.con.execute("UPDATE users SET department=?, section=? WHERE id=?", (new_dept, new_sect, uid))
        # old automatic rooms: keep them (and their history) as normal rooms
        old = self.con.execute("SELECT id, auto_key FROM rooms WHERE deleted=0 AND"
                               " (auto_key LIKE 'dept:%' OR auto_key LIKE 'sect:%')").fetchall()
        for room_id, key in old:
            kind, _, value = key.partition(":")
            new_id = None
            if kind == "dept":
                new_id = depts.get(value)
            else:
                d, _, s = value.partition("\x1f")
                new_id = sects.get((depts.get(d), s))
            if new_id:
                key = f"{'dept' if kind == 'dept' else 'sect'}#{new_id}"
                self.con.execute("INSERT OR REPLACE INTO meta(key, value) VALUES(?,?)",
                                 (f"released_room:{key}", str(room_id)))
        self.con.execute("UPDATE rooms SET auto_key=NULL WHERE auto_key LIKE 'dept:%' OR auto_key LIKE 'sect:%'")

    def close(self):
        self.con.close()

    def _one(self, sql, *args):
        return self.con.execute(sql, args).fetchone()

    def _all(self, sql, *args):
        return self.con.execute(sql, args).fetchall()

    def _exec(self, sql, *args):
        cur = self.con.execute(sql, args)
        self.con.commit()
        return cur

    # ------------------------------------------------------------------ users
    def user_count(self) -> int:
        return self._one("SELECT COUNT(*) FROM users WHERE deleted=0")[0]

    def create_user(self, username, password, display_name="", department="", title="",
                    is_admin=False, can_broadcast=False, section="", role_id=None,
                    manager_id=None, **extra) -> int:
        username = check_label(username, "Username")
        if not username:
            raise ValueError("Username is required")
        if any(ch.isspace() for ch in username):
            raise ValueError("Username can't contain spaces")
        display_name = check_label(display_name, "Display name")
        department = check_label(department, "Department")
        section = check_label(section, "Section")
        title = check_label(title, "Job title")
        if self._one("SELECT id FROM users WHERE username=?", username):
            raise ValueError(f"Username '{username}' already exists")
        if len(password) < 4:
            raise ValueError("Password must be at least 4 characters")
        pw_hash, salt = hash_password(password)
        cur = self._exec(
            "INSERT INTO users(username, display_name, pw_hash, pw_salt, department, section,"
            " title, role_id, manager_id, is_admin, can_broadcast, created_at)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            username, display_name.strip() or username, pw_hash, salt, department.strip(),
            section.strip(), title.strip(), role_id or None, manager_id or None, int(is_admin),
            int(can_broadcast), time.time())
        extra = {k: v for k, v in extra.items() if k in ("birthday", "joined_on", "employee_id")}
        if extra:
            self.update_user(cur.lastrowid, **extra)
        return cur.lastrowid

    def update_user(self, user_id: int, **fields):
        fields = {k: v for k, v in fields.items() if k in USER_FIELDS}
        if "username" in fields:
            fields["username"] = check_label(fields["username"], "Username")
            other = self._one("SELECT id FROM users WHERE username=? AND id<>?",
                              fields["username"], user_id)
            if not fields["username"] or other or any(ch.isspace() for ch in fields["username"]):
                raise ValueError("Username is empty, has spaces or is already taken")
        for key, what in (("department", "Department"), ("section", "Section"), ("title", "Job title"),
                          ("display_name", "Display name")):
            if key in fields:
                fields[key] = check_label(fields[key] or "", what)
        for key in ("role_id", "manager_id"):
            if key in fields:
                fields[key] = fields[key] or None
        if "birthday" in fields:
            fields["birthday"] = check_date(fields["birthday"], "Birthday", year_optional=True)
        if "joined_on" in fields:
            fields["joined_on"] = check_date(fields["joined_on"], "Joining date")
        if "employee_id" in fields:
            fields["employee_id"] = check_label(str(fields["employee_id"] or ""), "Employee ID")
            if fields["employee_id"] and self._one("SELECT id FROM users WHERE employee_id=? AND id<>? AND deleted=0",
                                                   fields["employee_id"], user_id):
                raise ValueError(f"Employee ID '{fields['employee_id']}' belongs to someone else")
        if not fields:
            return
        cols = ", ".join(f"{k}=?" for k in fields)
        self._exec(f"UPDATE users SET {cols} WHERE id=?", *fields.values(), user_id)

    def set_password(self, user_id: int, password: str, must_change: bool = False):
        if len(password) < 4:
            raise ValueError("Password must be at least 4 characters")
        pw_hash, salt = hash_password(password)
        self._exec("UPDATE users SET pw_hash=?, pw_salt=?, pw_changed_at=?, must_change_pw=? WHERE id=?",
                   pw_hash, salt, time.time(), int(must_change), user_id)

    def set_must_change(self, user_id: int, value: bool):
        self._exec("UPDATE users SET must_change_pw=? WHERE id=?", int(value), user_id)

    # ------------------------------------------------------------------ audit
    def add_audit(self, actor, action, target="", details=""):
        self._exec("INSERT INTO audit(ts, actor, action, target, details) VALUES(?,?,?,?,?)",
                   time.time(), str(actor)[:100], action, str(target)[:200], str(details)[:1000])

    def list_audit(self, query="", limit=1000):
        if query:
            like = f"%{query}%"
            return self._all("SELECT * FROM audit WHERE actor LIKE ? OR action LIKE ? OR target LIKE ?"
                             " OR details LIKE ? ORDER BY id DESC LIMIT ?", like, like, like, like, limit)
        return self._all("SELECT * FROM audit ORDER BY id DESC LIMIT ?", limit)

    def backup_to(self, path: str):
        """Consistent copy of the live database (safe while the server runs).

        Uses its own connection, so it can run in a worker thread while the server keeps serving chats."""
        src = sqlite3.connect(self.path, check_same_thread=False)
        dst = sqlite3.connect(path)
        try:
            src.backup(dst, pages=4096)
        finally:
            dst.close()
            src.close()

    def delete_user(self, user_id: int):
        # Soft delete: messages keep pointing at the row, the name is freed.
        self._exec("UPDATE users SET deleted=1, disabled=1, username=username||'#deleted'||id"
                   " WHERE id=?", user_id)
        self._exec("DELETE FROM room_members WHERE user_id=?", user_id)
        self._exec("UPDATE users SET manager_id=NULL WHERE manager_id=?", user_id)

    # ------------------------------------------------------------------ roles
    def list_roles(self):
        return self._all("SELECT * FROM roles ORDER BY level DESC, name COLLATE NOCASE")

    def get_role(self, role_id):
        return self._one("SELECT * FROM roles WHERE id=?", role_id) if role_id else None

    def get_role_by_name(self, name):
        return self._one("SELECT * FROM roles WHERE name=?", name.strip())

    def save_role(self, role_id, **fields):
        fields = {k: v for k, v in fields.items() if k in ROLE_FIELDS}
        if "name" in fields:
            fields["name"] = check_label(fields["name"], "Designation")
            if not fields["name"]:
                raise ValueError("Designation name is required")
            other = self._one("SELECT id FROM roles WHERE name=? AND id<>?", fields["name"], role_id or 0)
            if other:
                raise ValueError(f"Designation '{fields['name']}' already exists")
        if fields.get("announce", "none") not in ANNOUNCE_LEVELS:
            raise ValueError("Invalid announcement permission")
        if role_id is None:
            if "name" not in fields:
                raise ValueError("Designation name is required")
            cols = ", ".join(fields)
            cur = self._exec(f"INSERT INTO roles({cols}) VALUES({', '.join('?' * len(fields))})",
                             *fields.values())
            return cur.lastrowid
        if fields:
            self._exec(f"UPDATE roles SET {', '.join(f'{k}=?' for k in fields)} WHERE id=?",
                       *fields.values(), role_id)
        return role_id

    def delete_role(self, role_id):
        self._exec("UPDATE users SET role_id=NULL WHERE role_id=?", role_id)
        self._exec("DELETE FROM roles WHERE id=?", role_id)

    def role_usage(self):
        return dict(self._all("SELECT role_id, COUNT(*) FROM users WHERE deleted=0 GROUP BY role_id"))

    # ------------------------------------------------------------ departments
    # A row without parent_id is a department; a row with one is a section of that department.
    # Users keep the names as text (announcements, the org views and the client read them from there).
    def list_departments(self):
        return self._all("SELECT * FROM departments ORDER BY name COLLATE NOCASE")

    def get_department(self, dept_id):
        return self._one("SELECT * FROM departments WHERE id=?", dept_id) if dept_id else None

    def find_department(self, name, parent_id=None):
        """Case-insensitive lookup of a department (or of a section when parent_id is given)."""
        name = (name or "").strip().lower()
        for r in self._all("SELECT * FROM departments WHERE parent_id IS ?", parent_id):
            if r["name"].lower() == name:
                return r
        return None

    def _members_of(self, dept):
        """Ids of (not deleted) users in a department or section row."""
        if dept["parent_id"] is None:
            name, section = dept["name"].lower(), None
        else:
            parent = self.get_department(dept["parent_id"])
            name, section = (parent["name"].lower() if parent else None), dept["name"].lower()
        return [r[0] for r in self._all("SELECT id, department, section FROM users WHERE deleted=0")
                if r[1].strip().lower() == name and (section is None or r[2].strip().lower() == section)]

    def department_usage(self) -> dict:
        """{department id: number of people} (a department counts everyone in it, its sections included)."""
        rows = self.list_departments()
        people = {}
        for r in self._all("SELECT department, section FROM users WHERE deleted=0"):
            key = (r[0].strip().lower(), r[1].strip().lower())
            people[key] = people.get(key, 0) + 1
        names = {r["id"]: r["name"].lower() for r in rows}
        out = {}
        for r in rows:
            if r["parent_id"] is None:
                out[r["id"]] = sum(n for (d, _), n in people.items() if d == names[r["id"]])
            else:
                out[r["id"]] = people.get((names.get(r["parent_id"]), r["name"].lower()), 0)
        return out

    def _check_department_name(self, name, parent_id, dept_id=None):
        what = "Section" if parent_id else "Department"
        name = check_label(name, f"{what} name")
        if not name:
            raise ValueError(f"{what} name is required")
        other = self.find_department(name, parent_id)
        if other and other["id"] != dept_id:
            raise ValueError(f"{what} '{other['name']}' already exists")
        return name

    def create_department(self, name, parent_id=None) -> int:
        if parent_id is not None:
            parent = self.get_department(parent_id)
            if not parent or parent["parent_id"] is not None:
                raise ValueError("Sections can only be added to a department")
        name = self._check_department_name(name, parent_id)
        return self._exec("INSERT INTO departments(name, parent_id, has_room, created_at) VALUES(?,?,0,?)",
                          name, parent_id, time.time()).lastrowid

    def rename_department(self, dept_id, name):
        """Rename a department/section and every user's text with it. Returns the old name."""
        dept = self.get_department(dept_id)
        if not dept:
            raise ValueError("Department not found")
        name = self._check_department_name(name, dept["parent_id"], dept_id)
        members = self._members_of(dept)
        column = "department" if dept["parent_id"] is None else "section"
        with self.con:
            self.con.execute("UPDATE departments SET name=? WHERE id=?", (name, dept_id))
            self.con.executemany(f"UPDATE users SET {column}=? WHERE id=?", [(name, uid) for uid in members])
        return dept["name"]

    def set_department_room(self, dept_id, has_room):
        self._exec("UPDATE departments SET has_room=? WHERE id=?", int(bool(has_room)), dept_id)

    def delete_department(self, dept_id):
        """Delete a department (with its sections) or a section - only when nobody is in it."""
        dept = self.get_department(dept_id)
        if not dept:
            raise ValueError("Department not found")
        people = len(self._members_of(dept))
        if people:
            what = "department" if dept["parent_id"] is None else "section"
            raise ValueError(f"{people} {'person is' if people == 1 else 'people are'} still in the {what} "
                             f"'{dept['name']}'. Move them to another {what} on the Users page first.")
        with self.con:
            self.con.execute("DELETE FROM departments WHERE parent_id=?", (dept_id,))
            self.con.execute("DELETE FROM departments WHERE id=?", (dept_id,))

    def set_status_msg(self, user_id: int, msg: str, emoji: str = "", until: float | None = None):
        self._exec("UPDATE users SET status_msg=?, status_emoji=?, status_until=? WHERE id=?",
                   msg[:200], emoji[:16], until, user_id)

    def expired_statuses(self, now: float):
        return self._all("SELECT id FROM users WHERE status_until IS NOT NULL AND status_until<=?", now)

    def set_avatar_ver(self, user_id: int, ver: int):
        self._exec("UPDATE users SET avatar_ver=? WHERE id=?", ver, user_id)

    # ------------------------------------------------------------------ polls
    def add_poll(self, message_id, creator_id, question, options, multi, anonymous) -> int:
        return self._exec("INSERT INTO polls(message_id, creator_id, question, options, multi, anonymous)"
                          " VALUES(?,?,?,?,?,?)", message_id, creator_id, question, json.dumps(options),
                          int(bool(multi)), int(bool(anonymous))).lastrowid

    def poll_for_message(self, message_id):
        return self._one("SELECT * FROM polls WHERE message_id=?", message_id)

    def get_poll(self, poll_id):
        return self._one("SELECT * FROM polls WHERE id=?", poll_id)

    def poll_votes(self, poll_id):
        """[(option, user_id)] in voting order."""
        return self._all("SELECT option, user_id FROM poll_votes WHERE poll_id=? ORDER BY rowid", poll_id)

    def set_votes(self, poll_id, user_id, options):
        with self.con:
            self.con.execute("DELETE FROM poll_votes WHERE poll_id=? AND user_id=?", (poll_id, user_id))
            self.con.executemany("INSERT INTO poll_votes(poll_id, user_id, option) VALUES(?,?,?)",
                                 [(poll_id, user_id, o) for o in options])

    def close_poll(self, poll_id, closed=True):
        self._exec("UPDATE polls SET closed=? WHERE id=?", int(closed), poll_id)

    # ------------------------------------------------------------- reminders
    def add_reminder(self, user_id, conv, message_id, text, due_at) -> int:
        return self._exec("INSERT INTO reminders(user_id, conv, message_id, text, due_at, created_at)"
                          " VALUES(?,?,?,?,?,?)", user_id, conv, message_id, text, due_at, time.time()).lastrowid

    def get_reminder(self, rid):
        return self._one("SELECT * FROM reminders WHERE id=?", rid)

    def reminders_for(self, user_id):
        """Waiting and fired-but-not-done reminders, soonest first."""
        return self._all("SELECT * FROM reminders WHERE user_id=? AND state<2 ORDER BY due_at", user_id)

    def count_pending_reminders(self, user_id) -> int:
        return self._one("SELECT COUNT(*) FROM reminders WHERE user_id=? AND state<2", user_id)[0]

    def due_reminders(self, now):
        return self._all("SELECT * FROM reminders WHERE state=0 AND due_at<=? ORDER BY due_at", now)

    def set_reminder_state(self, rid, state):
        self._exec("UPDATE reminders SET state=? WHERE id=?", state, rid)

    def reschedule_reminder(self, rid, due_at):
        self._exec("UPDATE reminders SET due_at=?, state=0 WHERE id=?", due_at, rid)

    def delete_reminder(self, rid):
        self._exec("DELETE FROM reminders WHERE id=?", rid)

    # ---------------------------------------------------- scheduled messages
    def add_scheduled(self, user_id, conv, text, sticker, due_at) -> int:
        return self._exec("INSERT INTO scheduled(user_id, conv, text, sticker, due_at, created_at)"
                          " VALUES(?,?,?,?,?,?)", user_id, conv, text, sticker, due_at, time.time()).lastrowid

    def get_scheduled(self, sid):
        return self._one("SELECT * FROM scheduled WHERE id=?", sid)

    def scheduled_for(self, user_id):
        """Waiting messages, plus ones that failed in the last week (so the user sees why)."""
        return self._all("SELECT * FROM scheduled WHERE user_id=? AND (state='pending' OR"
                         " (state='failed' AND due_at>?)) ORDER BY due_at", user_id, time.time() - 7 * 86400)

    def count_pending_scheduled(self, user_id) -> int:
        return self._one("SELECT COUNT(*) FROM scheduled WHERE user_id=? AND state='pending'", user_id)[0]

    def due_scheduled(self, now):
        return self._all("SELECT * FROM scheduled WHERE state='pending' AND due_at<=? ORDER BY due_at", now)

    def update_scheduled(self, sid, text, due_at):
        self._exec("UPDATE scheduled SET text=?, due_at=? WHERE id=?", text, due_at, sid)

    def set_scheduled_state(self, sid, state, message_id=None, error=""):
        self._exec("UPDATE scheduled SET state=?, message_id=COALESCE(?, message_id), error=? WHERE id=?",
                   state, message_id, error, sid)

    # ------------------------------------------------------------------- meta
    def get_meta(self, key, default=None):
        row = self._one("SELECT value FROM meta WHERE key=?", key)
        return row[0] if row else default

    def set_meta(self, key, value):
        self._exec("INSERT INTO meta(key, value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                   key, str(value))

    # --------------------------------------------------------- chat archive
    def messages_after(self, last_id, limit=5000):
        """Messages with id > last_id (oldest first), with file details, for the chat log export."""
        return self._all(
            "SELECT m.*, f.name AS file_name, f.size AS file_size, f.purged AS file_purged"
            " FROM messages m LEFT JOIN files f ON f.id=m.file_id WHERE m.id>? ORDER BY m.id LIMIT ?",
            last_id, limit)

    def changed_before_removal(self, before_ts, max_id):
        """Old messages about to be removed whose log line may be out of date (edited, deleted, polls)."""
        return self._all(
            "SELECT m.*, f.name AS file_name, f.size AS file_size, f.purged AS file_purged"
            " FROM messages m LEFT JOIN files f ON f.id=m.file_id WHERE m.created_at<? AND m.id<=?"
            " AND (m.edited_at IS NOT NULL OR m.deleted=1 OR m.kind='poll') ORDER BY m.id", before_ts, max_id)

    def delete_old_messages(self, before_ts, max_id) -> int:
        """Remove messages older than before_ts (and only those up to max_id, i.e. already exported)."""
        where = "SELECT id FROM messages WHERE created_at<? AND id<=?"
        with self.con:
            for table, col in (("reactions", "message_id"), ("pins", "message_id")):
                self.con.execute(f"DELETE FROM {table} WHERE {col} IN ({where})", (before_ts, max_id))
            self.con.execute(f"DELETE FROM poll_votes WHERE poll_id IN (SELECT id FROM polls WHERE message_id IN "
                             f"({where}))", (before_ts, max_id))
            self.con.execute(f"DELETE FROM polls WHERE message_id IN ({where})", (before_ts, max_id))
            cur = self.con.execute("DELETE FROM messages WHERE created_at<? AND id<=?", (before_ts, max_id))
        return cur.rowcount

    # -------------------------------------------------------------- reactions
    def set_reaction(self, message_id, user_id, emoji, on=True):
        if on:
            self._exec("INSERT OR IGNORE INTO reactions(message_id, user_id, emoji, created_at) VALUES(?,?,?,?)",
                       message_id, user_id, emoji, time.time())
        else:
            self._exec("DELETE FROM reactions WHERE message_id=? AND user_id=? AND emoji=?",
                       message_id, user_id, emoji)

    def reactions(self, message_id):
        """[(emoji, user_id)] oldest first."""
        return self._all("SELECT emoji, user_id FROM reactions WHERE message_id=? ORDER BY created_at, rowid",
                         message_id)

    def reaction_kinds(self, message_id) -> int:
        return self._one("SELECT COUNT(DISTINCT emoji) FROM reactions WHERE message_id=?", message_id)[0]

    def touch_last_seen(self, user_id: int):
        self._exec("UPDATE users SET last_seen=? WHERE id=?", time.time(), user_id)

    def get_user(self, user_id: int):
        return self._one("SELECT * FROM users WHERE id=?", user_id)

    def get_user_by_name(self, username: str):
        return self._one("SELECT * FROM users WHERE username=? AND deleted=0", username.strip())

    def list_users(self, include_disabled=True):
        sql = "SELECT * FROM users WHERE deleted=0"
        if not include_disabled:
            sql += " AND disabled=0"
        return self._all(sql + " ORDER BY department, display_name COLLATE NOCASE")

    @staticmethod
    def check_password(row, password: str) -> bool:
        digest, _ = hash_password(password, row["pw_salt"])
        return hmac.compare_digest(digest, row["pw_hash"])

    # ------------------------------------------------------------------ rooms
    def create_room(self, name: str, owner_id: int | None, member_ids, topic="", auto_key=None) -> int:
        name = check_label(name, "Room name")
        if not name:
            raise ValueError("Room name is required")
        topic = (topic or "").strip()[:300]
        now = time.time()
        cur = self._exec("INSERT INTO rooms(name, topic, owner_id, created_at, auto_key) VALUES(?,?,?,?,?)",
                         name, topic, owner_id, now, auto_key)
        room_id = cur.lastrowid
        members = set(member_ids)
        if owner_id:
            members.add(owner_id)
        self.add_room_members(room_id, members)
        return room_id

    def room_co_members(self, user_id: int) -> set[int]:
        return {r[0] for r in self._all(
            "SELECT DISTINCT m2.user_id FROM room_members m1"
            " JOIN room_members m2 ON m2.room_id=m1.room_id JOIN rooms r ON r.id=m1.room_id"
            " WHERE m1.user_id=? AND r.deleted=0", user_id)}

    def auto_rooms(self):
        return self._all("SELECT * FROM rooms WHERE deleted=0 AND auto_key IS NOT NULL")

    def set_room_auto_key(self, room_id: int, auto_key):
        """None turns an automatic room into a normal one (members and history stay)."""
        self._exec("UPDATE rooms SET auto_key=? WHERE id=?", auto_key, room_id)

    def get_room(self, room_id: int):
        return self._one("SELECT * FROM rooms WHERE id=? AND deleted=0", room_id)

    def list_rooms(self):
        return self._all("SELECT * FROM rooms WHERE deleted=0 ORDER BY name COLLATE NOCASE")

    def rooms_for_user(self, user_id: int):
        return self._all(
            "SELECT r.* FROM rooms r JOIN room_members m ON m.room_id=r.id"
            " WHERE m.user_id=? AND r.deleted=0 ORDER BY r.name COLLATE NOCASE", user_id)

    def update_room(self, room_id: int, name=None, topic=None):
        if name is not None:
            name = check_label(name, "Room name")
            if not name:
                raise ValueError("Room name is required")
            self._exec("UPDATE rooms SET name=? WHERE id=?", name, room_id)
        if topic is not None:
            self._exec("UPDATE rooms SET topic=? WHERE id=?", topic.strip()[:300], room_id)

    def delete_room(self, room_id: int):
        self._exec("UPDATE rooms SET deleted=1 WHERE id=?", room_id)

    def set_room_owner(self, room_id: int, user_id):
        self._exec("UPDATE rooms SET owner_id=? WHERE id=?", user_id, room_id)

    def longest_member(self, room_id: int):
        """The member who has been in the room the longest (the next owner when the owner leaves)."""
        row = self._one("SELECT m.user_id FROM room_members m JOIN users u ON u.id=m.user_id"
                        " WHERE m.room_id=? AND u.deleted=0 AND u.disabled=0 ORDER BY m.joined_at, m.user_id LIMIT 1",
                        room_id)
        return row[0] if row else None

    def room_member_ids(self, room_id: int) -> list[int]:
        return [r[0] for r in self._all("SELECT user_id FROM room_members WHERE room_id=?", room_id)]

    def is_room_member(self, room_id: int, user_id: int) -> bool:
        return self._one("SELECT 1 FROM room_members m JOIN rooms r ON r.id=m.room_id"
                         " WHERE m.room_id=? AND m.user_id=? AND r.deleted=0",
                         room_id, user_id) is not None

    def add_room_members(self, room_id: int, user_ids):
        now = time.time()
        # New members start "caught up" so they don't see the whole backlog as unread.
        last = self._one("SELECT COALESCE(MAX(id),0) FROM messages WHERE room_id=?", room_id)[0]
        self.con.executemany(
            "INSERT OR IGNORE INTO room_members(room_id, user_id, joined_at, last_read)"
            " VALUES(?,?,?,?)", [(room_id, uid, now, last) for uid in user_ids])
        self.con.commit()

    def remove_room_member(self, room_id: int, user_id: int):
        self._exec("DELETE FROM room_members WHERE room_id=? AND user_id=?", room_id, user_id)

    # --------------------------------------------------------------- messages
    def add_message(self, conv, sender_id, body, kind="text", file_id=None,
                    recipient_id=None, room_id=None, delivered=False, reply_to=None, forwarded=False) -> int:
        now = time.time()
        cur = self._exec(
            "INSERT INTO messages(conv, sender_id, recipient_id, room_id, kind, body, file_id,"
            " created_at, delivered_at, reply_to, forwarded) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            conv, sender_id, recipient_id, room_id, kind, body, file_id, now,
            now if delivered else None, reply_to, int(bool(forwarded)))
        return cur.lastrowid

    def edit_message(self, msg_id: int, body: str):
        self._exec("UPDATE messages SET body=?, edited_at=? WHERE id=?", body, time.time(), msg_id)

    def delete_message(self, msg_id: int):
        # the text and the attachment link are removed; the row stays so replies/ordering still work
        self._exec("UPDATE messages SET deleted=1, body='', file_id=NULL WHERE id=?", msg_id)
        self._exec("DELETE FROM pins WHERE message_id=?", msg_id)

    # --------------------------------------------------------- pins / mutes
    def pin(self, conv: str, msg_id: int, user_id: int, pinned: bool):
        if pinned:
            self._exec("INSERT OR REPLACE INTO pins VALUES(?,?,?,?)", conv, msg_id, user_id, time.time())
        else:
            self._exec("DELETE FROM pins WHERE conv=? AND message_id=?", conv, msg_id)

    def pinned_ids(self, conv: str) -> list[int]:
        return [r[0] for r in self._all("SELECT message_id FROM pins WHERE conv=? ORDER BY ts DESC", conv)]

    def set_muted(self, user_id: int, conv: str, muted: bool):
        if muted:
            self._exec("INSERT OR IGNORE INTO mutes VALUES(?,?)", user_id, conv)
        else:
            self._exec("DELETE FROM mutes WHERE user_id=? AND conv=?", user_id, conv)

    def muted_convs(self, user_id: int) -> list[str]:
        return [r[0] for r in self._all("SELECT conv FROM mutes WHERE user_id=?", user_id)]

    def room_readers(self, room_id: int, msg_id: int) -> list[int]:
        return [r[0] for r in self._all("SELECT user_id FROM room_members WHERE room_id=? AND last_read>=?",
                                        room_id, msg_id)]

    def announcement_reader_ids(self, ann_id: int) -> set[int]:
        return {r[0] for r in self._all("SELECT user_id FROM announcement_reads WHERE announcement_id=?", ann_id)}

    def get_message(self, msg_id: int):
        return self._one(
            "SELECT m.*, f.name AS file_name, f.size AS file_size, f.purged AS file_purged"
            " FROM messages m LEFT JOIN files f ON f.id=m.file_id WHERE m.id=?", msg_id)

    def history(self, conv: str, before: int | None, limit: int):
        before = before or 2 ** 62
        rows = self._all(
            "SELECT m.*, f.name AS file_name, f.size AS file_size, f.purged AS file_purged"
            " FROM messages m LEFT JOIN files f ON f.id=m.file_id"
            " WHERE m.conv=? AND m.id<? ORDER BY m.id DESC LIMIT ?", conv, before, limit)
        return list(reversed(rows))

    def search(self, user_id: int, query: str, limit=100):
        like = "%" + query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        return self._all(
            "SELECT m.*, f.name AS file_name, f.size AS file_size, f.purged AS file_purged"
            " FROM messages m LEFT JOIN files f ON f.id=m.file_id"
            " WHERE m.deleted=0 AND m.kind<>'sticker' AND (m.body LIKE ? ESCAPE '\\' OR f.name LIKE ? ESCAPE '\\') AND ("
            "   m.sender_id=? OR m.recipient_id=? OR m.room_id IN"
            "   (SELECT room_id FROM room_members WHERE user_id=?))"
            " ORDER BY m.id DESC LIMIT ?", like, like, user_id, user_id, user_id, limit)

    def mark_delivered(self, recipient_id: int):
        """Mark all pending direct messages to a user delivered.

        Returns {sender_id: max_message_id} so senders can be notified."""
        rows = self._all("SELECT sender_id, MAX(id) FROM messages WHERE recipient_id=?"
                         " AND delivered_at IS NULL GROUP BY sender_id", recipient_id)
        if rows:
            self._exec("UPDATE messages SET delivered_at=? WHERE recipient_id=?"
                       " AND delivered_at IS NULL", time.time(), recipient_id)
        return {r[0]: r[1] for r in rows}

    def mark_direct_read(self, reader_id: int, sender_id: int, up_to: int) -> int:
        now = time.time()
        cur = self._exec(
            "UPDATE messages SET read_at=?, delivered_at=COALESCE(delivered_at, ?)"
            " WHERE recipient_id=? AND sender_id=? AND id<=? AND read_at IS NULL",
            now, now, reader_id, sender_id, up_to)
        return cur.rowcount

    def mark_room_read(self, room_id: int, user_id: int, up_to: int):
        self._exec("UPDATE room_members SET last_read=MAX(last_read, ?)"
                   " WHERE room_id=? AND user_id=?", up_to, room_id, user_id)

    def recent_conversations(self, user_id: int):
        """Latest message + unread count for every conversation of a user."""
        out = []
        rows = self._all(
            "SELECT conv, MAX(id) AS last_id FROM messages WHERE recipient_id IS NOT NULL"
            " AND (sender_id=? OR recipient_id=?) GROUP BY conv", user_id, user_id)
        unread = dict(self._all(
            "SELECT sender_id, COUNT(*) FROM messages WHERE recipient_id=? AND read_at IS NULL AND deleted=0"
            " GROUP BY sender_id", user_id))
        for r in rows:
            _, a, b = r["conv"].split(":")
            other = int(b) if int(a) == user_id else int(a)
            out.append(("u", other, r["last_id"], unread.get(other, 0)))
        rows = self._all(
            "SELECT m.room_id, m.last_read,"
            " (SELECT MAX(id) FROM messages WHERE room_id=m.room_id) AS last_id,"
            " (SELECT COUNT(*) FROM messages WHERE room_id=m.room_id AND id>m.last_read"
            "  AND sender_id<>? AND deleted=0) AS unread"
            " FROM room_members m JOIN rooms r ON r.id=m.room_id"
            " WHERE m.user_id=? AND r.deleted=0", user_id, user_id)
        for r in rows:
            out.append(("r", r["room_id"], r["last_id"], r["unread"]))
        return out

    def message_stats(self):
        return {
            "messages": self._one("SELECT COUNT(*) FROM messages")[0],
            "files": self._one("SELECT COUNT(*) FROM files WHERE complete=1 AND purged=0")[0],
            "files_bytes": self._one(
                "SELECT COALESCE(SUM(size),0) FROM files WHERE complete=1 AND purged=0")[0],
            "rooms": self._one("SELECT COUNT(*) FROM rooms WHERE deleted=0")[0],
        }

    # ------------------------------------------------------------------ files
    def add_file(self, file_id, name, size, uploader_id, path):
        self._exec("INSERT INTO files(id, name, size, uploader_id, path, created_at)"
                   " VALUES(?,?,?,?,?,?)", file_id, name, size, uploader_id, path, time.time())

    def complete_file(self, file_id):
        self._exec("UPDATE files SET complete=1 WHERE id=?", file_id)

    def delete_file_row(self, file_id):
        self._exec("DELETE FROM files WHERE id=?", file_id)

    def get_file(self, file_id):
        return self._one("SELECT * FROM files WHERE id=?", file_id)

    def can_access_file(self, user_id: int, file_id: str) -> bool:
        f = self.get_file(file_id)
        if not f:
            return False
        if f["uploader_id"] == user_id:
            return True
        return self._one(
            "SELECT 1 FROM messages WHERE file_id=? AND (sender_id=? OR recipient_id=? OR"
            " room_id IN (SELECT room_id FROM room_members WHERE user_id=?)) LIMIT 1",
            file_id, user_id, user_id, user_id) is not None

    # ------------------------------------------------------------ review / reports
    def user_conversations(self, user_id: int):
        """All conversations of a user: (conv key, message count, last time)."""
        return self._all(
            "SELECT conv, COUNT(*) AS n, MAX(created_at) AS last FROM messages"
            " WHERE (sender_id=? OR recipient_id=?) AND room_id IS NULL GROUP BY conv"
            " UNION ALL"
            " SELECT m.conv, COUNT(*), MAX(m.created_at) FROM messages m"
            " JOIN room_members rm ON rm.room_id=m.room_id AND rm.user_id=? GROUP BY m.conv"
            " ORDER BY 3 DESC", user_id, user_id, user_id)

    def activity(self, since: float):
        """Per-user activity since a time: messages sent, files sent, bytes uploaded."""
        msgs = dict(self._all("SELECT sender_id, COUNT(*) FROM messages WHERE created_at>=? AND kind<>'system'"
                              " GROUP BY sender_id", since))
        files = {r[0]: (r[1], r[2]) for r in self._all(
            "SELECT uploader_id, COUNT(*), COALESCE(SUM(size),0) FROM files WHERE created_at>=? AND complete=1"
            " GROUP BY uploader_id", since)}
        return msgs, files

    def room_activity(self, since: float, limit=20):
        return self._all("SELECT r.id, r.name, COUNT(m.id) AS n FROM rooms r JOIN messages m ON m.room_id=r.id"
                         " WHERE m.created_at>=? AND m.kind<>'system' AND r.deleted=0"
                         " GROUP BY r.id ORDER BY n DESC LIMIT ?", since, limit)

    def daily_counts(self, since: float):
        return self._all("SELECT date(created_at, 'unixepoch', 'localtime') AS day, COUNT(*) FROM messages"
                         " WHERE created_at>=? AND kind<>'system' GROUP BY day ORDER BY day", since)

    def storage_by_user(self):
        return dict((r[0], r[1]) for r in self._all(
            "SELECT uploader_id, COALESCE(SUM(size),0) FROM files WHERE complete=1 AND purged=0 GROUP BY uploader_id"))

    def record_download(self, file_id: str, user_id: int):
        self._exec("INSERT OR REPLACE INTO file_downloads VALUES(?,?,?)", file_id, user_id, time.time())

    def unclaimed_files(self, older_than: float):
        """Shared files older than the cutoff that nobody except the sender ever downloaded."""
        return self._all(
            "SELECT f.* FROM files f WHERE f.purged=0 AND f.complete=1 AND f.created_at<?"
            " AND NOT EXISTS (SELECT 1 FROM file_downloads d WHERE d.file_id=f.id AND d.user_id<>f.uploader_id)",
            older_than)

    def expired_files(self, now: float, default_days: float):
        """Shared files past the file retention of every chat they were posted in.

        A room can override the server default (rooms.file_retention_days); 0 means keep forever."""
        return self._all(
            "SELECT f.* FROM files f WHERE f.purged=0 AND f.complete=1"
            " AND EXISTS (SELECT 1 FROM messages m WHERE m.file_id=f.id)"
            " AND NOT EXISTS (SELECT 1 FROM messages m LEFT JOIN rooms r ON r.id=m.room_id WHERE m.file_id=f.id"
            "   AND (COALESCE(r.file_retention_days, ?) <= 0"
            "        OR f.created_at >= ? - COALESCE(r.file_retention_days, ?) * 86400))",
            default_days, now, default_days)

    def files_older_than(self, cutoff: float):
        return self._all("SELECT * FROM files WHERE purged=0 AND complete=1 AND created_at<?", cutoff)

    def set_room_retention(self, room_id: int, days):
        self._exec("UPDATE rooms SET file_retention_days=? WHERE id=? AND deleted=0", days, room_id)

    def storage_report(self, limit=25):
        """Who and which chats use the file storage (stored, not yet deleted files)."""
        live = "f.complete=1 AND f.purged=0"
        by_user = self._all(
            "SELECT u.id, u.display_name AS name, u.username, COUNT(f.id) AS files, COALESCE(SUM(f.size),0) AS bytes"
            f" FROM files f JOIN users u ON u.id=f.uploader_id WHERE {live}"
            " GROUP BY u.id ORDER BY bytes DESC")
        by_room = self._all(
            "SELECT r.id, r.name, r.file_retention_days AS retention, COUNT(DISTINCT f.id) AS files,"
            " COALESCE(SUM(f.size),0) AS bytes FROM rooms r JOIN messages m ON m.room_id=r.id"
            f" JOIN files f ON f.id=m.file_id WHERE {live} AND r.deleted=0 GROUP BY r.id ORDER BY bytes DESC")
        direct = self._one(
            "SELECT COUNT(DISTINCT f.id), COALESCE(SUM(f.size),0) FROM messages m JOIN files f ON f.id=m.file_id"
            f" WHERE {live} AND m.room_id IS NULL")
        largest = self._all(
            "SELECT f.id, f.name, f.size, f.created_at, u.display_name AS sender,"
            " (SELECT COALESCE(r.name, '') FROM messages m LEFT JOIN rooms r ON r.id=m.room_id"
            "  WHERE m.file_id=f.id LIMIT 1) AS room,"
            " (SELECT COUNT(*) FROM file_downloads d WHERE d.file_id=f.id AND d.user_id<>f.uploader_id) AS downloads"
            f" FROM files f LEFT JOIN users u ON u.id=f.uploader_id WHERE {live} ORDER BY f.size DESC LIMIT ?", limit)
        oldest = self._one(f"SELECT MIN(created_at) FROM files f WHERE {live}")[0]
        return {"by_user": [dict(r) for r in by_user], "by_room": [dict(r) for r in by_room],
                "direct": {"files": direct[0], "bytes": direct[1]},
                "largest": [dict(r) for r in largest], "oldest": oldest}

    def orphan_files(self, older_than: float):
        """Stored files no message points to any more (message deleted, or moved out by retention)."""
        return self._all("SELECT f.* FROM files f WHERE f.purged=0 AND f.complete=1 AND f.created_at<?"
                         " AND NOT EXISTS (SELECT 1 FROM messages m WHERE m.file_id=f.id)", older_than)

    def stale_uploads(self, older_than: float):
        return self._all("SELECT * FROM files WHERE complete=0 AND created_at<?", older_than)

    def mark_file_purged(self, file_id):
        self._exec("UPDATE files SET purged=1 WHERE id=?", file_id)

    # ---------------------------------------------------------- announcements
    def add_announcement(self, sender_id, title, body, target_kind="all", target_value="") -> int:
        cur = self._exec("INSERT INTO announcements(sender_id, title, body, target_kind, target_value,"
                         " created_at) VALUES(?,?,?,?,?,?)", sender_id, title, body, target_kind,
                         target_value, time.time())
        return cur.lastrowid

    def get_announcement(self, ann_id):
        return self._one("SELECT * FROM announcements WHERE id=?", ann_id)

    def recent_announcements(self, user_id: int, limit=300):
        """Latest announcements with a read flag for user_id (caller filters by target)."""
        return self._all(
            "SELECT a.*, (r.user_id IS NOT NULL) AS is_read FROM announcements a"
            " LEFT JOIN announcement_reads r ON r.announcement_id=a.id AND r.user_id=?"
            " ORDER BY a.id DESC LIMIT ?", user_id, limit)

    def mark_announcement_read(self, ann_id: int, user_id: int):
        self._exec("INSERT OR IGNORE INTO announcement_reads VALUES(?,?,?)",
                   ann_id, user_id, time.time())

    def announcement_read_count(self, ann_id: int) -> int:
        return self._one("SELECT COUNT(*) FROM announcement_reads WHERE announcement_id=?",
                         ann_id)[0]
