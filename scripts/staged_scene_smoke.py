"""Real hidden Tk checks for staged geometry, atomic commit, and rollback."""
import ctypes
from pathlib import Path
import sys
from unittest.mock import Mock, patch
app = source = Path(sys.argv[1]).resolve()
sys.path[:0] = [str(source),str(app)]
tcl = ctypes.CDLL(str(Path(sys.base_prefix)/"DLLs"/"tcl86t.dll"))
tcl.Tcl_FindExecutable.argtypes = [ctypes.c_char_p]
tcl.Tcl_FindExecutable(sys.executable.encode("utf-8"))
import tkinter as tk
from staged_canvas import SceneCanvas
from ui_scale import UIScale

root = tk.Tk()
root.withdraw()
ui = UIScale(root,.85)
canvas = SceneCanvas(root,width=952,height=553)
old = canvas.create_text(200,100,text="Nexus 电量",font=("Microsoft YaHei UI",10))
old_coords = canvas.coords(old)
canvas.begin_scene()
new = canvas.create_text(200,100,text="Nexus 电量",font=("Microsoft YaHei UI",10))
assert canvas.coords(new) == old_coords
assert canvas.bbox(new) == canvas.bbox(old)
ui.canvas(canvas)
assert canvas.coords(old) == old_coords, "outgoing scene was scaled"
expected = canvas.coords(new)
assert expected == [170,85], expected
canvas.commit_scene()
assert canvas.find_all() == (new,)
assert canvas.coords(new) == expected
canvas.begin_scene()
partial = canvas.create_rectangle(0,0,50,50)
canvas.abort_scene()
assert canvas.find_all() == (new,), "cancelled scene leaked items"
canvas.destroy()
root.destroy()

import my_devices as md
root = tk.Tk()
root.withdraw()
from saved_devices import SavedDevice, DEFAULT_DEVICE_ID
devices = [SavedDevice("test-mouse","HID\\VID_046D&PID_C539\\TEST","PRO WIRELESS","Logitech","046D:C539","鼠标"),
           SavedDevice("test-buds","BTHENUM\\DEV_020000000001","测试的Beats Studio Buds","Beats","02:00:00:00:00:01","耳机",transport="bluetooth")]
store = Mock()
store.load.return_value = devices, DEFAULT_DEVICE_ID
store.default_visible.return_value = True
with patch("aula_discovery.AulaDiscovery",return_value=Mock()), patch("my_devices.DeviceStore",return_value=store):
    panel = md.NexusPanel(root)
for name in ("startup_after_id","after_id","pump_after_id","runtime_after_id"):
    timer = getattr(panel,name)
    if timer is not None:
        root.after_cancel(timer)
        setattr(panel,name,None)

def snapshot(canvas):
    result = []
    for item in canvas.find_all():
        kind = canvas.type(item)
        text = canvas.itemcget(item,"text") if kind == "text" else ""
        coords = tuple(round(value,3) for value in canvas.coords(item))
        result.append((kind,text,coords,canvas.bbox(item)))
    return result

count = 0
for mode in ("light", "dark"):
    panel.appearance.set_mode(mode)
    for logical_width in (md.MIN_WIDTH, md.DEFAULT_WIDTH):
        panel._layout_size = (panel.ui.px(logical_width), panel._layout_size[1])
        panel.draw()
        body = panel.overview_canvas
        photos = body.find_withtag("product_photo")
        surfaces = body.find_withtag("product_surface")
        assert len(photos) == 3 and len(surfaces) == (3 if mode == "dark" else 0)
        badge = body.itemcget(body.find_withtag("status_badge")[0], "image")
        panel.transition.alpha = .5
        faded_badge = panel.transition.fade_image(badge)
        assert faded_badge.transparency_get(0, 0)
        assert faded_badge.width() == panel.ui.px(md.BADGE_WIDTH)
        panel.transition.alpha = 1.
        for surface, photo in zip(surfaces, photos):
            assert surface < photo
        for photo, card in zip(photos[1:], md.card_rectangles(logical_width, 3)[1:]):
            left, top, right, bottom = body.bbox(photo)
            x1, y1, x2, y2 = card
            assert bottom - top >= 100
            assert panel.ui.px((x1+x2)/2) < left < right < panel.ui.px(x2)
            assert panel.ui.px(y1+12) < top < bottom < panel.ui.px(y2-43)

        expected = snapshot(panel.canvas), snapshot(body)
        panel.transition.phase, panel.transition.alpha, panel.transition.offset = "prepare", 0, 24
        for _ in panel.reveal_transition():
            pass
        panel.transition.phase, panel.transition.alpha, panel.transition.offset = "idle", 1, 0
        panel.transition.paint()
        assert (snapshot(panel.canvas), snapshot(body)) == expected, (mode, logical_width)
        assert not panel.canvas.staging and not body.staging
        panel.finish_transition()
        count += 1
errors = []
root.report_callback_exception = lambda *error: errors.append(error)
def change(index):
    panel.saved_devices = devices[:1] if index % 3 == 0 else devices
    panel.card_hover.suspend()
    panel.transition.start()
for index, delay in enumerate((0,25,100,115,145,180,205,230)):
    root.after(delay,lambda index=index: change(index))
def finish():
    try:
        assert not errors, errors
        assert not panel.transition.active
        assert not panel.canvas.staging and not panel.overview_canvas.staging
        assert set(panel.visible_devices) == {key for key, _ in panel.device_entries()}
        for layer in panel.transition.layers:
            assert all(layer["canvas"].type(item) for item,_style in layer["entries"])
        from frame_pacer import _resolution
        assert _resolution.users == 0, "precision request leaked after cancellation"
    finally:
        panel.close()
root.after(1300,finish)
root.mainloop()
if errors:
    raise RuntimeError(errors)
print(f"Staged commit, cancellation, unchanged outgoing scene, scaling, {count} exact layouts and 8 rapid interrupted list updates passed.")
