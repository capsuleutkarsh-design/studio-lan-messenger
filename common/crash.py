"""Last line of defence: an unexpected error is logged and reported, and the program keeps running.

Without this, an error in window code before the Qt event loop starts ends the whole process (and with it
the chat server). Errors inside the event loop (clicks, timers, network events) come through
sys.excepthook as well; they are logged and shown once instead of being lost on a missing console.
"""

import logging
import sys
import threading
import time
import traceback

log = logging.getLogger("crash")
_last_shown = [0.0]


def install(app_name, log_file=""):
    def report(exc_type, exc, tb):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc, tb)
            return
        text = "".join(traceback.format_exception(exc_type, exc, tb))
        log.error("Unexpected error:\n%s", text)
        _show(app_name, log_file, exc)

    def thread_report(args):
        if args.exc_type is SystemExit:
            return
        report(args.exc_type, args.exc_value, args.exc_traceback)

    sys.excepthook = report
    threading.excepthook = thread_report


def _show(app_name, log_file, exc):
    """A short message box, at most every 30 s, and only from the GUI thread."""
    try:
        from PySide6.QtCore import QThread, QTimer, Qt
        from PySide6.QtWidgets import QApplication, QMessageBox
    except ImportError:
        return
    app = QApplication.instance()
    if app is None or QThread.currentThread() != app.thread():
        return
    if time.time() - _last_shown[0] < 30:
        return
    _last_shown[0] = time.time()
    where = f"\n\nDetails were saved to:\n{log_file}" if log_file else ""

    def box():
        m = QMessageBox(QMessageBox.Warning, app_name,
                        f"{app_name} ran into an unexpected problem and skipped what it was doing. "
                        f"It keeps running.\n\n{type(exc).__name__}: {str(exc)[:300]}{where}")
        m.setAttribute(Qt.WA_DeleteOnClose)
        m.setModal(False)
        m.show()
    QTimer.singleShot(0, box)
