"""Verify that the installed Windows entry point starts and closes the native main window."""
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryFile
import time


app = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(app))
import installer_helper as installer
import tray_common as tc

if sys.getwindowsversion().build < 22621:
    print("Native main window requires Windows build 22621 or higher")
    raise SystemExit(0)

if tc.ui_hwnd():
    raise SystemExit("Release startup check requires a clean test installation")
errors = TemporaryFile()
backend = subprocess.Popen(
    [str(app / "runtime" / "pythonw.exe"), "-B", str(app / "my_devices.pyw")],
    cwd=app, stderr=errors, creationflags=subprocess.CREATE_NO_WINDOW,
)
hwnd = 0
try:
    deadline = time.monotonic() + 30
    while not hwnd and backend.poll() is None and time.monotonic() < deadline:
        hwnd = tc.ui_hwnd()
        if not hwnd:
            time.sleep(.05)
    executable = installer.window_executable(hwnd)
    if executable != installer.FRONTEND:
        errors.seek(0)
        diagnostics = errors.read().decode("utf-8", errors="replace").strip()
        if diagnostics:
            print(diagnostics, file=sys.stderr)
        raise RuntimeError(f"Expected the WPF main window, got {executable!r}. Backend status: {backend.poll()!r}")
    if not installer.user32.PostMessageW(hwnd, 0x0010, 0, 0):  # WM_CLOSE
        raise RuntimeError("Could not close the native main window")
    if backend.wait(timeout=10) != 0 or tc.ui_hwnd():
        raise RuntimeError("The native main window did not close with its device backend")
    print("Installed entry point opened and closed the native WPF main window")
finally:
    if backend.poll() is None:
        if installer.owned_window(hwnd):
            installer.user32.PostMessageW(hwnd, 0x0010, 0, 0)
        try:
            backend.wait(timeout=3)
        except subprocess.TimeoutExpired:
            backend.terminate()
    errors.close()
