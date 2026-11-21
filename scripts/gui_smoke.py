"""Withdrawn Tk checks of the real navigation and both layouts; no hardware I/O."""
import ctypes
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import time
from types import SimpleNamespace
from unittest.mock import Mock, patch

app = Path(sys.argv[1]).resolve()
sys.path.insert(0,str(app))
tcl = ctypes.CDLL(str(Path(sys.base_prefix)/"DLLs"/"tcl86t.dll"))
tcl.Tcl_FindExecutable.argtypes = [ctypes.c_char_p]
tcl.Tcl_FindExecutable(sys.executable.encode("utf-8"))
import tkinter as tk
from ui_scale import UIScale
from device_navigation import DeviceNavigation
from view_switcher import ViewSwitcher
root = tk.Tk()
root.withdraw()
ui = UIScale(root,.85)
items = (("keyboard","F87 Pro","keyboard"),("mouse","PRO WIRELESS","mouse"),("buds","Studio Buds","beats-studio-buds"))
device = DeviceNavigation(root,ui=ui,command=lambda _key:None,width=600)
device.set_items(items,"mouse")
for index,(left,right,_slot) in enumerate(device.cells):
    bounds = device.bbox(device.labels[index])
    assert bounds[0]>=ui.px(left) and bounds[2]<=ui.px(right)
ids,images = device.find_all(),tuple(root.tk.call("image","names"))
device.set_items(items,"buds")
device.cancel_tick()
device.started -= .4
device.tick()
assert device.find_all()==ids and tuple(root.tk.call("image","names"))==images
device.set_items(items+(("four","Another receiver","usb"),),"four")
assert device.start==3 and len(device.cells)==1
device.set_items((),None)
assert device.hit(10,10) is None
device.close()
root.destroy()
import my_devices as md
from device_layout import CARD_PADDING, content_height
from receiver_scan import Receiver
from saved_devices import DeviceStore

root = tk.Tk()
root.withdraw()
root.attributes("-alpha", 0.0)
errors = []
root.report_callback_exception = lambda _kind, error, _trace: errors.append(error)

def wait_for(condition):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        root.update()
        if errors:
            raise errors[0]
        if condition():
            return
        time.sleep(.015)
    raise AssertionError("The dashboard did not reach the expected geometry")

with TemporaryDirectory() as data, patch("aula_discovery.AulaDiscovery", return_value=Mock()):
    store = DeviceStore(Path(data) / "devices.json")
    store.set_view_mode("single")
    with patch("my_devices.DeviceStore", return_value=store):
        panel = md.NexusPanel(root)
    for name in ("startup_after_id", "after_id", "pump_after_id", "runtime_after_id"):
        timer = getattr(panel, name)
        if timer is not None:
            root.after_cancel(timer)
            setattr(panel, name, None)
    panel.refresh = Mock()
    try:
        panel.place_initial_window()
        root.deiconify()
        single_card = panel.ui.px(md.OVERVIEW_TOP + content_height(1) + md.FOOTER_HEIGHT)
        wait_for(lambda: root.winfo_height() == single_card)
        assert root.winfo_width() == panel.ui.px(md.DEFAULT_WIDTH)
        assert root.resizable() == (1, 0)
        assert panel.theme_picker.winfo_ismapped() and not panel.overview_scrollable
        gauge_canvas, gauge_item, gauge_width = panel.battery_gauges[md.DEFAULT_DEVICE_ID, None]
        panel.readings[md.DEFAULT_DEVICE_ID] = md.Reading("F87 Pro", "", level=75, connected=True)
        panel.refresh_readings()
        left, _, right, _ = gauge_canvas.coords(gauge_item)
        assert right - left == panel.ui.px(gauge_width * .75)
        assert gauge_canvas.itemcget(gauge_item, "state") == "normal"
        badge_canvas, (badge_item,), _ = panel.live.bindings[md.DEFAULT_DEVICE_ID, "badge_bg"]
        assert badge_canvas.type(badge_item) == "image"
        online_badge = badge_canvas.itemcget(badge_item, "image")
        panel.readings[md.DEFAULT_DEVICE_ID] = md.Reading("F87 Pro", "", error="offline")
        panel.refresh_readings()
        assert gauge_canvas.itemcget(gauge_item, "state") == "hidden"
        assert panel.live.bindings[md.DEFAULT_DEVICE_ID, "badge_bg"][1] == (badge_item,)
        offline_badge = badge_canvas.itemcget(badge_item, "image")
        assert offline_badge != online_badge
        assert int(root.tk.call("image", "width", offline_badge)) == panel.ui.px(md.BADGE_WIDTH)

        def receiver(index):
            return Receiver(
                rf"USB\VID_0C45&PID_0{index:03X}\TEST", f"测试设备 {index}", "Fixture",
                f"0C45:0{index:03X}", "USB", "测试", True,
            )

        assert panel.add_receiver(receiver(1))
        double_card = panel.ui.px(md.OVERVIEW_TOP + content_height(2) + md.FOOTER_HEIGHT)
        wait_for(lambda: not panel.transition.active and root.winfo_height() == double_card)
        first, second = [entry["rect"] for entry in panel.card_hover.layers.values()]
        assert first[0] == second[0] and first[2] == second[2] and first[3] < second[1]

        narrow = panel.ui.px(md.MIN_WIDTH)
        root.geometry(f"{narrow}x{double_card}")
        wait_for(lambda: root.winfo_width() == narrow and panel._geometry_after_id is None)
        assert panel.theme_picker.winfo_y() + panel.theme_picker.winfo_height() < panel.receiver_button.winfo_y()
        labels = tuple(panel.canvas.itemcget(item, "text") for item in panel.canvas.find_all()
                       if panel.canvas.type(item) == "text")
        assert "设备总览" in labels and "单个设备" not in labels
        assert panel.receiver_button.winfo_x() + panel.receiver_button.winfo_width() < panel.refresh_button.winfo_x()
        assert panel.refresh_button.winfo_x() + panel.refresh_button.winfo_width() <= narrow

        wait_for(lambda: not panel.transition.active)
        x = panel.ui.px(second[0] + 42)
        y = panel.ui.px(second[1] + 25)
        event = SimpleNamespace(x=x, y=y, x_root=panel.overview_canvas.winfo_rootx()+x,
                                y_root=panel.overview_canvas.winfo_rooty()+y)
        selected = panel.saved_devices[0].id
        with patch.object(panel.device_menu, "tk_popup") as popup, patch.object(panel, "show_device_information") as info:
            panel.overview_click(event)
            popup.assert_called_once_with(event.x_root, event.y_root)
            panel.device_menu.invoke(0)
            info.assert_called_once_with(selected)
        assert len(panel.visible_devices) == 2 and root.winfo_height() == double_card
        panel.device_menu.invoke(1)
        wait_for(lambda: not panel.transition.active and root.winfo_height() == single_card)
        assert root.winfo_width() == narrow and set(panel.visible_devices) == {md.DEFAULT_DEVICE_ID}

        with patch("my_devices.window_work_area", return_value=((0, 0, 950, 615), 14, 34)):
            for index in range(2, 7):
                assert panel.add_receiver(receiver(index))
            wait_for(lambda: not panel.transition.active and root.winfo_height() == 615 - 34 - 2 * md.WINDOW_MARGIN)
            assert panel.overview_scrollable and panel.overview_scrollbar.winfo_ismapped()
            assert len(panel.card_hover.layers) == 6
            last = next(reversed(panel.card_hover.layers.values()))["rect"]
            panel.overview_canvas.yview_moveto(1)
            root.update()
            assert panel.overview_canvas.yview()[1] == 1.0
            assert panel.overview_canvas.canvasy(panel.overview_canvas.winfo_height()) >= panel.ui.px(last[3])
            assert panel.ui.px(last[3] + CARD_PADDING) <= int(panel.overview_canvas.cget("scrollregion").split()[-1])

        store.remove(md.DEFAULT_DEVICE_ID)
        for device in panel.saved_devices:
            store.remove(device.id)
        panel.saved_devices, panel.selected_device_id = store.load()
        panel.device_changed()
        empty = panel.ui.px(md.OVERVIEW_TOP + content_height(0) + md.FOOTER_HEIGHT)
        wait_for(lambda: not panel.transition.active and root.winfo_height() == empty)
        assert empty < single_card and not panel.overview_scrollable
    finally:
        panel.close()
print("Navigation and vertical dashboard passed: narrower width, automatic height, legacy preference, card actions, removal and overflow.")
