"""Messenger server core: asyncio TCP server, sessions and message routing.

The server runs its event loop in a background thread. Everything that
touches the database or the session tables happens on that thread; the admin
GUI uses :meth:`ServerCore.call` to run functions there.
"""

import asyncio
import base64
import binascii
import json
import logging
import os
import re
import secrets
import shutil
import socket
import sqlite3
import ssl
import sys
import threading
import time
import uuid

from common import protocol as P
from common.files import replace_file
from server import archive
from server.calendar import CalendarMixin
from server.planner import PlannerMixin
from server.config import ServerConfig
from server.db import ANNOUNCE_LEVELS, Database, check_label, clean_label, direct_key
from server.org import SECTION_SEP, Org, section_key

log = logging.getLogger("server")

ADMIN_SENDER_ID = 0          # sender id used for announcements made from the server console


STICKER_RE = re.compile(r"^[a-z0-9_]{1,40}/[0-9]{2,3}\.webp$")


class ClientError(Exception):
    """An error reported back to the client in the reply."""


class Session:
    def __init__(self, core, writer, user_id, token, addr):
        self.core = core
        self.writer = writer
        self.user_id = user_id
        self.token = token
        self.addr = addr
        self.since = time.time()
        self.queue: asyncio.Queue = asyncio.Queue()
        self.queued = 0                # bytes waiting to be written
        self.closed = False
        self.writer_task = None
        self.must_change = ""          # reason text while the user must change their password
        self.version = ""              # the Quillo version on that PC (shown in the console)

    def send(self, obj):
        self.send_bytes(P.encode(obj))

    def send_bytes(self, data: bytes):
        """Queue an already-encoded line (lets one encoding be shared by many recipients)."""
        if self.closed:
            return
        if self.queue.qsize() > 5000 or self.queued > MAX_QUEUED:       # client stopped reading
            log.warning("Dropping slow session of user %s", self.user_id)
            self.close()
            return
        self.queued += len(data)
        self.queue.put_nowait(data)

    async def writer_loop(self):
        try:
            while True:
                data = await self.queue.get()
                if data is None:
                    break
                # send everything that is waiting in one write (far fewer system calls under load)
                parts = [data]
                while not self.queue.empty() and len(parts) < 512:
                    nxt = self.queue.get_nowait()
                    if nxt is None:
                        if not self.writer.is_closing():
                            self.writer.write(b"".join(parts))
                            await self.writer.drain()
                        return
                    parts.append(nxt)
                if self.writer.is_closing():
                    break
                self.writer.write(b"".join(parts) if len(parts) > 1 else data)
                await self.writer.drain()
                self.queued -= sum(len(p) for p in parts)
        except (ConnectionError, OSError, AttributeError, RuntimeError):
            pass            # peer went away (TLS transports raise AttributeError after closing)
        finally:
            self.closed = True
            self.writer.close()

    def close(self):
        if not self.closed:
            self.closed = True
            self.queue.put_nowait(None)


MAX_QUEUED = 64 * 1024 * 1024      # bytes queued for one client before it counts as not reading
FIRST_LINE = 64 * 1024              # longest first line accepted before sign-in
MAX_CONNECTIONS = 4000              # open sockets in total
MAX_PER_IP = 300                    # open sockets from one PC (chat + transfers + screen share)
MAX_UPLOADS = 4                     # parallel uploads per user


def safe_filename(name: str) -> str:
    name = os.path.basename(str(name).replace("\\", "/")).strip()
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name)
    return name[:200] or "file"


class ServerCore(PlannerMixin, CalendarMixin):
    def __init__(self, data_dir: str):
        self.config = ServerConfig(data_dir)
        self.db: Database | None = None
        self.loop: asyncio.AbstractEventLoop | None = None
        self.thread: threading.Thread | None = None
        self.server = None
        self.udp_transport = None
        self.sessions: dict[int, set[Session]] = {}
        self.chosen_status: dict[int, str] = {}
        self.tokens: dict[str, int] = {}
        self.failed_logins: dict[tuple, list[float]] = {}
        self.shares: dict[str, dict] = {}
        self.listeners = []           # callables(event: str) - called on loop thread
        self.started_at = None
        self._ready = threading.Event()
        self._start_error = None
        self._org: Org | None = None
        self.tls_fingerprint = ""
        self._pub_cache: dict[int, dict] = {}
        self.handlers = {
            "ping": self.h_ping,
            "send": self.h_send,
            "history": self.h_history,
            "mark_read": self.h_mark_read,
            "typing": self.h_typing,
            "set_status": self.h_set_status,
            "create_room": self.h_create_room,
            "room_update": self.h_room_update,
            "room_leave": self.h_room_leave,
            "change_password": self.h_change_password,
            "announce": self.h_announce,
            "announcement_read": self.h_announcement_read,
            "search": self.h_search,
            "manage_user": self.h_manage_user,
            "admin_call": self.h_admin_call,
            "edit": self.h_edit,
            "delete_message": self.h_delete_message,
            "pin": self.h_pin,
            "pins": self.h_pins,
            "mute": self.h_mute,
            "read_by": self.h_read_by,
            "announcement_reads": self.h_announcement_reads,
            "screen_invite": self.h_screen_invite,
            "screen_answer": self.h_screen_answer,
            "screen_stop": self.h_screen_stop,
            "set_avatar": self.h_set_avatar,
            "get_avatar": self.h_get_avatar,
            "create_poll": self.h_create_poll,
            "vote": self.h_vote,
            "close_poll": self.h_close_poll,
            "react": self.h_react,
            "buzz": self.h_buzz,
            "set_name": self.h_set_name,
            "update_check": self.h_update_check,
            **{op: getattr(self, f"h_{op}") for op in self.PLANNER_HANDLERS},
            **{op: getattr(self, f"h_{op}") for op in self.CALENDAR_HANDLERS},
        }

    # ============================================================ lifecycle
    def start(self):
        """Start the server thread and wait until it is listening."""
        if self.thread and self.thread.is_alive():
            if self.running:
                return
            self.thread.join(10)             # a failed start is still cleaning up: let it finish first
            if self.thread.is_alive():
                raise RuntimeError("The server is still stopping - try again in a moment")
        self._ready.clear()
        self._start_error = None
        self.thread = threading.Thread(target=self._run, name="server-loop", daemon=True)
        self.thread.start()
        self._ready.wait(15)
        if self._start_error:
            raise self._start_error

    def _run(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        try:
            self.loop.run_until_complete(self._startup())
        except Exception as e:  # noqa: BLE001 - reported to the caller of start()
            log.exception("Server failed to start")
            self._start_error = e
            try:
                self.loop.run_until_complete(self._abort_startup())
            except Exception:  # noqa: BLE001
                pass
            self._ready.set()
            self.loop.close()
            return
        self._ready.set()
        try:
            self.loop.run_forever()
        finally:
            self.loop.run_until_complete(self._shutdown())
            self.loop.close()
            log.info("Server stopped")

    async def _startup(self):
        self.storage_error = ""
        try:
            os.makedirs(self.config.storage_dir, exist_ok=True)
        except OSError as e:          # e.g. a network share that is down: chat still works, uploads don't
            self.storage_error = f"File storage folder not available: {self.config.storage_dir} ({e.strerror or e})"
            log.error("%s - uploads are refused until it is back", self.storage_error)
        db_path = self.config.db_path
        if os.path.exists(db_path) and os.path.getsize(db_path) == 0:
            # an empty file would silently become a brand-new database (and admin/admin)
            raise sqlite3.DatabaseError("messenger.db is empty (0 bytes) - the file is damaged")
        self.db = Database(db_path)
        if self.db.user_count() == 0:
            uid = self.db.create_user("admin", "admin", "Administrator", is_admin=True, can_broadcast=True)
            self.db.set_must_change(uid, True)
            log.warning("Created default account admin / admin - it must be changed at first sign-in")
        self.calendar_setup()
        port = int(self.config["tcp_port"])
        # large backlog: after a server restart every client reconnects within a few seconds
        ssl_ctx = None
        self.tls_fingerprint = ""
        if self.config["tls_enabled"]:
            from server import tls
            cert, key = tls.ensure_certificate(os.path.join(self.config.data_dir, "tls"), self.config["server_name"])
            ssl_ctx, self.tls_fingerprint = tls.server_context(cert, key)
        else:
            log.warning("TLS is OFF: chat and files travel unencrypted")
        self.server = await asyncio.start_server(self._handle_conn, host="0.0.0.0", port=port,
                                                 limit=FIRST_LINE, backlog=2048, ssl=ssl_ctx,
                                                 ssl_handshake_timeout=20 if ssl_ctx else None)
        try:
            self.udp_transport, _ = await self.loop.create_datagram_endpoint(
                lambda: _DiscoveryProtocol(self), local_addr=("0.0.0.0", int(self.config["discovery_port"])),
                allow_broadcast=True)
        except OSError as e:
            owner = port_owner(int(self.config["discovery_port"]), udp=True)
            self.discovery_error = (f"UDP port {self.config['discovery_port']} is used by "
                                    f"{owner or 'another program'}")
            log.error("Automatic discovery is OFF: %s (%s). Clients must type the server address.",
                      self.discovery_error, e)
        else:
            self.discovery_error = ""
        self._org = None
        self.sync_auto_rooms()
        self.pipeline = None
        if self.config["api_enabled"] and self.config["api_key"]:
            from server.pipeline_api import PipelineApi
            try:
                self.pipeline = PipelineApi(self)
                await self.pipeline.start(int(self.config["api_port"]))
            except OSError as e:
                log.error("Pipeline API disabled (port %s busy?): %s", self.config["api_port"], e)
                self.pipeline = None
        self.maintenance_task = self.loop.create_task(self._maintenance())
        self.planner_task = self.loop.create_task(self._planner())
        self.started_at = time.time()
        log.info("Server '%s' listening on port %s (IPs: %s)",
                 self.config["server_name"], port, ", ".join(local_ips()))

    async def _abort_startup(self):
        """Undo a half-finished start (so 'Start' can be tried again)."""
        if getattr(self, "pipeline", None):
            await self.pipeline.stop()
        if getattr(self, "udp_transport", None):
            self.udp_transport.close()
        if self.server:
            self.server.close()
            await self.server.wait_closed()
            self.server = None
        if self.db:
            self.db.close()
            self.db = None

    async def _shutdown(self):
        for sessions in list(self.sessions.values()):
            for s in list(sessions):
                s.close()
                try:        # cut the connection now: a polite TLS close would wait for the client
                    s.writer.transport.abort()
                except Exception:  # noqa: BLE001
                    pass
        if self.udp_transport:
            self.udp_transport.close()
        if getattr(self, "pipeline", None):
            await self.pipeline.stop()
        if self.server:
            self.server.close()
            await self.server.wait_closed()
        self.maintenance_task.cancel()
        self.planner_task.cancel()
        await asyncio.sleep(0.1)
        self.db.close()
        self.sessions.clear()
        self.started_at = None

    def stop(self):
        if self.loop and self.loop.is_running():
            self.loop.call_soon_threadsafe(self.loop.stop)
            self.thread.join(10)

    @property
    def running(self) -> bool:
        return bool(self.loop and self.loop.is_running() and self.started_at)

    def call(self, fn, *args, **kwargs):
        """Run fn on the server thread and return its result (for the admin GUI)."""
        if not self.running:
            raise RuntimeError("Server is not running")

        async def runner():
            return fn(*args, **kwargs)
        slow = fn in (self.backup_now, self.chat_backup_now)
        return asyncio.run_coroutine_threadsafe(runner(), self.loop).result(900 if slow else 30)

    def _emit(self, event: str):
        for cb in self.listeners:
            try:
                cb(event)
            except Exception:  # noqa: BLE001
                log.exception("listener failed")

    async def _maintenance(self):
        last_purge = time.time()
        while True:
            await asyncio.sleep(60)
            hourly = time.time() - last_purge >= 3600
            if hourly:
                last_purge = time.time()
            if hourly:
                self._prune_login_failures()
            steps = [("statuses", self.expire_statuses, lambda: True),
                     ("backup", "backup", self._backup_due),
                     ("chat backup", None, lambda: archive.due(self.db, self.config)),
                     ("updates", self._check_updates, lambda: True),
                     ("file purge", self.purge_files, lambda: hourly)]
            for name, step, when in steps:     # each step on its own: one failure must not stop the rest
                try:
                    if when():
                        if step is None:
                            await self.chat_backup_async()
                        elif step == "backup":
                            await self.backup_async()
                        else:
                            step()
                except Exception:  # noqa: BLE001
                    log.exception("maintenance step '%s' failed", name)

    def purge_files(self):
        days = float(self.config["file_retention_days"] or 0)
        unclaimed = float(self.config["unclaimed_file_days"] or 0)
        rows = list(self.db.stale_uploads(time.time() - 86400))
        rows += list(self.db.expired_files(time.time(), days))        # rooms may keep files longer or shorter
        if unclaimed > 0:
            seen = {r["id"] for r in rows}
            rows += [r for r in self.db.unclaimed_files(time.time() - unclaimed * 86400) if r["id"] not in seen]
        seen = {r["id"] for r in rows}
        rows += [r for r in self.db.orphan_files(time.time() - 86400) if r["id"] not in seen]
        removed, freed = self._delete_stored(rows)
        if removed:
            log.info("Purged %d stored files (%s)", removed, P.human_size(freed))

    def _delete_stored(self, rows):
        """Delete these stored files from disk; the chats then show them as expired. Returns (count, bytes)."""
        removed = freed = 0
        for f in rows:
            try:
                if os.path.exists(f["path"]):
                    os.remove(f["path"])
            except OSError as e:
                log.warning("Could not delete %s: %s", f["path"], e)
                continue
            if f["complete"]:
                self.db.mark_file_purged(f["id"])
            else:
                self.db.delete_file_row(f["id"])
            removed += 1
            freed += f["size"] or 0
        return removed, freed

    # ========================================================= connections
    async def _handle_conn(self, reader, writer):
        addr = writer.get_extra_info("peername")
        ip = addr[0] if addr else "?"
        conns = self.__dict__.setdefault("_conns", {})
        if sum(conns.values()) >= MAX_CONNECTIONS or conns.get(ip, 0) >= MAX_PER_IP:
            log.warning("Refused a connection from %s (too many open connections)", ip)
            writer.close()
            return
        conns[ip] = conns.get(ip, 0) + 1
        try:
            await self._handle_conn_inner(reader, writer, addr)
        finally:
            conns[ip] -= 1
            if not conns[ip]:
                del conns[ip]

    async def _handle_conn_inner(self, reader, writer, addr):
        try:
            line = await asyncio.wait_for(reader.readline(), 30)
            if not line:
                return
            msg = P.decode(line)
            if not isinstance(msg, dict):
                return
            op = msg.get("op")
            if op == "login":
                await self._session_loop(reader, writer, msg, addr)
            elif op == "upload":
                await self._handle_upload(reader, writer, msg)
            elif op == "download":
                await self._handle_download(writer, msg)
            elif op in ("screen_pub", "screen_sub"):
                await self._handle_screen(reader, writer, msg)
            else:
                writer.write(P.encode({"op": "error", "error": "unknown op"}))
        except (asyncio.TimeoutError, ConnectionError, OSError, ValueError, asyncio.LimitOverrunError):
            pass
        except Exception:  # noqa: BLE001
            log.exception("Connection error from %s", addr)
        finally:
            try:
                writer.close()
            except Exception:  # noqa: BLE001
                pass

    # -------------------------------------------------------------- session
    async def _session_loop(self, reader, writer, msg, addr):
        username = str(msg.get("username", ""))[:100]
        password = str(msg.get("password", ""))[:200]
        ip = addr[0] if addr else "?"
        wait = self._login_blocked(ip, username)
        if wait:
            writer.write(P.encode({"op": "login_error", "error":
                                   f"Too many wrong passwords. Try again in {wait} seconds."}))
            await writer.drain()
            return
        inflight = self.__dict__.setdefault("_login_inflight", {})
        if inflight.get(ip, 0) >= 3:        # parallel attempts would all pass the throttle check above
            writer.write(P.encode({"op": "login_error", "error": "Too many sign-ins at once. Try again."}))
            await writer.drain()
            return
        inflight[ip] = inflight.get(ip, 0) + 1
        try:
            row = self.db.get_user_by_name(username)
            ok = bool(row) and await self.loop.run_in_executor(None, Database.check_password, row, password)
        finally:
            inflight[ip] -= 1
            if not inflight[ip]:
                del inflight[ip]
        if ok and row["disabled"]:
            ok, disabled = False, True
        else:
            disabled = False
        if not ok:
            log.warning("Failed login for '%s' from %s", username, ip)
            self._login_failed(ip, username)
            await asyncio.sleep(1)          # slows down password guessing
            error = "Invalid username or password"
            if disabled:
                error = "This account is disabled. Please contact your administrator."
            writer.write(P.encode({"op": "login_error", "error": error}))
            await writer.drain()
            return
        self.failed_logins.pop((ip, username.lower()), None)
        must_change = self.password_change_reason(row, password)
        reader._limit = P.MAX_LINE          # signed in: full-size messages from now on
        if msg.get("console"):
            await self._console_loop(reader, writer, row, addr, must_change)
            return

        uid = row["id"]
        token = secrets.token_hex(16)
        session = Session(self, writer, uid, token, addr)
        session.must_change = must_change
        session.version = str(msg.get("version") or "")[:20]
        session.writer_task = self.loop.create_task(session.writer_loop())
        self.tokens[token] = uid
        was_visible = self.visible_status(uid)
        self.sessions.setdefault(uid, set()).add(session)
        status = msg.get("status") if msg.get("status") in P.STATUSES else "online"
        self.chosen_status[uid] = status

        boot = self.bootstrap(uid, token)
        if must_change:
            boot["must_change_password"] = must_change
        session.send(boot)
        for sender_id, up_to in self.db.mark_delivered(uid).items():
            self.push_user(sender_id, {"op": "receipt", "conv": P.direct_conv(uid),
                                       "type": "delivered", "up_to": up_to})
        if self.visible_status(uid) != was_visible:
            self.broadcast_presence(uid)
        log.info("%s logged in from %s", row["username"], addr[0] if addr else "?")
        self._emit("sessions")

        try:
            while not session.closed:
                line = await asyncio.wait_for(reader.readline(), P.IDLE_TIMEOUT)
                if not line:
                    break
                try:
                    req = P.decode(line)
                except ValueError:
                    continue
                if isinstance(req, dict):
                    self._dispatch(session, req)
        except (asyncio.TimeoutError, ConnectionError, OSError):
            pass
        finally:
            session.close()
            self.tokens.pop(token, None)
            user_sessions = self.sessions.get(uid, set())
            user_sessions.discard(session)
            try:
                if not user_sessions:
                    self.sessions.pop(uid, None)
                    self.broadcast_presence(uid)
                    for sid, sh in list(self.shares.items()):
                        if uid in (sh["sharer"], sh["viewer"]):
                            self._end_share(sid)
                self.db.touch_last_seen(uid)
                self._org_user_changed(uid, last_seen=time.time())
            except Exception:  # noqa: BLE001 - db may already be closed on shutdown
                pass
            log.info("%s disconnected", row["username"])
            self._emit("sessions")

    async def _console_loop(self, reader, writer, row, addr, must_change):
        """A server-console connection: admin requests only, invisible to chat users."""
        if not row["is_admin"]:
            writer.write(P.encode({"op": "login_error",
                                   "error": "Only administrators can use the server console"}))
            await writer.drain()
            return
        session = Session(self, writer, row["id"], None, addr)
        session.must_change = must_change
        session.writer_task = self.loop.create_task(session.writer_loop())
        session.send({"op": "login_ok", "console": True, "server_name": self.config["server_name"],
                       "me": {"id": row["id"], "username": row["username"], "name": row["display_name"]},
                       "must_change_password": must_change})
        self.audit(row["username"], "console connected", addr[0] if addr else "?")
        try:
            while not session.closed:
                line = await asyncio.wait_for(reader.readline(), P.IDLE_TIMEOUT)
                if not line:
                    break
                try:
                    req = P.decode(line)
                except ValueError:
                    continue
                if isinstance(req, dict) and req.get("op") in ("admin_call", "ping", "change_password"):
                    self._dispatch(session, req)
        except (asyncio.TimeoutError, ConnectionError, OSError):
            pass
        finally:
            session.close()
            try:                             # let the last replies (e.g. "kicked") go out before the socket closes
                await asyncio.wait_for(asyncio.shield(session.writer_task), 5)
            except (asyncio.TimeoutError, asyncio.CancelledError, ConnectionError, OSError):
                pass

    def _dispatch(self, session, req):
        rid = req.get("rid")
        try:
            op = req.get("op")
            handler = self.handlers.get(op) if isinstance(op, str) else None
            if not handler:
                raise ClientError(f"Unknown request: {req.get('op')}")
            if session.must_change and req.get("op") not in ("change_password", "ping"):
                raise ClientError("Please change your password first")
            result = handler(session, req) or {}
            if rid is not None:
                session.send({"op": "reply", "rid": rid, "ok": True, **result})
        except ClientError as e:
            if rid is not None:
                session.send({"op": "reply", "rid": rid, "ok": False, "error": str(e)})
        except (ValueError, TypeError, KeyError, AttributeError, OverflowError, sqlite3.Error) as e:
            # bad input from the client (wrong types) or a validation message from the database layer
            text = str(e)
            if isinstance(e, ValueError) and "invalid literal" not in text and text:
                error = text
            else:
                error = "Invalid request"
                log.warning("Bad %s request from user %s: %s", req.get("op"), session.user_id, text[:200])
            if rid is not None:
                session.send({"op": "reply", "rid": rid, "ok": False, "error": error})
        except Exception:  # noqa: BLE001
            log.exception("Handler %s failed", req.get("op"))
            if rid is not None:
                session.send({"op": "reply", "rid": rid, "ok": False,
                              "error": "Something went wrong on the server - please try again"})

    # ======================================================= login throttle
    LOGIN_WINDOW = 300          # seconds
    MAX_USER_FAILS = 5          # per PC + username, then wait LOCK_SECONDS
    MAX_IP_FAILS = 20           # per PC (any username), then wait LOGIN_WINDOW
    LOCK_SECONDS = 60

    def _login_failed(self, ip, username):
        now = time.time()
        for key in ((ip, username.lower()), (ip, None)):
            self.failed_logins.setdefault(key, []).append(now)
        if len(self.failed_logins[(ip, username.lower())]) == self.MAX_USER_FAILS:
            self.audit("server", "sign-in locked", username, f"{self.MAX_USER_FAILS} wrong passwords from {ip}")

    def _login_blocked(self, ip, username) -> int:
        now = time.time()
        for key, limit, lock in (((ip, username.lower()), self.MAX_USER_FAILS, self.LOCK_SECONDS),
                                 ((ip, None), self.MAX_IP_FAILS, self.LOGIN_WINDOW)):
            times = [t for t in self.failed_logins.get(key, []) if now - t < self.LOGIN_WINDOW]
            if times:
                self.failed_logins[key] = times
            else:
                self.failed_logins.pop(key, None)
            if len(times) >= limit:
                remaining = int(times[-1] + lock - now)
                if remaining > 0:
                    return remaining
        return 0

    def _prune_login_failures(self):
        """Forget failed sign-ins older than the lock window (the table must not grow for ever)."""
        cutoff = time.time() - self.LOGIN_WINDOW
        for key in [k for k, v in self.failed_logins.items() if not v or v[-1] < cutoff]:
            del self.failed_logins[key]

    def _token_uid(self, token):
        """The user a transfer token belongs to - only for a live session that has no pending password change."""
        uid = self.tokens.get(token)
        if uid and any(s.token == token and not s.must_change for s in self.sessions.get(uid, ())):
            return uid
        return None

    # =========================================================== helpers
    def visible_status(self, uid: int) -> str:
        if not self.sessions.get(uid):
            return "offline"
        st = self.chosen_status.get(uid, "online")
        return "offline" if st == "invisible" else st

    def user_public(self, row) -> dict:
        uid = row["id"]
        static = self._pub_cache.get(uid)
        if static is None:                      # org-derived fields: cached until users/roles change
            org = self.org
            static = {"designation": org.designation(uid), "level": org.level(uid),
                      "manager_id": org.manager(uid), "can_broadcast": org.perms(uid)["announce"] != "none"}
            self._pub_cache[uid] = static
        return {
            "id": uid, "username": row["username"], "name": row["display_name"],
            "department": row["department"], "section": row["section"], "title": row["title"],
            **static,
            "status": self.visible_status(uid), "status_msg": row["status_msg"],
            "on_leave": self.on_leave_today(uid),
            "status_emoji": row["status_emoji"], "avatar": row["avatar_ver"],
            "birthday": (row["birthday"] or "")[-5:], "joined_on": row["joined_on"] or "",
            "is_admin": bool(row["is_admin"]), "last_seen": row["last_seen"],
        }

    def me_public(self, uid) -> dict:
        return self.user_public(self.db.get_user(uid)) | {
            "status": self.chosen_status.get(uid, "online"), "perms": self.org.perms(uid)}

    def room_public(self, row) -> dict:
        return {"id": row["id"], "name": row["name"], "topic": row["topic"],
                "owner_id": row["owner_id"], "members": self.db.room_member_ids(row["id"]),
                "auto": bool(row["auto_key"]), "file_retention_days": row["file_retention_days"]}

    # ---------------------------------------------------------- organisation
    @property
    def org(self) -> Org:
        if self._org is None:
            self._org = Org(self.db)
        return self._org

    def invalidate_org(self):
        self._org = None
        self._pub_cache.clear()

    def can_see(self, viewer, target) -> bool:
        if viewer == target or viewer == ADMIN_SENDER_ID:
            return True
        org = self.org
        if viewer in org._visible:
            return target in org._visible[viewer]
        co = () if org.perms(viewer)["see_all"] else self.db.room_co_members(viewer)
        return target in org.visible_to(viewer, co)

    def visible_users(self, viewer):
        # the org snapshot already holds every active user; no database read per login
        return [self.user_public(r) for r in self.org.users.values()
                if r["id"] != viewer and self.can_see(viewer, r["id"])]

    def _org_user_changed(self, uid, **fields):
        """Keep the in-memory snapshot in step with small per-user updates (status message, last seen)."""
        if self._org and uid in self._org.users:
            self._org.users[uid].update(fields)

    def push_directory(self):
        """Send every connected user their (possibly changed) user list and permissions."""
        self.invalidate_org()
        for uid in list(self.sessions):
            row = self.db.get_user(uid)
            if not row or row["disabled"] or row["deleted"]:
                continue
            self.push_user(uid, {"op": "directory", "me": self.me_public(uid),
                                 "users": self.visible_users(uid)})

    def sync_auto_rooms(self):
        """Keep the automatic rooms in step: "All Studio" (a setting) and every department/section with
        "Chat room" ticked on the Departments page. Nothing else gets a room by itself."""
        self.invalidate_org()
        org = self.org
        wanted = {}          # auto_key -> (name, topic, member ids)
        if self.config["auto_all_room"]:
            wanted["all"] = ("All Studio", "Everyone in the studio", list(org.users))
        depts = {d["id"]: d for d in self.db.list_departments()}
        for d in depts.values():
            if not d["has_room"]:
                continue
            parent = depts.get(d["parent_id"])
            if parent is None:            # a department (the key uses the id, so a rename keeps the room)
                ids = [u["id"] for u in org.users.values() if u["department"].strip().lower() == d["name"].lower()]
                wanted[f"dept#{d['id']}"] = (clean_label(d["name"]), f"Everyone in {d['name']}", ids)
            else:
                ids = [u["id"] for u in org.users.values()
                       if u["department"].strip().lower() == parent["name"].lower()
                       and u["section"].strip().lower() == d["name"].lower()]
                wanted[f"sect#{d['id']}"] = (clean_label(f"{parent['name']} · {d['name']}"),
                                             f"{d['name']} section of {parent['name']}", ids)
        existing = {r["auto_key"]: r for r in self.db.auto_rooms()}
        for key, room in existing.items():
            if key in wanted:
                continue
            if key == "all":
                self.admin_delete_room(room["id"])
                continue
            # unticked or department deleted: keep the room and its history as a normal room
            self.db.set_room_auto_key(room["id"], None)
            self.db.set_meta(f"released_room:{key}", room["id"])
            self._push_room(room["id"])
            log.info("Room '%s' is no longer automatic (kept as a normal room)", room["name"])
        for key, (name, topic, ids) in wanted.items():
            room = existing.get(key) or self._readopt_room(key)
            if room is None:
                room_id = self.db.create_room(name, None, ids, topic, auto_key=key)
                self._push_room(room_id)
                continue
            before = set(self.db.room_member_ids(room["id"]))
            changed = key not in existing           # a room picked up again is automatic once more
            if room["name"] != name or room["topic"] != topic:
                self.db.update_room(room["id"], name, topic)
                changed = True
            if set(ids) - before:
                self.db.add_room_members(room["id"], set(ids) - before)
                changed = True
            for uid in before - set(ids):
                self.db.remove_room_member(room["id"], uid)
                changed = True
            if changed:
                self._push_room(room["id"], extra_uids=before - set(ids))
        self.invalidate_org()

    def _readopt_room(self, key):
        """The room this department/section had before its "Chat room" was unticked (or before the managed
        departments existed), if it is still there as a normal room: ticking again continues that chat."""
        room_id = self.db.get_meta(f"released_room:{key}")
        room = self.db.get_room(int(room_id)) if room_id and room_id.isdigit() else None
        if room is None or room["auto_key"]:
            return None
        self.db.set_room_auto_key(room["id"], key)
        return self.db.get_room(room["id"])

    def _extras(self, row) -> dict:
        """Poll and reaction data of a message, shared by every viewer (see msg_for)."""
        out = {}
        if row["kind"] == "poll" and not row["deleted"]:
            poll = self.db.poll_for_message(row["id"])
            if poll:
                out["poll"] = (poll, self.db.poll_votes(poll["id"]))
        if not row["deleted"]:
            reacts = self.db.reactions(row["id"])
            if reacts:
                out["reactions"] = reacts
        return out

    def _poll_view(self, poll, votes, viewer_id):
        options = json.loads(poll["options"])
        voters = [[] for _ in options]
        for option, uid in votes:
            if 0 <= option < len(options):
                voters[option].append(uid)
        anonymous = bool(poll["anonymous"])
        return {"id": poll["id"], "question": poll["question"], "multi": bool(poll["multi"]),
                "anonymous": anonymous, "closed": bool(poll["closed"]), "creator_id": poll["creator_id"],
                "options": [{"text": t, "count": len(v), **({} if anonymous else {"voters": v[:200]})}
                            for t, v in zip(options, voters)],
                "mine": [i for i, v in enumerate(voters) if viewer_id in v],
                "total": len({uid for _, uid in votes})}

    @staticmethod
    def _reaction_view(reacts, viewer_id):
        groups = {}
        for emoji, uid in reacts:
            groups.setdefault(emoji, []).append(uid)
        return [{"emoji": e, "count": len(u), "mine": viewer_id in u, "users": u[:30]} for e, u in groups.items()]

    def msg_for(self, row, viewer_id: int, extras=None) -> dict:
        if row["room_id"]:
            conv = P.room_conv(row["room_id"])
        else:
            other = row["recipient_id"] if row["sender_id"] == viewer_id else row["sender_id"]
            conv = P.direct_conv(other)
        sender = row["sender_id"]
        d = {"id": row["id"], "conv": conv, "sender_id": sender, "kind": row["kind"],
             "sender_name": "Administrator" if sender == ADMIN_SENDER_ID else self._user_name(sender),
             "body": row["body"], "ts": row["created_at"],
             "delivered": row["delivered_at"] is not None, "read": row["read_at"] is not None}
        if row["file_id"]:
            d["file"] = {"id": row["file_id"], "name": row["file_name"], "size": row["file_size"],
                         "purged": bool(row["file_purged"])}
        if row["deleted"]:
            d["deleted"] = True
        if row["edited_at"]:
            d["edited"] = True
        if row["forwarded"]:
            d["forwarded"] = True
        extras = self._extras(row) if extras is None else extras
        if "poll" in extras:
            d["poll"] = self._poll_view(*extras["poll"], viewer_id)
        if "reactions" in extras:
            d["reactions"] = self._reaction_view(extras["reactions"], viewer_id)
        if row["reply_to"]:
            q = self.db.get_message(row["reply_to"])
            if q:
                snippet = "Deleted message" if q["deleted"] else "Sticker" if q["kind"] == "sticker" else (
                    f"📊 {q['body'][:130]}") if q["kind"] == "poll" else (
                    q["body"][:140] or (f"📎 {q['file_name']}" if q["file_name"] else ""))
                d["reply"] = {"id": q["id"], "sender_id": q["sender_id"],
                              "sender_name": self._user_name(q["sender_id"]), "snippet": snippet}
        return d

    def _participants(self, row) -> list[int]:
        if row["room_id"]:
            return self.db.room_member_ids(row["room_id"])
        return list({row["sender_id"], row["recipient_id"]})

    def _push_message_update(self, row):
        extras = self._extras(row)            # read once, then shaped per viewer ("mine")
        for uid in self._participants(row):
            if self.sessions.get(uid):
                self.push_user(uid, {"op": "message_update", "message": self.msg_for(row, uid, extras)})

    def _own_message(self, s, msg_id, allow_admin=False):
        row = self.db.get_message(int(msg_id or 0))
        if not row or row["deleted"] or row["kind"] == "system":
            raise ClientError("Message not found")
        if s.user_id not in self._participants(row):
            raise ClientError("Message not found")
        if row["sender_id"] != s.user_id:
            if not (allow_admin and self.db.get_user(s.user_id)["is_admin"]):
                raise ClientError("You can only change your own messages")
        return row

    def announcement_public(self, row, is_read=None) -> dict:
        kind, value = row["target_kind"], row["target_value"]
        label = "Everyone"
        if kind == "department":
            label = value
        elif kind == "section":
            dept, _, sect = value.partition(SECTION_SEP)
            names = {section_key(d, s): f"{d} · {s}" for d, s in self.org.sections()}
            label = names.get(value, f"{dept} · {sect}".title())
        elif kind == "team":
            label = f"{self._user_name(int(value))}'s team" if value.isdigit() else "Team"
        d = {"id": row["id"], "sender_id": row["sender_id"], "title": row["title"],
             "body": row["body"], "target_kind": kind, "target_value": value,
             "target_label": label, "department": value if kind == "department" else "",
             "ts": row["created_at"]}
        if is_read is not None:
            d["read"] = bool(is_read)
        return d

    def push_user(self, uid: int, obj, exclude: Session | None = None):
        for s in list(self.sessions.get(uid, ())):
            if s is not exclude:
                s.send(obj)

    def push_all(self, obj):
        for uid in list(self.sessions):
            self.push_user(uid, obj)

    def broadcast_presence(self, uid: int):
        row = self.db.get_user(uid)
        if row:
            data = P.encode({"op": "presence", "user_id": uid, "status": self.visible_status(uid),
                             "status_msg": row["status_msg"], "status_emoji": row["status_emoji"]})
            for viewer, sessions in list(self.sessions.items()):
                if viewer != uid and self.can_see(viewer, uid):
                    for sess in list(sessions):
                        sess.send_bytes(data)

    def bootstrap(self, uid: int, token: str) -> dict:
        recent = []
        for kind, target, last_id, unread in self.db.recent_conversations(uid):
            last = self.db.get_message(last_id) if last_id else None
            recent.append({"conv": f"{kind}:{target}", "unread": unread,
                           "last": self.msg_for(last, uid) if last else None})
        anns = [self.announcement_public(r, r["is_read"]) for r in self.db.recent_announcements(uid)
                if r["sender_id"] == uid or self.org.receives(r["target_kind"], r["target_value"], uid)]
        return {
            "op": "login_ok", "token": token, "server_name": self.config["server_name"],
            "protocol": P.PROTOCOL_VERSION,
            "me": self.me_public(uid),
            "users": self.visible_users(uid),
            "rooms": [self.room_public(r) for r in self.db.rooms_for_user(uid)],
            "recent": recent,
            "announcements": anns[:100],
            "muted": self.db.muted_convs(uid),
            "review_notice": bool(self.config["admin_review_enabled"]),
            "update": self.update_info(),
            "max_file_size": int(self.config["max_file_mb"]) * 1024 * 1024,
            "file_retention_days": float(self.config["file_retention_days"] or 0),
            **self.planner_boot(uid),
            **self.calendar_boot(uid),
            "buzz_enabled": bool(self.config["buzz_enabled"]),
            "allow_name_change": bool(self.config["allow_name_change"]),
        }

    def _user_name(self, uid):
        row = self.db.get_user(uid)
        return row["display_name"] if row else "Someone"

    def _room_system_message(self, room_id: int, actor_id: int, text: str):
        mid = self.db.add_message(P.room_conv(room_id), actor_id, text, kind="system", room_id=room_id)
        row = self.db.get_message(mid)
        data = P.encode({"op": "message", "message": self.msg_for(row, actor_id)})
        for member in self.db.room_member_ids(room_id):
            for sess in list(self.sessions.get(member, ())):
                sess.send_bytes(data)

    def _push_room(self, room_id: int, extra_uids=()):
        if self._org:
            self._org._visible.clear()      # room co-members affect restricted visibility
        room = self.db.get_room(room_id)
        members = self.db.room_member_ids(room_id)
        payload = {"op": "room", "room": self.room_public(room)}
        for uid in members:
            self.push_user(uid, payload)
        for uid in extra_uids:
            if uid not in members:
                self.push_user(uid, {"op": "room_removed", "room_id": room_id})

    def _require_room(self, room_id, uid):
        if not self.db.is_room_member(room_id, uid):
            raise ClientError("You are not a member of this room")
        return self.db.get_room(room_id)

    # =========================================================== handlers
    def h_ping(self, s, req):
        return {"time": time.time()}

    def h_send(self, s, req):
        try:
            kind, target = P.parse_conv(req.get("conv"))
        except ValueError as e:
            raise ClientError(str(e))
        text = req.get("text") or ""
        if not isinstance(text, str):
            raise ClientError("Invalid message")
        if len(text) > P.MAX_TEXT:
            raise ClientError(f"Message too long (max {P.MAX_TEXT:,} characters) — send it as a file instead")
        file_id = req.get("file_id")
        sticker = req.get("sticker")
        if sticker:
            if not isinstance(sticker, str) or not STICKER_RE.match(sticker):
                raise ClientError("Unknown sticker")
            text, file_id = sticker, None
        elif file_id:
            f = self.db.get_file(str(file_id))
            if not f or not f["complete"] or not self.db.can_access_file(s.user_id, f["id"]):
                raise ClientError("File not found on server")
            if f["purged"]:
                raise ClientError("That file was removed from the server")
        elif not text.strip():
            raise ClientError("Empty message")
        msg_kind = "sticker" if sticker else "file" if file_id else "text"
        conv_key = direct_key(s.user_id, target) if kind == "u" else P.room_conv(target)
        reply_to = req.get("reply_to")
        if reply_to:
            q = self.db.get_message(int(reply_to))
            if not q or q["conv"] != conv_key:
                raise ClientError("The message you reply to is not in this conversation")
            reply_to = q["id"]
        forwarded = bool(req.get("forwarded"))
        return self._post(s, kind, target, conv_key, text, msg_kind, file_id, reply_to, forwarded)

    def _post(self, s, kind, target, conv_key, text, msg_kind, file_id=None, reply_to=None, forwarded=False,
              on_created=None):
        """Store a new message and deliver it; on_created(message_id) runs before anyone sees it."""
        if kind == "u":
            other = self.db.get_user(target)
            if not other or other["deleted"] or not self.can_see(s.user_id, target):
                raise ClientError("User not found")
            online = bool(self.sessions.get(target))
            mid = self.db.add_message(conv_key, s.user_id, text, msg_kind, file_id, recipient_id=target,
                                      delivered=online, reply_to=reply_to, forwarded=forwarded)
            if on_created:
                on_created(mid)
            row = self.db.get_message(mid)
            if target != s.user_id:
                self.push_user(target, {"op": "message", "message": self.msg_for(row, target)})
        else:
            self._require_room(target, s.user_id)
            mid = self.db.add_message(conv_key, s.user_id, text, msg_kind, file_id, room_id=target,
                                      reply_to=reply_to, forwarded=forwarded)
            if on_created:
                on_created(mid)
            self.db.mark_room_read(target, s.user_id, mid)
            row = self.db.get_message(mid)
            # a room message looks the same to every member: build and encode it once
            data = P.encode({"op": "message", "message": self.msg_for(row, s.user_id)})
            for member in self.db.room_member_ids(target):
                if member != s.user_id:
                    for sess in list(self.sessions.get(member, ())):
                        sess.send_bytes(data)
        mine = self.msg_for(row, s.user_id)
        self.push_user(s.user_id, {"op": "message", "message": mine}, exclude=s)
        return {"message": mine}

    def _internal_conv(self, s, conv):
        try:
            kind, target = P.parse_conv(conv)
        except ValueError as e:
            raise ClientError(str(e))
        if kind == "u":
            return kind, target, direct_key(s.user_id, target)
        self._require_room(target, s.user_id)
        return kind, target, P.room_conv(target)

    def h_history(self, s, req):
        kind, target, key = self._internal_conv(s, req.get("conv"))
        limit = max(1, min(int(req.get("limit") or 50), 200))
        before = req.get("before")
        rows = self.db.history(key, int(before) if before else None, limit)
        return {"conv": req["conv"], "messages": [self.msg_for(r, s.user_id) for r in rows],
                "complete": len(rows) < limit}

    def h_mark_read(self, s, req):
        kind, target, key = self._internal_conv(s, req.get("conv"))
        latest = self.db.history(key, None, 1)
        up_to = min(int(req.get("up_to") or 0), latest[-1]["id"] if latest else 0)
        if kind == "u":
            if self.db.mark_direct_read(s.user_id, target, up_to):
                self.push_user(target, {"op": "receipt", "conv": P.direct_conv(s.user_id),
                                        "type": "read", "up_to": up_to})
        else:
            self.db.mark_room_read(target, s.user_id, up_to)
        self.push_user(s.user_id, {"op": "read_sync", "conv": req["conv"], "up_to": up_to}, exclude=s)

    def h_typing(self, s, req):
        try:
            kind, target = P.parse_conv(req.get("conv"))
        except ValueError:
            return
        if kind == "u":
            if self.can_see(s.user_id, target):
                self.push_user(target, {"op": "typing", "conv": P.direct_conv(s.user_id),
                                        "user_id": s.user_id})
        elif self.db.is_room_member(target, s.user_id):
            for member in self.db.room_member_ids(target):
                if member != s.user_id:
                    self.push_user(member, {"op": "typing", "conv": P.room_conv(target),
                                            "user_id": s.user_id})

    def h_set_status(self, s, req):
        status = req.get("status")
        if status in P.STATUSES:
            self.chosen_status[s.user_id] = status
        if "status_msg" in req:
            msg = str(req["status_msg"] or "")[:200]
            emoji = str(req.get("status_emoji") or "")[:16]
            if "<" in emoji or ">" in emoji:
                raise ClientError("Invalid status emoji")
            until = req.get("status_until")
            try:
                until = float(until) if until else None
            except (TypeError, ValueError):
                raise ClientError("Invalid 'clear after' time")
            if until and not time.time() < until < time.time() + 31 * 86400:
                raise ClientError("'Clear after' must be within the next month")
            self._set_status_msg(s.user_id, msg, emoji, until)
            return
        self.broadcast_presence(s.user_id)
        self._push_my_status(s.user_id, exclude=s)

    def _set_status_msg(self, uid, msg, emoji="", until=None):
        self.db.set_status_msg(uid, msg, emoji, until)
        self._org_user_changed(uid, status_msg=msg, status_emoji=emoji, status_until=until)
        self.broadcast_presence(uid)
        self._push_my_status(uid)

    def _push_my_status(self, uid, exclude=None):
        row = self.db.get_user(uid)
        self.push_user(uid, {"op": "my_status", "status": self.chosen_status.get(uid, "online"),
                             "status_msg": row["status_msg"], "status_emoji": row["status_emoji"],
                             "status_until": row["status_until"]}, exclude=exclude)

    def expire_statuses(self):
        for r in self.db.expired_statuses(time.time()):
            self._set_status_msg(r["id"], "", "", None)

    # -------------------------------------------------------- profile photos
    AVATAR_MAX = 400 * 1024

    def _avatar_path(self, uid):
        return os.path.join(self.config.data_dir, "avatars", f"{int(uid)}.img")

    def h_set_name(self, s, req):
        """People can change their own display name (unless the admin switched that off)."""
        if not self.config["allow_name_change"]:
            raise ClientError("Your administrator manages display names - ask them to change it")
        try:
            name = check_label(" ".join(str(req.get("name") or "").split()), "Name")
        except ValueError as e:
            raise ClientError(str(e))
        if len(name) < 2:
            raise ClientError("Please enter your name")
        row = self.db.get_user(s.user_id)
        if name == row["display_name"]:
            return {"name": name}
        key = ("rename", s.user_id)                  # a name is not a status: a few changes a day at most
        recent = [t for t in self.failed_logins.get(key, []) if time.time() - t < 3600]
        if len(recent) >= 5:
            raise ClientError("You changed your name a lot just now - try again later")
        self.failed_logins[key] = recent + [time.time()]
        self.db.update_user(s.user_id, display_name=name)
        self.audit(row["username"], "changed own name", row["display_name"], name)
        self.invalidate_org()
        self.push_directory()
        return {"name": name}

    def h_update_check(self, s, req):
        """'Check for updates' in the client: what the server offers right now (None = nothing)."""
        return {"update": self.update_info()}

    def h_set_avatar(self, s, req):
        data = req.get("data")
        if data is None:
            self._store_avatar(s.user_id, None)
            return {"avatar": 0}
        try:
            raw = base64.b64decode(str(data), validate=True)
        except (ValueError, binascii.Error):
            raise ClientError("Invalid picture")
        if len(raw) > self.AVATAR_MAX:
            raise ClientError("Picture too large (max 400 KB)")
        if not (raw.startswith(b"\x89PNG\r\n\x1a\n") or raw.startswith(b"\xff\xd8\xff")):
            raise ClientError("Only PNG or JPEG pictures")
        return {"avatar": self._store_avatar(s.user_id, raw)}

    def _store_avatar(self, uid, raw):
        path = self._avatar_path(uid)
        if raw is None:
            if os.path.exists(path):
                os.remove(path)
            ver = 0
        else:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            tmp = path + ".tmp"
            with open(tmp, "wb") as f:
                f.write(raw)
            replace_file(tmp, path)
            ver = int(time.time() * 1000) % 2_000_000_000
        self.db.set_avatar_ver(uid, ver)
        self._org_user_changed(uid, avatar_ver=ver)
        self._push_user(uid)
        return ver

    def _push_user(self, uid):
        """Send one user's public details to everyone who can see them (and to the user)."""
        row = self.db.get_user(uid)
        if not row:
            return
        data = P.encode({"op": "user", "user": self.user_public(row)})
        for viewer, sessions in list(self.sessions.items()):
            if viewer == uid or self.can_see(viewer, uid):
                for sess in list(sessions):
                    sess.send_bytes(data)

    def h_get_avatar(self, s, req):
        uid = int(req.get("user_id") or 0)
        if not self.can_see(s.user_id, uid):
            raise ClientError("User not found")
        row = self.db.get_user(uid)
        path = self._avatar_path(uid)
        if not row or not row["avatar_ver"] or not os.path.exists(path):
            raise ClientError("No picture")
        with open(path, "rb") as f:
            return {"user_id": uid, "ver": row["avatar_ver"], "data": base64.b64encode(f.read()).decode()}

    def admin_remove_avatar(self, uid):
        row = self.db.get_user(int(uid))
        if not row:
            raise ValueError("User not found")
        self._store_avatar(row["id"], None)
        self.audit(None, "profile photo removed", row["username"])

    # ---------------------------------------------------------------- polls
    def h_create_poll(self, s, req):
        try:
            kind, target = P.parse_conv(req.get("conv"))
        except ValueError as e:
            raise ClientError(str(e))
        question = str(req.get("question") or "").strip()[:300]
        options, seen = [], set()
        for o in req.get("options") or []:
            o = str(o).strip()[:100]
            if o and o.lower() not in seen:
                seen.add(o.lower())
                options.append(o)
        if not question:
            raise ClientError("Write a question")
        if not 2 <= len(options) <= 10:
            raise ClientError("A poll needs 2 to 10 different answers")
        conv_key = direct_key(s.user_id, target) if kind == "u" else P.room_conv(target)
        created = lambda mid: self.db.add_poll(mid, s.user_id, question, options,  # noqa: E731
                                               req.get("multi"), req.get("anonymous"))
        return self._post(s, kind, target, conv_key, question, "poll", on_created=created)

    def _poll_and_message(self, s, poll_id):
        poll = self.db.get_poll(int(poll_id or 0))
        row = self.db.get_message(poll["message_id"]) if poll else None
        if not row or row["deleted"] or s.user_id not in self._participants(row):
            raise ClientError("Poll not found")
        return poll, row

    def h_vote(self, s, req):
        poll, row = self._poll_and_message(s, req.get("poll_id"))
        if poll["closed"]:
            raise ClientError("This poll is closed")
        count = len(json.loads(poll["options"]))
        try:
            chosen = sorted({int(o) for o in req.get("options") or []})
        except (TypeError, ValueError):
            raise ClientError("Invalid answer")
        if any(not 0 <= o < count for o in chosen):
            raise ClientError("Invalid answer")
        if len(chosen) > 1 and not poll["multi"]:
            raise ClientError("Pick only one answer")
        self.db.set_votes(poll["id"], s.user_id, chosen)
        self._push_message_update(row)

    def h_close_poll(self, s, req):
        poll, row = self._poll_and_message(s, req.get("poll_id"))
        if poll["creator_id"] != s.user_id and not self.db.get_user(s.user_id)["is_admin"]:
            raise ClientError("Only the person who created the poll can close it")
        self.db.close_poll(poll["id"], not req.get("reopen"))
        self._push_message_update(row)

    # ----------------------------------------------------------------- buzz
    BUZZ_GAP = 20            # seconds between two buzzes to the same person
    ROOM_BUZZ_GAP = 60       # seconds between two buzzes to the same room (by anyone)

    def h_buzz(self, s, req):
        """Shake the other person's window and ring - also when they are on 'Do not disturb'."""
        if not self.config["buzz_enabled"]:
            raise ClientError("Buzz is switched off on this server")
        try:
            kind, target = P.parse_conv(req.get("conv"))
        except ValueError as e:
            raise ClientError(str(e))
        if kind == "u" and target == s.user_id:
            raise ClientError("You can't buzz yourself")
        times = self.__dict__.setdefault("_buzz_times", {})
        if kind == "r":                      # a whole room: everyone in it, at most once a minute per room
            self._require_room(target, s.user_id)
            key, gap, who = ("room", target), self.ROOM_BUZZ_GAP, "this room"
        else:
            key, gap, who = (s.user_id, target), self.BUZZ_GAP, "them"
        wait = gap - (time.time() - times.get(key, 0))
        if wait > 0:
            raise ClientError(f"{who.capitalize()} was just buzzed — wait {int(wait) + 1} s")
        conv_key = P.room_conv(target) if kind == "r" else direct_key(s.user_id, target)
        result = self._post(s, kind, target, conv_key, "", "buzz")
        times[key] = time.time()
        return result

    # ------------------------------------------------------------ reactions
    def h_react(self, s, req):
        row = self.db.get_message(int(req.get("message_id") or 0))
        if not row or row["deleted"] or row["kind"] == "system" or s.user_id not in self._participants(row):
            raise ClientError("Message not found")
        emoji = str(req.get("emoji") or "").strip()
        if not emoji or len(emoji) > 8 or any(ch.isascii() and (ch.isalnum() or ch in "<>&") for ch in emoji):
            raise ClientError("Invalid reaction")
        on = bool(req.get("on", True))
        if on and self.db.reaction_kinds(row["id"]) >= 20 and \
                emoji not in {e for e, _ in self.db.reactions(row["id"])}:
            raise ClientError("Too many different reactions on this message")
        self.db.set_reaction(row["id"], s.user_id, emoji, on)
        self._push_message_update(row)

    def _valid_user_ids(self, ids):
        valid = {r["id"] for r in self.db.list_users(include_disabled=False)}
        return [int(i) for i in ids or () if int(i) in valid]

    def h_create_room(self, s, req):
        if not self.org.perms(s.user_id)["create_rooms"]:
            raise ClientError("Your designation is not allowed to create rooms")
        members = [u for u in self._valid_user_ids(req.get("members")) if self.can_see(s.user_id, u)]
        room_id = self.db.create_room(str(req.get("name", "")), s.user_id, members,
                                      str(req.get("topic", "")))
        self._push_room(room_id)
        self._room_system_message(room_id, s.user_id, f"{self._user_name(s.user_id)} created the room")
        return {"room_id": room_id}

    def h_room_update(self, s, req):
        room_id = int(req.get("room_id") or 0)
        room = self._require_room(room_id, s.user_id)
        me = self.db.get_user(s.user_id)
        if room["auto_key"]:
            raise ClientError("This room is managed automatically from departments and sections")
        is_owner = room["owner_id"] == s.user_id or me["is_admin"]
        before = set(self.db.room_member_ids(room_id))
        if req.get("name") is not None or req.get("topic") is not None:
            if not is_owner:
                raise ClientError("Only the room owner can rename it")
            try:
                self.db.update_room(room_id, req.get("name"), req.get("topic"))
            except ValueError as e:
                raise ClientError(str(e))
        added = [u for u in self._valid_user_ids(req.get("add")) if u not in before and self.can_see(s.user_id, u)]
        removed = [int(u) for u in req.get("remove") or () if int(u) in before]
        if removed and not is_owner:
            raise ClientError("Only the room owner can remove members")
        new_owner = req.get("owner")
        if new_owner is not None:
            new_owner = int(new_owner)
            if not is_owner:
                raise ClientError("Only the room owner can hand the room over")
            if new_owner not in before or new_owner in removed:
                raise ClientError("The new owner must be a member of the room")
            self.db.set_room_owner(room_id, new_owner)
            self._room_system_message(room_id, s.user_id, f"{self._user_name(new_owner)} is now the room owner")
        if added:
            self.db.add_room_members(room_id, added)
        for uid in removed:
            self.db.remove_room_member(room_id, uid)
        self._push_room(room_id, extra_uids=removed)
        if added:
            names = ", ".join(self._user_name(u) for u in added)
            self._room_system_message(room_id, s.user_id, f"{self._user_name(s.user_id)} added {names}")
        if removed:
            names = ", ".join(self._user_name(u) for u in removed)
            self._room_system_message(room_id, s.user_id, f"{self._user_name(s.user_id)} removed {names}")

    def h_room_leave(self, s, req):
        room_id = int(req.get("room_id") or 0)
        if self._require_room(room_id, s.user_id)["auto_key"]:
            raise ClientError("You can't leave an automatic department/section room")
        room = self.db.get_room(room_id)
        self.db.remove_room_member(room_id, s.user_id)
        self.push_user(s.user_id, {"op": "room_removed", "room_id": room_id})
        if not self.db.room_member_ids(room_id):
            self.db.delete_room(room_id)         # the last person left: nobody can see it any more
            self.audit(self._user_name(s.user_id), "room closed", room["name"], "the last member left")
            return
        self._room_system_message(room_id, s.user_id, f"{self._user_name(s.user_id)} left the room")
        if room["owner_id"] == s.user_id:
            heir = self.db.longest_member(room_id)
            self.db.set_room_owner(room_id, heir)
            if heir:
                self._room_system_message(room_id, s.user_id, f"{self._user_name(heir)} is now the room owner")
        self._push_room(room_id)

    def h_change_password(self, s, req):
        row = self.db.get_user(s.user_id)
        old, new = str(req.get("old", ""))[:200], str(req.get("new", ""))[:200]
        key = ("change", s.user_id)
        recent = [t for t in self.failed_logins.get(key, []) if time.time() - t < self.LOGIN_WINDOW]
        self.failed_logins[key] = recent
        if len(recent) >= self.MAX_USER_FAILS:
            raise ClientError("Too many wrong passwords. Try again in a few minutes.")
        if not Database.check_password(row, old):
            recent.append(time.time())
            raise ClientError("Current password is wrong")
        if new == old:
            raise ClientError("The new password must be different from the current one")
        try:
            self.check_password_rules(new, row["username"])
            self.db.set_password(s.user_id, new)
        except ValueError as e:
            raise ClientError(str(e))
        s.must_change = ""
        for sess in list(self.sessions.get(s.user_id, ())):
            if sess is not s:               # other PCs signed in with the old password
                sess.send({"op": "kicked", "reason": "Your password was changed. Please sign in again."})
                sess.close()
        self.audit(row["username"], "password changed", row["username"])

    # ======================================================= password rules
    WEAK_PASSWORDS = {"admin", "password", "123456", "1234", "12345678", "qwerty", "welcome", "letmein",
                      "pass1234", "password1", "admin123", "changeme"}

    def check_password_rules(self, password: str, username: str = ""):
        cfg = self.config
        min_len = max(4, int(cfg["min_password_length"]))
        if len(password) < min_len:
            raise ValueError(f"Password must be at least {min_len} characters")
        if cfg["password_require_mix"] and not (any(c.isalpha() for c in password)
                                                and any(c.isdigit() for c in password)):
            raise ValueError("Password must contain both letters and numbers")
        if username.lower() == "admin" and password.lower() == "admin":
            raise ValueError("Choose a password other than 'admin' - everyone knows that one")
        if cfg["password_block_weak"]:
            if username and password.lower() == username.lower():
                raise ValueError("Password can't be the same as the username")
            if password.lower() in self.WEAK_PASSWORDS:
                raise ValueError("That password is too easy to guess")

    def password_change_reason(self, row, password) -> str:
        """Why this user must change their password now ('' = no need)."""
        if row["must_change_pw"]:
            return "Your password was set by an administrator. Please choose your own password."
        if row["username"].lower() == "admin" and password.lower() == "admin":
            return "The built-in admin password must be changed. Please choose your own password."
        if self.config["password_block_weak"] and (password.lower() in self.WEAK_PASSWORDS
                                                  or password.lower() == row["username"].lower()):
            return "Your password is too easy to guess. Please choose a stronger one."
        days = float(self.config["password_max_age_days"] or 0)
        if days > 0 and (row["pw_changed_at"] or row["created_at"]) < time.time() - days * 86400:
            return f"Your password is older than {int(days)} days. Please choose a new one."
        return ""

    # ================================================================ audit
    _actor = "server console"

    def audit(self, actor, action, target="", details=""):
        try:
            self.db.add_audit(actor or self._actor, action, target, details)
        except Exception:  # noqa: BLE001 - auditing must never break the action itself
            log.exception("audit failed")

    @staticmethod
    def announce_target(kind, department="", section="", sender_id=0):
        """Normalise a target to (kind, stored value)."""
        kind = kind or "all"
        if kind == "department":
            if not department.strip():
                raise ClientError("Choose a department")
            return kind, department.strip()
        if kind == "section":
            if not (department.strip() and section.strip()):
                raise ClientError("Choose a section")
            return kind, section_key(department, section)
        if kind == "team":
            return kind, str(sender_id)
        if kind != "all":
            raise ClientError("Unknown announcement target")
        return "all", ""

    def h_announce(self, s, req):
        kind, value = self.announce_target(req.get("target") or ("department" if req.get("department") else "all"),
                                           str(req.get("department", "")), str(req.get("section", "")), s.user_id)
        if not self.org.can_announce(s.user_id, kind, value):
            raise ClientError("Your designation is not allowed to send this announcement")
        return {"id": self.announce(s.user_id, str(req.get("title", "")), str(req.get("body", "")), kind, value)}

    def announce(self, sender_id, title, body, kind="all", value=""):
        """Store and deliver an announcement (permission checks are done by the caller)."""
        if not body.strip():
            raise ClientError("Announcement text is empty")
        if kind not in ANNOUNCE_LEVELS or kind == "none":
            kind, value = "all", ""
        title = title.strip() or "Announcement"
        ann_id = self.db.add_announcement(sender_id, title[:200], body[:P.MAX_TEXT], kind, value)
        payload = {"op": "announcement", "announcement": self.announcement_public(
            self.db.get_announcement(ann_id), False)}
        for uid in list(self.sessions):
            if uid == sender_id or self.org.receives(kind, value, uid):
                self.push_user(uid, payload)
        log.info("Announcement '%s' sent to %s", title, payload["announcement"]["target_label"])
        return ann_id

    def h_manage_user(self, s, req):
        """HR / IT: reset a password or disable an account from the client."""
        if not self.org.perms(s.user_id)["manage_users"]:
            raise ClientError("Your designation is not allowed to manage accounts")
        uid = int(req.get("user_id") or 0)
        target = self.db.get_user(uid)
        if not target or target["deleted"] or uid == s.user_id:
            raise ClientError("User not found")
        if target["is_admin"] and not self.db.get_user(s.user_id)["is_admin"]:
            raise ClientError("Only an administrator can change an administrator account")
        action = req.get("action")
        actor = self.db.get_user(s.user_id)["username"]
        try:
            if action == "reset_password":
                password = str(req.get("password", ""))
                self.check_password_rules(password, target["username"])
                self.db.set_password(uid, password, must_change=bool(self.config["force_password_change"]))
                self.kick(uid, "Your password was reset. Please sign in with the new one.")
                self.audit(actor, "password reset", target["username"], "from the client (HR/IT)")
            elif action == "disable":
                self._actor = actor
                try:
                    self.admin_update_user(uid, disabled=1)
                finally:
                    self._actor = ServerCore._actor
            else:
                raise ClientError("Unknown action")
        except ValueError as e:
            raise ClientError(str(e))

    # ------------------------------------------------ edit / delete / pin / mute
    def h_edit(self, s, req):
        row = self._own_message(s, req.get("id"))
        if row["kind"] in ("sticker", "poll", "buzz"):
            raise ClientError(f"A {row['kind']} can't be edited")
        text = req.get("text")
        if not isinstance(text, str):
            raise ClientError("Invalid message")
        if len(text) > P.MAX_TEXT:
            raise ClientError(f"Message too long (max {P.MAX_TEXT:,} characters)")
        if not text.strip() and not row["file_id"]:
            raise ClientError("A message can't be empty — delete it instead")
        self.db.edit_message(row["id"], text)
        self._push_message_update(self.db.get_message(row["id"]))

    def h_delete_message(self, s, req):
        row = self._own_message(s, req.get("id"), allow_admin=True)
        self.db.delete_message(row["id"])
        if row["sender_id"] != s.user_id:
            self.audit(self.db.get_user(s.user_id)["username"], "message deleted (moderation)",
                       self._user_name(row["sender_id"]), row["body"][:200])
        self._push_message_update(self.db.get_message(row["id"]))
        self._push_pins(row)

    def _pins_for(self, conv_key, viewer):
        out = []
        for mid in self.db.pinned_ids(conv_key):
            m = self.db.get_message(mid)
            if m and not m["deleted"]:
                out.append(self.msg_for(m, viewer))
        return out

    def _push_pins(self, row):
        for uid in self._participants(row):
            pins = self._pins_for(row["conv"], uid)
            self.push_user(uid, {"op": "pins", "conv": self.msg_for(row, uid)["conv"], "pins": pins})

    def h_pin(self, s, req):
        kind, target, key = self._internal_conv(s, req.get("conv"))
        row = self.db.get_message(int(req.get("message_id") or 0))
        if not row or row["conv"] != key or row["deleted"] or row["kind"] == "system":
            raise ClientError("Message not found")
        pinned = bool(req.get("pinned", True))
        if pinned and len(self.db.pinned_ids(key)) >= 50:
            raise ClientError("Too many pinned messages (max 50) — unpin some first")
        self.db.pin(key, row["id"], s.user_id, pinned)
        self._push_pins(row)
        if kind == "r":
            verb = "pinned" if pinned else "unpinned"
            self._room_system_message(target, s.user_id, f"{self._user_name(s.user_id)} {verb} a message")

    def h_pins(self, s, req):
        kind, target, key = self._internal_conv(s, req.get("conv"))
        return {"conv": req["conv"], "pins": self._pins_for(key, s.user_id)}

    def h_mute(self, s, req):
        conv = req.get("conv")
        kind, target, _key = self._internal_conv(s, conv)
        if kind == "u":
            other = self.db.get_user(target)
            if not other or other["deleted"]:
                raise ClientError("User not found")
        self.db.set_muted(s.user_id, conv, bool(req.get("muted", True)))
        self.push_user(s.user_id, {"op": "muted", "conv": conv, "muted": bool(req.get("muted", True))}, exclude=s)

    def h_read_by(self, s, req):
        """Who has read a room message (for 'Seen by')."""
        kind, target, key = self._internal_conv(s, req.get("conv"))
        if kind != "r":
            raise ClientError("Only for rooms")
        row = self.db.get_message(int(req.get("message_id") or 0))
        if not row or row["conv"] != key:
            raise ClientError("Message not found")
        members = [u for u in self.db.room_member_ids(target) if u != row["sender_id"]]
        readers = [u for u in self.db.room_readers(target, row["id"]) if u != row["sender_id"]]
        return {"read": readers, "total": len(members),
                "names": {str(u): self._user_name(u) for u in members}}

    def h_announcement_reads(self, s, req):
        """Who has read an announcement (its sender, or an admin)."""
        ann = self.db.get_announcement(int(req.get("id") or 0))
        me = self.db.get_user(s.user_id)
        if not ann or not (ann["sender_id"] == s.user_id or me["is_admin"]):
            raise ClientError("Not allowed")
        return self.announcement_reads(ann["id"])

    def announcement_reads(self, ann_id):
        ann = self.db.get_announcement(ann_id)
        readers = self.db.announcement_reader_ids(ann_id)
        recipients = [u for u in self.org.users if u != ann["sender_id"]
                      and self.org.receives(ann["target_kind"], ann["target_value"], u)]
        people = lambda ids: sorted(({"id": u, "name": self._user_name(u)} for u in ids),  # noqa: E731
                                    key=lambda d: d["name"].lower())
        return {"read": people([u for u in recipients if u in readers]),
                "unread": people([u for u in recipients if u not in readers])}

    def admin_announcements(self, limit=200):
        out = []
        for r in self.db._all("SELECT * FROM announcements ORDER BY id DESC LIMIT ?", limit):
            reads = self.announcement_reads(r["id"])
            out.append(self.announcement_public(r) | {"sender_name": self._user_name(r["sender_id"])
                                                       if r["sender_id"] else "Administrator",
                                                       "read_count": len(reads["read"]),
                                                       "total": len(reads["read"]) + len(reads["unread"])})
        return out

    def admin_announcement_reads(self, ann_id):
        return self.announcement_reads(int(ann_id))

    # ---------------------------------------------------- screen sharing
    # A share has a sharer (whose screen is shown) and a viewer. The sharer ALWAYS consents:
    # "offer" = sharer invites the viewer; "request" = viewer asks, sharer must accept.
    def _share(self, share_id, uid):
        share = self.shares.get(str(share_id))
        if not share or uid not in (share["sharer"], share["viewer"]):
            raise ClientError("This screen share has ended")
        return share

    def h_screen_invite(self, s, req):
        kind = req.get("kind")
        target = int(req.get("to") or 0)
        if kind not in ("offer", "request"):
            raise ClientError("Invalid request")
        if target == s.user_id or not self.db.get_user(target) or not self.can_see(s.user_id, target):
            raise ClientError("User not found")
        if not self.sessions.get(target):
            raise ClientError(f"{self._user_name(target)} is offline")
        now = time.time()
        for sid, sh in list(self.shares.items()):      # unanswered invites expire after 2 minutes
            if not sh["accepted"] and now - sh["created"] > 120:
                self.shares.pop(sid, None)
        if any(sh["inviter"] == s.user_id and sh["invited"] == target and not sh["accepted"]
               for sh in self.shares.values()):
            raise ClientError("You already invited them - wait for an answer")
        share_id = secrets.token_hex(8)
        sharer, viewer = (s.user_id, target) if kind == "offer" else (target, s.user_id)
        self.shares[share_id] = {"id": share_id, "sharer": sharer, "viewer": viewer, "accepted": False,
                                 "inviter": s.user_id, "invited": target,
                                 "created": time.time(), "subs": set(), "frame": None}
        self.push_user(target, {"op": "screen_invite", "share_id": share_id, "kind": kind,
                                "from": s.user_id, "from_name": self._user_name(s.user_id)})
        return {"share_id": share_id}

    def h_screen_answer(self, s, req):
        share = self._share(req.get("share_id"), s.user_id)
        if s.user_id != share["invited"] or share["accepted"]:
            raise ClientError("Invalid request")
        other = share["inviter"]
        if not req.get("accept"):
            self.shares.pop(share["id"], None)
            self.push_user(other, {"op": "screen_declined", "share_id": share["id"],
                                   "by_name": self._user_name(s.user_id)})
            return
        share["accepted"] = True
        payload = {"op": "screen_start", "share_id": share["id"], "sharer": share["sharer"],
                   "viewer": share["viewer"], "sharer_name": self._user_name(share["sharer"]),
                   "viewer_name": self._user_name(share["viewer"])}
        self.push_user(share["sharer"], payload)
        self.push_user(share["viewer"], payload)
        log.info("Screen share: %s -> %s", self._user_name(share["sharer"]), self._user_name(share["viewer"]))

    def h_screen_stop(self, s, req):
        share = self.shares.get(str(req.get("share_id")))
        if share and s.user_id in (share["sharer"], share["viewer"]):
            self._end_share(share["id"])

    def _end_share(self, share_id):
        share = self.shares.pop(share_id, None)
        if not share:
            return
        for uid in (share["sharer"], share["viewer"]):
            self.push_user(uid, {"op": "screen_stopped", "share_id": share_id})
        for w in list(share["subs"]):
            w.close()
        pub = share.get("pub")
        if pub:
            pub.close()

    async def _handle_screen(self, reader, writer, msg):
        """Relay connection: the sharer publishes JPEG frames, the viewer receives the latest one."""
        uid = self._token_uid(msg.get("token"))
        share = self.shares.get(str(msg.get("share_id", "")))
        role = msg.get("op")
        if (not uid or not share or not share["accepted"]
                or (role == "screen_pub" and uid != share["sharer"])
                or (role == "screen_sub" and uid != share["viewer"])):
            writer.write(P.encode({"ok": False, "error": "Not allowed"}))
            return
        writer.write(P.encode({"ok": True}))
        await writer.drain()
        if role == "screen_pub":
            share["pub"] = writer
            try:
                while share["id"] in self.shares:
                    head = await asyncio.wait_for(reader.readexactly(4), 60)
                    size = int.from_bytes(head, "big")
                    if size > 8 * 1024 * 1024:
                        break
                    frame = head + await reader.readexactly(size)
                    share["frame"] = frame
                    for sub in list(share["subs"]):
                        if sub.transport.get_write_buffer_size() < 2 * 1024 * 1024:   # slow viewer: skip frames
                            sub.write(frame)
            except (asyncio.IncompleteReadError, asyncio.TimeoutError, ConnectionError, OSError):
                pass
            self._end_share(share["id"])
        else:
            share["subs"].add(writer)
            if share["frame"]:
                writer.write(share["frame"])
            try:
                while share["id"] in self.shares:
                    if not await reader.read(1024):       # viewer closed
                        break
            except (ConnectionError, OSError):
                pass
            share["subs"].discard(writer)

    # ---------------------------------------------------- chat review (admin)
    def _review_allowed(self):
        if not self.config["admin_review_enabled"]:
            raise ValueError("Chat review is switched off in Settings > Privacy")

    def _conv_title(self, key):
        if key.startswith("r:"):
            room = self.db._one("SELECT name FROM rooms WHERE id=?", int(key[2:]))
            return f"# {room['name']}" if room else key
        _, a, b = key.split(":")
        return f"{self._user_name(int(a))}  ↔  {self._user_name(int(b))}"

    def admin_review_conversations(self, user_id):
        self._review_allowed()
        return [{"key": r[0], "title": self._conv_title(r[0]), "messages": r[1], "last": r[2]}
                for r in self.db.user_conversations(int(user_id))]

    def admin_review_history(self, key, before=None, limit=200):
        """Messages of one conversation (oldest first). Every call is written to the audit log."""
        self._review_allowed()
        rows = self.db.history(str(key), int(before) if before else None, min(int(limit), 1000))
        self.audit(None, "chat reviewed", self._conv_title(str(key)), f"{len(rows)} messages")
        out = []
        for r in rows:
            out.append({"id": r["id"], "ts": r["created_at"], "sender": self._user_name(r["sender_id"])
                        if r["sender_id"] else "Administrator", "kind": r["kind"],
                        "body": "(deleted)" if r["deleted"] else r["body"],
                        "file": r["file_name"] if r["file_id"] else "", "edited": bool(r["edited_at"])})
        return out

    # ---------------------------------------------------- reports (admin)
    def admin_report(self, days=30):
        since = time.time() - float(days) * 86400
        msgs, files = self.db.activity(since)
        storage = self.db.storage_by_user()
        users = [u for u in self.db.list_users() if u["username"] != self.BOT_USERNAME]
        people, depts = [], {}
        for u in users:
            sent = msgs.get(u["id"], 0)
            nfiles, nbytes = files.get(u["id"], (0, 0))
            people.append({"name": u["display_name"], "username": u["username"], "department": u["department"],
                           "section": u["section"], "messages": sent, "files": nfiles, "uploaded": nbytes,
                           "stored": storage.get(u["id"], 0), "last_seen": u["last_seen"],
                           "disabled": bool(u["disabled"])})
            d = depts.setdefault(u["department"] or "(none)", {"department": u["department"] or "(none)",
                                                               "users": 0, "active": 0, "messages": 0, "files": 0,
                                                               "uploaded": 0})
            d["users"] += 1
            d["active"] += 1 if sent or nfiles else 0
            d["messages"] += sent
            d["files"] += nfiles
            d["uploaded"] += nbytes
        rooms = [{"room": r["name"], "messages": r["n"]} for r in self.db.room_activity(since)]
        daily = [{"day": r[0], "messages": r[1]} for r in self.db.daily_counts(since)]
        return {"days": days, "total_messages": sum(msgs.values()),
                "total_files": sum(v[0] for v in files.values()),
                "total_uploaded": sum(v[1] for v in files.values()),
                "active_users": sum(1 for p in people if p["messages"] or p["files"]),
                "users": len(people), "people": sorted(people, key=lambda p: -p["messages"]),
                "departments": sorted(depts.values(), key=lambda d: -d["messages"]),
                "rooms": rooms, "daily": daily}

    # ---------------------------------------------------- client updates
    UPDATE_PREFIXES = ("Quillo-Client-Setup-", "LANMessenger-Client-Setup-")   # new name, and before 1.6.0

    @property
    def updates_dir(self):
        return os.path.join(self.config.data_dir, "updates")

    def latest_client_update(self):
        """(version string, path) of the newest client installer in the updates folder, or None."""
        try:
            names = os.listdir(self.updates_dir)
        except OSError:
            return None
        best = None
        for n in names:
            prefix = next((p for p in self.UPDATE_PREFIXES if n.startswith(p)), None)
            if prefix and n.lower().endswith(".exe"):
                ver = n[len(prefix):-4]
                try:
                    key = tuple(int(x) for x in ver.split("."))
                except ValueError:
                    continue
                if best is None or key > best[0]:
                    best = (key, ver, os.path.join(self.updates_dir, n))
        return (best[1], best[2]) if best else None

    def update_info(self):
        u = self.latest_client_update()
        if not u:
            return None
        return {"version": u[0], "size": os.path.getsize(u[1]), "name": os.path.basename(u[1])}

    def _check_updates(self):
        info = self.update_info()
        ver = info["version"] if info else None
        if ver != getattr(self, "_announced_update", None):
            self._announced_update = ver
            if info:
                log.info("Client update %s is available to clients", ver)
                self.push_all({"op": "update_available", "update": info})

    def admin_updates(self):
        os.makedirs(self.updates_dir, exist_ok=True)
        versions = {}
        for sessions in self.sessions.values():
            for s in sessions:
                versions[s.version or "older than 1.6.2"] = versions.get(s.version or "older than 1.6.2", 0) + 1
        return {"folder": self.updates_dir, "latest": self.update_info(), "versions": versions}

    # ---- holidays from the console
    def admin_holidays(self, year):
        return self.holiday_list(int(year))

    def admin_holiday_save(self, hid, day, name, observed=True):
        return self.holiday_save(hid, day, name, observed)

    def admin_holiday_delete(self, hid):
        self.holiday_delete(hid)

    def admin_holiday_observe(self, ids, observed):
        self.holiday_observe(ids, observed)

    def admin_holiday_add_year(self, year):
        return self.holiday_add_year(year)

    def admin_holidays_import(self, text):
        from common import ics
        try:
            events = ics.parse(str(text or ""))
        except Exception:  # noqa: BLE001
            raise ValueError("That doesn't look like an .ics calendar file")
        return self.holidays_import(events)

    def admin_check_updates(self):
        """After a new installer was put in the updates folder: tell signed-in clients now, not in a minute."""
        self._check_updates()
        return self.update_info()

    # ---------------------------------------------------- pipeline bot
    BOT_USERNAME = "pipeline-bot"

    def _bot_id(self):
        row = self.db.get_user_by_name(self.BOT_USERNAME)
        name = clean_label(self.config["api_bot_name"] or "Pipeline Bot")
        if row:
            if row["display_name"] != name:
                self.db.update_user(row["id"], display_name=name)
                self.push_directory()
            return row["id"]
        uid = self.db.create_user(self.BOT_USERNAME, secrets.token_urlsafe(24), name,
                                  title="Automated messages")
        self._after_user_change(uid)
        return uid

    def bot_message(self, to: str, text: str) -> str:
        """Send `text` from the pipeline bot to a user ('alice') or a room ('#Comp Team')."""
        bot = self._bot_id()
        text = text[:P.MAX_TEXT]
        to = to.strip()
        if to.startswith("#"):
            name = to[1:].strip().lower()
            room = next((r for r in self.db.list_rooms() if r["name"].lower() == name), None)
            if not room:
                raise ValueError(f"No room called {to[1:]!r}")
            mid = self.db.add_message(P.room_conv(room["id"]), bot, text, room_id=room["id"])
            row = self.db.get_message(mid)
            data = P.encode({"op": "message", "message": self.msg_for(row, bot)})
            for member in self.db.room_member_ids(room["id"]):
                for sess in list(self.sessions.get(member, ())):
                    sess.send_bytes(data)
            return f"room {room['name']}"
        user = self.db.get_user_by_name(to)
        if not user or user["disabled"]:
            raise ValueError(f"No user called {to!r}")
        online = bool(self.sessions.get(user["id"]))
        mid = self.db.add_message(direct_key(bot, user["id"]), bot, text, recipient_id=user["id"], delivered=online)
        self.push_user(user["id"], {"op": "message", "message": self.msg_for(self.db.get_message(mid), user["id"])})
        return f"user {user['username']}"

    def bot_announce(self, title, text, department=""):
        bot = self._bot_id()
        if department:
            return self.announce(bot, title, text, "department", department.strip())
        return self.announce(bot, title, text, "all", "")

    # functions the server console may call over the network (admins only)
    ADMIN_API = {"admin_users", "admin_create_user", "admin_update_user", "admin_delete_user", "admin_user_by_name",
                 "admin_roles", "admin_save_role", "admin_delete_role", "admin_org", "admin_rooms",
                 "admin_save_room", "admin_delete_room", "admin_sessions", "admin_kick", "admin_stats",
                 "admin_announce", "admin_audit", "admin_config", "admin_update_config", "admin_server_info",
                 "admin_log_tail", "backup_now", "purge_files", "sync_auto_rooms",
                 "admin_announcements", "admin_announcement_reads", "admin_review_conversations",
                 "admin_review_history", "admin_report", "admin_updates", "admin_remove_avatar",
                 "chat_backup_now", "admin_departments", "admin_save_department", "admin_set_department_room",
                 "admin_delete_department", "admin_storage", "admin_cleanup_files", "admin_set_room_retention",
                 "admin_check_updates", "admin_import_users", "admin_holidays", "admin_holiday_save",
                 "admin_holiday_delete", "admin_holiday_observe", "admin_holiday_add_year",
                 "admin_holidays_import"}

    def h_admin_call(self, s, req):
        row = self.db.get_user(s.user_id)
        if not row or not row["is_admin"] or row["disabled"] or row["deleted"]:
            if s.token is None:              # a console whose admin was disabled, deleted or demoted meanwhile
                s.send({"op": "kicked", "reason": "Your administrator access was removed"})
                s.close()
            raise ClientError("Administrator rights are required")
        fn = str(req.get("fn", ""))
        if fn not in self.ADMIN_API:
            raise ClientError(f"Unknown admin function {fn!r}")
        args = req.get("args") or []
        kwargs = req.get("kwargs") or {}
        if not isinstance(args, list) or not isinstance(kwargs, dict):
            raise ClientError("Invalid request")
        self._actor = row["username"]
        try:
            return {"result": getattr(self, fn)(*args, **kwargs)}
        finally:
            self._actor = ServerCore._actor

    def h_announcement_read(self, s, req):
        self.db.mark_announcement_read(int(req.get("id") or 0), s.user_id)

    def h_search(self, s, req):
        query = str(req.get("query", "")).strip()[:200]
        if len(query) < 2:
            raise ClientError("Type at least 2 characters")
        return {"messages": [self.msg_for(r, s.user_id) for r in self.db.search(s.user_id, query)]}

    # ======================================================= file transfer
    async def _handle_upload(self, reader, writer, msg):
        uid = self._token_uid(msg.get("token"))
        size = int(msg.get("size", -1))
        max_size = int(self.config["max_file_mb"]) * 1024 * 1024
        if not uid:
            writer.write(P.encode({"ok": False, "error": "Not logged in"}))
            return
        if size < 0 or size > max_size:
            writer.write(P.encode({"ok": False, "error": f"File too large (max {P.human_size(max_size)})"}))
            return
        name = safe_filename(msg.get("name", "file"))
        file_id = uuid.uuid4().hex
        folder = os.path.join(self.config.storage_dir, time.strftime("%Y-%m"))
        try:
            os.makedirs(folder, exist_ok=True)
            free = shutil.disk_usage(folder).free
        except OSError as e:
            log.error("File storage folder not available (%s): %s", folder, e)
            writer.write(P.encode({"ok": False, "error": "The server's file storage is not available. "
                                                         "Please tell your administrator."}))
            return
        uploads = self.__dict__.setdefault("_uploads", {})
        reserved = self.__dict__.setdefault("_reserved", [0])
        if uploads.get(uid, 0) >= MAX_UPLOADS:
            writer.write(P.encode({"ok": False, "error": f"At most {MAX_UPLOADS} uploads at a time - "
                                                         "wait for one to finish"}))
            return
        if free < size + reserved[0] + 200 * 1024 * 1024:          # keep 200 MB spare for the database
            log.error("Not enough disk space for '%s' (%s needed, %s free)", name, P.human_size(size),
                      P.human_size(free))
            writer.write(P.encode({"ok": False, "error": "The server is out of disk space. "
                                                         "Please tell your administrator."}))
            return
        path = os.path.join(folder, file_id)
        self.db.add_file(file_id, name, size, uid, path)
        uploads[uid] = uploads.get(uid, 0) + 1
        reserved[0] += size
        try:
            await self._receive_upload(reader, writer, file_id, name, size, uid, path)
        finally:
            uploads[uid] -= 1
            reserved[0] -= size

    async def _receive_upload(self, reader, writer, file_id, name, size, uid, path):
        writer.write(P.encode({"ok": True, "file_id": file_id}))
        await writer.drain()
        remaining = size
        try:
            with open(path, "wb") as fh:
                while remaining:
                    chunk = await asyncio.wait_for(reader.read(min(P.CHUNK, remaining)), 120)
                    if not chunk:
                        raise ConnectionError("upload interrupted")
                    fh.write(chunk)
                    remaining -= len(chunk)
        except BaseException:
            try:
                os.remove(path)
            except OSError:
                pass
            self.db.delete_file_row(file_id)
            raise
        self.db.complete_file(file_id)
        writer.write(P.encode({"ok": True, "done": True, "file_id": file_id}))
        await writer.drain()
        log.info("Stored file '%s' (%s) from user %s", name, P.human_size(size), uid)

    async def _handle_download(self, writer, msg):
        uid = self._token_uid(msg.get("token"))
        file_id = str(msg.get("file_id", ""))
        if file_id == "client-update" and uid:
            u = self.latest_client_update()
            if not u:
                writer.write(P.encode({"ok": False, "error": "No update available"}))
                return
            size = os.path.getsize(u[1])
            offset = max(0, min(int(msg.get("offset") or 0), size))
            writer.write(P.encode({"ok": True, "size": size, "name": os.path.basename(u[1]), "offset": offset}))
            await writer.drain()
            with open(u[1], "rb") as fh:
                await self.loop.sendfile(writer.transport, fh, offset, size - offset)
            await writer.drain()
            return
        f = self.db.get_file(file_id) if uid else None
        error = None
        if not uid:
            error = "Not logged in"
        elif not f or not f["complete"] or not self.db.can_access_file(uid, file_id):
            error = "File not found"
        elif f["purged"] or not os.path.exists(f["path"]):
            error = "File was removed from the server"
        if error:
            writer.write(P.encode({"ok": False, "error": error}))
            await writer.drain()
            return
        offset = max(0, min(int(msg.get("offset") or 0), f["size"]))
        self.db.record_download(file_id, uid)
        writer.write(P.encode({"ok": True, "size": f["size"], "name": f["name"], "offset": offset}))
        await writer.drain()
        with open(f["path"], "rb") as fh:
            await self.loop.sendfile(writer.transport, fh, offset, f["size"] - offset)
        await writer.drain()

    # ======================================================== admin API
    # These run on the loop thread via call().
    def admin_users(self):
        roles = {r["id"]: r["name"] for r in self.db.list_roles()}
        rows = self.db.list_users()
        names = {r["id"]: r["display_name"] for r in rows}
        out = []
        for r in rows:
            d = dict(r)
            d.pop("pw_hash")
            d.pop("pw_salt")
            d["designation"] = roles.get(r["role_id"], "")
            d["manager_name"] = names.get(r["manager_id"], "")
            d["status"] = self.visible_status(r["id"]) if not r["disabled"] else "disabled"
            d["sessions"] = len(self.sessions.get(r["id"], ()))
            out.append(d)
        return out

    def _resolve_user_fields(self, uid, fields):
        """Accept 'designation' (name) and 'reports_to' (username) as used by CSV import."""
        if "designation" in fields:
            name = (fields.pop("designation") or "").strip()
            role = self.db.get_role_by_name(name) if name else None
            if name and not role:
                raise ValueError(f"Unknown designation '{name}'")
            fields["role_id"] = role["id"] if role else None
        if "reports_to" in fields:
            uname = (fields.pop("reports_to") or "").strip()
            mgr = self.db.get_user_by_name(uname) if uname else None
            if uname and not mgr:
                raise ValueError(f"Unknown 'reports to' user '{uname}'")
            fields["manager_id"] = mgr["id"] if mgr else None
        if fields.get("manager_id") and uid is not None:
            self.invalidate_org()
            if self.org.would_cycle(uid, int(fields["manager_id"])):
                raise ValueError("That reporting line would make a loop (someone reporting to their own team)")
        if "department" in fields or "section" in fields:
            self._check_department_fields(uid, fields)
        return fields

    def _check_department_fields(self, uid, fields):
        """Departments and sections come from the Departments page: accept only those (any capitalisation)
        and store the spelling used there. Empty is fine (no department)."""
        before = self.db.get_user(uid) if uid is not None else None
        explicit_section = "section" in fields
        dept_name = str(fields.get("department", before["department"] if before else "") or "").strip()
        sect_name = str(fields.get("section", before["section"] if before else "") or "").strip()
        if not dept_name:
            if sect_name and explicit_section:
                raise ValueError("Choose a department before choosing a section")
            fields["department"], fields["section"] = "", ""
            return
        dept = self.db.find_department(dept_name)
        if not dept:
            raise ValueError(f"Unknown department '{dept_name}' - add it on the Departments page first")
        fields["department"] = dept["name"]
        if not sect_name:
            fields["section"] = ""
            return
        sect = self.db.find_department(sect_name, dept["id"])
        if not sect:
            if not explicit_section and before:
                fields["section"] = ""        # moved to another department: the old section doesn't apply
                return
            raise ValueError(f"Unknown section '{sect_name}' in {dept['name']} - add it on the Departments "
                             "page first")
        fields["section"] = sect["name"]

    def _after_user_change(self, uid=None):
        if uid is not None:
            row = self.db.get_user(uid)
            if row and (row["disabled"] or row["deleted"]):
                self.kick(uid)
        self.sync_auto_rooms()
        self.push_directory()
        self._emit("users")

    IMPORT_FIELDS = ("display_name", "department", "section", "designation", "reports_to", "title",
                     "birthday", "joined_on", "employee_id")

    @staticmethod
    def _new_password():
        """A random first password that is easy to read out and type (no 0/O, 1/l/I)."""
        import secrets
        letters = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKMNPQRSTUVWXYZ"
        return "".join(secrets.choice(letters) for _ in range(5)) + "-" + \
            "".join(secrets.choice("23456789") for _ in range(4))

    def admin_import_users(self, rows, dry_run=False):
        """The Excel/CSV user list in one go: new usernames are created, existing ones updated.

        Rows are dicts (username, display_name, department, section, designation, reports_to, title, password,
        birthday, joined_on, employee_id). An empty cell keeps the current value; '-' clears it. Departments
        and sections that don't exist yet are created. New people without a password get a random one
        (returned in 'passwords') and must change it at first sign-in. dry_run: only report what would happen."""
        report = {"created": [], "updated": [], "unchanged": 0, "errors": [], "passwords": [],
                  "new_departments": []}
        clean = []
        for n, raw in enumerate(rows, 2):                     # Excel row numbers: row 1 is the header
            row = {str(k).strip().lower(): ("" if v is None else str(v).strip()) for k, v in raw.items() if k}
            if not any(row.values()):
                continue
            if not row.get("username"):
                report["errors"].append(f"Row {n}: the username is empty")
                continue
            clean.append((n, row))
        # departments and sections first (created in a dry run too, as a list only)
        for n, row in clean:
            dept, sect = row.get("department", ""), row.get("section", "")
            if dept and dept != "-" and not self.db.find_department(dept):
                report["new_departments"].append(dept)
                if not dry_run:
                    self.admin_save_department(name=dept)
            if dept and dept != "-" and sect and sect != "-":
                parent = self.db.find_department(dept)
                if (not parent or not self.db.find_department(sect, parent["id"])) \
                        and f"{dept} / {sect}" not in report["new_departments"]:
                    report["new_departments"].append(f"{dept} / {sect}")
                    if not dry_run and parent:
                        self.admin_save_department(name=sect, parent_id=parent["id"])
        report["new_departments"] = sorted(set(report["new_departments"]), key=str.lower)
        # people: pass 1 without "reports to" (leads may come later in the sheet), pass 2 links them
        for n, row in clean:
            existing = self.db.get_user_by_name(row["username"])
            fields = {}
            for key in self.IMPORT_FIELDS:
                if key == "reports_to" or key not in row or row[key] == "":
                    continue
                fields[key] = "" if row[key] == "-" else row[key]
            try:
                from server.db import check_date
                if fields.get("birthday"):
                    fields["birthday"] = check_date(fields["birthday"], "Birthday", year_optional=True)
                if fields.get("joined_on"):
                    fields["joined_on"] = check_date(fields["joined_on"], "Joining date")
                # checked here as well, so the preview (dry run) already shows the rows that would fail
                if fields.get("designation") and not self.db.get_role_by_name(fields["designation"]):
                    raise ValueError(f"Unknown designation '{fields['designation']}'")
                reports_to = row.get("reports_to", "")
                if reports_to and reports_to != "-" and not self.db.get_user_by_name(reports_to) \
                        and reports_to.lower() not in {r["username"].lower() for _n, r in clean}:
                    raise ValueError(f"Unknown 'reports to' user '{reports_to}'")
                if not existing:
                    name = check_label(row["username"], "Username")
                    if not name or any(ch.isspace() for ch in name):
                        raise ValueError("The username can't be empty or contain spaces")
                    if row.get("password"):
                        self.check_password_rules(row["password"], name)
                if existing:
                    before = dict(existing)
                    before["designation"] = (self.db.get_role(existing["role_id"]) or {"name": ""})["name"] \
                        if existing["role_id"] else ""
                    changed = {k: v for k, v in fields.items() if str(before.get(k) or "").lower() != v.lower()}
                    if row.get("password"):
                        changed["password"] = row["password"]
                    if changed:
                        if not dry_run:
                            self.admin_update_user(existing["id"], **changed)
                        report["updated"].append(row["username"])
                    else:
                        report["unchanged"] += 1
                else:
                    password = row.get("password") or self._new_password()
                    if not dry_run:
                        self.admin_create_user(must_change=True if not row.get("password") else None,
                                               username=row["username"], password=password, **fields)
                    report["created"].append(row["username"])
                    if not row.get("password"):
                        report["passwords"].append({"username": row["username"],
                                                    "name": fields.get("display_name") or row["username"],
                                                    "password": password})
            except ValueError as e:
                report["errors"].append(f"Row {n} ({row['username']}): {e}")
        if not dry_run:
            for n, row in clean:
                if row.get("reports_to"):
                    user = self.db.get_user_by_name(row["username"])
                    try:
                        if user:
                            self.admin_update_user(user["id"], reports_to="" if row["reports_to"] == "-"
                                                   else row["reports_to"])
                    except ValueError as e:
                        report["errors"].append(f"Row {n} ({row['username']}): {e}")
            self.audit(None, "users imported", f"{len(report['created'])} created, {len(report['updated'])} updated",
                       f"{len(report['errors'])} rows skipped")
        return report

    def admin_create_user(self, must_change=None, **kw):
        kw = self._resolve_user_fields(None, dict(kw))
        self.check_password_rules(kw.get("password", ""), kw.get("username", ""))
        uid = self.db.create_user(**kw)
        if must_change is None:
            must_change = bool(self.config["force_password_change"])
        if must_change:
            self.db.set_must_change(uid, True)
        self._after_user_change(uid)
        self.audit(None, "user created", kw.get("username"),
                   ", ".join(f"{k}={v}" for k, v in kw.items() if k != "password" and v not in ("", None)))
        return uid

    def admin_update_user(self, uid, password=None, must_change=None, **fields):
        before = self.db.get_user(uid)
        fields = self._resolve_user_fields(uid, dict(fields))
        if password:
            self.check_password_rules(password, fields.get("username") or before["username"])
        self.db.update_user(uid, **fields)
        if password:
            if must_change is None:
                must_change = bool(self.config["force_password_change"])
            self.db.set_password(uid, password, must_change=must_change)
            self.kick(uid, "Your password was reset. Please sign in with the new one.")
        self._after_user_change(uid)
        changes = [f"{k}: {before[k]!r} -> {v!r}" for k, v in fields.items()
                   if k in before.keys() and before[k] != v]
        name = before["username"]
        if "disabled" in fields and before["disabled"] != fields["disabled"]:
            self.audit(None, "account disabled" if fields["disabled"] else "account enabled", name)
            changes = [c for c in changes if not c.startswith("disabled")]
        if changes:
            self.audit(None, "user changed", name, "; ".join(changes))
        if password:
            self.audit(None, "password reset", name, "must change at next sign-in" if must_change else "")

    def admin_delete_user(self, uid):
        name = self.db.get_user(uid)["username"]
        self.db.delete_user(uid)
        self._after_user_change(uid)
        for room in self.db.list_rooms():
            self._push_room(room["id"])
        self.audit(None, "user deleted", name)

    def admin_user_by_name(self, username):
        row = self.db.get_user_by_name(username)
        return {"id": row["id"], "username": row["username"]} if row else None

    # ---- designations (roles)
    def admin_roles(self):
        usage = self.db.role_usage()
        return [dict(r) | {"users": usage.get(r["id"], 0)} for r in self.db.list_roles()]

    def admin_save_role(self, role_id, **fields):
        new = role_id is None
        role_id = self.db.save_role(role_id, **fields)
        self.push_directory()
        self.audit(None, "designation created" if new else "designation changed", fields.get("name", role_id),
                   ", ".join(f"{k}={v}" for k, v in fields.items() if k != "name"))
        return role_id

    def admin_delete_role(self, role_id):
        role = self.db.get_role(role_id)
        self.db.delete_role(role_id)
        self.push_directory()
        self.audit(None, "designation deleted", role["name"] if role else role_id)

    # ---- departments and sections (Departments page)
    def admin_departments(self):
        usage = self.db.department_usage()
        return [{"id": d["id"], "name": d["name"], "parent_id": d["parent_id"], "has_room": bool(d["has_room"]),
                 "people": usage.get(d["id"], 0)} for d in self.db.list_departments()]

    def _department_label(self, dept):
        parent = self.db.get_department(dept["parent_id"])
        return f"{parent['name']} · {dept['name']}" if parent else dept["name"]

    def admin_save_department(self, dept_id=None, name="", parent_id=None):
        """Add a department (or a section when parent_id is given), or rename one (dept_id)."""
        if dept_id is None:
            dept_id = self.db.create_department(name, parent_id)
            dept = self.db.get_department(dept_id)
            self.audit(None, "section created" if parent_id else "department created", self._department_label(dept))
        else:
            old = self.db.rename_department(dept_id, name)
            dept = self.db.get_department(dept_id)
            if old != dept["name"]:
                self.audit(None, "section renamed" if dept["parent_id"] else "department renamed",
                           self._department_label(dept), f"was '{old}'")
                self.sync_auto_rooms()           # the room name follows
                self.push_directory()            # everyone's department/section text changed
        self._emit("users")
        return dept_id

    def admin_set_department_room(self, dept_id, has_room):
        dept = self.db.get_department(dept_id)
        if not dept:
            raise ValueError("Department not found")
        self.db.set_department_room(dept_id, has_room)
        self.sync_auto_rooms()
        self.audit(None, "chat room turned on" if has_room else "chat room turned off", self._department_label(dept),
                   "" if has_room else "the room is kept as a normal room")
        self._emit("users")

    def admin_delete_department(self, dept_id):
        dept = self.db.get_department(dept_id)
        if not dept:
            raise ValueError("Department not found")
        label = self._department_label(dept)
        self.db.delete_department(dept_id)
        self.sync_auto_rooms()                  # its rooms become normal rooms
        self.audit(None, "section deleted" if dept["parent_id"] else "department deleted", label)
        self._emit("users")

    def admin_org(self):
        """Users + reporting data for the console's org chart."""
        return [self.user_public(self.db.get_user(u)) for u in self.org.users]

    def admin_rooms(self):
        return [self.room_public(r) for r in self.db.list_rooms()]

    def admin_save_room(self, room_id, name, topic, member_ids):
        member_ids = set(self._valid_user_ids(member_ids))
        room = self.db.get_room(room_id) if room_id is not None else None
        if room_id is not None and not room:
            raise ValueError("This room no longer exists")
        if room and room["auto_key"]:
            raise ValueError("This room is automatic: its members follow the department/section of each user")
        if room_id is None:
            room_id = self.db.create_room(name, None, member_ids, topic)
            self._push_room(room_id)
            self._room_system_message(room_id, ADMIN_SENDER_ID, "Administrator created the room")
            self.audit(None, "room created", name, f"{len(member_ids)} members")
            return room_id
        self.db.update_room(room_id, name, topic)
        before = set(self.db.room_member_ids(room_id))
        self.db.add_room_members(room_id, member_ids - before)
        for uid in before - member_ids:
            self.db.remove_room_member(room_id, uid)
        self._push_room(room_id, extra_uids=before - member_ids)
        self.audit(None, "room changed", name, f"+{len(member_ids - before)} / -{len(before - member_ids)} members")
        return room_id

    def admin_delete_room(self, room_id):
        if self._org:
            self._org._visible.clear()
        room = self.db.get_room(room_id)
        members = self.db.room_member_ids(room_id)
        self.db.delete_room(room_id)
        for uid in members:
            self.push_user(uid, {"op": "room_removed", "room_id": room_id})
        if room and not room["auto_key"]:
            self.audit(None, "room deleted", room["name"])

    def admin_announce(self, title, body, kind="all", department="", section=""):
        kind, value = self.announce_target(kind, department, section, ADMIN_SENDER_ID)
        ann_id = self.announce(ADMIN_SENDER_ID, title, body, kind, value)
        self.audit(None, "announcement sent", title or "Announcement", kind + (f": {department} {section}" if department else ""))
        return ann_id

    def admin_kick(self, uid):
        row = self.db.get_user(uid)
        self.kick(uid)
        self.audit(None, "user disconnected", row["username"] if row else uid)

    def admin_audit(self, query="", limit=1000):
        return [dict(r) for r in self.db.list_audit(query, limit)]

    def admin_config(self):
        cfg = self.config
        return dict(cfg.values) | {"_data_dir": cfg.data_dir, "_storage_dir": cfg.storage_dir,
                                   "_backup_dir": cfg.backup_dir, "_db_path": cfg.db_path,
                                   "_log_dir": os.path.dirname(cfg.log_path),
                                   "_chat_log_dir": archive.log_dir(cfg)}

    def admin_update_config(self, **values):
        old = dict(self.config.values)
        self.config.update(**values)
        changed = {k: v for k, v in values.items() if old.get(k) != v}
        if changed:
            self.sync_auto_rooms()
            self.audit(None, "settings changed", "", ", ".join(
                f"{k}={'(hidden)' if 'key' in k or 'password' in k else v}" for k, v in changed.items()))
        return self.admin_config()

    def admin_server_info(self):
        from common.version import APP_VERSION
        return {"version": APP_VERSION, "ips": local_ips(), "tcp_port": self.config["tcp_port"],
                "discovery_port": self.config["discovery_port"], "server_name": self.config["server_name"],
                "started_at": self.started_at, "data_dir": self.config.data_dir,
                "storage_dir": self.config.storage_dir, "last_backup": self.last_backup,
                "tls": bool(self.tls_fingerprint), "fingerprint": self.tls_fingerprint,
                "discovery_error": getattr(self, "discovery_error", ""),
                "storage_error": getattr(self, "storage_error", ""),
                "last_chat_backup": self.last_chat_backup, "chat_log_dir": archive.log_dir(self.config)}

    def admin_log_tail(self, lines=400):
        try:
            with open(self.config.log_path, encoding="utf-8", errors="replace") as f:
                return f.readlines()[-int(lines):]
        except OSError:
            return []

    # ---- backups
    last_backup = None       # {"time": ts, "path": ..., "ok": bool, "error": ...}

    def _backup_plan(self):
        folder = self.config.backup_dir
        stamp = time.strftime("%Y-%m-%d_%H%M")
        return folder, stamp, os.path.join(folder, f"messenger_{stamp}.db"), max(1, int(self.config["backup_keep"]))

    def _backup_copy(self, folder, stamp, path, keep, config_path):
        """The slow part (safe in a worker thread): copy the database + settings, prune old copies."""
        try:
            os.makedirs(folder, exist_ok=True)
            self.db.backup_to(path + ".tmp")
            replace_file(path + ".tmp", path)
            shutil.copy2(config_path, os.path.join(folder, f"config_{stamp}.json"))
            just_written = {os.path.basename(path), f"config_{stamp}.json"}
            for prefix in ("messenger_", "config_"):
                old = sorted((f for f in os.listdir(folder) if f.startswith(prefix) and not f.endswith(".tmp")
                              and f not in just_written),
                             key=lambda f: os.path.getmtime(os.path.join(folder, f)))
                for f in old[:max(0, len(old) - (keep - 1))]:
                    os.remove(os.path.join(folder, f))
            return {"time": time.time(), "path": path, "ok": True, "size": os.path.getsize(path)}
        except (OSError, sqlite3.Error) as e:
            try:
                os.remove(path + ".tmp")
            except OSError:
                pass
            return {"time": time.time(), "path": path, "ok": False, "error": str(e)}

    def _backup_done(self, result, folder):
        self.last_backup = result
        if result["ok"]:
            log.info("Backup written to %s (%s)", result["path"], P.human_size(result["size"]))
        else:
            log.error("BACKUP FAILED (%s): %s", result["path"], result["error"])
            self.audit("server", "backup failed", folder, result["error"])
        return result

    def backup_now(self):
        """Copy the database + settings into the backup folder and prune old copies (waits for it)."""
        folder, stamp, path, keep = self._backup_plan()
        return self._backup_done(self._backup_copy(folder, stamp, path, keep, self.config.path), folder)

    async def backup_async(self):
        """The nightly backup: the copy runs in a worker thread, so chats never stall while it runs."""
        folder, stamp, path, keep = self._backup_plan()
        result = await self.loop.run_in_executor(None, self._backup_copy, folder, stamp, path, keep,
                                                 self.config.path)
        return self._backup_done(result, folder)

    # ---- storage: who uses the space, per-room file retention, manual clean-up
    def admin_storage(self):
        report = self.db.storage_report()
        try:
            usage = shutil.disk_usage(self.config.storage_dir)
            disk = {"total": usage.total, "free": usage.free}
        except OSError:
            disk = None
        stats = self.db.message_stats()
        report.update(total_bytes=stats["files_bytes"], total_files=stats["files"], disk=disk,
                      storage_dir=self.config.storage_dir,
                      default_days=float(self.config["file_retention_days"] or 0),
                      unclaimed_days=float(self.config["unclaimed_file_days"] or 0))
        return report

    def admin_cleanup_files(self, days):
        """Delete every stored file older than `days` days now (at least 1 day)."""
        days = float(days)
        if not days >= 1:
            raise ValueError("Choose at least 1 day")
        removed, freed = self._delete_stored(self.db.files_older_than(time.time() - days * 86400))
        self.audit(None, "files cleaned up", f"older than {days:g} days", f"{removed} files, {P.human_size(freed)}")
        self._emit("stats")
        return {"removed": removed, "bytes": freed}

    def admin_set_room_retention(self, room_id, days):
        """Keep a room's shared files for `days` days (0 = forever, None = the server default)."""
        room = self.db.get_room(int(room_id))
        if not room:
            raise ValueError("This room no longer exists")
        if days is not None:
            days = float(days)
            if not 0 <= days <= 36500:
                raise ValueError("Days must be between 0 and 36500")
        self.db.set_room_retention(room["id"], days)
        what = "server default" if days is None else ("forever" if days == 0 else f"{days:g} days")
        self.audit(None, "room file retention", room["name"], what)
        return True

    # ---- readable chat backup + message retention (see server/archive.py)
    last_chat_backup = None

    def chat_backup_now(self, batches=None):
        """Append new messages to the chat log files, then drop messages older than the retention period.

        With `batches`, stop after that many 5000-message batches (result["more"] is then True)."""
        try:
            result = archive.export_chat_logs(self.db, self.config, batches)
            if not result.get("more"):
                result["removed"] = archive.apply_retention(self.db, self.config)
            result.update(time=time.time(), ok=True)
        except Exception as e:  # noqa: BLE001 - reported on the dashboard, retried tomorrow
            log.exception("Chat backup failed")
            result = {"time": time.time(), "ok": False, "error": str(e), "folder": archive.log_dir(self.config)}
            archive.mark_run(self.db)
        self.last_chat_backup = result
        return result

    async def chat_backup_async(self):
        """The nightly run: a batch at a time, so chat keeps flowing during a big first export."""
        exported = 0
        while True:
            result = self.chat_backup_now(batches=1)
            exported += result.get("messages", 0)
            if not result.get("more"):
                result["messages"] = exported
                return result
            await asyncio.sleep(0.05)

    def _backup_due(self) -> bool:
        if not self.config["backup_enabled"]:
            return False
        if time.localtime().tm_hour < int(self.config["backup_hour"]):
            return False
        today = time.strftime("%Y-%m-%d")
        if self.last_backup and time.strftime("%Y-%m-%d", time.localtime(self.last_backup["time"])) == today:
            return False
        folder = self.config.backup_dir
        try:
            return not any(f.startswith(f"messenger_{today}") for f in os.listdir(folder))
        except OSError:
            return True

    def admin_sessions(self):
        out = []
        for uid, sessions in self.sessions.items():
            row = self.db.get_user(uid)
            for s in sessions:
                out.append({"user_id": uid, "username": row["username"], "name": row["display_name"],
                            "ip": s.addr[0] if s.addr else "?", "since": s.since,
                            "status": self.chosen_status.get(uid, "online"), "version": s.version})
        return sorted(out, key=lambda d: d["name"].lower())

    def kick(self, uid, reason="Disconnected by the administrator"):
        for s in list(self.sessions.get(uid, ())):
            s.send({"op": "kicked", "reason": reason})
            s.close()

    def admin_stats(self):
        stats = self.db.message_stats()
        stats["users"] = self.db.user_count()
        stats["online"] = sum(1 for u in self.sessions if self.sessions[u])
        stats["started_at"] = self.started_at
        return stats


class _DiscoveryProtocol(asyncio.DatagramProtocol):
    def __init__(self, core: ServerCore):
        self.core = core
        self.transport = None

    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, data, addr):
        if data.strip() == P.DISCOVERY_MAGIC:
            self.transport.sendto(P.encode({
                "name": self.core.config["server_name"], "port": int(self.core.config["tcp_port"]),
                "protocol": P.PROTOCOL_VERSION, "tls": bool(self.core.tls_fingerprint),
                "fingerprint": self.core.tls_fingerprint}), addr)


def port_owner(port: int, udp=False) -> str:
    """Name and PID of the program using a local port, e.g. 'nginx.exe (PID 4312)'; '' if unknown."""
    if sys.platform != "win32":
        return ""
    import subprocess
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        out = subprocess.run(["netstat", "-ano", "-p", "UDP" if udp else "TCP"], capture_output=True, text=True,
                             timeout=5, creationflags=flags).stdout
        pid = None
        for line in out.splitlines():
            parts = line.split()
            if len(parts) >= 4 and parts[1].endswith(f":{port}") and (udp or parts[3] == "LISTENING"):
                pid = parts[-1]
                break
        if not pid or not pid.isdigit():
            return ""
        if pid == str(os.getpid()):
            return "this program"
        name = ""
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"], capture_output=True,
                             text=True, timeout=5, creationflags=flags).stdout.strip()
        if out.startswith('"'):
            name = out.split('","')[0].strip('"')
        return f"{name or 'a program'} (PID {pid})"
    except (OSError, subprocess.SubprocessError):
        return ""


def startup_error_text(e: Exception, config) -> str:
    """Plain-language explanation for a failed server start."""
    import sqlite3
    if isinstance(e, OSError) and getattr(e, "winerror", None) == 10048 or "10048" in str(e) or "in use" in str(e):
        port = config["tcp_port"]
        owner = port_owner(int(port))
        who = f"It is used by {owner}." if owner else "Another program is using it."
        hint = (" That is probably another Quillo server already running on this PC "
                "(check the system tray, or the background service)."
                if "LANMessenger" in owner or "python" in owner.lower() or not owner else "")
        return (f"Port {port} is already in use, so the server cannot start.\n\n{who}{hint}\n\n"
                f"Either close that program, or choose another chat port in Settings (clients that find the "
                f"server automatically follow the new port; PCs with a typed address need 'IP:port').")
    if isinstance(e, sqlite3.OperationalError) and "locked" in str(e).lower():
        return (f"The message database is in use by another program:\n{config.db_path}\n\n"
                f"Close any database viewer or backup tool that has it open, then start the server again. "
                f"The database itself is fine - don't delete or replace it.")
    if isinstance(e, sqlite3.DatabaseError):
        return (f"The message database could not be opened:\n{e}\n\nFile: {config.db_path}\n\n"
                f"To restore a backup: stop the server, copy the newest file from the backups folder over "
                f"messenger.db, and DELETE messenger.db-wal and messenger.db-shm next to it (if they exist) - "
                f"otherwise the restore is undone. Or move messenger.db away to start with an empty database.")
    if isinstance(e, PermissionError):
        return (f"Windows denied access to a file or folder:\n{e}\n\nThe server data folder can only be changed by "
                f"administrators. If the server runs as the background service, open the console from the Start "
                f"menu (it connects to the service); otherwise start the console as administrator.")
    if isinstance(e, ssl.SSLError) or "PEM" in str(e):
        return (f"The server's encryption certificate is damaged:\n{e}\n\nDelete the 'tls' folder in "
                f"{config.data_dir} and start again. A new certificate is created; every client then asks once "
                f"to trust the server's new identity.")
    if isinstance(e, OverflowError) or "port must be" in str(e):
        return f"A port number in the settings is not valid:\n{e}\n\nPorts must be between 1 and 65535."
    return f"{e}"


def local_ips() -> list[str]:
    ips = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    except OSError:
        pass
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            ips.add(s.getsockname()[0])
    except OSError:
        pass
    ips.discard("127.0.0.1")
    return sorted(ips) or ["127.0.0.1"]
