"""Placement and optional header dragging for the search window."""
import ctypes
from ctypes import wintypes
import sys


def _windows_bounds(owner, window):
    class MonitorInfo(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("monitor", wintypes.RECT),
                    ("work", wintypes.RECT), ("flags", wintypes.DWORD)]

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    user32.GetAncestor.restype = wintypes.HWND
    user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
    user32.MonitorFromWindow.restype = wintypes.HMONITOR
    user32.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.POINTER(MonitorInfo)]
    user32.GetMonitorInfoW.restype = wintypes.BOOL
    user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user32.GetWindowRect.restype = wintypes.BOOL

    owner_hwnd = user32.GetAncestor(owner.winfo_id(), 2)  # GA_ROOT
    monitor = user32.MonitorFromWindow(owner_hwnd, 2)  # MONITOR_DEFAULTTONEAREST
    info = MonitorInfo(size=ctypes.sizeof(MonitorInfo))
    work = None
    if monitor and user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
        work = info.work.left, info.work.top, info.work.right, info.work.bottom

    child = window.winfo_id()
    wrapper = user32.GetAncestor(child, 2)
    frame = wintypes.RECT()
    border_x, border_y = 0, 0
    if wrapper and wrapper != child and user32.GetWindowRect(wrapper, ctypes.byref(frame)):
        border_x = max(0, frame.right - frame.left - window.winfo_width())
        border_y = max(0, frame.bottom - frame.top - window.winfo_height())
    return work, border_x, border_y


def place_bottom_right(window, parent, width, height, *, margin=16):
    """Place a withdrawn toplevel once in its owner's monitor work area."""
    owner = parent.winfo_toplevel()
    # Realize the frame while hidden, on the owner's monitor so the border
    # measurements use the same DPI as the eventual placement.
    window.geometry(f"{width}x{height}+{owner.winfo_x()}+{owner.winfo_y()}")
    window.update_idletasks()
    work, border_x, border_y = None, 0, 0
    if sys.platform == "win32":
        work, border_x, border_y = _windows_bounds(owner, window)
    if work is None:
        work = 0, 0, owner.winfo_screenwidth(), owner.winfo_screenheight()

    left, top, right, bottom = work
    width = min(width, max(1, right - left - border_x - margin * 2))
    height = min(height, max(1, bottom - top - border_y - margin * 2))
    min_width, min_height = window.minsize()
    if min_width > width or min_height > height:
        window.minsize(min(min_width, width), min(min_height, height))
    x = max(left, right - width - border_x - margin)
    y = max(top, bottom - height - border_y - margin)
    # The '+' selects absolute coordinates even if the values are negative.
    window.geometry(f"{width}x{height}+{x}+{y}")
    window.deiconify()


def bind_window_drag(window, *widgets):
    """Make the specified header widgets act as an extra title bar."""
    start = None

    def press(event):
        nonlocal start
        start = event.x_root - window.winfo_x(), event.y_root - window.winfo_y()

    def move(event):
        if start is not None:
            window.geometry(f"+{event.x_root - start[0]}+{event.y_root - start[1]}")

    def release(_event):
        nonlocal start
        start = None

    for widget in widgets:
        widget.configure(cursor="fleur")
        widget.bind("<ButtonPress-1>", press, add="+")
        widget.bind("<B1-Motion>", move, add="+")
        widget.bind("<ButtonRelease-1>", release, add="+")
