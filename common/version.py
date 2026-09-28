"""Version and product information (used by the apps, the exe file properties and the installers)."""

APP_VERSION = "1.7.1"
AUTHOR = "Utkarsh Tripathi"
PUBLISHER = AUTHOR
PRODUCT_NAME = "Quillo"
SERVER_PRODUCT_NAME = "Quillo Server"
LICENSE_NAME = "UT Community Licence 2.0"
# The credit line every window must show (licence section 5). common/licence.py checks it at startup.
LICENSE_LINE = f"{PRODUCT_NAME}  ·  © 2026 {AUTHOR}  ·  {LICENSE_NAME}"
REPOSITORY = "https://github.com/capsuleutkarsh-design/studio-lan-messenger"


def program_dir():
    """The folder with the licence files: next to the installed program, or the source folder."""
    import os
    import sys
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def license_path():
    """The licence to open for reading: plain LICENSE.txt when installed, LICENSE.md in the source folder."""
    import os
    import sys
    name = "LICENSE.txt" if getattr(sys, "frozen", False) else "LICENSE.md"
    return os.path.join(program_dir(), name)


# Internal names below keep the old "LANMessenger" spelling on purpose: installed copies, the installers
# and the Windows service find each other by them, so renaming would break upgrades.
# Named mutexes let the installers detect a running copy and ask to close it.
CLIENT_MUTEX = "LANMessengerClientMutex"
SERVER_MUTEX = "LANMessengerServerMutex"


ERROR_ALREADY_EXISTS = 183


def hold_mutex(name):
    """Create a named Windows mutex for the lifetime of the process.

    Returns (handle, already_running). No-op outside Windows."""
    import sys
    if sys.platform != "win32":
        return None, False
    import ctypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateMutexW.restype = ctypes.c_void_p
    handle = k32.CreateMutexW(None, False, name)
    # ACCESS_DENIED (5): it exists but belongs to another account (e.g. the SYSTEM service)
    return handle, ctypes.get_last_error() in (ERROR_ALREADY_EXISTS, 5)
