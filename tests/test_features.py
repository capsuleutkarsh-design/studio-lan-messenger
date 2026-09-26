"""Chat features: reply, edit, delete, forward, pins, mute, seen-by, stickers, photos, status, polls, reactions."""

import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from common import protocol as P  # noqa: E402
from server.core import ServerCore  # noqa: E402
from tests import test_server as base  # noqa: E402

PORT = 15450


class Client(base.Client):
    def __init__(self, username, password="Artist2026"):
        base.PORT, old = PORT, base.PORT
        try:
            super().__init__(username, password)
        finally:
            base.PORT = old


class FeatureTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.core = c = ServerCore(cls.tmp)
        c.config.update(tcp_port=PORT, discovery_port=PORT + 1)
        c.start()
        mk = lambda u, **kw: c.call(c.admin_create_user, must_change=False, username=u,  # noqa: E731
                                    password="Artist2026", display_name=u.title(), **kw)
        cls.a, cls.b, cls.c = mk("ann"), mk("ben"), mk("cat")
        cls.boss = mk("boss", is_admin=1)

    @classmethod
    def tearDownClass(cls):
        cls.core.stop()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_reply_edit_delete(self):
        a, b = Client("ann"), Client("ben")
        m1 = a.request("send", conv=f"u:{self.b}", text="Is FAL_010 final?")["message"]
        r = b.request("send", conv=f"u:{self.a}", text="Yes!", reply_to=m1["id"])
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["message"]["reply"]["snippet"], "Is FAL_010 final?")
        self.assertEqual(r["message"]["reply"]["sender_name"], "Ann")
        # replying to a message from another conversation is refused
        other = a.request("send", conv=f"u:{self.c}", text="hi cat")["message"]
        self.assertFalse(b.request("send", conv=f"u:{self.a}", text="x", reply_to=other["id"])["ok"])
        # only the sender can edit
        self.assertFalse(b.request("edit", id=m1["id"], text="hacked")["ok"])
        self.assertTrue(a.request("edit", id=m1["id"], text="Is FAL_010 final (v12)?")["ok"])
        upd = b.wait_for("message_update")["message"]
        self.assertTrue(upd["edited"])
        self.assertEqual(upd["body"], "Is FAL_010 final (v12)?")
        # delete
        self.assertFalse(b.request("delete_message", id=m1["id"])["ok"])
        self.assertTrue(a.request("delete_message", id=m1["id"])["ok"])
        upd = b.wait_for("message_update")["message"]
        while upd["id"] != m1["id"]:
            upd = b.wait_for("message_update")["message"]
        self.assertTrue(upd["deleted"])
        self.assertEqual(upd["body"], "")
        hist = b.request("history", conv=f"u:{self.a}")["messages"]
        self.assertEqual([m for m in hist if m["id"] == m1["id"]][0]["body"], "")
        self.assertEqual([m for m in hist if m["id"] == r["message"]["id"]][0]["reply"]["snippet"], "Deleted message")
        self.assertFalse(any(m["id"] == m1["id"] for m in b.request("search", query="FAL_010")["messages"]))
        a.close()
        b.close()

    def test_stickers(self):
        a, b = Client("ann"), Client("ben")
        r = a.request("send", conv=f"u:{self.b}", sticker="desi_chat/03.webp")
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["message"]["kind"], "sticker")
        self.assertEqual(r["message"]["body"], "desi_chat/03.webp")
        got = b.wait_for("message")["message"]
        self.assertEqual((got["kind"], got["body"]), ("sticker", "desi_chat/03.webp"))
        # every sticker id must look like pack/NN.webp (no paths, no text smuggled in)
        for bad in ("../../etc/passwd", "desi_chat/03.png", "x" * 200, "Desi/01.webp", 5):
            self.assertFalse(a.request("send", conv=f"u:{self.b}", sticker=bad)["ok"], bad)
        # a sticker can't be edited, but it can be replied to, forwarded and deleted
        self.assertFalse(a.request("edit", id=got["id"], text="hello")["ok"])
        rep = b.request("send", conv=f"u:{self.a}", text="haha", reply_to=got["id"])["message"]
        self.assertEqual(rep["reply"]["snippet"], "Sticker")
        fwd = b.request("send", conv=f"u:{self.c}", sticker=got["body"], forwarded=True)["message"]
        self.assertTrue(fwd["forwarded"])
        self.assertEqual(fwd["kind"], "sticker")
        self.assertFalse(any(m["kind"] == "sticker" for m in a.request("search", query="desi")["messages"]))
        self.assertTrue(a.request("delete_message", id=got["id"])["ok"])
        a.close()
        b.close()

    def test_profile_photo(self):
        import base64
        png = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"\0" * 100).decode()
        a, b = Client("ann"), Client("ben")
        r = a.request("set_avatar", data=png)
        self.assertTrue(r["ok"], r)
        ver = r["avatar"]
        self.assertTrue(ver)
        upd = b.wait_for("user")["user"]
        self.assertEqual((upd["id"], upd["avatar"]), (self.a, ver))
        got = b.request("get_avatar", user_id=self.a)
        self.assertEqual(base64.b64decode(got["data"])[:4], b"\x89PNG")
        # not a picture / too big
        self.assertFalse(a.request("set_avatar", data=base64.b64encode(b"MZ\x90\x00 evil").decode())["ok"])
        big = base64.b64encode(b"\xff\xd8\xff" + b"\0" * 500_000).decode()
        self.assertFalse(a.request("set_avatar", data=big)["ok"])
        # remove it; an admin can remove it too
        self.assertEqual(a.request("set_avatar", data=None)["avatar"], 0)
        self.assertFalse(b.request("get_avatar", user_id=self.a)["ok"])
        a.request("set_avatar", data=png)
        self.core.call(self.core.admin_remove_avatar, self.a)
        self.assertEqual(self.core.db.get_user(self.a)["avatar_ver"], 0)
        a.close()
        b.close()

    def test_status_emoji_and_expiry(self):
        import time as _t
        a, b = Client("ann"), Client("ben")
        r = a.request("set_status", status_msg="Lunch", status_emoji="🍽️", status_until=_t.time() + 3600)
        self.assertTrue(r["ok"], r)
        pres = b.wait_for("presence")
        while pres["user_id"] != self.a:
            pres = b.wait_for("presence")
        self.assertEqual((pres["status_msg"], pres["status_emoji"]), ("Lunch", "🍽️"))
        self.assertFalse(a.request("set_status", status_msg="x", status_until=_t.time() + 90 * 86400)["ok"])
        # expiry clears it (the maintenance loop does this every minute)
        self.core.db.set_status_msg(self.a, "Lunch", "🍽️", _t.time() - 1)
        self.core.call(self.core.expire_statuses)
        pres = b.wait_for("presence")
        self.assertEqual((pres["status_msg"], pres["status_emoji"]), ("", ""))
        a.close()
        b.close()

    def test_polls(self):
        a, b, c = Client("ann"), Client("ben"), Client("cat")
        room = a.request("create_room", name="Lunch poll", members=[self.b])["room_id"]
        self.assertFalse(a.request("create_poll", conv=f"r:{room}", question="Where?", options=["A"])["ok"])
        r = a.request("create_poll", conv=f"r:{room}", question="Where for lunch?",
                      options=["Canteen", "Pizza", "pizza", "Thali"], multi=False)
        self.assertTrue(r["ok"], r)
        poll = r["message"]["poll"]
        self.assertEqual([o["text"] for o in poll["options"]], ["Canteen", "Pizza", "Thali"])   # duplicates dropped
        pid = poll["id"]
        self.assertTrue(b.request("vote", poll_id=pid, options=[1])["ok"])
        self.assertFalse(b.request("vote", poll_id=pid, options=[0, 1])["ok"])     # single choice
        self.assertFalse(b.request("vote", poll_id=pid, options=[7])["ok"])
        self.assertFalse(c.request("vote", poll_id=pid, options=[0])["ok"])        # not in the room
        upd = a.wait_for("message_update")["message"]["poll"]
        self.assertEqual(upd["options"][1]["count"], 1)
        self.assertEqual(upd["options"][1]["voters"], [self.b])
        self.assertEqual(upd["mine"], [])
        self.assertTrue(a.request("vote", poll_id=pid, options=[1])["ok"])
        mine = b.wait_for("message_update")["message"]["poll"]
        while mine["total"] < 2:
            mine = b.wait_for("message_update")["message"]["poll"]
        self.assertEqual(mine["mine"], [1])
        self.assertFalse(a.request("edit", id=r["message"]["id"], text="changed")["ok"])
        self.assertFalse(b.request("close_poll", poll_id=pid)["ok"])              # only the creator
        self.assertTrue(a.request("close_poll", poll_id=pid)["ok"])
        self.assertFalse(b.request("vote", poll_id=pid, options=[0])["ok"])
        hist = b.request("history", conv=f"r:{room}")["messages"]
        p = [m for m in hist if m["kind"] == "poll"][0]["poll"]
        self.assertTrue(p["closed"])
        self.assertEqual((p["total"], p["mine"]), (2, [1]))
        # anonymous polls never reveal who voted
        r = a.request("create_poll", conv=f"u:{self.b}", question="Secret?", options=["Yes", "No"], anonymous=True)
        secret = r["message"]["poll"]["id"]
        b.request("vote", poll_id=secret, options=[0])
        upd = a.wait_for("message_update")["message"]["poll"]
        while upd["id"] != secret:
            upd = a.wait_for("message_update")["message"]["poll"]
        self.assertNotIn("voters", upd["options"][0])
        for x in (a, b, c):
            x.close()

    def test_reactions(self):
        a, b = Client("ann"), Client("ben")
        m = a.request("send", conv=f"u:{self.b}", text="Final render is out!")["message"]
        self.assertTrue(b.request("react", message_id=m["id"], emoji="🔥")["ok"])
        self.assertTrue(b.request("react", message_id=m["id"], emoji="🔥")["ok"])   # idempotent
        self.assertTrue(a.request("react", message_id=m["id"], emoji="🔥")["ok"])
        for bad in ("<b>", "hello", "", "🔥" * 9):
            self.assertFalse(b.request("react", message_id=m["id"], emoji=bad)["ok"], bad)
        hist = b.request("history", conv=f"u:{self.a}")["messages"]
        r = [x for x in hist if x["id"] == m["id"]][0]["reactions"]
        self.assertEqual(r, [{"emoji": "🔥", "count": 2, "mine": True, "users": [self.b, self.a]}])
        self.assertTrue(b.request("react", message_id=m["id"], emoji="🔥", on=False)["ok"])
        hist = a.request("history", conv=f"u:{self.b}")["messages"]
        r = [x for x in hist if x["id"] == m["id"]][0]["reactions"]
        self.assertEqual((r[0]["count"], r[0]["mine"]), (1, True))
        other = Client("cat")
        self.assertFalse(other.request("react", message_id=m["id"], emoji="👍")["ok"])   # not their chat
        for x in (a, b, other):
            x.close()

    def test_long_messages(self):
        a, b = Client("ann"), Client("ben")
        script = "set cut_paste_input [stack 0]\n" + "\n".join(f"Grade {{\n name Grade{i}\n}}" for i in range(2000))
        self.assertLess(len(script), P.MAX_TEXT)
        r = a.request("send", conv=f"u:{self.b}", text=script)
        self.assertTrue(r["ok"], r)
        got = b.wait_for("message")["message"]
        self.assertEqual(got["body"], script)                      # nothing cut off
        too_long = "x" * (P.MAX_TEXT + 1)
        r = a.request("send", conv=f"u:{self.b}", text=too_long)
        self.assertFalse(r["ok"])
        self.assertIn("too long", r["error"])                      # refused clearly, never silently truncated
        self.assertFalse(a.request("edit", id=got["id"], text=too_long)["ok"])
        a.close()
        b.close()

    def test_buzz(self):
        a, b = Client("ann"), Client("ben")
        r = a.request("buzz", conv=f"u:{self.b}")
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["message"]["kind"], "buzz")
        got = b.wait_for("message")["message"]
        self.assertEqual((got["kind"], got["sender_id"]), ("buzz", self.a))
        again = a.request("buzz", conv=f"u:{self.b}")
        self.assertFalse(again["ok"])                               # one buzz per 20 s per person
        self.assertIn("wait", again["error"])
        room = a.request("create_room", name="Buzz room", members=[self.b])["room_id"]
        r = a.request("buzz", conv=f"r:{room}")                     # the whole room (since 1.6.3)
        self.assertTrue(r["ok"], r)
        self.assertEqual(b.wait_for("message")["message"]["conv"], f"r:{room}")
        self.assertFalse(b.request("buzz", conv=f"r:{room}")["ok"])  # a room: once a minute, by anyone
        outsider = Client("cat")
        self.assertFalse(outsider.request("buzz", conv=f"r:{room}")["ok"])   # members only
        outsider.close()
        self.assertFalse(a.request("buzz", conv=f"u:{self.a}")["ok"])       # not yourself
        self.assertFalse(a.request("edit", id=got["id"], text="x")["ok"])
        self.core.config.update(buzz_enabled=False)
        self.assertFalse(b.request("buzz", conv=f"u:{self.a}")["ok"])
        self.core.config.update(buzz_enabled=True)
        a.close()
        b.close()

    def test_admin_can_moderate(self):
        a, boss = Client("ann"), Client("boss")
        room = boss.request("create_room", name="Mod", members=[self.a])["room_id"]
        bad = a.request("send", conv=f"r:{room}", text="something rude")["message"]
        self.assertTrue(boss.request("delete_message", id=bad["id"])["ok"])
        self.assertTrue(any(e["action"].startswith("message deleted") for e in self.core.call(self.core.admin_audit)))
        a.close()
        boss.close()

    def test_pins_and_mute(self):
        a, b = Client("ann"), Client("ben")
        room = a.request("create_room", name="Pins", members=[self.b])["room_id"]
        m = a.request("send", conv=f"r:{room}", text="Deadline Friday 6 PM")["message"]
        self.assertTrue(b.request("pin", conv=f"r:{room}", message_id=m["id"], pinned=True)["ok"])
        pins = a.wait_for("pins")
        self.assertEqual([p["body"] for p in pins["pins"]], ["Deadline Friday 6 PM"])
        self.assertEqual(len(b.request("pins", conv=f"r:{room}")["pins"]), 1)
        self.assertTrue(a.request("pin", conv=f"r:{room}", message_id=m["id"], pinned=False)["ok"])
        self.assertEqual(b.request("pins", conv=f"r:{room}")["pins"], [])
        # mute is remembered for the next login
        self.assertTrue(b.request("mute", conv=f"r:{room}", muted=True)["ok"])
        b.close()
        b2 = Client("ben")
        self.assertIn(f"r:{room}", b2.login["muted"])
        b2.close()
        a.close()

    def test_seen_by(self):
        a, b, c = Client("ann"), Client("ben"), Client("cat")
        room = a.request("create_room", name="Seen", members=[self.b, self.c])["room_id"]
        m = a.request("send", conv=f"r:{room}", text="Please review")["message"]
        b.request("mark_read", conv=f"r:{room}", up_to=m["id"])
        r = a.request("read_by", conv=f"r:{room}", message_id=m["id"])
        self.assertEqual((r["read"], r["total"]), ([self.b], 2))
        for x in (a, b, c):
            x.close()

    def test_forward_file(self):
        a, b = Client("ann"), Client("ben")
        s = base.tls_connect(PORT)
        f = s.makefile("rb")
        s.sendall(P.encode({"op": "upload", "token": a.login["token"], "name": "ref.jpg", "size": 3}))
        hdr = json.loads(f.readline())
        s.sendall(b"abc")
        json.loads(f.readline())
        s.close()
        a.request("send", conv=f"u:{self.b}", text="", file_id=hdr["file_id"])
        room = b.request("create_room", name="Fwd", members=[self.c])["room_id"]
        r = b.request("send", conv=f"r:{room}", text="", file_id=hdr["file_id"], forwarded=True)
        self.assertTrue(r["ok"], r)
        self.assertTrue(r["message"]["forwarded"])
        self.assertEqual(r["message"]["file"]["name"], "ref.jpg")
        a.close()
        b.close()

    def test_unclaimed_files_cleanup(self):
        a = Client("ann")
        core = self.core
        tokens = a.login["token"]

        def up(name):
            s = base.tls_connect(PORT)
            f = s.makefile("rb")
            s.sendall(P.encode({"op": "upload", "token": tokens, "name": name, "size": 4}))
            hdr = json.loads(f.readline())
            s.sendall(b"data")
            json.loads(f.readline())
            s.close()
            a.request("send", conv=f"u:{self.b}", text="", file_id=hdr["file_id"])
            return hdr["file_id"]
        claimed, unclaimed = up("claimed.bin"), up("forgotten.bin")
        # ben downloads only the first one
        b = Client("ben")
        s = base.tls_connect(PORT)
        s.sendall(P.encode({"op": "download", "token": b.login["token"], "file_id": claimed}))
        s.makefile("rb").readline()
        s.close()
        core.call(core.db._exec, "UPDATE files SET created_at=created_at-10*86400")
        core.config.update(unclaimed_file_days=7, file_retention_days=0)
        try:
            core.call(core.purge_files)
        finally:
            core.config.update(unclaimed_file_days=0, file_retention_days=3)
        self.assertFalse(core.call(core.db.get_file, claimed)["purged"])
        self.assertTrue(core.call(core.db.get_file, unclaimed)["purged"])
        # the default: every shared file is removed from the server after 3 days
        fresh, old = up("today.bin"), up("last_week.bin")
        core.call(core.db._exec, "UPDATE files SET created_at=created_at-4*86400 WHERE id=?", old)
        core.call(core.purge_files)
        self.assertFalse(core.call(core.db.get_file, fresh)["purged"])
        self.assertTrue(core.call(core.db.get_file, old)["purged"])
        self.assertEqual(a.login["file_retention_days"], 3)          # clients show "available until ..."
        a.close()
        b.close()

    def test_pipeline_api(self):
        import urllib.request
        core = self.core
        core.config.update(api_enabled=True, api_port=PORT + 2, api_key="k3y-for-tests")
        from server.pipeline_api import PipelineApi
        import asyncio
        api = PipelineApi(core)
        asyncio.run_coroutine_threadsafe(api.start(PORT + 2), core.loop).result(5)
        try:
            def post(path, body, key="k3y-for-tests"):
                req = urllib.request.Request(f"http://127.0.0.1:{PORT + 2}{path}", data=json.dumps(body).encode(),
                                             headers={"Authorization": f"Bearer {key}",
                                                      "Content-Type": "application/json"})
                try:
                    with urllib.request.urlopen(req, timeout=5) as r:
                        return r.status, json.loads(r.read())
                except urllib.error.HTTPError as e:
                    return e.code, json.loads(e.read())
            b = Client("ben")
            self.assertEqual(post("/api/message", {"to": "ben", "text": "x"}, key="wrong")[0], 401)
            status, body = post("/api/message", {"to": "ben", "text": "Render FAL_020 v014 finished"})
            self.assertEqual(status, 200, body)
            msg = b.wait_for("message")["message"]
            self.assertEqual((msg["body"], msg["sender_name"]), ("Render FAL_020 v014 finished", "Pipeline Bot"))
            room = b.request("create_room", name="Renders", members=[self.a])["room_id"]
            status, body = post("/api/message", {"to": "#renders", "text": "farm queue empty"})
            self.assertEqual(status, 200, body)
            self.assertEqual(post("/api/message", {"to": "#nope", "text": "x"})[0], 400)
            self.assertEqual(post("/api/message", {"to": "ben"})[0], 400)
            b.close()
        finally:
            asyncio.run_coroutine_threadsafe(api.stop(), core.loop).result(5)
            core.config.update(api_enabled=False)

    def test_screen_share_consent_and_relay(self):
        a, b, c = Client("ann"), Client("ben"), Client("cat")
        # ben asks to see ann's screen; ann must accept
        share = b.request("screen_invite", kind="request", to=self.a)["share_id"]
        inv = a.wait_for("screen_invite")
        self.assertEqual((inv["kind"], inv["from"]), ("request", self.b))
        # nobody can connect before ann accepts
        s = base.tls_connect(PORT)
        s.sendall(P.encode({"op": "screen_sub", "token": b.login["token"], "share_id": share}))
        self.assertFalse(json.loads(s.makefile("rb").readline())["ok"])
        s.close()
        # the asker cannot accept on ann's behalf
        self.assertFalse(b.request("screen_answer", share_id=share, accept=True)["ok"])
        self.assertTrue(a.request("screen_answer", share_id=share, accept=True)["ok"])
        start = b.wait_for("screen_start")
        self.assertEqual((start["sharer"], start["viewer"]), (self.a, self.b))
        # a third person cannot watch
        s = base.tls_connect(PORT)
        s.sendall(P.encode({"op": "screen_sub", "token": c.login["token"], "share_id": share}))
        self.assertFalse(json.loads(s.makefile("rb").readline())["ok"])
        s.close()
        # relay: ann publishes a frame, ben receives it
        sub = base.tls_connect(PORT)
        sub.sendall(P.encode({"op": "screen_sub", "token": b.login["token"], "share_id": share}))
        subf = sub.makefile("rb")
        self.assertTrue(json.loads(subf.readline())["ok"])
        pub = base.tls_connect(PORT)
        pub.sendall(P.encode({"op": "screen_pub", "token": a.login["token"], "share_id": share}))
        self.assertTrue(json.loads(pub.makefile("rb").readline())["ok"])
        frame = b"\xff\xd8fake-jpeg\xff\xd9"
        pub.sendall(len(frame).to_bytes(4, "big") + frame)
        size = int.from_bytes(subf.read(4), "big")
        self.assertEqual(subf.read(size), frame)
        # either side can stop
        self.assertTrue(b.request("screen_stop", share_id=share)["ok"])
        self.assertEqual(a.wait_for("screen_stopped")["share_id"], share)
        pub.close()
        sub.close()
        # declining
        share2 = a.request("screen_invite", kind="offer", to=self.b)["share_id"]
        b.wait_for("screen_invite")
        b.request("screen_answer", share_id=share2, accept=False)
        self.assertEqual(a.wait_for("screen_declined")["share_id"], share2)
        for x in (a, b, c):
            x.close()

    def test_announcement_read_tracking(self):
        core = self.core
        b = Client("ben")
        ann_id = core.call(core.admin_announce, "Fire drill", "At 3 PM", "all")
        b.request("announcement_read", id=ann_id)
        reads = core.call(core.admin_announcement_reads, ann_id)
        self.assertIn("Ben", [p["name"] for p in reads["read"]])
        self.assertIn("Cat", [p["name"] for p in reads["unread"]])
        # a normal user may not see who read someone else's announcement
        self.assertFalse(b.request("announcement_reads", id=ann_id)["ok"])
        b.close()


if __name__ == "__main__":
    unittest.main()
