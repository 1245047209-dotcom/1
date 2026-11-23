"""Connect the native acrylic window to the existing device panel and dialogs."""

from collections import deque
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
from queue import Empty, Queue
import subprocess
import sys
from threading import Condition, Thread
import time
from tkinter import TclError


APP_ROOT = Path(__file__).resolve().parent
PANEL_EXE = APP_ROOT / "native" / "NexusNative.exe"
_ARTWORK = {
    "keyboard": ("assets/optimized/keyboard-small.png", "assets/aula-f87-pro-product.png"),
    "keyboard-v2d": ("assets/aula-f87-pro-v2d-product.png",),
    "mouse": ("assets/optimized/mouse-crop.png", "assets/logitech-pro-wireless.png"),
    "mouse-g304": ("assets/logitech-g304-black.png",),
    "beats-studio-buds": ("assets/optimized/beats-crop.png", "assets/beats-studio-buds-red.png"),
}


def _asset(device):
    from my_devices import product_image_kind

    kind = product_image_kind(device)
    if kind and kind.startswith("aula:"):
        from aula_catalog import image_path

        _, key, variant = kind.split(":", 2)
        path = image_path(key, variant)
        if path is not None and path.is_file():
            return path.relative_to(APP_ROOT).as_posix()
    return next((relative for relative in _ARTWORK.get(kind, ()) if (APP_ROOT / relative).is_file()), None)


def make_snapshot(panel):
    """Report the same device, battery, and status data used by the Tk panel."""
    from device_sheet import sheet_values
    from my_devices import BATTERY_PARTS, KEYBOARD_TITLE, Reading, connection_text, product_kind

    entries = panel.device_entries()
    ring_entries = list(panel.ring_entries(entries))
    rings = []
    for key, _device, part, label, glyph in ring_entries:
        level, charging = panel.ring_state(panel.readings.get(key), part)
        rings.append({"id": key, "kind": "audio" if glyph == "headphones" else glyph,
                      "label": label, "level": level, "charging": charging})

    devices = []
    for key, device in entries:
        reading = panel.readings.get(key) or Reading(device.label if device else KEYBOARD_TITLE, "正在读取…")
        styles = panel.reading_styles(key, reading)
        detail = sheet_values(panel, key)
        detail_rows = {name: value for name, _label, value in detail["rows"]}
        component_values = (reading.components or {}) if not reading.error else {}
        kinds = product_kind(device)
        details = {
            "level": detail["level"],
            "caption": styles["caption"]["text"],
            "timestamp": detail_rows.get("time", ""),
            "detail": detail["footer"],
            "source": detail_rows.get("source", ""),
            "status": detail["status"],
            "low": detail["low"],
            "rows": [{"label": label, "value": value} for _name, label, value in detail["rows"]],
            "components": [{"label": label, "level": styles[part + "_level"]["text"],
                            "time": styles[part + "_time"]["text"],
                            "charging": (component_values.get(part) or {}).get("charging") is True}
                           for part, label in BATTERY_PARTS] if detail["parts"] else [],
        }
        devices.append({"id": key, "name": detail["title"],
                        "kind": "audio" if kinds == "beats-studio-buds" else kinds or "usb",
                        "status": styles["row_sub"]["text"], "connection": connection_text(device),
                        "power": styles["power"]["text"], "low": detail["low"],
                        "asset": _asset(device), "details": details})

    header = panel.header_styles()
    return {"type": "snapshot", "theme": "dark" if panel.appearance.dark else "light",
            "glass": panel.glass_enabled, "summary": header["summary"]["text"],
            "summaryLow": any(device["low"] for device in devices),
            "updated": header["summary_time"]["text"].removeprefix(" · "),
            "refreshing": panel.busy, "rings": rings, "devices": devices,
            "earbudNote": "耳机显示左右耳中较低的一侧。" if panel.earbud_note(ring_entries) else ""}


def _window_belongs_to_process(hwnd, expected_pid):
    if type(hwnd) is not int or not 0 < hwnd < 1 << (8 * ctypes.sizeof(wintypes.HWND)):
        return False
    owner = wintypes.DWORD()
    get_pid = ctypes.WinDLL("user32", use_last_error=True).GetWindowThreadProcessId
    get_pid.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
    get_pid.restype = wintypes.DWORD
    return bool(get_pid(hwnd, ctypes.byref(owner))) and owner.value == expected_pid


class NativePanel:
    """Keep byte transport off the Tk thread and run all device actions on it."""

    def __init__(self, panel, process):
        self.panel = panel
        self.process = process
        self.hwnd = None
        self._timer = None
        self._closed = False
        self._incoming = Queue()
        self._condition = Condition()
        self._commands = deque()
        self._pending_snapshot = None
        self._last_snapshot = None
        self._next_snapshot = 0.0
        Thread(target=self._read, name="native-panel-reader", daemon=True).start()
        Thread(target=self._write, name="native-panel-writer", daemon=True).start()

    def _read(self):
        try:
            with self.process.stdout as output:
                for line in output:
                    try:
                        message = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(message, dict):
                        self._incoming.put(message)
        except (OSError, ValueError):
            pass
        finally:
            self._incoming.put(None)

    def _write(self):
        try:
            with self.process.stdin as target:
                while True:
                    with self._condition:
                        self._condition.wait_for(lambda: self._commands or self._pending_snapshot is not None or self._closed)
                        if self._commands:
                            message = self._commands.popleft()
                        elif self._closed:
                            break
                        else:
                            message, self._pending_snapshot = self._pending_snapshot, None
                    target.write(json.dumps(message, ensure_ascii=False, allow_nan=False) + "\n")
                    target.flush()
        except (OSError, ValueError):
            # The reader owns EOF handling so an explicit close can drain first.
            pass

    def wait_until_ready(self, timeout=4.0):
        deadline = time.monotonic() + timeout
        while (remaining := deadline - time.monotonic()) > 0:
            try:
                message = self._incoming.get(timeout=remaining)
            except Empty:
                return False
            if message is None or message.get("action") == "close":
                return False
            if message.get("action") == "ready":
                hwnd = message.get("hwnd")
                if _window_belongs_to_process(hwnd, self.process.pid):
                    self.hwnd = hwnd
                    return True
        return False

    def start(self):
        import tray_common as tc

        snapshot = make_snapshot(self.panel)
        tc.register_ui_handle(self.hwnd)
        self.panel.native_panel = self
        self.panel.root._nexus_native_hwnd = self.hwnd
        self.panel.root.bind("<Destroy>", self._on_destroy, add="+")
        self._send_snapshot(snapshot)
        self._timer = self.panel.root.after(50, self._tick)

    def _send_snapshot(self, snapshot):
        self._next_snapshot = time.monotonic() + .4
        if snapshot == self._last_snapshot:
            return
        self._last_snapshot = snapshot
        with self._condition:
            self._pending_snapshot = snapshot
            self._condition.notify()

    def show(self):
        with self._condition:
            if not self._closed:
                self._commands.append({"type": "show"})
                self._condition.notify()

    def _tick(self):
        self._timer = None
        for _ in range(24):
            try:
                message = self._incoming.get_nowait()
            except Empty:
                break
            if message is None:
                self._recover()
                return
            if message.get("action") == "close":
                self.panel.close()
                return
            self._dispatch(message)
            self._next_snapshot = 0.0

        if time.monotonic() >= self._next_snapshot:
            try:
                self._send_snapshot(make_snapshot(self.panel))
            except Exception:
                self._recover()
                return
        self._timer = self.panel.root.after(50, self._tick)

    def _dispatch(self, message):
        action = message.get("action")
        if action == "refresh":
            self.panel.refresh()
        elif action == "settings":
            self.panel.open_settings()
        elif action == "updates":
            self.panel.open_updates()
        elif action == "add":
            self.panel.open_receiver_search()
        elif action == "remove":
            key = message.get("id")
            if isinstance(key, str):
                self.panel.remove_device(key)

    def _recover(self):
        """Keep the device panel usable if the native child stops unexpectedly."""
        import tray_common as tc

        self.close()
        if not getattr(self.panel.root, "_nexus_closing", False):
            self.panel.native_panel = None
            self.panel.root._nexus_native_hwnd = None
            self.panel.place_initial_window()
            self.panel.draw()
            self.panel.root.deiconify()
            tc.register_ui_window(self.panel.root)

    def _on_destroy(self, event):
        if event.widget is self.panel.root:
            self.close()

    def close(self):
        with self._condition:
            if self._closed:
                return
            self._closed = True
            self._commands.clear()
            self._commands.append({"type": "close"})
            self._pending_snapshot = None
            self._condition.notify()
        if self.hwnd is not None:
            from tray_common import unregister_ui_handle

            unregister_ui_handle(self.hwnd)
        if self._timer is not None:
            try:
                self.panel.root.after_cancel(self._timer)
            except TclError:
                pass
            self._timer = None
        try:
            self.process.wait(timeout=.35)
        except subprocess.TimeoutExpired:
            try:
                self.process.terminate()
            except OSError:
                pass


def start_native_panel(panel):
    """Start the installed native window, or let the ordinary Tk main window open."""
    if sys.platform != "win32" or sys.getwindowsversion().build < 22621 or not PANEL_EXE.is_file():
        return None
    command = [str(PANEL_EXE), "--backend-pid", str(os.getpid()),
               "--dark" if panel.appearance.dark else "--light"]
    if not panel.glass_enabled:
        command.append("--glass-off")
    try:
        process = subprocess.Popen(command, cwd=APP_ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.DEVNULL, encoding="utf-8", errors="replace",
                                   creationflags=subprocess.CREATE_NO_WINDOW)
    except OSError:
        return None
    native = NativePanel(panel, process)
    try:
        if native.wait_until_ready():
            native.start()
            return native
    except Exception:
        # Fall back to Tk if the native child can't finish its initial handshake.
        pass
    native.close()
    panel.native_panel = None
    panel.root._nexus_native_hwnd = None
    return None
