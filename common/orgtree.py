"""Org chart tree shared by the client directory and the server console.

``users`` are dicts as sent by the server (id, name, department, section,
designation, level, manager_id, status, ...).
"""

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor, QFont
from PySide6.QtWidgets import QAbstractItemView, QHeaderView, QTreeWidget, QTreeWidgetItem

from common import theme as T
from common.icons import icon

COLUMNS = ["Name", "Designation", "Department / Section", "Status"]


def make_tree():
    t = QTreeWidget()
    t.setColumnCount(len(COLUMNS))
    t.setHeaderLabels(COLUMNS)
    t.setUniformRowHeights(True)
    t.setAnimated(True)
    t.setIndentation(22)
    t.setSelectionMode(QAbstractItemView.SingleSelection)
    t.header().setSectionResizeMode(QHeaderView.ResizeToContents)
    t.header().setStretchLastSection(True)
    return t


def _sort_key(u):
    return (-(u.get("level") or 0), u["name"].lower())


def _person(u, me_id=None):
    status = u.get("status", "offline")
    where = " · ".join(x for x in (u.get("department"), u.get("section")) if x)
    name = u["name"] + ("  (you)" if u["id"] == me_id else "")
    it = QTreeWidgetItem([name, u.get("designation") or "", where, T.STATUS_LABELS.get(status, status)])
    it.setIcon(0, icon("user", T.STATUS_COLORS.get(status, T.MUTED), 16))
    it.setForeground(3, QBrush(QColor(T.STATUS_COLORS.get(status, T.MUTED))))
    if u.get("designation"):
        it.setForeground(1, QBrush(QColor(T.ACCENT if (u.get("level") or 0) >= 60 else T.MUTED)))
    it.setData(0, Qt.UserRole, u["id"])
    return it


def _group(text, count, icon_name):
    it = QTreeWidgetItem([f"{text}   ({count})"])
    f = QFont("Segoe UI")
    f.setBold(True)
    it.setFont(0, f)
    it.setIcon(0, icon(icon_name, T.ACCENT, 16))
    it.setFirstColumnSpanned(True)
    return it


def _matches(u, q):
    return not q or any(q in (u.get(k) or "").lower()
                        for k in ("name", "username", "department", "section", "designation", "title"))


def fill(tree: QTreeWidget, users, mode="department", query="", me_id=None):
    """mode: 'department' (Department > Section > people) or 'reporting' (lead > reports)."""
    tree.clear()
    q = query.strip().lower()
    by_id = {u["id"]: u for u in users}
    if mode == "department":
        depts = {}
        for u in users:
            if _matches(u, q):
                depts.setdefault(u.get("department") or "No department", {}) \
                     .setdefault(u.get("section") or "", []).append(u)
        for dept in sorted(depts, key=lambda d: (d == "No department", d.lower())):
            sections = depts[dept]
            d_item = _group(dept, sum(len(v) for v in sections.values()), "users")
            tree.addTopLevelItem(d_item)
            for u in sorted(sections.pop("", []), key=_sort_key):
                d_item.addChild(_person(u, me_id))
            for sect in sorted(sections, key=str.lower):
                s_item = _group(sect, len(sections[sect]), "hash")
                d_item.addChild(s_item)
                for u in sorted(sections[sect], key=_sort_key):
                    s_item.addChild(_person(u, me_id))
                s_item.setFirstColumnSpanned(True)
            d_item.setFirstColumnSpanned(True)
    else:
        children = {}
        for u in users:
            m = u.get("manager_id")
            children.setdefault(m if m in by_id else None, []).append(u)
        keep = None
        if q:                                  # show matches plus everyone above them
            keep = set()
            for u in users:
                if _matches(u, q):
                    x, seen = u, set()
                    while x and x["id"] not in seen:
                        seen.add(x["id"])
                        keep.add(x["id"])
                        x = by_id.get(x.get("manager_id"))

        def add(parent_item, uid, seen):
            for u in sorted(children.get(uid, []), key=_sort_key):
                if u["id"] in seen or (keep is not None and u["id"] not in keep):
                    continue
                it = _person(u, me_id)
                n = len(children.get(u["id"], []))
                if n:
                    it.setText(0, f"{it.text(0)}   ({n})")
                if parent_item is None:
                    tree.addTopLevelItem(it)
                else:
                    parent_item.addChild(it)
                add(it, u["id"], seen | {u["id"]})
        add(None, None, set())
    tree.expandAll()


def selected_user_id(tree):
    it = tree.currentItem()
    return it.data(0, Qt.UserRole) if it else None
