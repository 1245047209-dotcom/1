"""Withdrawn Tk checks of the real navigation and both layouts; no hardware I/O."""
import ctypes
from pathlib import Path
import sys
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
root = tk.Tk()
root.withdraw()
with patch("aula_discovery.AulaDiscovery",return_value=Mock()):
    panel = md.NexusPanel(root)
for name in ("startup_after_id","after_id","pump_after_id","runtime_after_id"):
    timer = getattr(panel,name)
    if timer is not None:
        root.after_cancel(timer)
        setattr(panel,name,None)
for mode in ("all","single"):
    panel.view_mode = mode
    panel.draw()
    root.update_idletasks()
    assert root.minsize()==(952,553)
panel.close()
print("Navigation, pagination, empty list and both fixed-window GUI layouts passed")
