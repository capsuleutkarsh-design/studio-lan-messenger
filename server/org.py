"""Organisation rules: designations (roles), reporting lines, visibility and
announcement permissions.

``Org`` is a snapshot of the active users and roles. The server rebuilds it
whenever users or roles change, so all questions here are cheap lookups.
"""

from server.db import ANNOUNCE_LEVELS

SECTION_SEP = "\x1f"        # separates department and section in announcement targets

DEFAULT_PERMS = {"announce": "none", "create_rooms": True, "manage_users": False,
                 "see_all": True, "always_visible": False}
ADMIN_PERMS = {"announce": "all", "create_rooms": True, "manage_users": True,
               "see_all": True, "always_visible": True}


def section_key(department, section):
    return f"{department.strip().lower()}{SECTION_SEP}{section.strip().lower()}"


class Org:
    def __init__(self, db):
        self.roles = {r["id"]: dict(r) for r in db.list_roles()}
        self.users = {r["id"]: dict(r) for r in db.list_users(include_disabled=False)}
        self.children: dict[int | None, list[int]] = {}
        for u in self.users.values():
            manager = u["manager_id"] if u["manager_id"] in self.users else None
            self.children.setdefault(manager, []).append(u["id"])
        self._visible: dict[int, set[int]] = {}

    # ----------------------------------------------------------- roles/perms
    def role(self, uid):
        u = self.users.get(uid)
        return self.roles.get(u["role_id"]) if u else None

    def designation(self, uid) -> str:
        r = self.role(uid)
        return r["name"] if r else ""

    def level(self, uid) -> int:
        r = self.role(uid)
        return r["level"] if r else 0

    def perms(self, uid) -> dict:
        u = self.users.get(uid)
        if not u:
            return dict(DEFAULT_PERMS, create_rooms=False, see_all=False)
        if u["is_admin"]:
            return dict(ADMIN_PERMS)
        r = self.role(uid)
        p = dict(DEFAULT_PERMS)
        if r:
            p.update(announce=r["announce"], create_rooms=bool(r["create_rooms"]),
                     manage_users=bool(r["manage_users"]), see_all=bool(r["see_all"]),
                     always_visible=bool(r["always_visible"]))
        if u["can_broadcast"]:          # v1 per-user flag still honoured
            p["announce"] = "all"
        return p

    # -------------------------------------------------------- reporting lines
    def manager(self, uid):
        u = self.users.get(uid)
        return u["manager_id"] if u and u["manager_id"] in self.users else None

    def chain(self, uid) -> list[int]:
        """Managers above uid, nearest first."""
        out, seen = [], {uid}
        m = self.manager(uid)
        while m and m not in seen:
            out.append(m)
            seen.add(m)
            m = self.manager(m)
        return out

    def team(self, uid) -> set[int]:
        """Everyone reporting to uid, directly or indirectly."""
        out, todo = set(), list(self.children.get(uid, ()))
        while todo:
            x = todo.pop()
            if x not in out and x != uid:
                out.add(x)
                todo.extend(self.children.get(x, ()))
        return out

    def direct_reports(self, uid) -> list[int]:
        return list(self.children.get(uid, ()))

    def would_cycle(self, uid, manager_id) -> bool:
        return bool(manager_id) and (manager_id == uid or uid in self.chain(manager_id) or manager_id in self.team(uid))

    # ------------------------------------------------------------- visibility
    def visible_to(self, viewer, co_members=()) -> set[int]:
        if viewer in self._visible:
            return self._visible[viewer]
        if self.perms(viewer)["see_all"]:
            vis = set(self.users)
        else:
            me = self.users.get(viewer)
            dept = (me["department"] if me else "").lower()
            vis = {viewer}
            vis |= {u["id"] for u in self.users.values() if dept and u["department"].lower() == dept}
            vis |= set(self.chain(viewer)) | self.team(viewer)
            vis |= {u for u in self.users if self.perms(u)["always_visible"]}
            vis |= set(co_members) & set(self.users)
        self._visible[viewer] = vis
        return vis

    # ---------------------------------------------------------- announcements
    def can_announce(self, uid, kind, value) -> bool:
        level = self.perms(uid)["announce"]
        rank = ANNOUNCE_LEVELS.index(level) if level in ANNOUNCE_LEVELS else 0
        me = self.users.get(uid)
        if not me or rank == 0:
            return False
        if rank >= ANNOUNCE_LEVELS.index("all"):
            return kind in ("all", "department", "section", "team")
        if kind == "team":
            return str(value) == str(uid)
        if kind == "section" and rank >= ANNOUNCE_LEVELS.index("section"):
            dept, _, _ = str(value).partition(SECTION_SEP)
            if rank >= ANNOUNCE_LEVELS.index("department"):
                return dept == me["department"].lower()
            return str(value) == section_key(me["department"], me["section"])
        if kind == "department" and rank >= ANNOUNCE_LEVELS.index("department"):
            return str(value).lower() == me["department"].lower()
        return False

    def receives(self, kind, value, uid) -> bool:
        u = self.users.get(uid)
        if not u:
            return False
        if kind == "all":
            return True
        if kind == "department":
            return u["department"].lower() == str(value).lower()
        if kind == "section":
            return section_key(u["department"], u["section"]) == str(value)
        if kind == "team":
            try:
                lead = int(value)
            except ValueError:
                return False
            return uid == lead or uid in self.team(lead)
        return False

    # ---------------------------------------------------------------- groups
    def departments(self) -> dict[str, list[int]]:
        out: dict[str, list[int]] = {}
        names: dict[str, str] = {}
        for u in self.users.values():
            d = u["department"].strip()
            if d:
                names.setdefault(d.lower(), d)
                out.setdefault(names[d.lower()], []).append(u["id"])
        return out

    def sections(self) -> dict[tuple[str, str], list[int]]:
        out: dict[tuple[str, str], list[int]] = {}
        names: dict[str, tuple[str, str]] = {}
        for u in self.users.values():
            d, s = u["department"].strip(), u["section"].strip()
            if d and s:
                key = section_key(d, s)
                names.setdefault(key, (d, s))
                out.setdefault(names[key], []).append(u["id"])
        return out
