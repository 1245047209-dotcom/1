"""Check the receiver popup with real Tk widgets and isolated fixture devices."""

from contextlib import ExitStack
import ctypes
from pathlib import Path
import sys
from threading import Event
import time
from types import SimpleNamespace
from unittest.mock import Mock, patch


def smoke():
    app, candidate = (Path(arg).resolve() for arg in sys.argv[1:3])
    sys.path[:0] = [str(candidate), str(app)]
    tcl = ctypes.CDLL(str(Path(sys.base_prefix) / "DLLs" / "tcl86t.dll"))
    tcl.Tcl_FindExecutable.argtypes = [ctypes.c_char_p]
    tcl.Tcl_FindExecutable.restype = None
    tcl.Tcl_FindExecutable(sys.executable.encode("utf-8"))

    import tkinter as tk

    from bluetooth_devices import BluetoothPeripheral
    from receiver_scan import Receiver
    from receiver_search import ReceiverSearch
    from ui_scale import UIScale

    usb_keyboard = Receiver(
        r"USB\VID_0C45&PID_FEF9\TEST", "F87S", "AULA", "0C45:FEF9", "键盘", "测试", True
    )
    usb_other = Receiver(
        r"USB\VID_0100&PID_0100\TEST", "USB Input Device", "测试设备", "0100:0100", "USB", "测试", False
    )
    bluetooth = BluetoothPeripheral(
        r"BTHENUM\DEV_020000000001", "测试耳机", "测试设备", "02:00:00:00:00:01", "耳机", True, 47
    )
    release_usb, started_usb = Event(), Event()
    add = Mock(return_value=True)
    saved = Mock(side_effect=lambda device: device is bluetooth)
    first_scan = True

    def scan_usb():
        nonlocal first_scan
        if first_scan:
            first_scan = False
            started_usb.set()
            if not release_usb.wait(5):
                raise TimeoutError("Smoke test never released the simulated USB scan")
        return [usb_keyboard, usb_other]

    root = tk.Tk()
    root.withdraw()
    root._nexus_ui = UIScale(root, .85)
    errors = []
    root.report_callback_exception = lambda cls, exc, trace: errors.append(exc)

    def wait_until(condition, description):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            root.update()
            if errors:
                raise errors[0]
            if condition():
                return
            time.sleep(.015)
        raise AssertionError(description)

    with ExitStack() as stack:
        stack.callback(root.destroy)
        stack.callback(release_usb.set)
        stack.enter_context(patch("receiver_search.scan_receivers", side_effect=scan_usb))
        scan_bluetooth = stack.enter_context(patch("receiver_search.scan_bluetooth", return_value=[bluetooth]))
        stack.enter_context(patch("aula_protocols.identify_receivers", side_effect=lambda devices: devices))
        search = ReceiverSearch(root, on_add=add, is_added=saved)
        root.update_idletasks()
        assert Path(sys.modules["receiver_search"].__file__).parent == candidate
        assert str(search.window.transient()) == str(root)
        assert search.mode_tabs.selected == "usb"
        assert started_usb.wait(5), "initial USB scan did not start"

        search.mode_tabs.key(SimpleNamespace(keysym="End"))
        assert search.mode.get() == "bluetooth"
        assert search.mode_tabs.selected == "bluetooth"
        release_usb.set()
        wait_until(lambda: not search.busy and bool(search.table.get_children()), "Bluetooth scan did not finish")
        assert search.table.get_children() == ("0",), "stale USB rows appeared in the Bluetooth tab"
        bt_values = search.table.item("0", "values")
        scan_bluetooth.assert_called_once_with(force=True)
        assert bluetooth.model in bt_values and "47%" in bt_values
        assert bluetooth.hardware_id in bt_values
        assert search.aula_combo.instate(("disabled",))
        assert search.variant_combo.instate(("disabled",))
        assert "蓝牙" in search.detail.get("1.0", "end")
        assert not search.other_option.winfo_manager()
        saved.assert_called_with(bluetooth)

        search.mode_tabs.key(SimpleNamespace(keysym="Home"))
        wait_until(lambda: not search.busy and bool(search.table.get_children()), "USB rescan did not finish")
        assert search.mode_tabs.selected == "usb"
        assert search.other_option.winfo_manager()
        assert search.table.get_children() == ("0",), "unconfirmed USB row was not filtered"
        assert usb_keyboard.model in search.table.item("0", "values")
        assert search.aula_combo.instate(("readonly", "!disabled"))
        assert search.aula_choice.get() == usb_keyboard.model

        with patch("receiver_search.scan_receivers", side_effect=TimeoutError("USB 搜索超时")):
            search.search_button.invoke()
            wait_until(lambda: not search.busy, "failing USB rescan did not finish")
        assert search.search_button.phase == "error"
        assert not search.table.get_children()
        assert "USB 搜索超时" in search.detail.get("1.0", "end")
        for show_other in (True, False):
            search.other_switch.invoke()
            assert search.show_other.get() == show_other
            assert "搜索失败" in search.status.get(), "changing the filter hid the scan failure"
            assert "USB 搜索超时" in search.detail.get("1.0", "end")

        with patch("receiver_search.scan_receivers", side_effect=OSError()):
            search.search_button.invoke()
            wait_until(lambda: not search.busy, "USB rescan with a blank error did not finish")
        assert search.search_button.phase == "error"
        assert "OSError" in search.detail.get("1.0", "end")

        search.search_button.invoke()
        wait_until(lambda: not search.busy and bool(search.table.get_children()), "USB retry did not recover")
        assert search.search_button.phase == "idle"
        assert search.table.get_children() == ("0",)

        search.other_switch.invoke()
        assert search.table.get_children() == ("0", "1")
        search.table.selection_set("1")
        search.selection_changed()
        assert search.aula_combo.instate(("disabled",))
        assert usb_other.model in search.detail.get("1.0", "end")

        search.table.selection_set("0")
        search.selection_changed()
        override = next(name for name, model in search.aula_models.items() if len(model["photos"]) > 1)
        search.aula_choice.set(override)
        search.choose_aula_model()
        assert search.aula_combo.instate(("readonly", "!disabled"))
        assert override in search.table.item("0", "values")
        assert search.variant_combo.instate(("readonly", "!disabled"))
        search.variant_combo.current(1)
        search.choose_aula_variant()
        selected = search.devices[0]
        assert selected.product_variant == 1

        with patch.object(search.window, "clipboard_clear") as clear, patch.object(search.window, "clipboard_append") as copy:
            search.copy_button.invoke()
            clear.assert_called_once()
            copy.assert_called_once_with(selected.details())

        search.add_button.invoke()
        add.assert_called_once_with(selected)
        assert search.closed and search.timer is None
        assert not search.window.winfo_exists()
        root.update()
        assert not errors

    print("Tk search smoke passed: mode switch, scan failure and retry, filtering, details, AULA options, copy, add and cleanup.")


if __name__ == "__main__":
    smoke()
