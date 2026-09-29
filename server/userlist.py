"""A readable list of everyone on the server, kept in the central folder.

    <central folder>/User list/Users.xlsx   the same sheet as Users > Export: import it into any Quillo server
    <central folder>/User list/Users.csv    for Excel, HR or anything else
    <central folder>/User list/README.txt   what these are and how to use them

Rewritten when people change (and once a day). It never holds passwords: those come back with the safe copy
(server/safecopy.py), or people get new ones when the list is imported into an empty server.
"""

import csv
import os
import time

from common.files import replace_file

CSV_COLUMNS = [
    ("Username", "username"), ("Name", "display_name"), ("Employee ID", "employee_id"),
    ("Department", "department"), ("Section", "section"), ("Designation", "designation"), ("Title", "title"),
    ("Reports to", "manager_name"), ("Birthday", "birthday"), ("Joined on", "joined_on"),
    ("Administrator", "is_admin"), ("Disabled", "disabled"), ("Last seen", "last_seen"),
]

README = """Quillo user list  ·  {server}
Written {when} by the Quillo server. It is rewritten whenever people are added or changed.

Users.xlsx  every person, in the same sheet as "Users > Export" in the server console.
            To fill a new, empty server: Users > Import, and choose this file.
Users.csv   the same list for Excel, HR or anything else.

Passwords are not in these files. When a server is moved to another PC, the safe copy in this central
folder brings back every account with its password; an imported list gives people new first passwords.
"""


def _cell(key, user):
    value = user.get(key)
    if key in ("is_admin", "disabled"):
        return "yes" if value else ""
    if key == "last_seen":
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(value)) if value else ""
    return "" if value is None else str(value)


def write(folder, data, server_name) -> dict:
    """data: (admin_users(), admin_departments(), admin_roles()). Runs in a worker thread."""
    users, departments, roles = data
    try:
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, "Users.xlsx")
        from server import excel_users
        excel_users.write_template(path + ".tmp.xlsx", users, departments, roles)
        replace_file(path + ".tmp.xlsx", path)

        path = os.path.join(folder, "Users.csv")
        people = sorted(users, key=lambda u: (u.get("display_name") or u.get("username") or "").lower())
        with open(path + ".tmp", "w", encoding="utf-8-sig", newline="") as f:     # the BOM: Excel reads UTF-8
            out = csv.writer(f)
            out.writerow([title for title, _key in CSV_COLUMNS])
            for user in people:
                out.writerow([_cell(key, user) for _title, key in CSV_COLUMNS])
        replace_file(path + ".tmp", path)

        path = os.path.join(folder, "README.txt")
        with open(path + ".tmp", "w", encoding="utf-8") as f:
            f.write(README.format(server=server_name, when=time.strftime("%Y-%m-%d %H:%M")))
        replace_file(path + ".tmp", path)
        return {"ok": True, "time": time.time(), "folder": folder, "people": len(users)}
    except (OSError, ValueError, ImportError) as e:
        return {"ok": False, "time": time.time(), "folder": folder, "error": str(e)}
