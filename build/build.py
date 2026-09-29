"""Build Quillo: both programs (PyInstaller) and both installers (Inno Setup).

    build\\build.bat                 (or: .venv\\Scripts\\python build\\build.py)

Options:
    --no-installer    only build the program folders
    --no-exe          only recompile the installers from the existing program folders

Output (all inside the build folder):
    build\\dist\\LANMessenger\\          client program folder
    build\\dist\\LANMessengerServer\\    server program folder
    build\\output\\Quillo-Client-Setup-<version>.exe
    build\\output\\Quillo-Server-Setup-<version>.exe
"""

import os
import shutil
import subprocess
import sys
import time

BUILD = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(BUILD)
sys.path.insert(0, ROOT)

from common.version import APP_VERSION, PRODUCT_NAME, PUBLISHER, SERVER_PRODUCT_NAME  # noqa: E402

WORK = os.path.join(BUILD, "_work")
DIST = os.path.join(BUILD, "dist")
OUTPUT = os.path.join(BUILD, "output")

# Qt parts the app never uses (QML/Quick, PDF, software OpenGL, translations): ~45 MB per program
PRUNE = ["opengl32sw.dll", "Qt6Quick.dll", "Qt6Qml.dll", "Qt6QmlModels.dll", "Qt6QmlMeta.dll",
         "Qt6QmlWorkerScript.dll", "Qt6Pdf.dll", "Qt6VirtualKeyboard.dll", "Qt6OpenGL.dll",
         os.path.join("plugins", "imageformats", "qpdf.dll"), "translations",
         os.path.join("plugins", "platforminputcontexts")]

PROGRAMS = [
    # exe name, entry script, description (shown in the file properties)
    ("LANMessenger", os.path.join("client", "main.py"), PRODUCT_NAME),
    ("LANMessengerServer", os.path.join("server", "main.py"), SERVER_PRODUCT_NAME),
]

def plain_licence():
    """LICENSE.md as plain text: no HTML tags or markdown marks (the setup's licence page shows raw text)."""
    import re
    with open(os.path.join(ROOT, "LICENSE.md"), encoding="utf-8") as f:
        text = f.read()
    text = re.sub(r"<br>", "\n", text)
    text = re.sub(r"</?(div|sub)[^>]*>", "", text)
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", lambda m: m.group(1) if m.group(1) in m.group(2) else
                  f"{m.group(1)} ({m.group(2).removeprefix('mailto:')})", text)
    text = re.sub(r"^#+\s*", "", text, flags=re.M)
    text = re.sub(r"^>\s?", "", text, flags=re.M)
    text = text.replace("**", "").replace("`", "")
    text = re.sub(r"(?<!\w)\*([^*\n]+)\*(?!\w)", r"\1", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip() + "\n"


ISCC_CANDIDATES = [
    os.path.join(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"), "Inno Setup 6", "ISCC.exe"),
    os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), "Inno Setup 6", "ISCC.exe"),
    os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Inno Setup 6", "ISCC.exe"),
]


def step(text):
    print(f"\n=== {text} ===", flush=True)


def version_file(name, description):
    """Windows 'Details' tab info for the exe."""
    parts = (APP_VERSION.split(".") + ["0", "0", "0"])[:4]
    tup = ", ".join(str(int(p)) for p in parts)
    path = os.path.join(WORK, f"{name}_version.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers=({tup}), prodvers=({tup}), mask=0x3f, flags=0x0, OS=0x40004,
                    fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('CompanyName', '{PUBLISHER}'),
      StringStruct('FileDescription', '{description}'),
      StringStruct('FileVersion', '{APP_VERSION}'),
      StringStruct('InternalName', '{name}'),
      StringStruct('OriginalFilename', '{name}.exe'),
      StringStruct('ProductName', '{description}'),
      StringStruct('ProductVersion', '{APP_VERSION}')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
""")
    return path


def build_programs():
    step("Installing build requirements")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", "-r",
                           os.path.join(ROOT, "requirements.txt"), "pyinstaller"])
    os.makedirs(WORK, exist_ok=True)
    for name, script, description in PROGRAMS:
        step(f"Building {name}.exe")
        subprocess.check_call([
            sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--windowed", "--log-level", "WARN",
            "--name", name, "--icon", os.path.join(ROOT, "assets", "app.ico"),
            "--add-data", f"{os.path.join(ROOT, 'assets')}{os.pathsep}assets",
            "--version-file", version_file(name, description),
            "--paths", ROOT, "--workpath", WORK, "--specpath", WORK, "--distpath", DIST,
            os.path.join(ROOT, script),
        ], cwd=ROOT)
        base = os.path.join(DIST, name, "_internal", "PySide6")
        for rel in PRUNE:
            path = os.path.join(base, rel)
            if os.path.isdir(path):
                shutil.rmtree(path, ignore_errors=True)
            elif os.path.exists(path):
                os.remove(path)
    shutil.copy(os.path.join(ROOT, "client_config.example.json"), os.path.join(DIST, "LANMessenger"))
    shutil.copy(os.path.join(ROOT, "firewall_setup.bat"), os.path.join(DIST, "LANMessengerServer"))
    copy_ffmpeg(os.path.join(DIST, "LANMessengerServer"))
    for folder in ("LANMessenger", "LANMessengerServer"):          # the licence travels with the program
        for name in ("LICENSE.md", "THIRD_PARTY_NOTICES.md"):     # the apps check these at startup
            shutil.copy(os.path.join(ROOT, name), os.path.join(DIST, folder, name))
        with open(os.path.join(DIST, folder, "LICENSE.txt"), "w", encoding="utf-8-sig") as f:
            f.write(plain_licence())                              # readable copy for the setup page and shortcut
    for name, _, _ in PROGRAMS:
        size = sum(os.path.getsize(os.path.join(d, f)) for d, _, fs in os.walk(os.path.join(DIST, name)) for f in fs)
        print(f"  {name}: {size / 1e6:.0f} MB")


FFMPEG_NOTICE = """FFmpeg (ffmpeg.exe)

Quillo's server runs this separate program to make small previews of EXR, DPX, TIFF and video files that
people share in chats. It is not part of Quillo and is not changed by Quillo.

FFmpeg is free software by the FFmpeg developers (https://ffmpeg.org). This build ({version}) is licensed
under the GNU General Public License, version 3 (https://www.gnu.org/licenses/gpl-3.0.html).
The build comes from https://www.gyan.dev/ffmpeg/builds/ via the imageio-ffmpeg package; its source code
is available from https://ffmpeg.org/download.html and https://www.gyan.dev/ffmpeg/builds/.
"""


def copy_ffmpeg(folder):
    """ffmpeg.exe (from imageio-ffmpeg) next to the server, with its licence note. Only the server needs it."""
    try:
        import imageio_ffmpeg
        exe = imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as e:                               # noqa: BLE001
        print(f"  WARNING: no ffmpeg ({e}) - the server will not make EXR/MOV previews")
        return
    shutil.copy(exe, os.path.join(folder, "ffmpeg.exe"))
    version = subprocess.run([exe, "-version"], capture_output=True, text=True).stdout.split("\n")[0]
    with open(os.path.join(folder, "FFMPEG-LICENSE.txt"), "w", encoding="utf-8") as f:
        f.write(FFMPEG_NOTICE.format(version=version.strip() or "ffmpeg"))
    print(f"  ffmpeg: {version.strip()}")


def build_installers():
    iscc = next((p for p in ISCC_CANDIDATES if os.path.exists(p)), shutil.which("ISCC"))
    if not iscc:
        print("\nInno Setup 6 was not found - installers skipped.\n"
              "Install it from https://jrsoftware.org/isdl.php and run this build again.")
        return []
    os.makedirs(OUTPUT, exist_ok=True)
    made = []
    for iss in ("server.iss", "client.iss"):
        step(f"Compiling installer {iss}")
        subprocess.check_call([iscc, "/Q", f"/DAppVersion={APP_VERSION}", f"/DDistDir={DIST}",
                               f"/DRootDir={ROOT}", f"/O{OUTPUT}", os.path.join(BUILD, iss)])
    for f in sorted(os.listdir(OUTPUT)):
        if APP_VERSION in f:
            made.append(os.path.join(OUTPUT, f))
    return made


def main():
    t = time.time()
    args = set(sys.argv[1:])
    if "--no-exe" not in args:
        build_programs()
    made = [] if "--no-installer" in args else build_installers()
    step(f"Done in {time.time() - t:.0f}s")
    print(f"Program folders: {DIST}")
    for m in made:
        print(f"Installer:       {m}  ({os.path.getsize(m) / 1e6:.0f} MB)")


if __name__ == "__main__":
    main()
