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
    "auto_all_room": False,          # an "All Studio" room with everyone (department rooms: Departments page)
    # password rules
    "min_password_length": 4,
    "password_require_mix": False,   # at least one letter and one digit
    "password_block_weak": False,    # refuse 123456, password, the username, ...
    "force_password_change": False,  # new accounts and resets must choose their own password at first sign-in
    "password_max_age_days": 0,      # 0 = never expires
    "settings_version": 2,
    # automatic database backup
    "backup_enabled": True,
    "backup_dir": "",                # empty = <data dir>/backups
    "backup_hour": 2,                # daily, at this hour (0-23)
    "backup_keep": 14,               # number of daily backups kept
    # readable chat backup + how long messages stay in the app (older ones live only in the chat logs)
    "chat_log_enabled": True,        # append each day's messages to text files every night
    "chat_log_dir": "",              # empty = <backup folder>/Chat logs
    "log_dir": "",                   # server.log; empty = <data dir>
    "message_retention_days": 90,    # 0 = keep every message in the app forever
    # "buzz": shake the other person's window, even when they are busy
    "buzz_enabled": True,
    # people may change their own display name (Profile); admins can always change it in the console
    "allow_name_change": True,
    # admins may open any conversation from the console (users are told at sign-in)
    "admin_review_enabled": True,
}

# Settings of older versions that no longer do anything; dropped when an old config.json is read.
REMOVED = {"auto_department_rooms", "auto_section_rooms"}


def app_dir() -> str:
    """Folder of the running exe (frozen) or of the project (source)."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def default_data_dir() -> str:
    """Where the database, files and settings live.

    * running from source, or a portable copy that already has server_data next to the exe:
      <folder of the program>\\server_data
    * installed with the setup: the folder chosen during setup (saved in the registry),
      else %ProgramData%\\LAN Messenger Server
    """
    portable = os.path.join(app_dir(), "server_data")
    if not getattr(sys, "frozen", False) or os.path.isdir(portable):
        return portable
    return installed_data_dir() or os.path.join(os.environ.get("PROGRAMDATA", r"C:\ProgramData"),
                                                "LAN Messenger Server")


REG_KEY = r"Software\LAN Messenger Server"


def installed_data_dir() -> str:
    """The data folder picked in the server setup ('' if none / not Windows).

    "Install just for me" saves it for the user (HKCU), "for all users" for the PC (HKLM). A copy installed in
    the user's own folder looks in HKCU first."""
    try:
        import winreg
    except ImportError:
        return ""
    local = os.environ.get("LOCALAPPDATA", "")
    per_user = bool(local) and os.path.normcase(app_dir()).startswith(os.path.normcase(local))
    hives = (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE) if per_user else \
        (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER)
    for hive in hives:
        try:
            with winreg.OpenKey(hive, REG_KEY) as k:
                value, _ = winreg.QueryValueEx(k, "DataDir")
            if str(value).strip():
                return str(value).strip()
        except OSError:
            pass
    return ""


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
                self.values.update(self._checked({k: v for k, v in loaded.items() if k not in REMOVED}))
                self._upgrade(loaded)
            except (OSError, ValueError) as e:
                # keep the damaged file for inspection and start with defaults
                broken = self.path + ".broken"
                try:
                    os.replace(self.path, broken)
                except OSError:
                    pass
                logging.getLogger("server").error("config.json was unreadable (%s); saved it as %s and "
                                                  "started with default settings", e, broken)
        try:
            self.save()
        except OSError as e:           # read-only / full disk: run with what was read, report it
            logging.getLogger("server").error("Could not write %s: %s", self.path, e)

    def _upgrade(self, loaded):
        """Settings written by older versions: move untouched old defaults to the new, simpler ones."""
        try:
            version = int(float(loaded.get("settings_version") or 1))
        except (TypeError, ValueError, OverflowError):
            version = 1
        if version < 2:
            # 1.5.5: simpler sign-in. Only values still at the old defaults change; an admin's own choice stays.
            if loaded.get("min_password_length") == 6:
                self.values["min_password_length"] = 4
            if loaded.get("password_require_mix") is True:
                self.values["password_require_mix"] = False
            self.values["settings_version"] = 2

    def __getitem__(self, key):
        return self.values[key]

    def update(self, **kw):
        self.values.update(self._checked({k: v for k, v in kw.items() if k in DEFAULTS}))
        self.save()

    @staticmethod
    def _checked(values):
        """Coerce each setting to the type of its default; drop what can't be (a typo must not stop logins)."""
        out = {}
        for k, v in values.items():
            default = DEFAULTS.get(k)
            if k not in DEFAULTS or default is None:
                out[k] = v
                continue
            try:
                if isinstance(default, bool):
                    if isinstance(v, str):
                        word = v.strip().lower()
                        if word not in ("1", "true", "yes", "on", "0", "false", "no", "off"):
                            raise ValueError(v)          # a typo must not switch e.g. TLS off
                        v = word in ("1", "true", "yes", "on")
                    out[k] = bool(v)
                elif isinstance(default, int):
                    out[k] = int(float(v))
                    if k.endswith("_port") and not 1 <= out[k] <= 65535:
                        del out[k]
                        raise ValueError(v)
                    if out[k] < 0 or (k == "max_file_mb" and out[k] < 1):      # sizes, counts, days
                        del out[k]
                        raise ValueError(v)
                elif isinstance(default, float):
                    out[k] = float(v)
                elif isinstance(default, str):
                    out[k] = "" if v is None else str(v)
                elif isinstance(v, type(default)):
                    out[k] = v
                else:
                    raise TypeError
            except (TypeError, ValueError, OverflowError):
                logging.getLogger("server").error("Setting %s=%r is invalid; using the default %r", k, v, default)
        return out

    def save(self):
        tmp = self.path + ".tmp"          # write-then-rename: a power cut can't leave half a file
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.values, f, indent=2, ensure_ascii=False)     # folder names as typed (the setup reads them)
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
        return os.path.join(self.values.get("log_dir") or self.data_dir, "server.log")
