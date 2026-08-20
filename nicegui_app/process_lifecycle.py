from __future__ import annotations

import ctypes
import os
import threading
import time


SYNCHRONIZE = 0x00100000
WAIT_OBJECT_0 = 0


def start_launcher_watchdog() -> None:
    """Exit this server if its explicitly recorded PowerShell launcher exits."""
    raw_pid = os.environ.get("NICEGUI_LAUNCHER_PID", "").strip()
    try:
        launcher_pid = int(raw_pid)
    except ValueError:
        return
    if launcher_pid <= 0 or os.name != "nt":
        return

    def watch() -> None:
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(SYNCHRONIZE, False, launcher_pid)
        if not handle:
            os._exit(0)
        try:
            while True:
                if kernel32.WaitForSingleObject(handle, 1000) == WAIT_OBJECT_0:
                    os._exit(0)
                time.sleep(0.25)
        finally:
            kernel32.CloseHandle(handle)

    threading.Thread(target=watch, name="nicegui-launcher-watchdog", daemon=True).start()
