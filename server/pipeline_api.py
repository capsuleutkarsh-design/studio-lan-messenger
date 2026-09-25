"""Pipeline / render-farm hook: a tiny HTTP endpoint so scripts can post messages.

Enable it in the server console (Settings > Pipeline API). Examples:

    curl -X POST http://SERVER:5152/api/message -H "Authorization: Bearer KEY" \
         -H "Content-Type: application/json" -d "{\"to\": \"alice\", \"text\": \"Render FAL_020 done\"}"

    to = "alice"            a person (username)
    to = "#Comp Team"       a chat room (by name)

    POST /api/announce  {"title": "...", "text": "...", "department": "Lighting"}   (department optional)
    GET  /api/health

Python (Deadline / Nuke / Houdini / any render node, no extra packages):

    import json, urllib.request
    req = urllib.request.Request("http://SERVER:5152/api/message",
        data=json.dumps({"to": "#Comp Team", "text": "FAL_020 v014 rendered"}).encode(),
        headers={"Authorization": "Bearer KEY", "Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=10)
"""

import asyncio
import hmac
import json
import logging

log = logging.getLogger("server")
MAX_BODY = 64 * 1024

STATUS = {200: "OK", 400: "Bad Request", 401: "Unauthorized", 404: "Not Found", 405: "Method Not Allowed",
          413: "Payload Too Large", 500: "Internal Server Error"}


class PipelineApi:
    def __init__(self, core):
        self.core = core
        self.server = None

    async def start(self, port):
        self.server = await asyncio.start_server(self._handle, host="0.0.0.0", port=port)
        log.info("Pipeline API listening on http port %s", port)

    async def stop(self):
        if self.server:
            self.server.close()
            await self.server.wait_closed()

    async def _handle(self, reader, writer):
        status, body = 500, {"ok": False, "error": "server error"}
        try:
            status, body = await asyncio.wait_for(self._process(reader, writer), 15)
        except asyncio.TimeoutError:
            status, body = 400, {"ok": False, "error": "request timed out"}
        except Exception as e:  # noqa: BLE001
            log.exception("Pipeline API error")
            body = {"ok": False, "error": str(e)}
        data = json.dumps(body).encode()
        try:
            writer.write(f"HTTP/1.1 {status} {STATUS.get(status, 'OK')}\r\nContent-Type: application/json\r\n"
                         f"Content-Length: {len(data)}\r\nConnection: close\r\n\r\n".encode() + data)
            await writer.drain()
        except (ConnectionError, OSError):
            pass
        finally:
            writer.close()

    async def _process(self, reader, writer):
        line = (await reader.readline()).decode("latin-1").strip()
        parts = line.split()
        if len(parts) < 2:
            return 400, {"ok": False, "error": "bad request"}
        method, path = parts[0].upper(), parts[1].split("?")[0]
        headers = {}
        while True:
            h = (await reader.readline()).decode("latin-1")
            if h in ("\r\n", "\n", ""):
                break
            k, _, v = h.partition(":")
            headers[k.strip().lower()] = v.strip()
        length = int(headers.get("content-length", "0") or 0)
        if length > MAX_BODY:
            return 413, {"ok": False, "error": "body too large"}
        raw = await reader.readexactly(length) if length else b""

        if path == "/api/health":
            return 200, {"ok": True, "server": self.core.config["server_name"]}
        key = self.core.config["api_key"]
        given = headers.get("authorization", "").removeprefix("Bearer ").strip() or headers.get("x-api-key", "")
        if not key or not hmac.compare_digest(given.encode(), key.encode()):
            peer = writer.get_extra_info("peername")
            log.warning("Pipeline API: rejected request with a wrong key from %s", peer[0] if peer else "?")
            return 401, {"ok": False, "error": "missing or wrong API key"}
        if method != "POST":
            return 405, {"ok": False, "error": "use POST"}
        try:
            req = json.loads(raw or b"{}")
            if not isinstance(req, dict):
                raise ValueError
        except ValueError:
            return 400, {"ok": False, "error": "body must be a JSON object"}

        text = str(req.get("text") or "").strip()
        if not text:
            return 400, {"ok": False, "error": "'text' is required"}
        try:
            if path == "/api/message":
                where = self.core.bot_message(str(req.get("to") or ""), text)
                return 200, {"ok": True, "delivered_to": where}
            if path == "/api/announce":
                dept = str(req.get("department") or "")
                ann_id = self.core.bot_announce(str(req.get("title") or "Pipeline"), text, dept)
                return 200, {"ok": True, "announcement": ann_id}
        except ValueError as e:
            return 400, {"ok": False, "error": str(e)}
        return 404, {"ok": False, "error": "unknown endpoint"}
