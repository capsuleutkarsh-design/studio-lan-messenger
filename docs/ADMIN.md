# Administrator & IT guide

Everything needed to install, run and look after LAN Messenger in a studio.
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

Pick one always-on PC (a small workstation or a VM is plenty; tested with 1,000 people online at once using
about 155 MB of RAM). Run **`LANMessenger-Server-Setup-x.y.z.exe`** as administrator and keep the tasks:

- **Allow the server through Windows Firewall**: needed for other PCs to connect
- **Run as a background service**: starts with Windows, nobody needs to be logged in
  (alternative: *start in the tray when I log in*)

The setup installs to `C:\Program Files\LAN Messenger Server` and keeps all data in
**`C:\ProgramData\LAN Messenger Server`**, which upgrades never touch. On uninstall it asks whether to delete the data.

## 2. First-time setup

Open **LAN Messenger Server** from the Start menu. It opens the console for the running service; the console
can also connect to a server on another PC (admin / IT login).

1. Sign in as **admin / admin**. You must choose a new password straight away.
2. **Designations**: rename or add titles to match your studio (see below).
3. **Users → Add user**, or **Import CSV** with the columns
   `username,password,display_name,department,section,designation,reports_to,title`
   (`reports_to` = the lead's username; `designation` must match a name on the Designations page).
   New users choose their own password at their first sign-in.
4. **Settings**: backup folder (ideally another disk), password rules, file size and clean-up, pipeline API,
   chat review.
5. Department and section rooms appear by themselves. Use **Rooms → New room** for projects.
6. **Org chart** shows who reports to whom; people without a lead appear under *Not in a reporting line*.

## 3. Designations and permissions

Every person has a **department**, a **section** (e.g. Compositing → Roto), a **designation** and a **Reports to**.
Designations carry the permissions (console → *Designations*). Defaults:

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
"see only own department". Automatic rooms: one per department and per section (optionally "All Studio");
membership follows each person's department and section.

## 4. Install the clients

Run **`LANMessenger-Client-Setup-x.y.z.exe`** on every PC. Leave the *Server address* page empty and the client finds
the server by itself; enter the server's IP only for PCs on a different subnet / VLAN.

Silent install (GPO, PDQ Deploy, login script…):

```
LANMessenger-Client-Setup-1.3.0.exe /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /SERVER=192.168.1.10
LANMessenger-Client-Setup-1.3.0.exe /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /MERGETASKS="autostart,!desktopicon"
```

Per-user settings live in `%APPDATA%\LANMessenger\`, the photo and preview cache in `%LOCALAPPDATA%\LANMessenger\`,
downloads in `Downloads\LAN Messenger` (changeable in Settings).

**Portable use** without installing: copy a `build\dist\...` folder anywhere. A portable server keeps its data in
`server_data\` next to the exe if that folder exists (`firewall_setup.bat`, run as administrator, opens the ports).
For a preconfigured portable client, rename `client_config.example.json` to `client_config.json` and put the server
IP in it.

## 5. Updating

1. **Back up first**: console → **Settings → Back up now**.
2. **Server first:** run the new server setup over the old one. Accounts, messages, files and settings are kept;
   the database updates itself on start.
3. **Then the clients:** either run the new client setup on each PC (it closes and restarts a running client and
   keeps everyone's settings), or copy `LANMessenger-Client-Setup-x.y.z.exe` into the server's `updates` folder
   (console → *Client updates*). Signed-in users then see "version x.y.z is available"; people who are
   administrators on their PC install it with one click, everyone else is told to ask IT.

## 6. Running the server: service, data, backups

Service control (administrator command prompt, in the program folder; also `start`, `stop`, `restart`):

```
powershell -ExecutionPolicy Bypass -File service.ps1 status
```

Data folder `C:\ProgramData\LAN Messenger Server`:

| Item | What |
|---|---|
| `messenger.db` | accounts, messages, rooms (SQLite) |
| `files\` | shared files |
| `avatars\` | profile photos |
| `backups\` | nightly database backups (retention set in Settings) |
| `updates\` | client setups offered to the PCs |
| `tls\` | the server certificate. **Keep it**; a new one makes every PC show "server identity changed" |
| `config.json`, `server.log` | settings and log |

**Restore a backup:** stop the service, copy `backups\messenger_YYYY-MM-DD_HHMM.db` over `messenger.db`, start it again.

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
- **Passwords:** rules for length and letters + digits, common passwords refused, forced change at first sign-in and
  after an admin reset, 60-second lock-out after 5 wrong passwords. Remembered passwords are encrypted with
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
- One server per studio; tested with 1,000 people online at once.
- Screen sharing is view-only. Windows 10 / 11 only.
