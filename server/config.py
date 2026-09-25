import json
import logging
import os
import sys

from common.protocol import DISCOVERY_PORT, TCP_PORT
from common.files import replace_file

DEFAULTS = {
    "server_name": "Studio Messenger",
    "tcp_port": TCP_PORT,
    "tls_enabled": True,          # encrypt all chat, file and console connections
    "discovery_port": DISCOVERY_PORT,
    "storage_dir": "",            # empty = <data dir>/files
    "max_file_mb": 20480,         # 20 GB per file
    "file_retention_days": 3,     # shared files are deleted from the server after N days (0 = keep forever)
    "unclaimed_file_days": 0,     # delete files nobody downloaded after N days (0 = never)
    # pipeline / render-farm hook (HTTP)
    "api_enabled": False,
    "api_port": 5152,
    "api_key": "",
    "api_bot_name": "Pipeline Bot",
    "auto_department_rooms": True,   # one room per department, members kept in sync
    "auto_section_rooms": True,      # one room per department section
    "auto_all_room": False,          # an "All Studio" room with everyone
    # password rules
    "min_password_length": 6,
    "password_require_mix": True,    # at least one letter and one digit
    "password_max_age_days": 0,      # 0 = never expires
    # automatic database backup
    "backup_enabled": True,
    "backup_dir": "",                # empty = <data dir>/backups
    "backup_hour": 2,                # daily, at this hour (0-23)
    "backup_keep": 14,               # number of daily backups kept
    # readable chat backup + how long messages stay in the app (older ones live only in the chat logs)
    "chat_log_enabled": True,        # append each day's messages to text files every night
    "chat_log_dir": "",              # empty = <backup folder>/Chat logs
    "message_retention_days": 90,    # 0 = keep every message in the app forever
    # "buzz": shake the other person's window, even when they are busy
    "buzz_enabled": True,
    # admins may open any conversation from the console (users are told at sign-in)
    "admin_review_enabled": True,
}


def app_dir() -> str:
    """Folder of the running exe (frozen) or of the project (source)."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def default_data_dir() -> str:
    """Where the database, files and settings live.

    * running from source, or a portable copy that already has server_data next to the exe:
      <folder of the program>\\server_data
    * installed with the setup (Program Files is read-only): %ProgramData%\\LAN Messenger Server
    """
    portable = os.path.join(app_dir(), "server_data")
    if not getattr(sys, "frozen", False) or os.path.isdir(portable):
        return portable
    return os.path.join(os.environ.get("PROGRAMDATA", r"C:\ProgramData"), "LAN Messenger Server")


class ServerConfig:
    def __init__(self, data_dir: str):
        self.data_dir = data_dir
        self.path = os.path.join(data_dir, "config.json")
        self.values = dict(DEFAULTS)
        os.makedirs(data_dir, exist_ok=True)
        if os.path.exists(self.path):
            try:
                with open(self.path, encoding="utf-8") as f:
                    loaded = json.load(f)
                if not isinstance(loaded, dict):
                    raise ValueError("config is not a JSON object")
                self.values.update(loaded)
            except (OSError, ValueError) as e:
                # keep the damaged file for inspection and start with defaults
                broken = self.path + ".broken"
                try:
                    os.replace(self.path, broken)
                except OSError:
                    pass
                logging.getLogger("server").error("config.json was unreadable (%s); saved it as %s and "
                                                  "started with default settings", e, broken)
        self.save()

    def __getitem__(self, key):
        return self.values[key]

    def update(self, **kw):
        self.values.update({k: v for k, v in kw.items() if k in DEFAULTS})
        self.save()

    def save(self):
        tmp = self.path + ".tmp"          # write-then-rename: a power cut can't leave half a file
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.values, f, indent=2)
        replace_file(tmp, self.path)

    @property
    def db_path(self) -> str:
        return os.path.join(self.data_dir, "messenger.db")

    @property
    def storage_dir(self) -> str:
        return self.values["storage_dir"] or os.path.join(self.data_dir, "files")

    @property
    def backup_dir(self) -> str:
        return self.values["backup_dir"] or os.path.join(self.data_dir, "backups")

    @property
    def log_path(self) -> str:
        return os.path.join(self.data_dir, "server.log")
