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
    settings_values = {"autostart": False, "interval": 30, "low_battery_alert": True, "gpu_animation": True}
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

        picker.focus_set()
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

        picker.focus_set()
        key(picker, "Return")
        key(picker, "Tab")
        assert picker.popup is None, "Tab should move focus out of the picker and dismiss its list"
        assert root.focus_get() is not picker

        for index, (_seconds, label) in enumerate(choices):
            picker.focus_set()
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
        write_settings.assert_called_once_with({**settings_values, "interval": 1800})
        reload_tray.assert_called_once_with(tray_common.MSG_RELOAD)
        set_autostart.assert_not_called()
        panel.schedule.assert_called_once_with()
        assert panel.interval == 1800

        picker.focus_set()
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
        window.close_button.focus_set()
        flush()
        assert root.focus_get() is window.close_button, root.focus_get()
        key(window.close_button, "Return")
        assert not window.winfo_exists() and panel.settings_window is None
        assert manager.window is None
        assert write_settings.call_count == 1

    print("Settings smoke passed: six interval options, cancel and save, outside dismissal, Escape, close mouse and keyboard.")


if __name__ == "__main__":
    smoke(*(Path(argument).resolve() for argument in sys.argv[1:3]))
