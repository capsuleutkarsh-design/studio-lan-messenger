"""How the server console talks to a server.

* LocalApi  - the console runs the server itself (in this process).
* RemoteApi - the server already runs elsewhere (Windows service, another PC); the console connects
              over TCP as an administrator and calls the same admin functions (ServerCore.ADMIN_API).

Both raise ValueError with a readable message when the server refuses something.
"""

import json
import os
import socket

from common import protocol as P


class LocalApi:
    remote = False

    def __init__(self, core):
        self.core = core
        self.label = "this PC"

    @property
    def running(self):
        return self.core.running

    def call(self, fn, *args, **kwargs):
        return self.core.call(getattr(self.core, fn), *args, **kwargs)

    def config(self):
        if self.running:
            return self.call("admin_config")
        cfg = self.core.config
        from server import archive
        from server import safecopy
        return dict(cfg.values) | {"_safe_copy_dir": safecopy.folder(cfg),
                                   "_data_dir": cfg.data_dir, "_storage_dir": cfg.storage_dir,
                                   "_backup_dir": cfg.backup_dir, "_db_path": cfg.db_path,
                                   "_log_dir": os.path.dirname(cfg.log_path),
                                   "_chat_log_dir": archive.log_dir(cfg)}

    def update_config(self, **values):
        if self.running:
            return self.call("admin_update_config", **values)
        self.core.config.update(**values)
        return self.config()

    def info(self):
        if self.running:
            return self.call("admin_server_info")
        from common.version import APP_VERSION
        from server.core import local_ips
        cfg = self.core.config
        return {"version": APP_VERSION, "ips": local_ips(), "tcp_port": cfg["tcp_port"],
                "discovery_port": cfg["discovery_port"], "server_name": cfg["server_name"],
                "started_at": None, "data_dir": cfg.data_dir, "storage_dir": cfg.storage_dir,
                "last_backup": None}

    def ping(self):
        return True

    def close(self):
        pass


def _pins_path():
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    return os.path.join(base, "LANMessenger", "console_pins.json")


def load_pins() -> dict:
    try:
        with open(_pins_path(), encoding="utf-8") as f:
            pins = json.load(f)
        return pins if isinstance(pins, dict) else {}
    except (OSError, ValueError):
        return {}


def save_pin(key, fingerprint):
    pins = load_pins()
    pins[key] = fingerprint
    try:
        os.makedirs(os.path.dirname(_pins_path()), exist_ok=True)
        with open(_pins_path(), "w", encoding="utf-8") as f:
            json.dump(pins, f, indent=2)
    except OSError:
        pass


class RemoteApi:
    remote = True

    def __init__(self, host, port):
        self.host, self.port = host, int(port)
        self.sock = None
        self.file = None
        self.rid = 0
        self.connected = False
        self.username = ""
        self.label = f"{host}:{port}"
        self.must_change = ""
        self.pin_mismatch = None          # (remembered, presented) fingerprints when the server changed

    def connect(self, username, password, trust=""):
        """Returns '' on success, else an error message.

        The server's certificate is remembered the first time (like the chat client does); a different
        one later is refused before the password is sent, unless `trust` names that new fingerprint."""
        self.close()
        self.pin_mismatch = None
        key = f"{self.host.lower()}:{self.port}"
        try:
            raw = socket.create_connection((self.host, self.port), timeout=10)
            try:
                from server.tls import client_context, peer_fingerprint
                self.sock = client_context().wrap_socket(raw)
                self.fingerprint = peer_fingerprint(self.sock)
            except OSError as e:       # never fall back to plain text: the password would cross the LAN
                raw.close()
                return (f"Could not open an encrypted connection to {self.host}:{self.port} ({e}). "
                        "The console only connects to servers with encryption (TLS) switched on.")
            self.sock.settimeout(900)      # 'Back up now' on a big database takes a while
            known = load_pins().get(key)
            if known and known != self.fingerprint and trust != self.fingerprint:
                self.pin_mismatch = (known, self.fingerprint)
                self.close()
                return "The server's identity has changed since the last connection."
            if known != self.fingerprint:
                save_pin(key, self.fingerprint)
            self.file = self.sock.makefile("rb")
            self.sock.sendall(P.encode({"op": "login", "username": username, "password": password,
                                        "console": True}))
            reply = json.loads(self.file.readline() or b"{}")
        except (OSError, ValueError) as e:
            self.close()
            return f"Cannot reach the server at {self.host}:{self.port} ({e})"
        if reply.get("op") != "login_ok":
            self.close()
            return reply.get("error", "Sign-in failed")
        self.connected = True
        self.username = username
        self.must_change = reply.get("must_change_password") or ""
        self.label = f"{reply.get('server_name', self.host)} ({self.host}:{self.port})"
        return ""

    @property
    def running(self):
        return self.connected

    def _request(self, op, **kw):
        if not self.connected:
            raise ConnectionError("Not connected to the server")
        self.rid += 1
        rid = self.rid
        try:
            self.sock.sendall(P.encode({"op": op, "rid": rid, **kw}))
            while True:
                line = self.file.readline()
                if not line:
                    raise ConnectionError("The server closed the connection")
                msg = json.loads(line)
                if msg.get("op") == "reply" and msg.get("rid") == rid:
                    break
        except (OSError, ValueError) as e:
            self.close()
            raise ConnectionError(f"Lost connection to the server ({e})") from e
        if not msg.get("ok"):
            raise ValueError(msg.get("error", "The server refused the request"))
        return msg

    def call(self, fn, *args, **kwargs):
        return self._request("admin_call", fn=fn, args=list(args), kwargs=kwargs).get("result")

    def config(self):
        return self.call("admin_config")

    def update_config(self, **values):
        return self.call("admin_update_config", **values)

    def info(self):
        return self.call("admin_server_info")

    def change_password(self, old, new):
        self._request("change_password", old=old, new=new)
        self.must_change = ""

    def ping(self):
        try:
            self._request("ping")
            return True
        except (ConnectionError, ValueError):
            return False

    def close(self):
        self.connected = False
        for obj in (self.file, self.sock):
            try:
                if obj:
                    obj.close()
            except OSError:
                pass
        self.file = self.sock = None
