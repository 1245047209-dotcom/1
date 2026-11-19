"""Exercise the settings controls in an isolated, invisible Tk window."""

from contextlib import ExitStack
import ctypes
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest.mock as mocks


def smoke(app: Path, candidate: Path) -> None:
    sys.path[:0] = [str(candidate), str(app)]
    tcl = ctypes.CDLL(str(Path(sys.base_prefix) / "DLLs" / "tcl86t.dll"))
    tcl.Tcl_FindExecutable.argtypes = [ctypes.c_char_p]
    tcl.Tcl_FindExecutable.restype = None
    tcl.Tcl_FindExecutable(sys.executable.encode("utf-8"))

    import tkinter as tk

    import app_dialogs
    import tray_common
    from interval_picker import IntervalPicker
    from my_devices import NexusPanel

    assert Path(app_dialogs.__file__).resolve().parent == candidate
    settings_values = {"autostart": False, "interval": 30, "low_battery_alert": True, "gpu_animation": True,
                       "theme": "dark"}
    choices = tray_common.INTERVAL_CHOICES

    root = tk.Tk()
    root.withdraw()
    root.geometry("952x553+30000+30000")
    root.attributes("-alpha", 0.0)
    errors = []
    root.report_callback_exception = lambda _type, error, _trace: errors.append(error)

    def flush():
        root.update()
        if errors:
            raise errors[0]

    def key(widget, keysym):
        widget.event_generate(f"<KeyPress-{keysym}>")
        flush()

    def mouse(widget, sequence, x=None, y=None):
        x = widget.winfo_width() // 2 if x is None else x
        y = widget.winfo_height() // 2 if y is None else y
        widget.event_generate(sequence, x=x, y=y, rootx=widget.winfo_rootx() + x, rooty=widget.winfo_rooty() + y)
        flush()

    manager = mocks.Mock(window=None)
    manager._attach.side_effect = lambda window: setattr(manager, "window", window)
    panel = SimpleNamespace(root=root, settings_window=None, updates=manager, interval=30, auto=True,
                            transition=mocks.Mock(), schedule=mocks.Mock(), draw=mocks.Mock())

    with ExitStack() as stack:
        stack.callback(root.destroy)
        stack.enter_context(mocks.patch.object(tray_common, "load_config", side_effect=settings_values.copy))
        stack.enter_context(mocks.patch.object(tray_common, "is_autostart_enabled", return_value=False))
        write_settings = stack.enter_context(mocks.patch.object(tray_common, "save_config"))
        reload_tray = stack.enter_context(mocks.patch.object(tray_common, "post_to_tray"))
        set_autostart = stack.enter_context(mocks.patch.object(tray_common, "set_autostart"))
        stack.enter_context(mocks.patch("update_client.load_state", return_value={"auto_check": True}))

        root.deiconify()
        flush()
        NexusPanel.open_settings(panel)
        flush()
        window = panel.settings_window
        picker = window.combo
        assert isinstance(picker, IntervalPicker)
        assert window.interval.get() == "30 秒"
        assert window.close_button.winfo_ismapped()
        assert root.focus_get() is not window.close_button

        picker.focus_force()
        key(picker, "Down")
        assert picker.popup is not None
        key(picker, "End")
        assert window.interval.get() == "30 秒", "Navigation should not commit an option"
        key(picker, "Escape")
        assert picker.popup is None and window.winfo_exists()
        assert window.interval.get() == "30 秒"

        mouse(picker, "<ButtonPress-1>")
        mouse(picker, "<ButtonRelease-1>")
        menu = picker.popup
        assert menu is not None and menu.winfo_ismapped()
        assert 0 <= menu.winfo_x() < menu.winfo_x() + menu.winfo_width() <= window.winfo_width()
        assert 0 <= menu.winfo_y() < menu.winfo_y() + menu.winfo_height() <= window.winfo_height()
        mouse(window.update_section, "<ButtonPress-1>")
        assert picker.popup is None, "Clicking elsewhere in settings should close the list"

        mouse(picker, "<ButtonPress-1>")
        mouse(picker, "<ButtonRelease-1>")
        menu = picker.popup
        third_row = picker.padding + 2 * picker.row_height + picker.row_height // 2
        mouse(menu, "<ButtonPress-1>", y=third_row)
        mouse(menu, "<ButtonRelease-1>", y=third_row)
        assert picker.popup is None and window.interval.get() == choices[2][1]

        picker.focus_force()
        key(picker, "Return")
        key(picker, "Tab")
        assert picker.popup is None, "Tab should move focus out of the picker and dismiss its list"
        assert root.focus_get() is not picker

        for index, (_seconds, label) in enumerate(choices):
            picker.focus_force()
            key(picker, "Return")
            assert picker.popup is not None
            key(picker, "Home")
            for _ in range(index):
                key(picker, "Down")
            key(picker, "Return")
            assert window.interval.get() == label
            assert picker.popup is None

        assert panel.interval == 30
        write_settings.assert_not_called()
        NexusPanel.open_settings(panel)
        flush()
        assert panel.settings_window is window and window.interval.get() == "30 分钟"
        window._save()
        write_settings.assert_called_once_with({**settings_values, "interval": 1800}, strict=True)
        reload_tray.assert_called_once_with(tray_common.MSG_RELOAD)
        set_autostart.assert_not_called()
        panel.schedule.assert_called_once_with()
        assert panel.interval == 1800

        picker.focus_force()
        key(picker, "Return")
        key(picker, "Escape")
        assert window.winfo_exists() and picker.popup is None
        key(picker, "Escape")
        assert not window.winfo_exists() and panel.settings_window is None

        NexusPanel.open_settings(panel)
        flush()
        window = panel.settings_window
        assert window.interval.get() == "30 分钟"
        close = window.close_button
        mouse(close, "<ButtonPress-1>")
        mouse(close, "<ButtonRelease-1>", x=-1)
        assert window.winfo_exists(), "A release outside the close button should not dismiss settings"
        mouse(close, "<ButtonPress-1>")
        mouse(close, "<ButtonRelease-1>")
        assert not window.winfo_exists() and panel.settings_window is None

        NexusPanel.open_settings(panel)
        flush()
        window = panel.settings_window
        window.close_button.focus_force()
        flush()
        assert root.focus_get() is window.close_button, root.focus_get()
        key(window.close_button, "Return")
        assert not window.winfo_exists() and panel.settings_window is None
        assert manager.window is None
        assert write_settings.call_count == 1

    print("Settings smoke passed: six interval options, cancel and save, outside dismissal, Escape, close mouse and keyboard.")


def smoke_theme():
    import tempfile
    import time
    import tkinter as tk

    import theme
    import tray_common as tc
    from app_dialogs import SettingsDialog
    from my_devices import NexusPanel
    from receiver_scan import Receiver
    from saved_devices import DeviceStore

    root = tk.Tk()
    root.withdraw()
    root.attributes("-alpha", 0.0)
    errors = []
    root.report_callback_exception = lambda _type, error, _trace: errors.append(error)

    def wait_for(condition):
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            root.update()
            if errors:
                raise errors[0]
            if condition():
                return
            time.sleep(.015)
        raise AssertionError("Timed out waiting for theme or scan")

    with ExitStack() as stack:
        stack.callback(root.destroy)
        data = Path(stack.enter_context(tempfile.TemporaryDirectory()))
        stack.enter_context(mocks.patch.object(tc, "CONFIG_PATH", data / "tray_config.json"))
        tc.save_config({"interval": 30, "gpu_animation": False}, strict=True)
        stack.enter_context(mocks.patch.object(tc, "is_autostart_enabled", return_value=False))
        stack.enter_context(mocks.patch.object(tc, "post_to_tray"))
        stack.enter_context(mocks.patch("my_devices.DeviceStore", return_value=DeviceStore(data / "devices.json")))
        stack.enter_context(mocks.patch("aula_discovery.AulaDiscovery", return_value=mocks.Mock()))
        stack.enter_context(mocks.patch("update_client.load_state", return_value={"auto_check": True}))
        system = stack.enter_context(mocks.patch.object(theme, "windows_dark", return_value=False))
        device = Receiver(r"USB\VID_0C45&PID_FEF9\TEST", "F87S", "AULA", "0C45:FEF9", "键盘", "测试", True)
        stack.enter_context(mocks.patch("receiver_search.scan_receivers", return_value=[device]))
        stack.enter_context(mocks.patch("aula_protocols.identify_receivers", side_effect=lambda devices: devices))
        manager = mocks.Mock(window=None)
        manager._attach.side_effect = lambda window: setattr(manager, "window", window)

        panel = NexusPanel(root)
        for name in ("startup_after_id", "after_id", "pump_after_id", "runtime_after_id"):
            root.after_cancel(getattr(panel, name))
            setattr(panel, name, None)
        panel.schedule = mocks.Mock()
        assert panel.appearance.mode == "system" and tc.load_config()["theme"] == "system"
        assert panel.canvas.cget("bg") == theme.BACKGROUND.light
        root.deiconify()
        root.update()
        picker = panel.theme_picker
        assert picker.values == ("跟随系统", "浅色", "深色")
        assert panel.theme_choice.get() == "跟随系统"
        assert picker.winfo_ismapped() and picker.master is panel.canvas
        assert picker.winfo_x() + picker.winfo_width() < panel.receiver_button.winfo_x()
        assert abs(picker.winfo_y() + picker.winfo_height()/2 -
                   panel.receiver_button.winfo_y() - panel.receiver_button.winfo_height()/2) <= 1

        def key(keysym):
            picker.event_generate(f"<KeyPress-{keysym}>")
            root.update()
            if errors:
                raise errors[0]

        def choose_with_keys(*keys):
            picker.focus_force()
            root.update()
            key("Return")
            assert picker.popup is not None
            for keysym in keys:
                key(keysym)
            key("Return")
            assert picker.popup is None

        panel.open_receiver_search()
        search = panel.receiver_search
        wait_for(lambda: bool(search.table.get_children()))
        details = search.detail.get("1.0", "end")
        picker.focus_force()
        root.update()
        key("Return")
        assert picker.popup is not None
        key("End")
        key("Escape")
        assert picker.popup is None and tc.load_config()["theme"] == "system"

        def click(widget, x, y):
            widget.event_generate("<ButtonPress-1>", x=x, y=y)
            widget.event_generate("<ButtonRelease-1>", x=x, y=y)
            root.update()
            if errors:
                raise errors[0]

        click(picker, picker.winfo_width() // 2, picker.winfo_height() // 2)
        menu = picker.popup
        assert menu is not None and menu.winfo_ismapped()
        assert 0 <= menu.winfo_x() < menu.winfo_x() + menu.winfo_width() <= root.winfo_width()
        assert picker.winfo_y() < menu.winfo_y() < menu.winfo_y() + menu.winfo_height() <= root.winfo_height()
        click(menu, menu.winfo_width() // 2, picker.padding + 2 * picker.row_height + picker.row_height // 2)
        assert picker.popup is None and panel.theme_choice.get() == "深色"
        assert tc.load_config()["theme"] == "dark"
        assert panel.appearance.mode == "dark" and panel.appearance.timer is None
        assert tuple(panel.toolbar_photo.get(500, 20)) == (30, 35, 50)
        assert panel.canvas.cget("bg") == theme.BACKGROUND.dark
        assert panel.overview_canvas.cget("bg") == theme.BACKGROUND.dark
        dialog = panel.settings_window = SettingsDialog(panel, manager)
        assert not hasattr(dialog, "theme_picker")
        assert dialog.inner.cget("bg") == theme.SURFACE.dark
        assert dialog.update_section.notes.cget("bg") == theme.SURFACE.dark
        dialog._save()
        assert tc.load_config()["theme"] == "dark"
        dialog.destroy()
        assert search.window.cget("bg") == theme.BACKGROUND.dark
        assert search.detail.cget("bg") == theme.SOFT.dark
        assert panel.appearance.ttk.lookup("Receiver.Treeview", "background") == theme.SURFACE.dark
        assert search.table.selection() == ("0",) and search.detail.get("1.0", "end") == details
        panel.transition.alpha = 0.
        assert panel.transition.fade_color("#ffffff") == theme.BACKGROUND.dark
        panel.transition.alpha = 1.
        panel.set_view_mode("single")
        wait_for(lambda: not panel.transition.active)
        assert panel.view_mode == "single" and not panel.canvas.staging
        panel.set_view_mode("all")
        assert panel.transition.active

        system.return_value = True
        with mocks.patch.object(tc, "save_config", side_effect=OSError("disk unavailable")), \
                mocks.patch("tkinter.messagebox.showerror") as show_error:
            choose_with_keys("Home", "Down")
            show_error.assert_called_once()
        assert panel.theme_choice.get() == "深色" and tc.load_config()["theme"] == "dark"
        choose_with_keys("Home", "Down")
        assert tc.load_config()["theme"] == "light"
        assert panel.theme_choice.get() == "浅色"
        assert not panel.appearance.dark and panel.appearance.timer is None
        assert not panel.transition.active and not panel.canvas.staging
        assert panel.canvas.cget("bg") == theme.BACKGROUND.light
        assert tuple(panel.toolbar_photo.get(500, 20)) == (255, 255, 255)
        assert panel.appearance.ttk.lookup("Receiver.Treeview", "background") == theme.SURFACE.light
        assert search.detail.get("1.0", "end") == details

        choose_with_keys("Home")
        assert tc.load_config()["theme"] == "system" and panel.appearance.dark
        dialog = panel.settings_window = SettingsDialog(panel, manager)
        assert dialog.inner.cget("bg") == theme.SURFACE.dark
        system.return_value = False
        wait_for(lambda: not panel.appearance.dark)
        assert panel.theme_choice.get() == "跟随系统"
        assert dialog.inner.cget("bg") == theme.SURFACE.light
        assert search.window.winfo_exists() and search.table.selection() == ("0",)
        assert panel.canvas.cget("bg") == theme.BACKGROUND.light
        assert not errors

    print("Theme smoke passed: toolbar placement, mouse and keyboard, persistent choices, and live system changes.")


if __name__ == "__main__":
    smoke(*(Path(argument).resolve() for argument in sys.argv[1:3]))
    smoke_theme()
