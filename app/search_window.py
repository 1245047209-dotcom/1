"""Placement and optional header dragging for the search window."""
import ctypes
from ctypes import wintypes
import sys


def _native_owner(parent):
    handle = getattr(parent, "_nexus_native_hwnd", 0)
    return handle if sys.platform == "win32" and isinstance(handle, int) else 0


def attach_dialog_owner(window, parent):
    """Use the visible main window as the owner of a settings or search dialog."""
    main = _native_owner(parent)
    if not main:
        window.transient(parent)
        return

    user = ctypes.WinDLL("user32")
    user.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    user.GetAncestor.restype = wintypes.HWND
    user.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
    user.SetWindowLongPtrW.restype = ctypes.c_ssize_t

    def link_owner(event):
        if event.widget is window:
            wrapper = user.GetAncestor(window.winfo_id(), 2)
            if wrapper:
                user.SetWindowLongPtrW(wrapper, -8, main)  # GWLP_HWNDPARENT: top-level owner

    window.bind("<Map>", link_owner, add="+")


def _native_geometry(handle):
    user = ctypes.WinDLL("user32")
    user.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user.GetWindowRect.restype = wintypes.BOOL
    user.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user.GetClientRect.restype = wintypes.BOOL
    frame, client = wintypes.RECT(), wintypes.RECT()
    if user.GetWindowRect(handle, ctypes.byref(frame)) and user.GetClientRect(handle, ctypes.byref(client)):
        return client.right, client.bottom, frame.left, frame.top
    return None


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

    owner_hwnd = _native_owner(owner) or user32.GetAncestor(owner.winfo_id(), 2)  # GA_ROOT
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


def window_work_area(owner, window):
    work, border_x, border_y = None, 0, 0
    if sys.platform == "win32":
        work, border_x, border_y = _windows_bounds(owner, window)
    if work is None:
        work = 0, 0, owner.winfo_screenwidth(), owner.winfo_screenheight()
    return work, border_x, border_y


def match_owner_size(window, parent):
    """Keep the dialog's client size in sync with the main window."""
    owner = parent.winfo_toplevel()
    native = _native_owner(owner)
    window.resizable(False, False)
    owner.update_idletasks()
    last_size = None

    def sync(event=None):
        nonlocal last_size
        if event is not None and event.widget is not owner:
            return
        native_geometry = _native_geometry(native) if native else None
        if native_geometry:
            width, height, x, y = native_geometry
        elif event is None:
            width, height = owner.winfo_width(), owner.winfo_height()
            if width <= 1:
                width = owner.winfo_reqwidth()
            if height <= 1:
                height = owner.winfo_reqheight()
            x, y = owner.winfo_x(), owner.winfo_y()
            window.geometry(f"{width}x{height}+{x}+{y}")
            window.update_idletasks()
        else:
            width, height = event.width, event.height
            x, y = window.winfo_x(), window.winfo_y()
        if min(width, height) <= 1 or last_size == (width, height):
            return
        (left, top, right, bottom), border_x, border_y = window_work_area(owner if native else window, window)
        x = max(left, min(x, right - width - border_x))
        y = max(top, min(y, bottom - height - border_y))
        window.geometry(f"{width}x{height}+{x}+{y}")
        last_size = width, height

    sync()
    if not native:
        subscription = owner.bind("<Configure>", sync, add="+")

        def detach(event):
            if event.widget is window and owner.winfo_exists():
                owner.unbind("<Configure>", subscription)

        window.bind("<Destroy>", detach, add="+")
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
