# Administrator & IT guide

Everything needed to install, run and look after Quillo in a studio.

> Quillo was called *LAN Messenger* before version 1.6.0. Upgrading keeps everything: the installers still use the old internal names (data folders, registry keys, the service task, program files), so only what you see changes.
For what the app does, see the [README](../README.md).

- [1. Install the server](#1-install-the-server)
- [2. First-time setup](#2-first-time-setup)
- [3. Designations and permissions](#3-designations-and-permissions)
- [4. Install the clients](#4-install-the-clients)
- [5. Updating](#5-updating)
- [6. Running the server: service, data, backups](#6-running-the-server-service-data-backups)
- [6a. Chat backup, message history and shared files](#6a-chat-backup-message-history-and-shared-files)
- [7. Pipeline API (render farm, scripts)](#7-pipeline-api-render-farm-scripts)
- [8. Network ports](#8-network-ports)
- [9. Security and privacy](#9-security-and-privacy)
- [10. Limits](#10-limits)

---

## 1. Install the server

Pick one always-on PC (a small workstation or a VM is plenty; load-tested with 500 people online at once: 500 signed in within 2 s, ~390,000 deliveries with none lost, typical delivery 3–40 ms, server ~460 MB of RAM).
Run **`Quillo-Server-Setup-x.y.z.exe`**. It first asks how to install:

- **Install for me only** (recommended, **no administrator needed**): installs into your user folder and runs the
  server in the system tray under your Windows account, starting when you sign in. Your mapped drives and NAS
  logins work as they are. The server runs while that account is signed in (a locked screen is fine). The first
  time it starts, Windows Firewall may ask to *Allow access* (an administrator approves this once; or IT allows
  TCP 5150 and UDP 5151).
- **Install for all users** (administrator): keep the tasks
  - **Allow the server through Windows Firewall**: needed for other PCs to connect
  - **Run as a background service**: starts with Windows, nobody needs to be logged in
    (alternative: *start in the tray when I log in*)

The setup installs to `C:\Program Files\Quillo Server` (all users) or `%LOCALAPPDATA%\Programs\Quillo Server` (just for me); an upgrade keeps the folder the earlier version used. The page **Where to keep the data** asks for:

| Folder | Default | Where it may be |
|---|---|---|
| Server data (database, settings, certificate) | `C:\ProgramData\LAN Messenger Server` (all users) or `%LOCALAPPDATA%\LAN Messenger Server` (just for me) | a **local disk** only (a database on a network share can get damaged) |
| Shared files | `<data>\files` | any disk, or a share `\\server\share\...` |
| Database backups + readable chat backups | `<data>\backups` | any disk, or a share (another disk is recommended) |
| Server log | `<data>` | any disk, or a share |

Use `\\server\share\...` paths, not mapped letters like `Z:` (the background service can't see those). The service
runs as SYSTEM and reaches a share as the **computer account** (`DOMAIN\PCNAME$`): give that account *Modify* on
the share, or run the server in the tray instead. If a share is down, the server still starts and the dashboard
says so. Setup lists your mapped network drives by their network path, and *Browse* offers them first.
On an upgrade the page shows the current folders (the database folder is fixed); folders can also be moved in
console → *Settings*.
In service mode the data folder is readable only by Administrators and SYSTEM. On uninstall setup asks whether
to delete the data folder (files and backups kept elsewhere are never deleted).

## 2. First-time setup

Open **Quillo Server console** from the Start menu. It opens the console for the running service; the console
can also connect to a server on another PC (admin / IT login).

1. Sign in as **admin / admin**. You must choose a new password straight away.
2. **Designations**: rename or add titles to match your studio (see below).
3. **Departments**: add your departments, and sections inside them (select a department → *Add section*).
   Tick **Chat room** for each department or section that should have its own room (see below).
4. **Users → Excel template**: an Excel file that already lists everyone, with dropdowns for department,
   section, designation and *reports to*, and columns for employee ID, birthday and joining date. Fill it in
   and use **Users → Import** (a CSV works too). You see "X new, Y changed" and any problems before anything
   is saved. New departments and sections are created; an empty cell keeps the current value, `-` clears it;
   new people without a password get a random one - the console saves a list of first passwords for you to
   hand out, and they choose their own at first sign-in. Or add people one by one with **Add user**.
5. **Settings**: backup folder (ideally another disk), password rules, file size and clean-up, pipeline API,
   chat review.
6. Use **Rooms → New room** for projects.
6a. **Holidays**: India's public and festival holidays are listed up to 2035. Tick the days your studio is
   closed (national days are ticked already). Festival dates follow the lunar calendar - the ones marked
   *check the date* can be a day off from your almanac, so check them each year. Add your own days, import
   an `.ics` file, or add India's list for a later year. HR (designations that can manage accounts) can do
   the same from the calendar in the app (*New → Studio holidays*).
7. **Org chart** shows who reports to whom; people without a lead appear under *Not in a reporting line*.

## 3. Designations and permissions

Every person has a **department**, a **section** (e.g. Compositing → Roto), a **designation** and a **Reports to**.
Designations carry the permissions (console → *Designations*). Since 1.6.2 every server also has a full VFX
list - Studio Head, VFX Supervisor, VFX Producer, DFX / CG / Compositing / FX / Lighting / Animation / Roto-Paint /
Matchmove / DMP supervisors, Line Producer, Production Manager and Coordinator, discipline leads, Compositor, FX
Artist, Lighting TD, Animator, Modeler, Rigger, Matte Painter, Matchmove / Roto / Paint / Prep artists, Pipeline TD,
Render Wrangler, Data I/O and more - with sensible permissions. Rename, change or delete any of them; deleted ones
do not come back. The original defaults:

| Designation | Announce to | Create rooms | Reset passwords / disable accounts | Always visible |
|---|---|---|---|---|
| Management | everyone | ✓ | | ✓ |
| HOD, Supervisor | own department (+ its sections) | ✓ | | HOD ✓ |
| HR, IT | everyone | ✓ | ✓ | ✓ |
| Production | everyone | ✓ | | ✓ |
| Lead | own section | ✓ | | |
| Senior Artist, Artist | — | ✓ | | |
| Junior Artist, Trainee | — | | | |

Anyone with people reporting to them can also announce to **My team**. A designation can be limited to
"see only own department".

**Departments page.** You create the departments and sections; on the Users page each person is then
picked from that list (no free typing). Nothing gets a room by itself: tick **Chat room** next to a department
or section and its room appears, with members kept in step with people's department/section. Unticking it (or
deleting the department) keeps the room and its history as a normal room — delete it on the Rooms page if it
is not needed; ticking again continues the same room. Renaming a department or section renames it for
everyone in it and renames its room. A department or section can only be deleted when nobody is in it
(deleting a department also deletes its sections). The "All Studio" room is still a switch in Settings.
When upgrading, the departments and sections people already had are added to the list (without chat rooms),
and the old automatic rooms stay as normal rooms; ticking Chat room picks the old room up again.

## 4. Install the clients

Run **`Quillo-Client-Setup-x.y.z.exe`** on every PC. It asks *Install for me only* (no administrator
rights needed; goes to `%LOCALAPPDATA%\Programs`) or *for all users* (administrator). Leave the *Server address*
page empty and the client finds the server by itself; enter the server's IP only for PCs on a different subnet /
VLAN. Add `/ALLUSERS` or `/CURRENTUSER` to a silent install to skip the question.

Silent install (GPO, PDQ Deploy, login script…):

```
Quillo-Client-Setup-1.6.0.exe /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /ALLUSERS /SERVER=192.168.1.10
Quillo-Client-Setup-1.6.0.exe /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /ALLUSERS /MERGETASKS="autostart,!desktopicon"
```

Per-user settings live in `%APPDATA%\LANMessenger\`, the photo and preview cache in `%LOCALAPPDATA%\LANMessenger\`,
downloads in `Downloads\Quillo` (changeable in Settings; earlier installs keep `Downloads\LAN Messenger`).

**Portable use** without installing: copy a `build\dist\...` folder anywhere. A portable server keeps its data in
`server_data\` next to the exe if that folder exists (`firewall_setup.bat`, run as administrator, opens the ports).
For a preconfigured portable client, rename `client_config.example.json` to `client_config.json` and put the server
IP in it.

## 5. Updating

1. **Back up first**: console → **Settings → Back up now**.
2. **Server first:** run the new server setup over the old one. Accounts, messages, files and settings are kept;
   the database updates itself on start.
   On the server PC you can also use console → **Updates → Update this server...** and pick the new
   `Quillo-Server-Setup-x.y.z.exe`: the console closes, the setup runs and people reconnect by themselves.
3. **Then the clients:** console → **Updates → Publish client update...** and pick the new
   `Quillo-Client-Setup-x.y.z.exe` (or copy it into the server's `updates` folder yourself). Signed-in PCs are
   told straight away and show "version x.y.z is available". PCs where Quillo was installed *just for me* update
   with one click and **no administrator**; PCs installed *for all users* ask for an administrator (or run the
   setup with your deployment tool). People can also press *Settings → Check for updates*.
   The **Updates** page lists which versions the signed-in PCs run, so you can see who is behind; *Online now*
   shows it per person.

## 6. Running the server: service, data, backups

Service control (administrator command prompt, in the program folder; also `start`, `stop`, `restart`):

```
powershell -ExecutionPolicy Bypass -File service.ps1 status
```

Data folder (default `C:\ProgramData\LAN Messenger Server`; Start menu → *Server data folder* opens it):

| Item | What |
|---|---|
| `messenger.db` | accounts, messages, rooms (SQLite) |
| `files\` | shared files |
| `avatars\` | profile photos |
| `backups\` | nightly database backups (retention set in Settings) |
| `updates\` | client setups offered to the PCs |
| `tls\` | the server certificate. **Keep it**; a new one makes every PC show "server identity changed" |
| `config.json`, `server.log` | settings and log |

**Restore a backup:** stop the service, copy `backups\messenger_YYYY-MM-DD_HHMM.db` over `messenger.db`, **delete
`messenger.db-wal` and `messenger.db-shm`** next to it if they exist (otherwise the restore is undone), start it again.

If the server hits an unexpected error in the console window, it logs it to `server.log`, shows a short message and
keeps running. If it can't even read its settings, the note is in `%TEMP%\LANMessengerServer-startup.log`.

The console also has **Reports** (activity per day, department, person and room, storage per user; CSV export),
the **Audit log** of every administrative action, **Online now** and the **Server log**.

## 6a. Chat backup, message history and shared files

Console → **Settings**:

| Setting | Default | What it does |
|---|---|---|
| *Write a readable chat backup every night* | on | At the backup time, new messages are appended to text files, one per chat per month: `Chat logs\2026-09\Room - Falcon Comp (r12).txt`. Grep-able, open in Notepad. |
| *Keep messages in the app for* | 90 days | Older messages leave the live database (the app stays fast) — **only after** they are in the chat backup. *Forever* keeps everything. |
| *Delete shared files after* | 3 days (new installs) | Files are removed from the server's file storage; the file card tells people "available until …". Point *File storage folder* at a separate (temp) drive if you like. Upgraded servers keep their old value — change it here. |
| *Delete files nobody downloaded after* | never | Extra clean-up for forgotten uploads. |

**Per room:** Console → **Rooms** → *Keep files...* lets one room keep its shared files longer or shorter than the
setting above — e.g. *Forever* for a plates room, *30 days* for dailies. Direct chats always follow the server
setting. A file posted in several chats stays as long as the longest of them keeps it.

**Storage page:** Console → **Storage** shows how much space the shared files use, the free space on that disk,
and the space per person, per chat and the largest files. *Clean up now* deletes every file older than the number
of days you choose, straight away (the messages stay; the files show as expired). Every clean-up is in the audit
log.
| *Allow Buzz* | on | Lets people buzz one person (shake + ring, even on Do not disturb). One buzz per 20 s per person; users can opt out in their Settings. |

Reminders and scheduled messages are kept on the server, so they fire (and scheduled messages are sent) even when
the person's PC is off; a scheduled message is sent as that person and fails with a visible reason if they are no
longer allowed to post there (e.g. they left the room).

*Back up chats now* writes the text backup immediately. Deleted messages written before they were deleted stay in the
text backup (it is an archive).

## 7. Pipeline API (render farm, scripts)

Console → Settings → *Pipeline API*: tick *Allow*, click *New key*, save, restart the server. Then from any machine
(Windows or Linux render nodes):

```
curl -X POST http://SERVER:5152/api/message -H "Authorization: Bearer KEY" -H "Content-Type: application/json" -d "{\"to\": \"#Comp Team\", \"text\": \"FAL_020 v014 rendered\"}"
```

`to` is a username (`alice`) or a room (`#Comp Team`). `POST /api/announce` takes `title`, `text` and an optional
`department`; `GET /api/health` checks the server. Python (Deadline, Nuke, Houdini…), no extra packages:

```python
import json, urllib.request
req = urllib.request.Request("http://SERVER:5152/api/message",
    data=json.dumps({"to": "alice", "text": "Your render finished"}).encode(),
    headers={"Authorization": "Bearer KEY", "Content-Type": "application/json"})
urllib.request.urlopen(req, timeout=10)
```

Messages come from the account "Pipeline Bot" (renamable in Settings). The API is plain HTTP on the LAN,
protected by the key.

## 8. Network ports

| Port | What |
|---|---|
| TCP 5150 | chat, files, screen sharing, console (TLS encrypted) |
| UDP 5151 | automatic server discovery |
| TCP 5152 | pipeline API (only when enabled) |

The server setup allows the server program through Windows Firewall, which covers all three.

**A port is already in use?**
- **TCP (chat) port busy** → the server does not start and says which program holds the port
  (e.g. *"used by nginx.exe (PID 4312)"*). Close that program or pick another port in Settings. Clients that find the
  server automatically follow the new port; PCs with a typed address need `IP:port`.
- **UDP (discovery) port busy** → the server runs, but the Dashboard shows *"Automatic discovery is OFF"* with the
  program holding the port. Clients can still connect by typing the server address.
- **Pipeline API port busy** → the server runs without the API; the reason is in the Server log.

## 9. Security and privacy

- **Encrypted connections (TLS).** The server makes its own certificate; each PC remembers the server's fingerprint
  (shown on the console Dashboard) and refuses to send a password to a different machine pretending to be the server.
- **Passwords:** simple by default (at least 4 characters); the built-in admin/admin must be changed. Stricter
  rules are switches in Settings → Passwords: minimum length, letters + digits, refuse easy passwords, make
  people choose their own password at first sign-in and after a reset, expiry. Always on: a 60-second lock-out
  after 5 wrong passwords. Remembered passwords are encrypted with
  Windows DPAPI.
- **Audit log** of users, designations, rooms, settings, password resets, photo removals and chat reviews.
- **Chat review** (for HR / policy cases): an administrator can read a user's conversations. Every review is logged,
  users are told once at sign-in, and it can be switched off in Settings.
- **Screen sharing** always needs the consent of the person whose screen is shown, and a red bar stays visible.
- IT/HR can remove an inappropriate profile photo: Users → right-click → *Remove profile photo*.

## 10. Limits

- Files: 20 GB each by default; the admin can set up to 1 TB (Settings → *Max file size*). The server refuses an
  upload it has no disk space for, and interrupted transfers resume.
- Text messages: 100,000 characters (≈ 2,500 lines of Nuke script); longer text is offered as a file.
- Profile photos: 256 × 256, up to 400 KB (resized automatically).
- One server per studio; load-tested with 500 people online at once: 500 signed in within 2 s, ~390,000 deliveries with none lost, typical delivery 3–40 ms, server ~460 MB of RAM. The chat work runs on one CPU core, so around 500 *very* busy users
  delivery slows to about a second in the busiest rooms; nothing is lost.
- Screen sharing is view-only. Windows 10 / 11 only.
