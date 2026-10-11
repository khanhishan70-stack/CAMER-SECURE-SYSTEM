import subprocess
import sys


def lock_workstation() -> bool:
    """Send Windows to the lock screen (Ctrl+Alt+Del screen)."""
    if sys.platform != "win32":
        return False
    try:
        subprocess.run(
            ["rundll32.exe", "user32.dll,LockWorkStation"],
            check=False,
            capture_output=True,
            creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
        )
        return True
    except OSError:
        return False
