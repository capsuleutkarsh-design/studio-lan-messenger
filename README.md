# LAN Messenger

A studio messenger with file sharing that runs entirely on your local network,
in the spirit of Output Messenger. There are two programs:

| Program | Where | What it does |
|---|---|---|
| **LANMessengerServer.exe** | install **once**, on an always-on PC | stores accounts, messages and shared files; admin console |
| **LANMessenger.exe** | every artist PC | the chat client |

Nothing goes to the internet. The original look came from the PyBlackBox demo (MIT licensed, by Wanderson
M. Pimenta), kept as the "Classic" theme.

## Features

### Chat
- **1-to-1 chat** with *sent ✓ / delivered ✓✓ / read ✓✓ (lime)* ticks and a typing indicator
- **Chat rooms** (group chats) created by users or the admin; add/remove members, rename, leave
- **Reply / quote**, **edit** (↑ edits your last message), **delete for everyone**, **forward**,
  **pin** messages (pin bar at the top of a chat), **mute** a chat
- **@mentions** with name suggestions. A mention outlines the message and still notifies you in a muted chat
- **Seen by N of M** under your last message in a room (click for the list)
- **Emoji reactions** on any message (hover a message → 🙂), **stickers** (185 desi, festival, mood and
  character stickers in 15 packs, button next to the emoji button) and **polls** (📎 → *Create a poll*: single or
  multiple answers, optional anonymous voting, live results, the creator can close it)
- **Nuke scripts and long text**: paste up to 100,000 characters (≈ 2,500 lines of Nuke script) as a message.
  Long text and anything that looks like a Nuke script shows as a compact code card with *Copy* (paste
  straight into Nuke), *Show all* and *Save as .nk* — the chat never freezes on it. Longer text is offered as a file.
- **Image previews** in the chat, **message search** across all your chats
- `\\server\share\...`, `C:\...` and `http://` links are clickable; right-click a path for *Copy path* / *Open folder*
- **Announcements** to everyone, a department, a section or your team (depending on your designation). They pop up
  and stay on top until acknowledged; people who were offline see them when they log in; the sender can check
  **who has read it**
- **Screen sharing** (view only): ask to see someone's screen, or show yours. The person whose screen is shown
  must always accept, and a red bar with *Stop* stays visible while sharing
- **Profile photos** and a **custom status** with emoji and "clear after" (e.g. 🍽️ Out for lunch — 1 hour,
  🎬 Rendering — don't touch my PC). Click your picture at the bottom left
- **People grouped by department** with presence: Online / Away / Do not disturb / Invisible. Auto-away when idle.
  Tray icon with unread badge, Windows notifications and a sound
- **Three looks**: Midnight (dark, default), Light, and Classic (the original black & lime), each with six accent
  colours — Settings → Theme
- Keyboard: **Ctrl+K** find a person or room, **Ctrl+F** search messages, **↑** edit your last message

### Files
- Send files in any chat: attach button, **drag & drop** or **paste** (screenshots too). Files go through
  the server, so you can send to people who are **offline**. Progress/speed/cancel, and downloads resume after
  an interruption. Default max file size: 20 GB
- **Send a whole folder** (e.g. an image sequence): it is zipped automatically and the receiver gets an
  **Extract** button (protected against malicious zip paths)
- Old shared files can be deleted automatically after N days, and files nobody downloaded after N days

### Organisation
- Every user has a **department**, a **section** (e.g. Compositing → Roto), a **designation** and a
  **Reports to** (lead/supervisor). Contacts are grouped Department → Section, leads see **My team** on top
- **Designations with permissions** (console → *Designations*). Defaults:

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
- **Automatic rooms**: one per department and per section (and optionally "All Studio"); membership follows
  the user's department/section
- **Directory / org chart** in the client and the console: by department & section, or by reporting line

### Security
- **Encrypted connections (TLS)**. The server creates its own certificate on first start. Each PC remembers
  the server's fingerprint (shown on the console Dashboard); if a different machine pretends to be the server,
  the client warns and refuses to send the password
- **Password rules** (length, letters+digits, common passwords refused), **must change the password** at first
  sign-in or after an admin reset, lock-out for 60 s after 5 wrong passwords
- **Audit log** of every administrative action (users, designations, rooms, settings, password resets, chat review)
- Remembered passwords are encrypted with Windows DPAPI; names/messages are shown as plain text (no HTML injection)

### Administration (server console)
- The server runs as a **background service** (starts with Windows, nobody needs to be logged in) or in the tray
- The console connects to the running service, or to a server on another PC (admin/IT login)
- **Nightly database backups** with retention, plus *Back up now*
- **Reports**: messages per day, most active people/departments/rooms, storage per user; CSV export
- **Chat review** for HR/policy cases: an administrator can read a user's conversations. Every review is
  written to the audit log, and users are told about it once at sign-in. Can be switched off in Settings
- **Client updates**: drop a new client setup into the server's `updates` folder; clients offer to install it
- **Pipeline API**: render farm / scripts can post messages and announcements (see below)

## 1. Install the server (once, on an always-on PC)

Run **`LANMessenger-Server-Setup-x.y.z.exe`** as administrator. Recommended tasks: *Allow through Windows Firewall*
and *Run as a background service* (alternative: *start in the tray when I log in*). It:
- installs to `C:\Program Files\LAN Messenger Server`
- keeps all data in **`C:\ProgramData\LAN Messenger Server`** (never deleted by an upgrade)
- stops a running server before upgrading, and on uninstall asks whether to delete the data

Then open **LAN Messenger Server** from the Start menu (it opens the console for the running service):
1. Sign in as **admin / admin**; you are asked to choose a new password straight away.
2. Check **Designations** (rename/add to match your studio's titles).
3. **Users → Add user**, or **Import CSV** with columns
   `username,password,display_name,department,section,designation,reports_to,title`
   (`reports_to` = the lead's username; `designation` must match a name on the Designations page).
   New users choose their own password at their first sign-in.
4. **Settings**: backup folder (ideally a different disk), password rules, file clean-up, pipeline API.
5. Department and section rooms appear by themselves. Use **Rooms → New room** for projects.

Data folder: `messenger.db` (SQLite: accounts, messages), `files\` (shared files), `backups\`, `updates\`,
`tls\` (server certificate — keep it, otherwise every PC shows the "server identity changed" warning),
`config.json`, `server.log`.

Service control (administrator command prompt, in the program folder):

```
powershell -ExecutionPolicy Bypass -File service.ps1 status
```

(`start`, `stop`, `restart` work the same way.)

**Restore a backup**: stop the service, copy `backups\messenger_YYYY-MM-DD_HHMM.db` over `messenger.db`, start it again.

## 2. Install the client (every PC)

Run **`LANMessenger-Client-Setup-x.y.z.exe`**. Leave the *Server address* page empty so the client finds the server
by itself, or enter the server's IP if the PC is on a different subnet/VLAN.

Silent install for IT (GPO, PDQ Deploy, login script...):

```
LANMessenger-Client-Setup-1.3.0.exe /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /SERVER=192.168.1.10
LANMessenger-Client-Setup-1.3.0.exe /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /MERGETASKS="autostart,!desktopicon"
```

Upgrades: run the new setup over the old one; a running client is closed and restarted, and every user's settings,
remembered logins and download history are kept (`%APPDATA%\LANMessenger\`). Downloads go to
`Downloads\LAN Messenger` (changeable in Settings).

**Updating all PCs from the server**: copy the new `LANMessenger-Client-Setup-x.y.z.exe` into the server's `updates`
folder (console → *Client updates* shows it). Signed-in users see "version x.y.z is available"; users who are
administrators on their PC can install it with one click, others are told to ask IT.

## 3. Pipeline API (render farm, scripts)

Console → Settings → *Pipeline API*: tick *Allow*, click *New key*, save, and restart the server. Then from any
machine (Windows or Linux render nodes):

```
curl -X POST http://SERVER:5152/api/message -H "Authorization: Bearer KEY" -H "Content-Type: application/json" -d "{\"to\": \"#Comp Team\", \"text\": \"FAL_020 v014 rendered\"}"
```

`to` is a username (`alice`) or a room (`#Comp Team`). `POST /api/announce` takes `title`, `text` and an optional
`department`; `GET /api/health` checks the server. Python (Deadline, Nuke, Houdini...), no extra packages:

```python
import json, urllib.request
req = urllib.request.Request("http://SERVER:5152/api/message",
    data=json.dumps({"to": "alice", "text": "Your render finished"}).encode(),
    headers={"Authorization": "Bearer KEY", "Content-Type": "application/json"})
urllib.request.urlopen(req, timeout=10)
```

Messages come from the account "Pipeline Bot" (the name can be changed in Settings). The API is plain HTTP on the LAN,
protected by the key.

## Network ports

| Port | What |
|---|---|
| TCP 5150 | chat, files, screen sharing, console (TLS encrypted) |
| UDP 5151 | automatic server discovery |
| TCP 5152 | pipeline API (only when enabled) |

## Build the programs and installers

Needs Python 3.11+ and [Inno Setup 6](https://jrsoftware.org/isdl.php) on the build PC only. Double-click:

```
build\build.bat
```

| Output | What |
|---|---|
| `build\output\LANMessenger-Server-Setup-x.y.z.exe` | server installer |
| `build\output\LANMessenger-Client-Setup-x.y.z.exe` | client installer |
| `build\dist\LANMessengerServer\`, `build\dist\LANMessenger\` | portable program folders (no install needed) |

The version number is in `common\version.py`; change it before building a new release.
`build\build.bat --no-installer` builds only the program folders; `--no-exe` only recompiles the installers.

Portable use without the installer: copy a `build\dist\...` folder anywhere. A portable server keeps its data in
`server_data\` next to the exe if that folder exists; use `firewall_setup.bat` (as administrator) to open the ports.
For a preconfigured portable client, rename `client_config.example.json` to `client_config.json` and put the server IP in it.

## Run from source (development)

```
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python -m server.main        (or run_server.bat)
.venv\Scripts\python -m client.main        (or run_client.bat)
.venv\Scripts\python -m unittest discover -s . -p "test_*.py"
```

Server options: `--headless` (no window, used by the service), `--minimized`, `--console [HOST:PORT]`
(console for a running server), `--data FOLDER`.

## How it works

```
 client ══TLS 5150══► server  one JSON line per message (login, send, history, typing...)
 client ══TLS 5150══► server  separate connection per file upload/download and per screen share
 client ──UDP 5151──► LAN     broadcast "where is the server?" → server replies (with its fingerprint)
```

- `common/`: wire protocol, theme, icons, org-chart tree, version, file helpers
- `server/`: `core.py` (asyncio server, routing, permissions, files, screen relay), `db.py` (SQLite), `org.py`
  (designations/visibility), `tls.py`, `pipeline_api.py`, `console_api.py`, `admin_gui.py` (console)
- `client/`: `network.py` (TLS connection + discovery), `transfers.py`, `store.py` (state), `folders.py`,
  `previews.py`, `screenshare.py`, `ui/` (windows)
- `build/`: build script, Inno Setup scripts, `service.ps1`
- `tests/`: integration tests against a real server

## Limits

- Files: 20 GB each by default (server console → Settings → *Max file size*); interrupted transfers resume.
- Text messages: 100,000 characters each. Profile photos: 256 × 256, up to 400 KB (resized automatically).

- One server per studio. Tested with 1,000 people online at the same time (0 errors, ~155 MB RAM).
- Screen sharing is view-only (no remote control). Windows only.

## Stickers

The packs in `assets/stickers` were cut from the Freepik sheets in `Sticker pack\` with
`tools\make_stickers.py` (needs Pillow, numpy and scipy on the build PC only). To add a pack, put its zip in that
folder, add a line to `PACKS` in the script and run it again. Freepik's free licence asks for the credit
"Designed by Freepik"; it is shown in the sticker picker (and stored in each pack's `pack.json`).
