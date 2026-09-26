"""Version and product information (used by the apps, the exe file properties and the installers)."""

APP_VERSION = "1.6.3"
AUTHOR = "Utkarsh Tripathi"
PUBLISHER = AUTHOR
PRODUCT_NAME = "Quillo"
SERVER_PRODUCT_NAME = "Quillo Server"
LICENSE_NAME = "Quillo Community License"
LICENSE_LINE = f"{LICENSE_NAME}  ·  © 2026 {AUTHOR}"
REPOSITORY = "https://github.com/capsuleutkarsh-design/studio-lan-messenger"


def license_path():
    """The full licence text: LICENSE.txt next to the installed program, or LICENSE in the source folder."""
    import os
    import sys
    if getattr(sys, "frozen", False):
        return os.path.join(os.path.dirname(sys.executable), "LICENSE.txt")
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "LICENSE")


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
