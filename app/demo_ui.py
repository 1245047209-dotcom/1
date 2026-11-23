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
            "demo-mouse": Reading("PRO WIRELESS", "", level=18, connected=True, status="电池供电", observed_at=now,
                                  detail="轻量无线游戏鼠标 · 模拟设备"),
            "demo-audio": Reading("Beats Studio Buds", "", level=94, connected=True, observed_at=now,
                                  detail="主动降噪真无线耳机 · 模拟设备",
                                  components={"left": {"level": 94, "charging": False},
                                              "right": {"level": 92, "charging": False},
                                              "case": {"level": 40, "charging": True}},
                                  component_observed_at={key: now for key in ("left", "right", "case")}),
        }
        self.reading = self.readings[self.selected_device_id]
        self.last_refresh_at = now
        self.refresh_readings()
        return True

    def schedule(self):
        pass

    def pump(self):
        self.pump_after_id = None

    def runtime_tick(self):
        self.runtime_after_id = None
        self.refresh_readings()

    def open_receiver_search(self):
        """The real 添加设备 window fed with simulated scan results; nothing is saved."""
        from receiver_search import ReceiverSearch
        if self.receiver_search and not self.receiver_search.closed:
            self.receiver_search.window.lift()
            return
        added = {device.instance_id for device in self.saved_devices}

        def add(device):
            created = device.instance_id not in added
            added.add(device.instance_id)
            return created
        self.receiver_search = ReceiverSearch(self.root, on_add=add, is_added=lambda device: device.instance_id in added)

    def remove_device(self, _device_id):
        """Demo devices live in memory only; removing is not simulated."""


def demo_scan(mode):
    from bluetooth_devices import BluetoothPeripheral
    from receiver_scan import Receiver
    if mode == "bluetooth":
        return [
            BluetoothPeripheral("demo-audio", "Beats Studio Buds", "Beats", "00:11:22:33:44:55", "耳机", True, 80),
            BluetoothPeripheral("demo-airpods", "AirPods Pro", "Apple", "66:77:88:99:AA:BB", "耳机", False, None),
        ]
    return [
        Receiver("demo-mouse", "PRO WIRELESS", "Logitech", "046D:C539", "鼠标", "模拟设备", True),
        Receiver("demo-aula", "F87 ProV2", "AULA", "258A:010C", "键盘", "模拟设备", True),
        Receiver("demo-g304", "LIGHTSPEED Receiver", "Logitech", "046D:C53F", "鼠标", "模拟设备", True),
        Receiver("demo-hid", "USB Input Device", "未提供", "1A2C:4C5E", "USB 外设", "模拟设备", False),
    ]

    def open_settings(self):
        """The real iOS settings sheet; saving only changes the in-memory config."""
        from app_dialogs import SettingsDialog
        if self.settings_window and self.settings_window.winfo_exists():
            self.settings_window.show()
            return
        if not getattr(self, "updates", None):
            from update_ui import UpdateManager
            self.updates = UpdateManager(self.root)  # no update check is started here
        self.settings_window = SettingsDialog(self, self.updates)


def main():
    parser = argparse.ArgumentParser(description="Nexus 安全模拟界面")
    parser.add_argument("--smoke", action="store_true", help="自动验证设备总览和电量更新后退出")
    parser.add_argument("--skip-frame-timing", action="store_true",
                        help="自检时保留功能验证，跳过帧间隔阈值")
    parser.add_argument("--capture", help="将窗口截图保存为 PNG（仅 Windows）")
    parser.add_argument("--theme", choices=("system", "light", "dark"), default="light", help="模拟界面使用的主题")
    parser.add_argument("--page", choices=("main", "settings", "settings-bottom", "sheet", "search", "search-expanded"),
                        default="main", help="截图的页面")
    parser.add_argument("--device", default="demo-mouse", help="--page sheet 时打开哪台设备的详情")
    parser.add_argument("--no-glass", action="store_true", help="关闭毛玻璃背景")
    args = parser.parse_args()
    with ExitStack() as stack:
        stack.enter_context(patch.object(ui, "DeviceStore", MemoryStore))
        config = {"gpu_animation": False, "interval": 60, "theme": args.theme, "low_battery_alert": True,
                  "glass_backdrop": not args.no_glass}
        stack.enter_context(patch.object(ui.tc, "load_config", return_value=config))
        stack.enter_context(patch.object(ui.tc, "save_config", side_effect=lambda changes, **_kw: config.update(changes)))
        for name, value in (("set_autostart", True), ("is_autostart_enabled", True),
                            ("post_to_tray", False), ("install_desktop_links", {})):
            stack.enter_context(patch.object(ui.tc, name, return_value=value))
        stack.enter_context(patch("receiver_search.scan_devices", side_effect=demo_scan))
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
        def capture(window=None):
            if args.capture:
                import subprocess
                from pathlib import Path
                window = window or root
                target = str(Path(args.capture).resolve()).replace("'", "''")
                window.attributes("-topmost", True)
                window.lift()
                window.update()
                x, y = window.winfo_rootx(), window.winfo_rooty()
                w, h = window.winfo_width(), window.winfo_height()
                script = ("Add-Type -AssemblyName System.Drawing; "
                          f"$b=New-Object System.Drawing.Bitmap({w},{h}); "
                          "$g=[System.Drawing.Graphics]::FromImage($b); "
                          f"$g.CopyFromScreen({x},{y},0,0,$b.Size); "
                          f"$b.Save('{target}',[System.Drawing.Imaging.ImageFormat]::Png); $g.Dispose(); $b.Dispose()")
                subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script], check=True)
        def check_glass():
            if panel.native_glass:
                assert not panel.glass_items and not panel.glass_buttons, "acrylic skips wallpaper crops"
                assert panel.canvas.cget("background") == panel.acrylic.background_color
                assert panel.overview_canvas.cget("background") == panel.acrylic.background_color
                print("Tk smoke: 实时桌面亚克力已接入主窗口和设备列表")
                return
            if not panel.glass_on():
                print("Tk smoke: 未能读取桌面壁纸，毛玻璃已自动改用纯色背景：" + (panel.glass.error or "无壁纸"))
                return
            roles = sorted(role for canvas, _item, _w, _h, role, _r in panel.glass_items)
            assert roles == ["base", "base", "card", "card"], roles
            assert len(panel.glass_buttons) == 3
            assert not panel.overview_canvas.find_withtag("surface") or all(
                "glass" in panel.overview_canvas.gettags(item) or panel.overview_canvas.type(item) == "image"
                for item in panel.overview_canvas.find_withtag("surface"))
            base = [item for canvas, item, _w, _h, role, _r in panel.glass_items
                    if canvas is panel.overview_canvas and role == "base"][0]
            assert panel.overview_canvas.find_all()[0] == base, "list backdrop is the lowest item"
            card = [item for canvas, item, _w, _h, role, _r in panel.glass_items if role == "card"][0]
            image = panel.overview_canvas.itemcget(card, "image")
            width = int(root.tk.call("image", "width", image))
            def brightness(x, y):  # Tk returns (r, g, b) as a tuple or as "r g b", depending on version
                value = root.tk.call(image, "get", x, y)
                parts = value if isinstance(value, tuple) else root.tk.splitlist(value)
                return sum(int(part) for part in parts)
            top, middle = brightness(width // 2, 0), brightness(width // 2, 12)
            assert top >= middle, ("card rim brighter than the glass", top, middle)
            root.geometry(f"+{root.winfo_x() + 40}+{root.winfo_y() + 30}")
            root.update()
            before = panel.overview_canvas.itemcget(base, "image")
            panel.refresh_glass()
            assert panel.overview_canvas.itemcget(base, "image") != before, "glass follows the window"
            print("Tk smoke: 毛玻璃背景（主窗口、卡片高光边、按钮、移动窗口后重新取样）均通过")
        def check_update():
            check_glass()
            assert len(panel.visible_devices) == 3
            canvas = panel.overview_canvas
            texts = [canvas.itemcget(item, "text") for item in canvas.find_all() if canvas.type(item) == "text"]
            assert texts.count("连接方式") == texts.count("供电状态") == 3
            assert texts.count("2.4G 无线") == 2 and "蓝牙" in texts
            assert all((key, "row_value") not in panel.live.bindings for key in panel.visible_devices)
            _, (power,), _ = panel.live.bindings[(DEFAULT_DEVICE_ID, "row_power")]
            assert canvas.itemcget(power, "text") == "电池供电"
            keyboard = panel.readings[DEFAULT_DEVICE_ID]
            keyboard.status = "充电中"
            panel.readings["demo-audio"].components["left"]["level"] = 70
            panel.refresh_readings()
            assert canvas.itemcget(power, "text") == "正在充电"
            _, (ring,), _ = panel.live.bindings[("demo-audio", "ring_value")]
            assert canvas.itemcget(ring, "text") == "70%"
            keyboard.status = "电池供电"
            panel.refresh_readings()
            assert canvas.itemcget(power, "text") == "电池供电"
            assert ("demo-audio", "ring_case") in panel.live.bindings
            canvas, (summary,), _ = panel.live.bindings[("header", "summary")]
            _canvas, (when,), _ = panel.live.bindings[("header", "summary_time")]
            assert canvas.itemcget(summary, "text") == "1 台需要充电"
            assert canvas.itemcget(when, "text").startswith(" · ")
            assert canvas.itemcget(summary, "fill") != canvas.itemcget(when, "fill")
            assert abs(canvas.coords(when)[0] - canvas.bbox(summary)[2]) <= 1, "time must follow the summary"
            check_settings()
            check_sheet()
            check_search()
        def check_search():
            panel.open_receiver_search()
            search = panel.receiver_search
            def verify_usb():
                root.update()
                tabs = search.mode_tabs
                assert not tabs.ring_visible, "no focus ring when the window opens"
                tabs.focus_force(); root.update()
                assert not tabs.ring_visible, "no focus ring after a click"
                tabs.event_generate("<<TraverseIn>>"); root.update()
                assert tabs.keyboard, "Tab marks keyboard focus"
                if tabs.focused:  # Windows may refuse focus to a background demo window
                    assert tabs.ring_visible, "focus ring after Tab"
                search.window.focus_force(); root.update()
                assert not tabs.ring_visible
                assert not search.busy and search.search_button.cget("text") == "重新搜索"
                names = [search.devices[i].model for i in search.visible_indexes()]
                assert names == ["PRO WIRELESS", "F87 ProV2", "LIGHTSPEED Receiver"], names
                assert search.status.get() == "找到 3 个无线接收器 / 外设"
                assert search.actions == {"demo-mouse": "已添加", "demo-aula": "添加", "demo-g304": "添加"}, search.actions
                search.add(search.index_of("demo-g304"))
                root.update()
                assert search.actions["demo-g304"] == "已添加" and not search.closed, "stays open after adding"
                assert search.feedback.get().startswith("已添加 LIGHTSPEED Receiver")
                search.other_switch.invoke()
                root.update()
                assert search.actions.get("demo-hid") == "仍然添加" and search.status.get() == "显示 4 个 USB 设备"
                aula = search.index_of("demo-aula")
                search.toggle(aula)
                root.update()
                assert search.expanded == "demo-aula" and search.model_value.get() == "F87 ProV2"
                assert search.variant_value.get() == "配色 1"
                search.choose_variant(aula, 2)
                assert search.variant_value.get() == "配色 3" and "demo-aula" in search.pending
                search.add(aula)
                assert search.actions["demo-aula"] == "已添加"
                search.choose_model(aula, "F87S")
                root.update()
                assert search.actions["demo-aula"] == "更新" and search.model_value.get() == "F87S"
                search.add(aula)
                assert search.feedback.get() == "已更新 F87S。" and search.actions["demo-aula"] == "已添加"
                search.mode_tabs._step(1)  # → 蓝牙设备 (keyboard)
                root.after(500, verify_bluetooth)
            def verify_bluetooth():
                root.update()
                assert search.mode.get() == "bluetooth" and not search.busy
                assert search.status.get() == "2 台已配对 · 1 台已连接", search.status.get()
                assert search.actions == {"demo-audio": "已添加", "demo-airpods": "添加"}, search.actions
                assert not search.options.winfo_manager(), "USB option hidden for Bluetooth"
                search.close()
                root.update()
                assert search.closed
                print("Tk smoke: 添加设备（连续添加、待确认 USB、狼蛛型号与配色、切换蓝牙）均通过")
                finish()
            root.after(500, verify_usb)
        def settle(sheet):
            deadline = time.perf_counter() + 2
            while sheet.animating and time.perf_counter() < deadline:
                root.update()
                time.sleep(.002)
            root.update()
            assert not sheet.animating, "sheet animation finished"
        def check_sheet():
            started = time.perf_counter()
            panel.show_device_information("demo-mouse")
            sheet = panel.device_sheet
            opened = time.perf_counter() - started
            settle(sheet)
            assert sheet.surface_renders == 1, ("sheet surface rendered once", sheet.surface_renders)
            frames = sheet.frame_times
            assert frames, "animated"
            longest = max(frames) * 1000
            stages = "、".join(f"{name} {value * 1000:.0f}" for name, value in sheet.open_times.items())
            print(f"Tk smoke: 详情面板打开准备 {opened * 1000:.0f} ms（{stages}），动画 {len(frames) + 1} 帧，"
                  f"平均 {sum(frames) / len(frames) * 1000:.1f} ms，最长 {longest:.1f} ms")
            slowest = max(range(len(sheet.frame_log)), key=lambda i: sheet.frame_log[i][0])
            gap, move, redraws = sheet.frame_log[slowest]
            print(f"Tk smoke: 最慢的是第 {slowest + 2} 帧：间隔 {gap:.1f} ms，其中移动面板 {move:.1f} ms，"
                  f"动画期间主窗口重绘 {sheet.frame_log[-1][2]} 次；各帧间隔 "
                  + " ".join(f"{entry[0]:.0f}" for entry in sheet.frame_log))
            def images(widget):
                found = []
                if widget.winfo_class() == "Canvas":
                    found += [widget.itemcget(item, "image") for item in widget.find_all()
                              if widget.type(item) == "image" and widget.itemcget(item, "image")]
                for child in widget.winfo_children():
                    found += images(child)
                return found
            def has_alpha(name):
                width, height = int(root.tk.call("image", "width", name)), int(root.tk.call("image", "height", name))
                points = {(0, 0), (width - 1, 0), (0, height - 1), (width - 1, height - 1), (width // 2, 0)}
                # Tk 8.6 only reports fully transparent pixels; an unblended rounded corner is one.
                return any(root.tk.getboolean(root.tk.call(name, "transparency", "get", x, y)) for x, y in points)
            translucent = [name for name in images(sheet.card) if has_alpha(name)]
            assert not translucent, ("sheet images must be opaque", translucent)
            if not args.skip_frame_timing:
                assert longest < 34, ("sheet animation stutters", longest)  # at most one missed 60 Hz frame
                assert sum(frames) / len(frames) < 21, ("sheet animation is slow", frames)
            assert sheet is not None and sheet.card.winfo_ismapped()
            assert sheet.vars["level"].get() == "18%", sheet.vars["level"].get()
            assert sheet.vars["status"].get() == "电量低，建议尽快充电"
            assert sheet.vars["status_time"].get() == " · 刚刚"
            assert sheet.status_label.cget("fg") != sheet.status_time_label.cget("fg"), "only the warning is red"
            assert sheet.vars["hardware"].get() == "046D:C539"
            assert getattr(root, "_nexus_active_dialog", None) is sheet
            panel.readings["demo-mouse"] = Reading("PRO WIRELESS", "", level=55, connected=True,
                                                   status="电池供电", observed_at=time.time())
            panel.refresh_readings()
            assert sheet.vars["level"].get() == "55%", "sheet follows live readings"
            sheet.remove()
            assert "再点一次" in sheet.remove_button.cget("text") and panel.device_sheet is sheet
            sheet.on_close()  # Esc path
            root.update()
            assert panel.device_sheet is None and panel.sheet_min_height == 0
            assert not sheet.winfo_exists() and getattr(root, "_nexus_active_dialog", None) is None
            draws, height = panel.draw_count, root.winfo_height()
            panel.show_device_information("demo-audio")
            sheet = panel.device_sheet
            settle(sheet)
            assert panel.draw_count == draws, "opening a sheet must not redraw the overview"
            assert root.winfo_height() == height, "the window keeps its size"
            assert sheet.surface_renders == 1, sheet.surface_renders
            assert sheet.part_names == ["left", "right", "case"]
            assert "充电中" in sheet.vars["case"].get()
            assert sheet.vars["status"].get() == "充电仓充电中", sheet.vars["status"].get()
            if sheet.body.winfo_reqheight() > sheet.scroll.winfo_height():
                assert sheet.scrollbar.winfo_manager() == "place", "a tall sheet scrolls"
                sheet.scroll.yview_moveto(1)
                root.update()
                assert sheet.scroll.yview()[1] >= .999
            sheet.close()
            root.update()
            print("Tk smoke: 设备详情面板（实时更新、二次确认移除、Esc 关闭、窗口大小不变、内容可滚动）均通过")
        def check_settings():
            import theme
            start = panel.appearance.mode
            panel.open_settings()
            dialog = panel.settings_window
            root.update()
            assert dialog.header_title.cget("text") == "设置"
            from ios_controls import ThinScrollbar
            bar = dialog.preferences_scrollbar
            assert isinstance(bar, ThinScrollbar) and bar.winfo_manager() == "pack", "settings overflow -> thin bar"
            dialog.preferences_canvas.yview_moveto(1)
            root.update()
            assert bar.get()[1] >= .999 and bar.find_all(), bar.get()
            bar.command("moveto", 0)
            root.update()
            assert bar.get()[0] <= .001
            assert isinstance(panel.overview_scrollbar, ThinScrollbar)
            dialog.pick_theme("dark")
            root.update()
            assert panel.appearance.mode == "dark" and theme.is_dark(root), "dark preview"
            assert dialog.auto_theme.get() is False
            dialog.cancel()
            root.update()
            assert panel.appearance.mode == start, "取消 must undo the preview"
            assert config["theme"] == args.theme, "取消 must not save"
            panel.open_settings()
            dialog = panel.settings_window
            dialog.select_page("updates")
            assert dialog.header_title.cget("text") == "软件更新"
            dialog.select_page("settings")
            dialog.interval.set("5 分钟")
            dialog.auto.set(False)
            dialog.pick_theme("dark")
            dialog.glass_switch.invoke()  # off
            dialog.done()
            assert config["glass_backdrop"] is False and not panel.native_glass and not panel.glass_on() and not panel.glass_items
            assert config["panel_auto_refresh"] is False and panel.auto is False
            panel.open_settings()
            assert panel.settings_window.auto.get() is False
            panel.settings_window.glass_switch.invoke()  # back on
            panel.settings_window.done()
            assert config["glass_backdrop"] is True
            root.update()
            assert not dialog.winfo_exists()
            assert config["theme"] == "dark" and config["interval"] == 300, config
            assert panel.appearance.mode == "dark" and panel.interval == 300
            panel.open_settings()
            dialog = panel.settings_window
            dialog.auto_theme_switch.invoke()  # 自动 on -> follow Windows
            assert dialog.theme_mode == "system" and panel.appearance.mode == "system"
            dialog.done()
            assert config["theme"] == "system"
            print("Tk smoke: 设置页预览、取消还原、完成保存均通过")
        def finish():
            capture()
            print("Tk smoke: 设备列表的连接方式、动态供电状态和电池小组件均通过")
            panel.close()
        if args.smoke:
            root.after(800, check_update)
        elif args.capture and args.page.startswith("search"):
            def search_shot():
                panel.open_receiver_search()
                search = panel.receiver_search
                def shoot():
                    if args.page == "search-expanded":
                        search.toggle(search.index_of("demo-aula"))
                    root.after(500, lambda: (capture(search.window), panel.close()))
                root.after(600, shoot)
            root.after(900, search_shot)
        elif args.capture and args.page == "sheet":
            def sheet_shot():
                panel.show_device_information(args.device)
                root.after(900, lambda: (capture(), panel.close()))
            root.after(900, sheet_shot)
        elif args.capture and args.page.startswith("settings"):
            def settings_shot():
                panel.open_settings()
                if args.page == "settings-bottom":
                    root.update()
                    panel.settings_window.preferences_canvas.yview_moveto(1)
                root.after(900, lambda: (capture(panel.settings_window), panel.close()))
            root.after(900, settings_shot)
        elif args.capture:
            root.after(1500, lambda: (capture(), panel.close()))
        root.mainloop()
        if errors:
            raise errors[0]


if __name__ == "__main__":
    main()
