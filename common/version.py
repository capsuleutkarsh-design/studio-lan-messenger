"""Version and product information (used by the apps, the exe file properties and the installers)."""

APP_VERSION = "1.5.5"
PUBLISHER = "LAN Messenger"
PRODUCT_NAME = "LAN Messenger"
SERVER_PRODUCT_NAME = "LAN Messenger Server"

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
