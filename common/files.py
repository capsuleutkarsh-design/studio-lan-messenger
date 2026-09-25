"""Small file helpers shared by the server and the client."""

import os
import time


def replace_file(src: str, dst: str, attempts: int = 20, delay: float = 0.05):
    """os.replace that survives Windows' short file locks.

    Antivirus scanners and the search indexer open freshly written files for a moment; a rename
    during that moment fails with "Access is denied". Retry for up to ~1 second before giving up.
    """
    for i in range(attempts):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if i == attempts - 1:
                raise
            time.sleep(delay)
