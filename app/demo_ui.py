"""安全的 Tk 界面演示：只使用内存中的模拟设备与读数。"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import time
import tkinter as tk
from unittest.mock import Mock, patch

import my_devices as ui
from device_readers import Reading
from saved_devices import SavedDevice, DEFAULT_DEVICE_ID


class MemoryStore:
    def __init__(self):
        self.selected = DEFAULT_DEVICE_ID
        self.devices = [
            SavedDevice("demo-mouse", "demo-mouse", "PRO WIRELESS", "Logitech", "046D:C539", "鼠标"),
            SavedDevice("demo-audio", "demo-audio", "Beats Studio Buds", "Beats", "00:11:22:33:44:55", "耳机", transport="bluetooth"),
        ]

    def load(self):
        return self.devices, self.selected

    def default_visible(self):
        return True

    def select(self, key):
        self.selected = key


class DemoPanel(ui.NexusPanel):
    def refresh(self):
        now = time.time()
        self.readings = {
            DEFAULT_DEVICE_ID: Reading("F87 Pro", "", level=86, connected=True, status="电池供电", observed_at=now,
                                       detail="2.4 GHz 低延迟无线 · 模拟设备"),
            "demo-mouse": Reading("PRO WIRELESS", "", level=72, connected=True, status="电池供电", observed_at=now,
                                  detail="轻量无线游戏鼠标 · 模拟设备"),
            "demo-audio": Reading("Beats Studio Buds", "", level=94, connected=True, observed_at=now,
                                  detail="主动降噪真无线耳机 · 模拟设备",
                                  components={"left": {"level": 94, "charging": False},
                                              "right": {"level": 92, "charging": False},
                                              "case": {"level": 68, "charging": False}},
                                  component_observed_at={key: now for key in ("left", "right", "case")}),
        }
        self.reading = self.readings[self.selected_device_id]
        self.refresh_readings()
        return True

    def schedule(self):
        pass

    def pump(self):
        self.pump_after_id = None

    def runtime_tick(self):
        self.runtime_after_id = None
        self.refresh_readings()

    def update_runtime_display(self):
        item = self.overview_runtime_items.get(DEFAULT_DEVICE_ID)
        if item is not None:
            self.overview_canvas.itemconfigure(item, text="约 12 天可用")

    def open_receiver_search(self):
        from app_dialogs import OverlayDialog
        OverlayDialog(self.root, "模拟设备已就绪", "演示模式不会搜索真实硬件或保存设备配置。", width=620, height=240)

    def remove_device(self, _device_id):
        self.open_receiver_search()

    def open_settings(self):
        from app_dialogs import OverlayDialog, setting_row
        from toggle_switch import Switch
        dialog = OverlayDialog(self.root, "演示偏好", "本页仅演示交互，所有设置都保存在内存中。", width=720, height=430)
        for title, description in (("动态效果", "列表更新与卡片悬停动画"), ("电量通知", "设备电量较低时提醒")):
            variable = tk.BooleanVar(dialog, value=True)
            row = setting_row(dialog, dialog.inner, title, description, height=85)
            switch = Switch(row, variable, scale=dialog.scale)
            switch.place(relx=1, x=-dialog.p(50), y=dialog.p(20))


def main():
    parser = argparse.ArgumentParser(description="Nexus 安全模拟界面")
    parser.add_argument("--smoke", action="store_true", help="自动验证设备总览和电量更新后退出")
    parser.add_argument("--capture", help="将窗口截图保存为 PNG（仅 Windows）")
    args = parser.parse_args()
    with ExitStack() as stack:
        stack.enter_context(patch.object(ui, "DeviceStore", MemoryStore))
        stack.enter_context(patch.object(ui.tc, "load_config", return_value={"gpu_animation": False, "interval": 60}))
        for module, name in (("logitech_live", "MouseMonitor"), ("beats_battery", "BeatsMonitor"), ("aula_discovery", "AulaDiscovery")):
            stack.enter_context(patch(f"{module}.{name}", return_value=Mock()))
        root = tk.Tk()
        errors = []
        def callback_error(kind, value, traceback):
            errors.append(value)
            root.quit()
        root.report_callback_exception = callback_error
        panel = DemoPanel(root)
        root.title("Nexus · UI 预览 · 模拟设备")
        root.geometry("+100+100")
        def capture():
            if args.capture:
                import subprocess
                from pathlib import Path
                target = str(Path(args.capture).resolve()).replace("'", "''")
                root.attributes("-topmost", True)
                root.lift()
                root.update()
                x, y = root.winfo_rootx(), root.winfo_rooty()
                w, h = root.winfo_width(), root.winfo_height()
                script = ("Add-Type -AssemblyName System.Drawing; "
                          f"$b=New-Object System.Drawing.Bitmap({w},{h}); "
                          "$g=[System.Drawing.Graphics]::FromImage($b); "
                          f"$g.CopyFromScreen({x},{y},0,0,$b.Size); "
                          f"$b.Save('{target}',[System.Drawing.Imaging.ImageFormat]::Png); $g.Dispose(); $b.Dispose()")
                subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script], check=True)
        def check_update():
            assert len(panel.visible_devices) == 3
            panel.readings["demo-audio"].components["left"]["level"] = 88
            panel.refresh_readings()
            binding = panel.live.bindings[("demo-audio", "left_level")]
            assert binding[0].itemcget(binding[1][0], "text") == "88%"
            finish()
        def finish():
            capture()
            print("Tk smoke: 设备总览、耳机动态电量绑定均通过")
            panel.close()
        if args.smoke:
            root.after(800, check_update)
        elif args.capture:
            root.after(900, capture)
        root.mainloop()
        if errors:
            raise errors[0]


if __name__ == "__main__":
    main()
