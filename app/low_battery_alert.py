"""Low-battery warnings shared by the panel and background tray."""
from __future__ import annotations

from contextlib import contextmanager
import ctypes
from ctypes import wintypes
import json
import msvcrt
import os
from pathlib import Path
import threading
import uuid

THRESHOLD = 10
REARM_LEVEL = 15
STATE_PATH = Path(__file__).resolve().with_name("low_battery_alert.json")
NOTIFICATION_MUTEX = "Local\\NexusLowBatteryNotification_3554_FA09"
_STATE_LOCK = threading.RLock()


class AlertState:
    def __init__(self, data=None):
        data = data if isinstance(data, dict) and data.get("version") == 1 else {}
        self.notified = data.get("notified") is True
        self.token = data.get("token") if isinstance(data.get("token"), str) else None
        level = data.get("low_level")
        self.low_level = level if type(level) is int and 0 <= level < THRESHOLD else None

    def observe(self, level, *, charging=False, valid=True):
        if not valid:
            self.low_level = None
        elif charging:
            self.low_level, self.notified, self.token = None, False, None
        elif type(level) is not int or not 0 <= level <= 100:
            self.low_level = None
        elif level >= REARM_LEVEL:
            self.low_level, self.notified, self.token = None, False, None
        else:
            self.low_level = level if level < THRESHOLD else None
        return self.low_level is not None and not self.notified

    def begin(self):
        if self.low_level is None or self.notified:
            return None
        # The caller holds the notification mutex. Any old pending token therefore
        # belongs to a terminated process and can safely be retried.
        self.token = uuid.uuid4().hex
        return self.token, self.low_level

    def finish(self, token, shown):
        if token == self.token:
            self.notified = bool(shown)
            self.token = None

    def to_dict(self):
        return {"version": 1, "notified": self.notified,
                "token": self.token, "low_level": self.low_level}


class AlertStore:
    def __init__(self, path=STATE_PATH):
        self.path = Path(path)
        self.memory = AlertState()
        self.memory_only = False

    def transact(self, operation):
        with _STATE_LOCK:
            if self.memory_only:
                return operation(self.memory)
            try:
                with self.path.with_suffix(".lock").open("a+b") as handle:
                    if handle.tell() == 0:
                        handle.write(b"\0")
                        handle.flush()
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
                    try:
                        try:
                            data = json.loads(self.path.read_text(encoding="utf-8"))
                        except (OSError, ValueError, UnicodeError):
                            data = None
                        self.memory = AlertState(data)
                        before = self.memory.to_dict()
                        result = operation(self.memory)
                        if before != self.memory.to_dict():
                            temporary = self.path.with_suffix(".json.tmp")
                            try:
                                temporary.write_text(json.dumps(self.memory.to_dict()), encoding="utf-8")
                                os.replace(temporary, self.path)
                            except OSError:
                                # Still warn once in this process if storage is read-only.
                                self.memory_only = True
                            finally:
                                try:
                                    temporary.unlink(missing_ok=True)
                                except OSError:
                                    pass
                        return result
                    finally:
                        handle.seek(0)
                        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            except OSError:
                self.memory_only = True
                return operation(self.memory)


@contextmanager
def _single_notification(name):
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
    kernel.CreateMutexW.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.ReleaseMutex.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.CreateMutexW(None, False, name)
    acquired = bool(handle) and kernel.WaitForSingleObject(handle, 0) in (0, 0x80)
    try:
        yield acquired
    finally:
        if acquired:
            kernel.ReleaseMutex(handle)
        if handle:
            kernel.CloseHandle(handle)


def _request_notification(hwnd, level, preview):
    import tray_common as tc
    user = tc.USER32
    user.RegisterWindowMessageW.argtypes = [wintypes.LPCWSTR]
    user.RegisterWindowMessageW.restype = wintypes.UINT
    user.SendMessageTimeoutW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM,
                                       wintypes.LPARAM, wintypes.UINT, wintypes.UINT,
                                       ctypes.POINTER(ctypes.c_size_t)]
    user.SendMessageTimeoutW.restype = ctypes.c_ssize_t
    message = user.RegisterWindowMessageW(tc.MSG_NOTIFY)
    result = ctypes.c_size_t()
    sent = user.SendMessageTimeoutW(hwnd, message, level, int(preview),
                                   0x0001 | 0x0002, 5000, ctypes.byref(result))
    return bool(sent and result.value)


def show_notification(_title, level, *, preview=False):
    """Ask the resident tray to send a native Windows notification."""
    import tray_common as tc
    if type(level) is not int or not 0 <= level < THRESHOLD:
        return False
    hwnd = tc.tray_hwnd()
    if not hwnd:
        if not tc.start_tray_app():
            return False
        hwnd = tc.tray_hwnd()
    return bool(hwnd) and _request_notification(hwnd, level, preview)


class LowBatteryNotifier:
    def __init__(self, *, path=STATE_PATH, enabled=None, notification=show_notification, mutex_name=NOTIFICATION_MUTEX):
        self.store = AlertStore(path)
        self.enabled = enabled or (lambda: True)
        self.notification = notification
        self.mutex_name = mutex_name
        self._guard = threading.Lock()
        self._inflight = False
        self._thread = None

    def check(self, reading):
        eligible = self.store.transact(lambda state: state.observe(
            reading.level, charging=reading.status in ("charging", "充电中"), valid=reading.ok))
        if not eligible or not self.enabled():
            return False
        return self._start(reading.title, preview=False)

    def preview(self, title="狼蛛 F87 Pro"):
        return self._start(title, preview=True)

    def _start(self, title, *, preview):
        with self._guard:
            if self._inflight:
                return False
            self._inflight = True
            self._thread = threading.Thread(target=self._worker, args=(title, preview), daemon=True)
            self._thread.start()
        return True

    def _worker(self, title, preview):
        try:
            with _single_notification(self.mutex_name) as acquired:
                if not acquired:
                    return
                if preview:
                    self.notification(title, 9, preview=True)
                    return
                if not self.enabled():
                    return
                claim = self.store.transact(lambda state: state.begin())
                if claim is None:
                    return
                token, level = claim
                shown = False
                try:
                    shown = self.notification(title, level, preview=False)
                finally:
                    self.store.transact(lambda state: state.finish(token, shown))
        except Exception:
            # Notification errors must not stop ongoing battery monitoring.
            pass
        finally:
            with self._guard:
                self._inflight = False


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="低电量预警")
    parser.add_argument("--preview", action="store_true", help="发送不影响实际提醒记录的测试系统通知")
    args = parser.parse_args()
    if args.preview:
        show_notification("狼蛛 F87 Pro", 9, preview=True)
