"""Client settings, stored per Windows user in %APPDATA%\\LANMessenger.

An optional ``client_config.json`` next to the exe provides studio-wide
defaults (e.g. the server address) so IT can deploy one preconfigured folder.
"""

import base64
import ctypes
import ctypes.wintypes
import json
import os
import shutil
import sys

from common.protocol import TCP_PORT
from common.files import replace_file

DEFAULTS = {
    "server_host": "",
    "server_port": TCP_PORT,
    "username": "",
    "remember": False,
    "password_blob": "",
    "download_dir": os.path.join(os.path.expanduser("~"), "Downloads", "LAN Messenger"),
    "notifications": True,
    "sounds": True,
    "close_to_tray": True,
    "start_with_windows": False,
    "auto_away_minutes": 10,
    "downloaded": {},            # file_id -> local path
    "tls": True,                 # encrypted connection (must match the server)
    "pins": {},                  # "host:port" -> server certificate fingerprint (trust on first use)
    "theme": "midnight",         # midnight | light | classic
    "accent": "violet",
    "festival_themes": True,     # 15 Aug, 26 Jan and Christmas switch to their festival look for the day
    "recent_stickers": [],
    "directory_view": "cards",   # cards | chart | list
    "allow_buzz": True,
    "compact_mode": False,       # narrow window docked to the right edge of the screen
    "compact_on_top": False,     # ...and kept above other windows
}


def app_dir() -> str:
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def config_dir() -> str:
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    path = os.path.join(base, "LANMessenger")
    try:
        os.makedirs(path, exist_ok=True)
    except OSError:              # profile folder offline (redirected): work from the temp folder
        import tempfile
        path = os.path.join(tempfile.gettempdir(), "LANMessenger")
        os.makedirs(path, exist_ok=True)
    return path


class ClientConfig:
    def __init__(self):
        self.path = os.path.join(config_dir(), "client.json")
        self.values = dict(DEFAULTS)
        self.values["downloaded"] = {}
        self.values["pins"] = {}
        preset = os.path.join(app_dir(), "client_config.json")
        for path in (preset, self.path):
            if os.path.exists(path):
                try:
                    with open(path, encoding="utf-8") as f:
                        loaded = json.load(f)
                except (OSError, ValueError):
                    loaded = None
                if not isinstance(loaded, dict):          # damaged: keep a copy (it holds the server pins)
                    try:
                        shutil.copyfile(path, path + ".bad")
                    except OSError:
                        pass
                    continue
                for key, value in loaded.items():
                    if self._fits(key, value):
                        self.values[key] = value

    def _fits(self, key, value):
        """A saved value is used only if it has the type the app expects (a bad file must not crash us)."""
        default = self.values.get(key)
        if default is None or key not in self.values:
            return True
        if isinstance(default, bool):
            return isinstance(value, bool)
        if isinstance(default, (int, float)):
            return isinstance(value, (int, float)) and not isinstance(value, bool)
        return isinstance(value, type(default))

    def __getitem__(self, key):
        return self.values[key]

    def __setitem__(self, key, value):
        self.values[key] = value

    def get(self, key, default=None):
        return self.values.get(key, default)

    def save(self):
        """Best effort: a read-only or full disk must not stop signing in."""
        tmp = self.path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.values, f, indent=2)
            replace_file(tmp, self.path)
        except OSError as e:
            import logging
            logging.getLogger("client").warning("Could not save settings to %s: %s", self.path, e)

    # ---- remembered password (encrypted with Windows DPAPI, per Windows user)
    def set_password(self, password: str | None):
        self.values["password_blob"] = _protect(password) if password else ""

    def get_password(self) -> str:
        blob = self.values.get("password_blob")
        return _unprotect(blob) if blob else ""

    # ---- downloaded files cache
    def downloaded_path(self, file_id: str) -> str | None:
        path = self.values["downloaded"].get(file_id)
        return path if path and os.path.exists(path) else None

    def remember_download(self, file_id: str, path: str):
        self.values["downloaded"][file_id] = path
        self.save()


# ------------------------------------------------------------------ DPAPI
class _Blob(ctypes.Structure):
    _fields_ = [("cbData", ctypes.wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _crypt(data: bytes, protect: bool) -> bytes:
    buf = ctypes.create_string_buffer(data, len(data))
    blob_in = _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    blob_out = _Blob()
    fn = ctypes.windll.crypt32.CryptProtectData if protect else ctypes.windll.crypt32.CryptUnprotectData
    if not fn(ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)):
        raise OSError("DPAPI failed")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(blob_out.pbData)


def _protect(text: str) -> str:
    try:
        return "dpapi:" + base64.b64encode(_crypt(text.encode("utf-8"), True)).decode()
    except (OSError, AttributeError):
        return ""          # not on Windows: simply don't remember the password


def _unprotect(blob: str) -> str:
    try:
        if blob.startswith("dpapi:"):
            return _crypt(base64.b64decode(blob[6:]), False).decode("utf-8")
    except (OSError, AttributeError, ValueError):
        pass
    return ""


# ------------------------------------------------------- start with Windows
_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_RUN_NAME = "LANMessenger"


def set_autostart(enabled: bool):
    if sys.platform != "win32":
        return
    import winreg
    if getattr(sys, "frozen", False):
        cmd = f'"{sys.executable}" --minimized'
    else:
        pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
        cmd = f'"{pyw}" "{os.path.join(app_dir(), "run_client.pyw")}" --minimized'
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if enabled:
            winreg.SetValueEx(key, _RUN_NAME, 0, winreg.REG_SZ, cmd)
        else:
            try:
                winreg.DeleteValue(key, _RUN_NAME)
            except FileNotFoundError:
                pass
