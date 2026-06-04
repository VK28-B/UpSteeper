from __future__ import annotations

import ctypes
import os
import sys


# =========================
# ADMIN CHECK
# =========================

def is_admin() -> bool:
    """
    Check if application is running
    with administrator privileges.
    """

    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())

    except Exception:
        return False


def run_as_admin() -> None:
    """
    Relaunch app with administrator
    privileges if not already elevated.
    """

    if is_admin():
        return

    try:
        ctypes.windll.shell32.ShellExecuteW(
            None,
            "runas",
            sys.executable,
            " ".join(sys.argv),
            None,
            1
        )

    except Exception as exc:
        print(f"Failed to elevate privileges: {exc}")

    sys.exit()


# =========================
# FORCE ADMIN RIGHTS
# =========================

if os.name == "nt":
    run_as_admin()


# =========================
# START APPLICATION
# =========================

from upsteeper.app import main


if __name__ == "__main__":
    main()