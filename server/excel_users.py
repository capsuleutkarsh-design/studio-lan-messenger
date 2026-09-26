"""The people list as a styled Excel workbook (server console): download a template that already holds everyone,
fill it in Excel, import it back. Also reads the older CSV files, and writes the list of first passwords.

Soft Quillo colours: navy text, a teal title, and a pale band per group of columns.
"""

import csv
import datetime

NAVY = "13235A"
TEAL = "2E9E90"
MUTED = "6B7A99"
LINE = "B7C3D9"          # soft blue-grey, but clearly visible cell lines
HEAD_LINE = "7F93B8"

# (key, header, width, group, note)
COLUMNS = [
    ("username", "Username", 18, "id", "Required. The sign-in name: no spaces (e.g. priya.n). "
                                       "It is how a row is matched to an existing person."),
    ("display_name", "Display name", 24, "id", "The name everyone sees, e.g. Priya Nair."),
    ("employee_id", "Employee ID", 14, "id", "Optional. Your HR / payroll code."),
    ("department", "Department", 20, "org", "Pick from the list. A new name creates the department."),
    ("section", "Section", 18, "org", "Optional, inside the department (e.g. Roto under Compositing). "
                                      "A new name creates the section."),
    ("designation", "Designation", 24, "org", "Pick from the list - it sets what the person may do."),
    ("reports_to", "Reports to", 18, "org", "The username of their lead or supervisor."),
    ("title", "Job title", 22, "org", "Optional free text."),
    ("birthday", "Birthday", 13, "dates", "DD-MM (e.g. 26-09) or DD-MM-YYYY. Everyone sees day and month on the "
                                          "calendar, never the year."),
    ("joined_on", "Joining date", 14, "dates", "DD-MM-YYYY - for work anniversaries on the calendar."),
    ("password", "First password", 16, "sign", "Leave empty: a random password is made and listed after the "
                                               "import; the person changes it at first sign-in. Filled in for an "
                                               "existing person: resets their password."),
]
GROUPS = {  # group: (title, band colour, pale cell colour, header colour)
    "id": ("PERSON", "DDF4F0", "F4FBFA", "A9E3DA"),
    "org": ("ORGANISATION", "E1E9F7", "F6F8FD", "BFD0EE"),
    "dates": ("CALENDAR", "ECE6F8", "F9F7FD", "D3C8F0"),
    "sign": ("SIGN-IN", "FCEBE1", "FEF8F4", "F5D0BB"),
}
HEADER_ALIASES = {c[1].lower(): c[0] for c in COLUMNS} | {c[0]: c[0] for c in COLUMNS} | {
    "name": "display_name", "full name": "display_name", "manager": "reports_to", "reports to (username)": "reports_to",
    "emp id": "employee_id", "employee code": "employee_id", "date of joining": "joined_on", "doj": "joined_on",
    "dob": "birthday", "date of birth": "birthday", "password": "password", "job title": "title"}
COLUMNS_BY_KEY = [(c[0], c[1]) for c in COLUMNS]
FIRST_ROW = 4          # title, group bands, headers, then people


def _styles():
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    thin = Side(style="thin", color=LINE)
    return {
        "fill": lambda c: PatternFill("solid", start_color=c, end_color=c),
        "border": Border(left=thin, right=thin, top=thin, bottom=thin),
        "head_border": Border(left=thin, right=thin, top=thin, bottom=Side(style="medium", color=HEAD_LINE)),
        "font": lambda **k: Font(name="Segoe UI", color=k.pop("color", NAVY), **k),
        "align": lambda **k: Alignment(vertical="center", **k),
    }


def write_template(path, users, departments, roles):
    """users: admin_users() rows; departments: admin_departments(); roles: admin_roles()."""
    from openpyxl import Workbook
    from openpyxl.comments import Comment
    from openpyxl.utils import get_column_letter
    from openpyxl.worksheet.datavalidation import DataValidation
    st = _styles()
    wb = Workbook()
    ws = wb.active
    ws.title = "People"
    ws.sheet_view.showGridLines = False
    ws.sheet_properties.tabColor = TEAL
    last = get_column_letter(len(COLUMNS))

    # row 1: title
    ws.row_dimensions[1].height = 34
    ws["A1"] = "Quillo  ·  People"
    ws["A1"].font = st["font"](size=16, bold=True, color=NAVY)
    ws["A1"].alignment = st["align"]()
    ws["E1"] = ("Fill one row per person, then Users > Import in the server console.  "
                "Empty cell = keep the current value,  '-' = clear it.")
    ws["E1"].font = st["font"](size=9, italic=True, color=MUTED)
    ws["E1"].alignment = st["align"]()
    # row 2: the groups as soft bands
    ws.row_dimensions[2].height = 20
    col = 1
    while col <= len(COLUMNS):
        group = COLUMNS[col - 1][3]
        end = col
        while end < len(COLUMNS) and COLUMNS[end][3] == group:
            end += 1
        title, band, _pale, _head = GROUPS[group]
        ws.merge_cells(start_row=2, start_column=col, end_row=2, end_column=end)
        cell = ws.cell(row=2, column=col, value=title)
        cell.font = st["font"](size=8, bold=True, color=NAVY)
        cell.alignment = st["align"](horizontal="left", indent=1)
        for c in range(col, end + 1):
            ws.cell(row=2, column=c).fill = st["fill"](band)
        col = end + 1
    # row 3: headers
    ws.row_dimensions[3].height = 26
    for i, (key, header, width, group, note) in enumerate(COLUMNS, 1):
        cell = ws.cell(row=3, column=i, value=header + (" *" if key in ("username", "display_name") else ""))
        cell.font = st["font"](size=10, bold=True, color=NAVY)
        cell.fill = st["fill"](GROUPS[group][3])
        cell.alignment = st["align"](horizontal="left", indent=1)
        cell.border = st["head_border"]
        cell.comment = Comment(note, "Quillo")
        cell.comment.width, cell.comment.height = 260, 90
        ws.column_dimensions[get_column_letter(i)].width = width

    # people already on the server: edit them right here
    names = {u["id"]: u["username"] for u in users}
    people = [u for u in users if not u.get("deleted") and u["username"] != "admin"]
    people.sort(key=lambda u: ((u.get("department") or "~").lower(), (u.get("display_name") or "").lower()))
    rows = max(len(people) + 60, 200)                    # plenty of empty, styled rows for new people
    for r in range(rows):
        excel_row = FIRST_ROW + r
        ws.row_dimensions[excel_row].height = 21
        u = people[r] if r < len(people) else None
        for i, (key, _h, _w, group, _n) in enumerate(COLUMNS, 1):
            cell = ws.cell(row=excel_row, column=i)
            if u:
                value = {"reports_to": names.get(u.get("manager_id"), ""), "password": ""}.get(key, u.get(key, ""))
                if key == "birthday" and value:
                    value = _birthday_text(value)
                elif key == "joined_on" and value:
                    value = datetime.date.fromisoformat(value)
                cell.value = value or None
            cell.font = st["font"](size=10, bold=key == "username", color=NAVY)
            cell.fill = st["fill"](GROUPS[group][2] if r % 2 == 0 else "FFFFFF")
            cell.border = st["border"]
            cell.alignment = st["align"](indent=1)
            if key == "joined_on":
                cell.number_format = "DD-MM-YYYY"
            elif key in ("birthday", "employee_id", "password", "username"):
                cell.number_format = "@"               # text: Excel must not turn 26-09 into a date
    ws.freeze_panes = f"C{FIRST_ROW}"
    ws.auto_filter.ref = f"A3:{last}{FIRST_ROW + rows - 1}"

    # the lists behind the dropdowns
    lists = wb.create_sheet("Lists")
    depts = sorted((d for d in departments if d["parent_id"] is None), key=lambda d: d["name"].lower())
    sections = sorted({d["name"] for d in departments if d["parent_id"] is not None}, key=str.lower)
    columns = [("Departments", [d["name"] for d in depts]), ("Sections", sections),
               ("Designations", [r["name"] for r in roles]),
               ("Usernames", sorted((u["username"] for u in people), key=str.lower))]
    for c, (title, values) in enumerate(columns, 1):
        lists.cell(row=1, column=c, value=title).font = st["font"](bold=True)
        for r, v in enumerate(values, 2):
            lists.cell(row=r, column=c, value=v)
        lists.column_dimensions[get_column_letter(c)].width = 26
    lists.sheet_state = "hidden"
    end = FIRST_ROW + rows - 1
    for key, list_col, n in (("department", "A", len(depts)), ("section", "B", len(sections)),
                             ("designation", "C", len(roles)), ("reports_to", "D", len(people))):
        if not n:
            continue
        letter = get_column_letter([c[0] for c in COLUMNS].index(key) + 1)
        # no leading "=": the file format stores the bare range; with "=" some Excel versions (and WPS, Google
        # Sheets) show the dropdown arrow with an empty list
        dv = DataValidation(type="list", formula1=f"Lists!${list_col}$2:${list_col}${n + 1}", allow_blank=True,
                            showErrorMessage=key in ("designation",), errorTitle="Designation",
                            error="Pick a designation from the list (add new ones on the Designations page).",
                            showInputMessage=True, promptTitle=dict(COLUMNS_BY_KEY)[key],
                            prompt="Pick from the list (the arrow on the right of the cell).")
        if key != "designation":
            dv.errorStyle = "information"              # a new department or section is allowed
        ws.add_data_validation(dv)
        dv.add(f"{letter}{FIRST_ROW}:{letter}{end}")

    _help_sheet(wb.create_sheet("How to fill", 0), st)
    wb.active = 1                                       # open on People
    wb.save(path)


def _birthday_text(value):
    """'09-26' or '1990-09-26' -> '26-09' / '26-09-1990' (how people write it)."""
    parts = value.split("-")
    return f"{parts[-1]}-{parts[-2]}" + (f"-{parts[0]}" if len(parts) == 3 else "")


def _help_sheet(ws, st):
    ws.sheet_view.showGridLines = False
    ws.sheet_properties.tabColor = "8FA8FF"
    ws.column_dimensions["A"].width = 3
    ws.column_dimensions["B"].width = 26
    ws.column_dimensions["C"].width = 92
    ws["B2"] = "How to fill the people list"
    ws["B2"].font = st["font"](size=16, bold=True)
    rows = [
        ("PERSON", "CDEFEA", [
            ("Username *", "The sign-in name. No spaces. It is how a row finds an existing person, so don't change "
                           "it for people who are already there."),
            ("Display name *", "The name everyone sees."),
            ("Employee ID", "Optional HR / payroll code. Must be unique."),
        ]),
        ("ORGANISATION", "D6E2F5", [
            ("Department / Section", "Pick from the dropdown. A name that doesn't exist yet is created during the "
                                     "import (without a chat room - switch that on in Departments)."),
            ("Designation", "Pick from the dropdown - it decides what the person may do (announce, create rooms, "
                            "manage accounts)."),
            ("Reports to", "The username of their lead or supervisor (builds the org chart and 'My team')."),
        ]),
        ("CALENDAR", "E4DDF5", [
            ("Birthday", "26-09 or 26-09-1990. The calendar shows day and month to everyone, never the year."),
            ("Joining date", "26-09-2021 - the calendar shows work anniversaries."),
        ]),
        ("SIGN-IN", "FBE3D6", [
            ("First password", "Leave it empty and Quillo makes a random one for each new person; after the "
                               "import you get a list to hand out, and they change it at first sign-in. "
                               "A password typed for an existing person resets theirs."),
        ]),
        ("GOOD TO KNOW", "EEF1F6", [
            ("Empty cell", "Keeps what is on the server. Type - (a dash) to clear a value."),
            ("Existing people", "They are already in the People sheet: change a cell and import, and only that "
                                "changes. Nobody is ever deleted by an import."),
            ("Before saving", "The console shows 'X new, Y changed' and any problems before anything is saved."),
        ]),
    ]
    r = 4
    for title, colour, items in rows:
        ws.cell(row=r, column=2, value=title).font = st["font"](size=9, bold=True)
        for c in (2, 3):
            ws.cell(row=r, column=c).fill = st["fill"](colour)
        ws.row_dimensions[r].height = 20
        r += 1
        for name, text in items:
            ws.cell(row=r, column=2, value=name).font = st["font"](size=10, bold=True)
            cell = ws.cell(row=r, column=3, value=text)
            cell.font = st["font"](size=10, color="33415C")
            cell.alignment = st["align"](wrap_text=True)
            ws.row_dimensions[r].height = 32
            r += 1
        r += 1


def read_rows(path):
    """Rows (dicts keyed like COLUMNS) from the Quillo workbook, any sheet with a 'Username' header, or a CSV."""
    if path.lower().endswith(".csv"):
        with open(path, newline="", encoding="utf-8-sig") as f:
            data = list(csv.reader(f))
    else:
        from openpyxl import load_workbook
        wb = load_workbook(path, data_only=True, read_only=True)
        sheet = wb["People"] if "People" in wb.sheetnames else next(
            (ws for ws in wb.worksheets if any(_is_header(r) for r in ws.iter_rows(max_row=10, values_only=True))),
            wb.active)
        data = [list(r) for r in sheet.iter_rows(values_only=True)]
    for i, row in enumerate(data[:10]):
        if _is_header(row):
            keys = [HEADER_ALIASES.get(str(v or "").replace("*", "").strip().lower()) for v in row]
            out = []
            for excel_row, values in enumerate(data[i + 1:], i + 2):
                d = {k: _text(v, k) for k, v in zip(keys, values) if k}
                d["_row"] = excel_row
                if any(v for k, v in d.items() if k != "_row"):
                    out.append(d)
            return out
    raise ValueError("No 'Username' column found - use the Quillo template (Users > Excel template).")


def _is_header(row):
    return any(str(v or "").replace("*", "").strip().lower() == "username" for v in row or ())


def _text(value, key):
    if value is None:
        return ""
    if isinstance(value, datetime.datetime):
        value = value.date()
    if isinstance(value, datetime.date):
        return f"{value:%d-%m}" if key == "birthday" and value.year == 1900 else value.isoformat()
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return str(value).strip()


def write_passwords(path, passwords, server_name):
    """The first passwords of the people just created, to print or hand out."""
    from openpyxl import Workbook
    st = _styles()
    wb = Workbook()
    ws = wb.active
    ws.title = "First passwords"
    ws.sheet_view.showGridLines = False
    ws["A1"] = "Quillo  ·  First passwords"
    ws["A1"].font = st["font"](size=16, bold=True)
    ws["A2"] = (f"Server: {server_name}.  Each person signs in with their username and this password, then chooses "
                f"their own.  Keep this list private and delete it when everyone has signed in.")
    ws["A2"].font = st["font"](size=9, italic=True, color=MUTED)
    for c, (title, width) in enumerate((("Name", 28), ("Username", 20), ("First password", 20)), 1):
        cell = ws.cell(row=4, column=c, value=title)
        cell.font = st["font"](bold=True, color=NAVY)
        cell.fill = st["fill"]("A9E3DA")
        cell.border = st["border"]
        cell.alignment = st["align"](indent=1)
        ws.column_dimensions["ABC"[c - 1]].width = width
    for r, p in enumerate(passwords, 5):
        for c, key in enumerate(("name", "username", "password"), 1):
            cell = ws.cell(row=r, column=c, value=p[key])
            cell.font = st["font"](size=11, bold=key == "password", color=NAVY)
            cell.fill = st["fill"]("F2FBF9" if r % 2 else "FFFFFF")
            cell.border = st["border"]
            cell.alignment = st["align"](indent=1)
        ws.row_dimensions[r].height = 22
    wb.save(path)
