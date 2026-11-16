"""Count activity time for the AULA receiver without reading key contents."""
from __future__ import annotations

import ctypes
from ctypes import wintypes as w
import json
import os
from pathlib import Path
import threading
import time
import uuid

ACTIVITY_PATH = Path(__file__).resolve().with_name("battery_activity.json")
IDLE_SECONDS = 60
HEARTBEAT_MAX_AGE = 4
_thread = None
_start_lock = threading.Lock()


def is_target_device(name):
    return "VID_3554&PID_FA09" in name.upper()


class ActivityClock:
    """Only an aggregate active-time counter and the last event time live here."""
    def __init__(self, now=None):
        self.accounted_at = time.monotonic() if now is None else now
        self.last_event = None
        self.active_seconds = 0.0
        self.session = uuid.uuid4().hex

    def advance(self, now):
        gap = now - self.accounted_at
        if gap < 0 or gap > 5:
            # Suspend/resume or a stalled observer has an unknown activity gap.
            self.last_event = None
            self.active_seconds = 0.0
            self.session = uuid.uuid4().hex
        elif self.last_event is not None:
            self.active_seconds += max(0, min(now, self.last_event + IDLE_SECONDS) - self.accounted_at)
        self.accounted_at = now

    def note_event(self, now):
        self.advance(now)
        self.last_event = now

    def snapshot(self, now):
        self.advance(now)
        return {"session": self.session, "active_seconds": self.active_seconds,
                "active": self.last_event is not None and now - self.last_event < IDLE_SECONDS}


def _monitor():
    user = ctypes.WinDLL("user32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    result_type = ctypes.c_ssize_t
    callback_type = ctypes.WINFUNCTYPE(result_type, w.HWND, w.UINT, w.WPARAM, w.LPARAM)

    class WindowClass(ctypes.Structure):
        _fields_ = [("style", w.UINT), ("wndproc", callback_type),
                    ("class_extra", ctypes.c_int), ("window_extra", ctypes.c_int),
                    ("instance", w.HINSTANCE), ("icon", w.HICON), ("cursor", w.HANDLE),
                    ("background", w.HBRUSH), ("menu", w.LPCWSTR), ("name", w.LPCWSTR)]

    class RawDevice(ctypes.Structure):
        _fields_ = [("page", w.USHORT), ("usage", w.USHORT), ("flags", w.DWORD), ("target", w.HWND)]

    class DeviceEntry(ctypes.Structure):
        _fields_ = [("device", w.HANDLE), ("kind", w.DWORD)]

    class Header(ctypes.Structure):
        _fields_ = [("kind", w.DWORD), ("size", w.DWORD), ("device", w.HANDLE), ("param", w.WPARAM)]

    kernel.CreateMutexW.argtypes = [w.LPVOID, w.BOOL, w.LPCWSTR]
    kernel.CreateMutexW.restype = w.HANDLE
    kernel.CloseHandle.argtypes = [w.HANDLE]
    kernel.ReleaseMutex.argtypes = [w.HANDLE]
    kernel.GetModuleHandleW.argtypes = [w.LPCWSTR]
    kernel.GetModuleHandleW.restype = w.HINSTANCE
    user.RegisterClassW.argtypes = [ctypes.POINTER(WindowClass)]
    user.RegisterClassW.restype = w.ATOM
    user.UnregisterClassW.argtypes = [w.LPCWSTR, w.HINSTANCE]
    user.CreateWindowExW.argtypes = [w.DWORD, w.LPCWSTR, w.LPCWSTR, w.DWORD,
                                    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                    w.HWND, w.HMENU, w.HINSTANCE, w.LPVOID]
    user.CreateWindowExW.restype = w.HWND
    user.DefWindowProcW.argtypes = [w.HWND, w.UINT, w.WPARAM, w.LPARAM]
    user.DefWindowProcW.restype = result_type
    user.DestroyWindow.argtypes = [w.HWND]
    user.GetMessageW.argtypes = [ctypes.POINTER(w.MSG), w.HWND, w.UINT, w.UINT]
    user.GetMessageW.restype = w.BOOL
    user.TranslateMessage.argtypes = [ctypes.POINTER(w.MSG)]
    user.DispatchMessageW.argtypes = [ctypes.POINTER(w.MSG)]
    user.DispatchMessageW.restype = result_type
    user.SetTimer.argtypes = [w.HWND, ctypes.c_size_t, w.UINT, w.LPVOID]
    user.SetTimer.restype = ctypes.c_size_t
    user.RegisterRawInputDevices.argtypes = [ctypes.POINTER(RawDevice), w.UINT, w.UINT]
    user.RegisterRawInputDevices.restype = w.BOOL
    user.GetRawInputData.argtypes = [w.HANDLE, w.UINT, w.LPVOID, ctypes.POINTER(w.UINT), w.UINT]
    user.GetRawInputData.restype = w.UINT
    user.GetRawInputDeviceInfoW.argtypes = [w.HANDLE, w.UINT, w.LPVOID, ctypes.POINTER(w.UINT)]
    user.GetRawInputDeviceInfoW.restype = w.UINT
    user.GetRawInputDeviceList.argtypes = [ctypes.POINTER(DeviceEntry), ctypes.POINTER(w.UINT), w.UINT]
    user.GetRawInputDeviceList.restype = w.UINT

    # One collector owns the Raw Input window; the panel/tray share its totals.
    while True:
        ctypes.set_last_error(0)
        mutex = kernel.CreateMutexW(None, True, "Local\\NexusAulaActivity_3554_FA09")
        if not mutex:
            return
        if ctypes.get_last_error() != 183:
            break
        kernel.CloseHandle(mutex)
        time.sleep(2)

    clock = ActivityClock()
    names = {}
    available = False
    temporary = ACTIVITY_PATH.with_name(f"battery_activity.{os.getpid()}.tmp")

    def matches(handle):
        if not handle:
            return False
        if handle not in names:
            size = w.UINT()
            user.GetRawInputDeviceInfoW(handle, 0x20000007, None, ctypes.byref(size))
            if not size.value or size.value > 4096:
                return False
            name = ctypes.create_unicode_buffer(size.value + 1)
            result = user.GetRawInputDeviceInfoW(handle, 0x20000007, name, ctypes.byref(size))
            names[handle] = result != 0xFFFFFFFF and is_target_device(name.value)
        return names[handle]

    def refresh_devices():
        nonlocal available
        names.clear()
        count = w.UINT()
        user.GetRawInputDeviceList(None, ctypes.byref(count), ctypes.sizeof(DeviceEntry))
        devices = (DeviceEntry * count.value)()
        result = user.GetRawInputDeviceList(devices, ctypes.byref(count), ctypes.sizeof(DeviceEntry))
        available = result != 0xFFFFFFFF and any(
            device.kind == 1 and matches(device.device) for device in devices)

    def publish():
        state = clock.snapshot(time.monotonic())
        state.update(at=time.time(), available=available)
        try:
            temporary.write_text(json.dumps(state), encoding="utf-8")
            os.replace(temporary, ACTIVITY_PATH)
        except OSError:
            pass

    @callback_type
    def procedure(hwnd, message, wp, lp):
        try:
            if message == 0x00FF:  # WM_INPUT; request the header, never key data.
                header = Header()
                size = w.UINT(ctypes.sizeof(header))
                result = user.GetRawInputData(w.HANDLE(lp), 0x10000005,
                                              ctypes.byref(header), ctypes.byref(size), ctypes.sizeof(header))
                if result != 0xFFFFFFFF and header.kind == 1 and matches(header.device):
                    clock.note_event(time.monotonic())
            elif message == 0x0113:  # WM_TIMER
                publish()
                return 0
            elif message == 0x00FE:  # WM_INPUT_DEVICE_CHANGE
                refresh_devices()
                clock.last_event = None
                clock.session = uuid.uuid4().hex
                publish()
                return 0
        except (OSError, ValueError, ctypes.ArgumentError):
            pass
        return user.DefWindowProcW(hwnd, message, wp, lp)

    instance = kernel.GetModuleHandleW(None)
    class_name = f"NexusKeyboardActivity_{os.getpid()}"
    window_class = WindowClass(instance=instance, name=class_name, wndproc=procedure)
    hwnd = None
    try:
        if not user.RegisterClassW(ctypes.byref(window_class)):
            return
        hwnd = user.CreateWindowExW(0, class_name, "", 0, 0, 0, 0, 0, w.HWND(-3), None, instance, None)
        if not hwnd:
            return
        # INPUTSINK + DEVNOTIFY: observe in the background, preserve normal input.
        device = RawDevice(1, 6, 0x00000100 | 0x00002000, hwnd)
        if not user.RegisterRawInputDevices(ctypes.byref(device), 1, ctypes.sizeof(device)):
            return
        refresh_devices()
        publish()
        if not user.SetTimer(hwnd, 1, 1000, None):
            return
        message = w.MSG()
        while user.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
            user.TranslateMessage(ctypes.byref(message))
            user.DispatchMessageW(ctypes.byref(message))
    finally:
        if hwnd:
            device = RawDevice(1, 6, 1, None)
            user.RegisterRawInputDevices(ctypes.byref(device), 1, ctypes.sizeof(device))
            user.DestroyWindow(hwnd)
        user.UnregisterClassW(class_name, instance)
        kernel.ReleaseMutex(mutex)
        kernel.CloseHandle(mutex)
        temporary.unlink(missing_ok=True)


def ensure_started():
    global _thread
    with _start_lock:
        if _thread is None or not _thread.is_alive():
            _thread = threading.Thread(target=_monitor, daemon=True, name="AulaActivityClock")
            _thread.start()


def get_snapshot(*, path=ACTIVITY_PATH, now=None):
    if path == ACTIVITY_PATH:
        ensure_started()
    now = time.time() if now is None else now
    try:
        state = json.loads(Path(path).read_text(encoding="utf-8"))
        seconds = float(state["active_seconds"])
        age = now - float(state["at"])
        if (0 <= age <= HEARTBEAT_MAX_AGE and isinstance(state["session"], str)
                and 0 <= seconds < 1e10 and state.get("available") is True):
            state["active_seconds"] = seconds
            return state
    except (OSError, ValueError, TypeError, KeyError):
        pass
    return None
