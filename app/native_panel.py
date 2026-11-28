"""Connect the native acrylic window to the existing device panel and dialogs."""

from collections import deque
import ctypes
from ctypes import wintypes
import json
from queue import Empty, Queue
import subprocess
import sys
from threading import Condition, Thread
import time
from tkinter import TclError

from panel_startup import APP_ROOT, launch_native_process
from ui_fonts import DEFAULT as DEFAULT_UI_FONT


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
    from device_sheet import format_sheet_values
    from my_devices import BATTERY_PARTS, KEYBOARD_TITLE, Reading, product_kind

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
        detail = format_sheet_values(panel, key, device, reading, styles)
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
                        "status": styles["row_sub"]["text"], "connection": detail_rows["connection"],
                        "power": styles["power"]["text"], "low": detail["low"],
                        "asset": _asset(device), "details": details})

    header = panel.header_styles(entries)
    return {"type": "snapshot", "theme": "dark" if panel.appearance.dark else "light",
            "font": getattr(panel.appearance, "font_choice", DEFAULT_UI_FONT),
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
        self._hidden = False
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

    def wait_until_ready(self, timeout=12.0):
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

        tc.register_ui_handle(self.hwnd)
        self.panel.native_panel = self
        self.panel.root._nexus_native_hwnd = self.hwnd
        self.panel.root.bind("<Destroy>", self._on_destroy, add="+")
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
        self._enqueue({"type": "show"})

    def open_settings(self, page="settings"):
        self.show()
        self._enqueue({"type": "openSettings", "page": page})

    def _enqueue(self, command):
        with self._condition:
            if not self._closed:
                self._commands.append(command)
                self._condition.notify()

    def attach_updates(self, manager):
        if getattr(self, "_update_proxy", None) is None:
            self._update_proxy = _NativeUpdateView(self, manager)
        if manager.window is not self._update_proxy:
            manager._attach(self._update_proxy)
        return self._update_proxy

    def _settings_payload(self):
        import theme
        import tray_common as tc
        import update_client

        cfg = tc.load_config()
        manager = getattr(self.panel, "updates", None)
        if manager is not None:
            self.attach_updates(manager)
        proxy = getattr(self, "_update_proxy", None)
        mode = getattr(self.panel.appearance, "mode", None) or cfg.get("theme", "system")
        if mode not in theme.MODE_LABELS:
            mode = "system"
        payload = {
            "type": "settings",
            "themeMode": mode,
            "interval": int(getattr(self.panel, "interval", cfg.get("interval", 60))),
            "autoRefresh": bool(getattr(self.panel, "auto", cfg.get("panel_auto_refresh", True))),
            "autostart": bool(tc.is_autostart_enabled()),
            "lowBatteryAlert": bool(cfg.get("low_battery_alert", True)),
            "gpuAnimation": bool(cfg.get("gpu_animation", True)),
            "version": update_client.current_version(),
            "updateAutoCheck": bool(update_client.load_state().get("auto_check")),
        }
        if proxy is not None:
            payload.update(proxy.state_fields())
        return payload

    def _save_settings(self, message):
        import theme
        import tray_common as tc

        panel = self.panel
        try:
            boot = bool(message.get("autostart", True))
            if boot != tc.is_autostart_enabled() and not tc.set_autostart(boot):
                raise OSError("开机启动设置未能保存，请重试")
            valid_intervals = {sec for sec, _label in tc.INTERVAL_CHOICES}
            interval = int(message.get("interval", 60))
            if interval not in valid_intervals:
                interval = 60
            mode = str(message.get("themeMode", "system"))
            if mode not in theme.MODE_LABELS:
                mode = "system"
            font_choice = str(message.get("uiFont", DEFAULT_UI_FONT))
            cfg = {
                "interval": interval,
                "panel_auto_refresh": bool(message.get("autoRefresh", True)),
                "autostart": boot,
                "low_battery_alert": bool(message.get("lowBatteryAlert", True)),
                "gpu_animation": bool(message.get("gpuAnimation", True)),
                "theme": mode,
                "glass_backdrop": bool(message.get("glassBackdrop", True)),
                "ui_font": font_choice,
            }
            tc.save_config(cfg, strict=True)
            panel.appearance.set_mode(mode)
            panel.appearance.set_font(font_choice)
            panel.auto, panel.interval = cfg["panel_auto_refresh"], interval
            panel.glass_enabled = cfg["glass_backdrop"]
            if hasattr(panel, "theme_choice"):
                panel.theme_choice.set(theme.MODE_LABELS[mode])
            tc.post_to_tray(tc.MSG_RELOAD)
            panel.schedule()
            panel.set_gpu_enabled(cfg["gpu_animation"])
            panel.draw()
            self._enqueue({"type": "settingsSaved", "ok": True})
        except (OSError, ValueError, TypeError) as exc:
            self._enqueue({"type": "settingsSaved", "ok": False, "error": str(exc) or "设置保存失败，请重试"})

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
            try:
                self._dispatch(message)
            except Exception as error:
                self.panel.root.report_callback_exception(type(error), error, error.__traceback__)
            self._next_snapshot = 0.0

        if not getattr(self, "_hidden", False) and time.monotonic() >= self._next_snapshot:
            try:
                self._send_snapshot(make_snapshot(self.panel))
            except Exception:
                self._recover()
                return
        self._timer = self.panel.root.after(50, self._tick)

    def _dispatch(self, message):
        action = message.get("action")
        if action == "hidden":
            self._hidden = True
            settings = self.panel.settings_window
            if settings is not None and settings.winfo_exists():
                settings.cancel()
            search = self.panel.receiver_search
            if search is not None and not search.closed:
                search.close()
        elif action == "shown":
            self._hidden = False
        elif action == "refresh":
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
        elif action == "querySettings":
            self._enqueue(self._settings_payload())
        elif action == "saveSettings":
            self._save_settings(message)
        elif action == "testAlert":
            ok = self.panel.low_battery_notifier.preview()
            self._enqueue({"type": "toolFeedback",
                           "feedback": "已发送测试提醒" if ok else "测试提醒已提交，请查看系统通知"})
        elif action == "rebuildLinks":
            import tray_common as tc

            result = tc.install_desktop_links()
            text = "；".join(result.get("errors", [])) or "桌面入口已更新"
            self._enqueue({"type": "toolFeedback", "feedback": text})
        elif action == "openReleases":
            import update_client
            import webbrowser

            webbrowser.open("https://github.com/" + update_client.DEFAULT_REPOSITORY + "/releases")
        elif action in ("updateCheck", "updatePrimary", "updateIgnore", "updateInstall", "updateAuto"):
            self._handle_update_action(action, message)

    def _handle_update_action(self, action, message):
        import update_client

        if action == "updateAuto":
            update_client.save_state(auto_check=bool(message.get("enabled")))
            return
        manager = getattr(self.panel, "updates", None)
        if manager is None:
            from update_ui import UpdateManager

            self.panel.updates = manager = UpdateManager(self.panel.root, on_install=self.panel.close)
        proxy = self.attach_updates(manager)
        if action == "updateCheck":
            if not manager.busy:
                manager.check(manual=bool(message.get("manual")))
        elif action == "updatePrimary":
            proxy.invoke_primary()
        elif action == "updateIgnore":
            manager._ignore()
        elif action == "updateInstall":
            manager._launch_install()

    def _recover(self):
        """Keep the device panel usable if the native child stops unexpectedly."""
        import tray_common as tc

        self.close()
        if not getattr(self.panel.root, "_nexus_closing", False):
            self.panel.native_panel = None
            self.panel.root._nexus_native_hwnd = None
            self.panel.place_initial_window()
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


class _VarProxy:
    def __init__(self, value, on_change):
        self._value = value
        self._on_change = on_change

    def get(self):
        return self._value

    def set(self, value):
        self._value = value
        self._on_change()


class _ProgressProxy:
    def __init__(self, on_change):
        self.value = 0.0
        self._on_change = on_change

    def __getitem__(self, key):
        if key == "value":
            return self.value
        raise KeyError(key)

    def __setitem__(self, key, value):
        if key == "value":
            self.value = max(0.0, min(100.0, float(value)))
            self._on_change()
        else:
            raise KeyError(key)


class _NotesProxy:
    def __init__(self, on_change):
        self.text = ""
        self._on_change = on_change

    def configure(self, **_kwargs):
        pass

    def delete(self, *_args):
        self.text = ""

    def insert(self, _index, text):
        self.text = str(text)
        self._on_change()


class _ButtonProxy:
    def __init__(self, text, command, enabled, on_change):
        self.text = text
        self.command = command
        self.enabled = enabled
        self._on_change = on_change

    def configure(self, **kwargs):
        if "text" in kwargs:
            self.text = str(kwargs["text"])
        if "command" in kwargs:
            self.command = kwargs["command"]
        if "state" in kwargs:
            self.enabled = kwargs["state"] != "disabled"
        self._on_change()


class _NativeUpdateSection:
    def __init__(self, manager, notify):
        import update_client

        self.status = _VarProxy("点击“检查更新”获取最新正式版本。", notify)
        self.notes = _NotesProxy(notify)
        self.progress = _ProgressProxy(notify)
        self.auto = _VarProxy(bool(update_client.load_state().get("auto_check")), notify)
        self.ignore = _ButtonProxy("忽略此版", manager._ignore, False, notify)
        self.primary = _ButtonProxy("检查更新", lambda: manager.check(manual=True), True, notify)


class _NativeUpdateView:
    """Headless adapter letting UpdateManager drive the native WPF settings sheet."""

    def __init__(self, bridge, manager):
        self.bridge = bridge
        self.manager = manager
        self._ready = False
        self.update_section = _NativeUpdateSection(manager, self._push)
        self._ready = True

    def winfo_exists(self):
        return not self.bridge._closed

    def show(self):
        pass

    def show_updates(self):
        self.bridge.open_settings("updates")

    def confirm_install(self, callback):
        callback()

    def destroy(self):
        pass

    def invoke_primary(self):
        cmd = self.update_section.primary.command
        if self.update_section.primary.enabled and cmd is not None:
            cmd()

    def state_fields(self):
        import update_client

        sec = self.update_section
        label = sec.primary.text
        action = "install" if label == "立即安装" else "download" if label in ("下载更新", "重新下载") else "check"
        return {
            "version": update_client.current_version(),
            "hasUpdate": self.manager.release is not None,
            "updateStatus": sec.status.get(),
            "updateNotes": sec.notes.text,
            "updateProgress": sec.progress.value,
            "updatePrimaryLabel": label,
            "updatePrimaryEnabled": sec.primary.enabled,
            "canIgnoreUpdate": sec.ignore.enabled,
            "updateAction": action,
        }

    def _push(self):
        if not self._ready or self.bridge._closed:
            return
        payload = {"type": "updateState"}
        payload.update(self.state_fields())
        self.bridge._enqueue(payload)


def start_native_panel(panel, process=None):
    """Attach the Tk backend to a prestarted native process, falling back on failure."""
    if process is None:
        process = launch_native_process(dark=panel.appearance.dark, glass=panel.glass_enabled,
                                        font=panel.appearance.font_choice)
        if process is None:
            return None
    native = NativePanel(panel, process)
    try:
        native._send_snapshot(make_snapshot(panel))
        if native.wait_until_ready():
            native.start()
            return native
        if sys.stderr is not None:
            print("Nexus native startup: no valid window handle, child status:", process.poll(), file=sys.stderr)
    except Exception as error:
        if sys.stderr is not None:
            print("Nexus native startup:", error, file=sys.stderr)
    native.close()
    panel.native_panel = None
    panel.root._nexus_native_hwnd = None
    return None
