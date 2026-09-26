"""@mentions: people (@username), everyone in a room (@everyone), who is online (@here) and teams
(@Compositing, @Roto-Paint - a department or section, spelled without spaces).

No Qt here, so it is easy to test. The text of a message keeps the plain tokens; the app highlights them.
"""

import re

GROUPS = {"everyone": "Everyone in this room", "here": "Everyone online now"}
TOKEN = re.compile(r"(?<![\w.@])@([\w][\w.\-]*)")


def group_token(name):
    """'Roto / Paint' -> 'Roto-Paint', usable after @."""
    return "-".join(re.findall(r"[\w]+", name or ""))


def tokens(body):
    """The @words in a message, lower case, without trailing punctuation ('@ann.' -> 'ann')."""
    return [m.group(1).rstrip(".-").lower() for m in TOKEN.finditer(body or "")]


def my_tokens(me, online=True):
    """Every @word that means me: my username, @everyone, @here (while online), my department and section."""
    mine = {(me.get("username") or "").lower(), "everyone"}
    if online:
        mine.add("here")
    for key in ("department", "section"):
        if me.get(key):
            mine.add(group_token(me[key]).lower())
    mine.discard("")
    return mine


def mentions(body, me, online=True):
    return bool(set(tokens(body)) & my_tokens(me, online))


def suggestions(query, members, me_id):
    """(token, label, kind) for the popup while typing '@query' in a room: groups first, then people.

    members: user dicts (id, username, name, department, section) of the room."""
    q = (query or "").lower()
    out = []
    for token, label in GROUPS.items():
        if token.startswith(q):
            out.append((token, label, "group"))
    teams = {}
    for u in members:
        for key in ("department", "section"):
            if u.get(key):
                teams.setdefault(group_token(u[key]), (u[key], []))[1].append(u["id"])
    for token, (name, ids) in sorted(teams.items(), key=lambda kv: kv[0].lower()):
        if len(ids) > 1 and (token.lower().startswith(q) or q in name.lower()):
            out.append((token, f"{name} team · {len(ids)} people", "group"))
    people = [u for u in members if u["id"] != me_id and (
        q in u["username"].lower() or any(w.startswith(q) for w in u["name"].lower().split()) or q in u["name"].lower())]
    people.sort(key=lambda u: (not u["name"].lower().startswith(q), u["name"].lower()))
    out += [(u["username"], u["name"], "person") for u in people]
    return out


def mark(escaped_html, known, mine, color, soft):
    """Highlight @tokens in already-escaped message HTML: known ones in the accent colour,
    the ones that mean me as a soft chip."""
    def sub(m):
        tok = m.group(1).rstrip(".-")
        rest = m.group(1)[len(tok):]
        low = tok.lower()
        if low in mine:
            return (f'<span style="color:{color}; background:{soft}; font-weight:600">&nbsp;@{tok}&nbsp;</span>'
                    f'{rest}')
        if low in known:
            return f'<span style="color:{color}; font-weight:600">@{tok}</span>{rest}'
        return m.group(0)
    return TOKEN.sub(sub, escaped_html)
