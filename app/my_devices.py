# -*- coding: utf-8 -*-
"""Nexus 风格的狼蛛 F87 Pro 电量面板。"""
from __future__ import annotations

import argparse
import queue
import threading
import time
import traceback
from pathlib import Path
from typing import Optional

import tray_common as tc
import win_link
from device_readers import KEYBOARD_TITLE, Reading, read_all
from battery_runtime import format_remaining, get_runtime_view
from low_battery_alert import LowBatteryNotifier
from saved_devices import DeviceStore, DeviceStoreError, DEFAULT_DEVICE_ID, read_saved_device
from device_layout import read_device_batch, card_rectangles

BG = "#f3f4f6"
CARD = "#ffffff"
TEXT = "#292c32"
MUTED = "#667384"
GREEN = "#20c878"
GREEN_SOFT = "#e7f7ed"
BLACK = "#17191d"
ORANGE = "#efad46"
BORDER = "#e9ebee"
FONT = "Microsoft YaHei UI"
UI_SCALE = .85
LAYOUT_WIDTH, LAYOUT_HEIGHT = 1120, 650
WINDOW_WIDTH, WINDOW_HEIGHT = int(LAYOUT_WIDTH * UI_SCALE + .5), int(LAYOUT_HEIGHT * UI_SCALE + .5)


def clipped_label(canvas, text, font, max_width):
    """Keep a single-line canvas label inside its own card/column."""
    if canvas.tk.call("font", "measure", font, text) <= max_width:
        return text
    while text and canvas.tk.call("font", "measure", font, text + "…") > max_width:
        text = text[:-1]
    return text + "…"


def level_color(level: Optional[int]) -> str:
    if level is None:
        return MUTED
    if level >= 60:
        return GREEN
    if level >= 30:
        return "#e6ae3d"
    return "#ef6f70"


def rounded_rect(canvas, x1, y1, x2, y2, radius, fill, outline="", width=1, tags=None):
    """Canvas 没有原生圆角矩形，用四个圆角和中间矩形拼出。"""
    radius = max(0, min(radius, (x2 - x1) / 2, (y2 - y1) / 2))
    tag = tags or "shape"
    canvas.create_rectangle(x1 + radius, y1, x2 - radius, y2,
                            fill=fill, outline="", tags=tag)
    canvas.create_rectangle(x1, y1 + radius, x2, y2 - radius,
                            fill=fill, outline="", tags=tag)
    for x, y, start in ((x1, y1, 90), (x2 - 2 * radius, y1, 0),
                        (x1, y2 - 2 * radius, 180), (x2 - 2 * radius, y2 - 2 * radius, 270)):
        canvas.create_arc(x, y, x + 2 * radius, y + 2 * radius,
                          start=start, extent=90, fill=fill, outline="", tags=tag)
    if outline:
        canvas.create_line(x1 + radius, y1, x2 - radius, y1,
                           fill=outline, width=width, tags=tag)
        canvas.create_line(x1 + radius, y2, x2 - radius, y2,
                           fill=outline, width=width, tags=tag)
        canvas.create_line(x1, y1 + radius, x1, y2 - radius,
                           fill=outline, width=width, tags=tag)
        canvas.create_line(x2, y1 + radius, x2, y2 - radius,
                           fill=outline, width=width, tags=tag)


def draw_card_surface(canvas, x1, y1, x2, y2):
    """九宫格伸缩白色卡片与柔和投影，保留圆角及模糊边缘的原始比例。"""
    import tkinter as tk

    width, height = round(x2 - x1) + 64, round(y2 - y1) + 64
    if not hasattr(canvas, "_card_surface_source"):
        asset = Path(__file__).resolve().parent / "assets" / "card-surface.png"
        canvas._card_surface_source = tk.PhotoImage(master=canvas, file=str(asset))
        canvas._card_surface_size = None
    if canvas._card_surface_size != (width, height):
        surface = tk.PhotoImage(master=canvas, width=width, height=height)
        # Corners stay 80x80; only the flat one-pixel edge/center slices stretch.
        columns = ((0, 80, 0, 80), (80, 1, 80, width - 160), (81, 80, width - 80, 80))
        rows = ((0, 80, 0, 80), (80, 1, 80, height - 160), (81, 80, height - 80, 80))
        for sx, sw, dx, dw in columns:
            for sy, sh, dy, dh in rows:
                tile = tk.PhotoImage(master=canvas, width=sw, height=sh)
                tile.tk.call(str(tile), "copy", str(canvas._card_surface_source),
                             "-from", sx, sy, sx + sw, sy + sh, "-to", 0, 0)
                if sw == 1 or sh == 1:
                    tile = tile.zoom(dw if sw == 1 else 1, dh if sh == 1 else 1)
                surface.tk.call(str(surface), "copy", str(tile), "-to", dx, dy)
        canvas._card_surface_image = surface
        canvas._card_surface_size = (width, height)
    return canvas.create_image(x1 - 32, y1 - 32, image=canvas._card_surface_image,
                               anchor="nw", tags="shape")


def battery_photo(canvas, level: Optional[int], charging: bool = False):
    """Reuse the icon atlas for initial layouts and in-place battery updates."""
    import tkinter as tk

    if not hasattr(canvas, "_battery_icon_sheet"):
        root = canvas._root()
        if not hasattr(root, "_battery_icon_sheet"):
            assets = Path(__file__).resolve().parent / "assets"
            asset = assets / "optimized" / "battery-icons.png"
            if not asset.exists():
                asset = assets / "battery-icons.png"
            root._battery_icon_sheet = tk.PhotoImage(master=root, file=str(asset))
            root._battery_icon_cache = {}
        canvas._battery_icon_sheet = root._battery_icon_sheet
        canvas._battery_icon_cache = root._battery_icon_cache
    index = 101 if level is None else max(0, min(100, int(level)))
    if charging:
        # 部分设备充电时只报告状态，不提供百分比。
        index = (100 if level is None else index) + 102
    width, height = 34, 17
    if index not in canvas._battery_icon_cache:
        image = tk.PhotoImage(master=canvas, width=width, height=height)
        columns = canvas._battery_icon_sheet.width() // width
        sx, sy = index % columns * width, index // columns * height
        image.tk.call(str(image), "copy", str(canvas._battery_icon_sheet),
                      "-from", sx, sy, sx + width, sy + height, "-to", 0, 0)
        canvas._battery_icon_cache[index] = image
    return canvas._battery_icon_cache[index]


def battery_icon(canvas, x, y, level: Optional[int], charging: bool = False):
    return canvas.create_image(x, y + 9, anchor="w",
                               image=battery_photo(canvas, level, charging), tags="shape")


def draw_wireless_icon(canvas, x, y):
    """显示参考图样式的黑色双弧 Wi-Fi 图标，背景透明。"""
    import tkinter as tk

    if not hasattr(canvas, "_wireless_icon"):
        asset = Path(__file__).resolve().parent / "assets" / "wireless-icon.png"
        canvas._wireless_icon = tk.PhotoImage(master=canvas, file=str(asset))
    canvas.create_image(x, y, image=canvas._wireless_icon, anchor="nw", tags="shape")


def draw_keyboard(canvas, x, y, w, h):
    """画一个轻量的键盘插画，避免额外图片依赖。"""
    canvas.create_oval(x + 18, y + h - 14, x + w - 8, y + h + 14,
                       fill="#e8eaed", outline="", tags="shape")
    rounded_rect(canvas, x, y, x + w, y + h, 18, "#e9edef", tags="shape")
    rounded_rect(canvas, x + 12, y + 12, x + w - 12, y + h - 12, 12,
                 "#f8f9fa", outline="#d9dde1", tags="shape")
    cols, rows = 15, 5
    gap = 4
    key_w = (w - 34 - gap * (cols - 1)) / cols
    key_h = (h - 38 - gap * (rows - 1)) / rows
    highlights = {(0, 0), (0, 1), (1, 0), (3, 12), (4, 13), (4, 14),
                  (2, 1), (2, 2), (2, 3)}
    for row in range(rows):
        for col in range(cols):
            kx = x + 17 + col * (key_w + gap)
            ky = y + 18 + row * (key_h + gap)
            if (row, col) in highlights:
                fill = "#d6e9ef" if row != 4 else "#f2d88a"
            else:
                fill = "#ffffff"
            rounded_rect(canvas, kx, ky, kx + key_w, ky + key_h, 3,
                         fill, outline="#d7dce0", width=1, tags="shape")
    # 空格键
    rounded_rect(canvas, x + 85, y + h - 43, x + w - 95, y + h - 23,
                 4, "#ffffff", outline="#d7dce0", tags="shape")


def draw_bluetooth_symbol(canvas, x, y):
    canvas.create_oval(x - 78, y - 92, x + 78, y + 92, fill="#edf3ff", outline="", tags="shape")
    for points in ((x, y - 58, x, y + 58),
                   (x, y - 58, x + 34, y - 28, x - 30, y + 29),
                   (x, y + 58, x + 34, y + 28, x - 30, y - 29)):
        canvas.create_line(*points, fill="#7196da", width=7, capstyle="round", joinstyle="round", tags="shape")


def is_bluetooth(device):
    return device is not None and getattr(device, "transport", "usb") == "bluetooth"


def connection_text(device):
    if is_bluetooth(device):
        return "蓝牙"
    if device is None or device.hardware_id.upper() in ("046D:C539", "046D:C53F"):
        return "2.4G 无线"
    from aula_catalog import model_for_device
    if model_for_device(device) is not None:
        return "2.4G 无线" if device.wireless else "USB 有线"
    from aula_f87_v2d import is_f87_v2d
    if is_f87_v2d(device.hardware_id, device.model):
        return "2.4G 无线"
    return "USB 无线接收器" if device.wireless else "USB 外设"


def product_kind(device):
    from aula_catalog import model_for_device
    if model_for_device(device) is not None:
        return "keyboard"
    from aula_f87_v2d import is_f87_v2d
    if device is not None and not is_bluetooth(device) and is_f87_v2d(device.hardware_id, device.model):
        return "keyboard"
    if device is None or (is_bluetooth(device) and "aula" in device.model.lower() and "f87" in device.model.lower()):
        return "keyboard"
    if is_bluetooth(device) and " ".join(device.model.casefold().split()).endswith("beats studio buds"):
        return "beats-studio-buds"
    return "mouse" if device.hardware_id.upper() in ("046D:C539", "046D:C53F") else None


def product_image_kind(device):
    if device is not None and not is_bluetooth(device) and device.hardware_id.upper() == "046D:C53F":
        return "mouse-g304"
    from aula_f87_v2d import is_f87_v2d
    if device is not None and not is_bluetooth(device) and is_f87_v2d(device.hardware_id, device.model):
        return "keyboard-v2d"
    from aula_catalog import model_for_device
    model = model_for_device(device)
    if model is not None:
        return "aula:" + model["key"] + ":" + str(getattr(device, "product_variant", 0))
    return product_kind(device)


def draw_component_batteries(canvas, x, y, components, *, size=13):
    components = components or {}
    for index, (key, label) in enumerate((("left", "左耳"), ("right", "右耳"), ("case", "充电仓"))):
        part = components.get(key) or {}
        level = part.get("level")
        charging = part.get("charging") is True
        row_y = y + index * 27
        canvas.create_text(x, row_y, text=label, anchor="w", fill=MUTED, font=(FONT, 9))
        battery_icon(canvas, x + 46, row_y - 7, level, charging)
        value = f"{level}%" if level is not None else "--"
        font = (FONT, size, "bold")
        canvas.create_text(x + 86, row_y, text=value,
                           anchor="w", fill=GREEN if charging else TEXT if level is not None else MUTED, font=font)


def component_charging_text(components):
    """Describe only charging flags actually reported by the device."""
    charging = [key for key in ("left", "right", "case")
                if ((components or {}).get(key) or {}).get("charging") is True]
    if not charging:
        return ""
    if len(charging) == 3:
        return "全部电池充电中"
    if charging == ["left", "right"]:
        return "左右耳充电中"
    labels = {"left": "左耳", "right": "右耳", "case": "充电仓"}
    return "及".join(labels[key] for key in charging) + "充电中"


class NexusPanel:
    def __init__(self, root):
        import tkinter as tk
        from tkinter import ttk
        from refresh_button import RefreshButton
        from device_navigation import DeviceNavigation
        from view_switcher import ViewSwitcher

        self.root = root
        from ui_scale import UIScale
        self.ui = UIScale(root, UI_SCALE)
        root._nexus_ui = self.ui
        root._nexus_panel = self
        self.canvas = tk.Canvas(root, bg=BG, highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        from ui_motion import DeviceTransition
        self.transition = DeviceTransition(root, self.reveal_transition, scale=UI_SCALE)
        from reading_ui import LiveItems, ReadingTimes
        self.live = LiveItems(self.transition.update_style)
        self.reading_times = ReadingTimes()
        self.visible_devices = {}
        brand_path = Path(__file__).resolve().parent / "assets" / "nexus-brand.png"
        self.toolbar_photo = tk.PhotoImage(master=self.canvas, file=str(brand_path.parent / "navigation" / "toolbar.png"))
        self.ui.images[str(self.toolbar_photo)] = tk.PhotoImage(master=self.canvas,
            file=str(brand_path.parent / "navigation" / "toolbar-85.png"))
        self.brand_photo = tk.PhotoImage(master=self.canvas, file=str(brand_path))
        self.window_icon_photos = [
            tk.PhotoImage(master=root, file=str(brand_path.parent / f"nexus-window-{size}.png"))
            for size in (16, 32, 48, 256)]
        root.iconphoto(True, *self.window_icon_photos)
        self.keyboard_photo_large = None
        self.keyboard_photo_small = None
        self.keyboard_image_error = ""
        self.product_png_sources = {}
        self.load_keyboard_product_image(tk)
        self.keyboard_v2d_crop = None
        v2d_path = brand_path.parent / "aula-f87-pro-v2d-product.png"
        if v2d_path.exists():
            self._keyboard_v2d_source = tk.PhotoImage(master=root, file=str(v2d_path))
            # Display the supplied image without its transparent outer margin.
            # The original PNG is preserved byte-for-byte in assets.
            self.keyboard_v2d_crop = tk.PhotoImage(master=root, width=543, height=208)
            root.tk.call(str(self.keyboard_v2d_crop), "copy", str(self._keyboard_v2d_source),
                         "-from", 137, 174, 680, 382, "-to", 0, 0)
        self.mouse_crop = None
        self.g304_crop = None
        g304_path = brand_path.parent / "logitech-g304-black.png"
        if g304_path.exists():
            self._g304_source = tk.PhotoImage(master=root, file=str(g304_path))
            self.g304_crop = tk.PhotoImage(master=root, width=135, height=237)
            root.tk.call(str(self.g304_crop), "copy", str(self._g304_source),
                         "-from", 141, 38, 276, 275, "-to", 0, 0)
        mouse_path = brand_path.parent / "optimized" / "mouse-crop.png"
        if mouse_path.exists():
            self.mouse_crop = tk.PhotoImage(master=root, file=str(mouse_path))
            self.product_png_sources["mouse"] = mouse_path.read_bytes()
        elif (brand_path.parent / "logitech-pro-wireless.png").exists():
            mouse_path = brand_path.parent / "logitech-pro-wireless.png"
            self.mouse_source = tk.PhotoImage(master=root, file=str(mouse_path))
            sw, sh = self.mouse_source.width(), self.mouse_source.height()
            self.mouse_crop = tk.PhotoImage(master=root, width=round(sw * .39), height=round(sh * .98))
            self.mouse_crop.tk.call(str(self.mouse_crop), "copy", str(self.mouse_source),
                                    "-from", round(sw * .30), round(sh * .01),
                                    round(sw * .69), round(sh * .99), "-to", 0, 0)
        self.beats_crop = None
        beats_path = brand_path.parent / "optimized" / "beats-crop.png"
        if beats_path.exists():
            self.beats_crop = tk.PhotoImage(master=root, file=str(beats_path))
            self.product_png_sources["beats-studio-buds"] = beats_path.read_bytes()
        elif (brand_path.parent / "beats-studio-buds-red.png").exists():
            beats_path = brand_path.parent / "beats-studio-buds-red.png"
            self.beats_source = tk.PhotoImage(master=root, file=str(beats_path))
            sw, sh = self.beats_source.width(), self.beats_source.height()
            self.beats_crop = tk.PhotoImage(master=root, width=round(sw * .80), height=round(sh * .90))
            self.beats_crop.tk.call(str(self.beats_crop), "copy", str(self.beats_source),
                                    "-from", round(sw * .10), round(sh * .02),
                                    round(sw * .90), round(sh * .92), "-to", 0, 0)
        self.display_image_cache = {}
        self.display_rgb_cache = {}
        self.display_photos = []
        self.results: "queue.Queue[tuple]" = queue.Queue()
        self.device_store = DeviceStore()
        self.saved_devices = []
        self.selected_device_id = DEFAULT_DEVICE_ID
        self.refresh_generation = 0
        from logitech_live import MouseMonitor
        self.mouse_monitor = MouseMonitor()
        from beats_battery import BeatsMonitor
        self.beats_monitor = BeatsMonitor()
        try:
            self.saved_devices, self.selected_device_id = self.device_store.load()
        except DeviceStoreError as exc:
            root.after(300, lambda text=str(exc): self.show_device_error(text))
        self.view_mode = self.device_store.view_mode()
        self.default_visible = self.device_store.default_visible()
        self.readings = {DEFAULT_DEVICE_ID: Reading(KEYBOARD_TITLE, "正在读取")} if self.default_visible else {}
        self.readings.update({d.id: Reading(d.label, "正在读取") for d in self.saved_devices})
        self.reading = self.readings.get(self.selected_device_id, Reading("暂无设备", ""))
        from aula_discovery import AulaDiscovery
        self.aula_discovery = AulaDiscovery()
        self.discovery_refresh_pending = False
        self.overview_runtime_items = {}
        self.overview_hits = []
        self.low_battery_notifier = LowBatteryNotifier(
            enabled=lambda: bool(tc.load_config().get("low_battery_alert", True)))
        self.busy = False
        self.refresh_started = 0.0
        self.refresh_result_after_id = None
        self.refresh_feedback_after_id = None
        self.startup_after_id = None
        self.pump_after_id = None
        self.runtime_after_id = None
        self.refresh_button = RefreshButton(self.canvas, command=self.refresh, ui=self.ui)
        self.auto = True
        self.interval = int(tc.load_config().get("interval", 60))
        self.after_id = None
        self.settings_window = None
        self.receiver_search = None
        self.receiver_button = tk.Button(
            self.canvas, text="搜索设备", command=self.open_receiver_search,
            font=(FONT, 9, "bold"), bg=CARD, fg=TEXT, activebackground="#e9ebee",
            activeforeground=TEXT, relief="flat", bd=0, cursor="hand2",
            highlightthickness=0, takefocus=True)
        self.device_switcher = DeviceNavigation(self.canvas, ui=self.ui, command=self.select_device, width=600)
        self.remove_device_button = tk.Button(
            self.canvas, text="移除设备", command=self.remove_selected_device,
            font=(FONT, 9), bg="white", fg="#929ba8", activebackground="#f5f7fa", activeforeground="#b45e68",
            relief="flat", bd=0, cursor="hand2", highlightthickness=0)
        self.view_switcher = ViewSwitcher(self.canvas, ui=self.ui, command=self.set_view_mode)
        self.overview_label = tk.Label(self.canvas, font=(FONT, 10), bg="white", fg=MUTED, anchor="w")
        for widget in (self.receiver_button, self.remove_device_button, self.overview_label):
            widget.configure(font=self.ui.font(widget.cget("font")))
        self.overview_canvas = tk.Canvas(self.canvas, bg=BG, highlightthickness=0, yscrollincrement=self.ui.px(24))
        self.overview_scrollbar = ttk.Scrollbar(self.canvas, orient="vertical", command=self.overview_canvas.yview)
        self.overview_canvas.configure(yscrollcommand=self.overview_scrollbar.set)
        self.overview_scrollable = False
        self.overview_canvas.bind("<ButtonRelease-1>", self.overview_click)
        self.overview_canvas.bind("<MouseWheel>", lambda event: self.overview_canvas.yview_scroll(
            -int(event.delta / 120) * 3, "units") if self.overview_scrollable else None)
        from card_hover import CardHover
        self.card_hover = CardHover(root, self.overview_canvas, scale=UI_SCALE, assets=brand_path.parent,
                                   blocked=lambda: self.view_mode != "all" or self.transition.active)
        self.overview_canvas.bind("<MouseWheel>", lambda _event: self.root.after_idle(self.card_hover.refresh_pointer), add="+")
        self.update_device_choices()
        self._drag = None

        root.title(tc.UI_TITLE)
        root.configure(bg=BG)
        root.geometry(f"{WINDOW_WIDTH}x{WINDOW_HEIGHT}")
        root.minsize(WINDOW_WIDTH, WINDOW_HEIGHT)
        root.maxsize(WINDOW_WIDTH, WINDOW_HEIGHT)
        root.resizable(False, False)
        self._layout_size = (WINDOW_WIDTH, WINDOW_HEIGHT)
        root.bind("<Configure>", self.on_configure)
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.bind("<F5>", self.refresh_shortcut)
        root.bind("<Control-r>", self.refresh_shortcut)
        self.canvas.bind("<ButtonPress-1>", self.start_drag)
        self.canvas.bind("<ButtonRelease-1>", self.click)
        self.canvas.bind("<B1-Motion>", self.drag)
        self.draw()
        self.startup_after_id = self.root.after(200, self.refresh)
        self.schedule()
        self.pump_after_id = self.root.after(100, self.pump)
        self.runtime_after_id = self.root.after(1000, self.runtime_tick)

    def load_keyboard_product_image(self, tk):
        """加载产品图并裁掉透明留白，生成两个适配窗口宽度的尺寸。"""
        prepared = Path(__file__).resolve().parent / "assets" / "optimized"
        if all((prepared / name).exists() for name in ("keyboard-crop.png", "keyboard-large.png", "keyboard-small.png")):
            self._keyboard_crop = tk.PhotoImage(master=self.root, file=str(prepared / "keyboard-crop.png"))
            self.keyboard_photo_large = tk.PhotoImage(master=self.root, file=str(prepared / "keyboard-large.png"))
            self.keyboard_photo_small = tk.PhotoImage(master=self.root, file=str(prepared / "keyboard-small.png"))
            self.product_png_sources["keyboard"] = (prepared / "keyboard-crop.png").read_bytes()
            for name, photo in (("large", self.keyboard_photo_large), ("small", self.keyboard_photo_small)):
                scaled = prepared / f"keyboard-{name}-{round(UI_SCALE * 100)}.png"
                if scaled.exists():
                    self.ui.images[str(photo)] = tk.PhotoImage(master=self.root, file=str(scaled))
            return
        image_path = Path(__file__).resolve().parent / "assets" / "aula-f87-pro-product.png"
        if not image_path.exists():
            self.keyboard_image_error = f"产品图不存在：{image_path.name}"
            return
        try:
            source = tk.PhotoImage(file=str(image_path))
            source_w, source_h = source.width(), source.height()
            # 适配新产品图的主体边界，保留顶部凸起与外壳四周的留白。
            x1, y1 = int(source_w * 0.19), int(source_h * 0.29)
            x2, y2 = int(source_w * 0.818), int(source_h * 0.714)
            crop = tk.PhotoImage(width=x2 - x1, height=y2 - y1)
            crop.tk.call(str(crop), "copy", str(source),
                         "-from", x1, y1, x2, y2, "-to", 0, 0)
            self.keyboard_photo_large = crop.subsample(2, 2)
            self.keyboard_photo_small = crop.subsample(3, 3)
            # 保存源图和裁剪图的引用，避免 Tk 回收图片对象。
            self._keyboard_source = source
            self._keyboard_crop = crop
        except Exception as exc:
            self.keyboard_image_error = f"产品图加载失败：{exc}"

    def start_drag(self, event):
        if event.y <= self.ui.px(68):
            self._drag = (event.x_root - self.root.winfo_x(),
                          event.y_root - self.root.winfo_y())
        else:
            self._drag = None

    def drag(self, event):
        if self._drag:
            self.root.geometry(f"+{event.x_root - self._drag[0]}+{event.y_root - self._drag[1]}")

    def schedule(self):
        if self.after_id:
            self.root.after_cancel(self.after_id)
            self.after_id = None
        if self.auto:
            self.after_id = self.root.after(self.interval * 1000, self.tick)

    def tick(self):
        self.refresh()
        self.schedule()

    def refresh(self):
        if not self.device_entries():
            self.busy = False
            self.refresh_button.set_state("idle")
            return False
        if self.busy:
            return False
        monitor = getattr(self, "beats_monitor", None)
        if monitor is not None:
            monitor.request_refresh()
        if any(is_bluetooth(device) for device in self.saved_devices):
            from bluetooth_devices import invalidate_bluetooth_cache
            invalidate_bluetooth_cache()
        if self.startup_after_id is not None:
            self.root.after_cancel(self.startup_after_id)
            self.startup_after_id = None
        if self.refresh_feedback_after_id is not None:
            self.root.after_cancel(self.refresh_feedback_after_id)
            self.refresh_feedback_after_id = None
        self.busy = True
        self.refresh_started = time.monotonic()
        self.refresh_button.set_state("loading")
        self.refresh_readings()
        self.refresh_generation += 1
        threading.Thread(target=self.read_worker,
                         args=(self.refresh_generation, list(self.saved_devices)), daemon=True).start()
        return True

    def refresh_shortcut(self, _event):
        self.refresh()
        return "break"

    def finish_refresh(self, reading, generation):
        if generation != self.refresh_generation:
            return
        self.refresh_result_after_id = None
        if isinstance(reading, dict):
            from reading_ui import keep_newer
            previous = getattr(self, "readings", {})
            reading = {key: previous[key] if keep_newer(previous.get(key), value) else value
                       for key, value in reading.items()}
            self.readings = reading
            identity_changed = self.sync_reported_models()
            self.sync_beats_updates(redraw=False)
            self.reading = self.readings.get(self.selected_device_id, Reading(KEYBOARD_TITLE, "", error="没有读取结果"))
            keyboard = reading.get(DEFAULT_DEVICE_ID)
            if keyboard is not None:
                self.low_battery_notifier.check(keyboard)
            success = any(r.ok or (r.connected is True and r.status in ("待适配电量", "未上报电量")) for r in self.readings.values())
            if self.view_mode == "single":
                success = self.reading.ok or (self.reading.connected is True and self.reading.status in ("待适配电量", "未上报电量"))
        else:
            self.reading = reading
            self.readings[self.selected_device_id] = reading
            if self.selected_device_id == DEFAULT_DEVICE_ID:
                self.low_battery_notifier.check(reading)
            success = reading.ok or (reading.connected is True and reading.status in ("待适配电量", "未上报电量"))
        self.busy = False
        self.refresh_button.set_state("success" if success else "error")
        if isinstance(reading, dict) and identity_changed:
            self.update_device_choices()
            self.draw()
        self.refresh_readings()
        if success:
            self.refresh_feedback_after_id = self.root.after(1600, self.clear_refresh_feedback)
        if getattr(self, "discovery_refresh_pending", False):
            self.discovery_refresh_pending = False
            self.refresh()

    def clear_refresh_feedback(self):
        self.refresh_feedback_after_id = None
        if not self.busy:
            self.refresh_button.set_state("idle")

    def sync_reported_models(self):
        changed = False
        for device in getattr(self, "saved_devices", []):
            reading = self.readings.get(device.id)
            if reading is None or not reading.reported_model:
                continue
            try:
                changed = self.device_store.confirm_logitech_model(device.id, reading.reported_model) or changed
            except (DeviceStoreError, OSError):
                pass  # A valid battery sample remains usable if metadata cannot be saved.
        if changed:
            self.saved_devices, _selected = self.device_store.load()
        return changed

    def close(self):
        self.root._nexus_closing = True
        navigation = getattr(self, "device_switcher", None)
        if navigation is not None:
            navigation.close()
        discovery = getattr(self, "aula_discovery", None)
        if discovery is not None:
            discovery.close()
        updates = getattr(self, "updates", None)
        if updates is not None:
            updates.close()
        view_switcher = getattr(self, "view_switcher", None)
        if view_switcher is not None:
            view_switcher.close()
        hover = getattr(self, "card_hover", None)
        if hover is not None:
            hover.close()
        transition = getattr(self, "transition", None)
        if transition is not None:
            transition.close()
        beats_monitor = getattr(self, "beats_monitor", None)
        if beats_monitor is not None:
            beats_monitor.close()
        self.mouse_monitor.close()
        if self.receiver_search and not self.receiver_search.closed:
            self.receiver_search.close()
        for timer in (self.after_id, self.refresh_result_after_id,
                      self.refresh_feedback_after_id, self.startup_after_id,
                      self.pump_after_id, self.runtime_after_id):
            if timer is not None:
                self.root.after_cancel(timer)
        self.root.destroy()

    def update_runtime_display(self):
        transition = getattr(self, "transition", None)
        if transition is not None and transition.phase == "out":
            return
        if self.view_mode == "all":
            item = self.overview_runtime_items.get(DEFAULT_DEVICE_ID)
            if item is not None:
                reading = self.readings.get(DEFAULT_DEVICE_ID)
                text = "暂不可用"
                if reading and reading.ok:
                    charging = reading.status in ("charging", "充电中")
                    view = get_runtime_view(reading.level, charging=charging, valid=True)
                    text = "充电中，暂不估算" if charging else format_remaining(view["hours"], live=True)
                if self.overview_canvas.itemcget(item, "text") != text:
                    self.overview_canvas.itemconfigure(item, text=text)
            return
        if self.selected_device_id != DEFAULT_DEVICE_ID:
            return
        charging = self.reading.status in ("charging", "充电中")
        view = get_runtime_view(self.reading.level, charging=charging, valid=self.reading.ok)
        if charging and self.reading.ok:
            text = "充电中，暂不估算"
        elif self.reading.ok and self.reading.level == 0:
            text = "电量即将耗尽"
        elif self.reading.ok:
            text = format_remaining(view["hours"], live=True)
        else:
            text = "暂不可用" if self.reading.error else "正在读取…"
        self.canvas.itemconfigure(self.runtime_text_item, text=text)
        hint = view["hint"]
        active = hint.startswith("使用中")
        prefix, detail = (hint[:3], hint[3:]) if active else (hint, "")
        self.canvas.itemconfigure(self.runtime_hint_item, text=prefix,
                                  fill="#0f8e55" if active else MUTED)
        self.transition.update_style(self.canvas, self.runtime_hint_item,
                                     fill="#0f8e55" if active else MUTED)
        self.canvas.itemconfigure(self.runtime_hint_detail_item, text=detail)
        bounds = self.canvas.bbox(self.runtime_hint_item)
        x, y = self.canvas.coords(self.runtime_hint_item)
        self.canvas.coords(self.runtime_hint_detail_item, bounds[2] - 1 if bounds else x, y)

    def runtime_tick(self):
        self.runtime_after_id = None
        self.sync_aula_discovery()
        self.sync_mouse_updates(redraw=False)
        self.sync_beats_updates(redraw=False)
        self.refresh_readings()
        self.runtime_after_id = self.root.after(1000, self.runtime_tick)

    def sync_aula_discovery(self):
        discovery = getattr(self, "aula_discovery", None)
        if discovery is None:
            return
        changed = False
        for receiver in discovery.take():
            try:
                if self.device_store.can_auto_add(receiver) and self.device_store.id_for(receiver) is None:
                    _key, added = self.device_store.add(receiver, select=False)
                    changed |= added
            except (OSError, ValueError):
                continue
        if changed:
            self.saved_devices, _selected = self.device_store.load()
            for device in self.saved_devices:
                self.readings.setdefault(device.id, Reading(device.label, "正在读取"))
            self.update_device_choices()
            self.draw()
            if self.busy:
                self.discovery_refresh_pending = True
            else:
                self.refresh()

    @staticmethod
    def device_runtime_text(reading):
        if reading.ok and reading.status == "充电中":
            return "充电中"
        return "暂不支持估算"

    def sync_mouse_updates(self, *, redraw=True):
        from reading_ui import keep_newer
        self.mouse_monitor.set_devices(self.saved_devices)
        changed = False
        for device in self.saved_devices:
            latest = self.mouse_monitor.cached_reading(device)
            if latest is not None:
                previous = self.readings.get(device.id)
                if keep_newer(previous, latest):
                    continue
                self.readings[device.id] = latest
                if self.selected_device_id == device.id:
                    self.reading = latest
                changed |= latest != previous or latest.observed_at != getattr(previous, "observed_at", None)
        if changed and redraw:
            self.refresh_readings()

    def sync_beats_updates(self, *, redraw=True):
        from reading_ui import keep_newer
        monitor = getattr(self, "beats_monitor", None)
        if monitor is None:
            return
        monitor.set_devices(self.saved_devices)
        changed = False
        for device in self.saved_devices:
            latest = monitor.cached_reading(device)
            previous = self.readings.get(device.id)
            if latest is not None:
                if keep_newer(previous, latest):
                    continue
                if latest.connected is None and previous is not None:
                    latest.connected = previous.connected
                self.readings[device.id] = latest
                if self.selected_device_id == device.id:
                    self.reading = latest
                changed |= latest != previous or latest.observed_at != getattr(previous, "observed_at", None)
        if changed and redraw:
            self.refresh_readings()

    def read_worker(self, generation, devices):
        self.results.put((generation, read_device_batch(devices, read_all, read_saved_device,
                                                      include_default=getattr(self, "default_visible", True))))

    def pump(self):
        self.pump_after_id = None
        try:
            while True:
                generation, value = self.results.get_nowait()
                if generation == self.refresh_generation:
                    self.finish_refresh(value, generation)
        except queue.Empty:
            pass
        self.pump_after_id = self.root.after(100, self.pump)

    def live_text(self, canvas, group, name, x, y, **options):
        item = canvas.create_text(x, y, **options)
        initial = {key: options[key] for key in ("text", "fill") if key in options}
        self.live.link(group, name, canvas, (item,), **initial)
        return item

    def live_round(self, canvas, group, name, x1, y1, x2, y2, radius, fill):
        tag = f"live_fill_{len(self.live.bindings)}"
        rounded_rect(canvas, x1, y1, x2, y2, radius, fill, tags=tag)
        self.live.link(group, name, canvas, canvas.find_withtag(tag), fill=fill)

    def live_battery(self, canvas, group, name, x, y, level, charging=False):
        item = battery_icon(canvas, x, y, level, charging)
        physical = str(self.ui.image(canvas.itemcget(item, "image")))
        self.live.link(group, name, canvas, (item,), image=physical)

    def live_components(self, canvas, group, x, y, reading, *, size=13):
        styles = self.reading_styles(group, reading)
        for index, (part, label) in enumerate((("left", "左耳"), ("right", "右耳"), ("case", "充电仓"))):
            row_y = y + index * 45
            value = ((reading.components or {}).get(part) or {}) if not reading.error else {}
            canvas.create_text(x, row_y, text=label, anchor="w", fill=MUTED, font=(FONT, 10))
            self.live_battery(canvas, group, part+"_battery", x+46, row_y-7,
                              value.get("level"), value.get("charging") is True)
            self.live_text(canvas, group, part+"_level", x+86, row_y, anchor="w",
                           font=(FONT, size, "bold"), **styles[part+"_level"])
            self.live_text(canvas, group, part+"_time", x, row_y+19, anchor="w",
                           font=(FONT, 10), **styles[part+"_time"])

    def reading_styles(self, key, reading):
        known_parts = not reading.error and any(
            part.get("level") is not None for part in (reading.components or {}).values())
        online = reading.connected is True or reading.ok or known_parts
        if online and reading.status == "待适配电量":
            state, color = "电量待适配", "#997126"
        elif reading.error:
            state, color = ("读取暂不可用", "#aa5d65") if online else ("未连接", MUTED)
        elif reading.ok or known_parts:
            state, color = component_charging_text(reading.components) or reading.status or "设备在线", "#0f8e55"
        elif reading.status in ("待适配电量", "未上报电量"):
            state, color = reading.status, "#997126"
        else:
            state, color = "读取中", MUTED
        charging = reading.status in ("charging", "充电中")
        power = "正在充电" if charging else "已充满" if reading.status in ("full", "已充满") else "电池供电"
        if reading.status == "已接电，未充电":
            power = reading.status
        if reading.components is not None:
            flags = [part.get("charging") for part in reading.components.values()]
            power = component_charging_text(reading.components) or ("部分状态未上报" if None in flags else "未在充电")
        elif reading.source == "Windows 蓝牙":
            power = "未上报"
        elif reading.status == "供电状态未上报":
            power = "未上报"
        if reading.error or not (reading.ok or known_parts):
            power = "未知"
        caption = ""
        if reading.coarse and reading.ok:
            caption = "设备分档上报 · AA 电池"
        elif reading.source in ("HID++ 实时", "HID++ 2.0") and reading.voltage_mv is not None and not reading.error:
            caption = f"电压估算 · {reading.voltage_mv / 1000:.3f} V"
        elif reading.source == "Beats 原生查询":
            caption = "原生查询 · 每 5 秒核对"
        elif reading.source == "Windows 蓝牙" and reading.ok:
            caption = "来自系统蓝牙上报"
        values = {
            "badge_bg": {"fill": GREEN_SOFT if online else "#f0f1f3"},
            "badge": {"text": "已连接" if online else "未连接" if reading.error else "读取中",
                      "fill": "#0f8e55" if online else MUTED},
            "state": {"text": "●  " + state, "fill": color},
            "status": {"text": state, "fill": color},
            "status_dot": {"fill": color},
            "level": {"text": reading.display_level if reading.ok else "--", "fill": TEXT if reading.ok else MUTED},
            "power": {"text": power, "fill": TEXT},
            "caption": {"text": caption, "fill": MUTED},
            "timestamp": {"text": self.reading_times.label(key, reading), "fill": MUTED},
            "runtime": {"fill": "#0f8e55" if reading.ok else MUTED},
            "detail": {"text": reading.error or reading.detail or "等待设备电量回复", "fill": MUTED},
        }
        for part in ("left", "right", "case"):
            value = ((reading.components or {}).get(part) or {}) if not reading.error else {}
            level = value.get("level")
            values[part+"_level"] = {"text": f"{level}%" if level is not None else "--",
                                     "fill": "#0f8e55" if value.get("charging") is True else TEXT if level is not None else MUTED}
            values[part+"_time"] = {"text": self.reading_times.label(key, reading, part), "fill": MUTED}
        return values

    def header_styles(self):
        if not self.device_entries():
            return {"badge_bg": {"fill": "#f0f1f3"}, "dot": {"fill": MUTED},
                    "badge": {"text": "暂无设备", "fill": MUTED}}
        if self.view_mode == "all":
            count = sum(reading.ok or reading.connected is True for reading in self.readings.values())
            online, label = count > 0, f"{count}/{len(self.device_entries())} 在线"
        else:
            online = self.reading.connected if self.reading.connected is not None else self.reading.ok
            label = "已连接" if online else "设备离线"
            if self.busy:
                label = "检查中"
            elif self.current_device() and self.reading.connected is None:
                label = "状态未知"
        return {"badge_bg": {"fill": GREEN_SOFT if online else "#f0f1f3"},
                "dot": {"fill": GREEN if online else MUTED},
                "badge": {"text": label, "fill": "#0f8e55" if online else MUTED}}

    def refresh_readings(self):
        if not getattr(self, "live", None) or not self.live.bindings:
            self.draw()
            return
        for key, reading in self.readings.items():
            self.reading_times.observe(key, reading)
        if self.transition.active:
            return
        self.live.apply("header", self.header_styles())
        for key in self.visible_devices:
            reading = self.readings.get(key)
            if reading is None:
                continue
            values = self.reading_styles(key, reading)
            for part in (None, "left", "right", "case"):
                name = part+"_battery" if part else "battery"
                binding = self.live.bindings.get((key, name))
                if binding is None:
                    continue
                canvas = binding[0]
                if part:
                    value = ((reading.components or {}).get(part) or {}) if not reading.error else {}
                    level, charging = value.get("level"), value.get("charging") is True
                else:
                    level = reading.level if not reading.error else None
                    charging = not reading.error and reading.status in ("charging", "充电中")
                source = battery_photo(canvas, level, charging)
                values[name] = {"image": str(self.ui.image(str(source)))}
            if self.view_mode == "single":
                values["detail"]["text"] = clipped_label(self.canvas, values["detail"]["text"].replace("\n", " "),
                                                         (FONT, 10), self.detail_width)
            self.live.apply(key, values)
        self.update_runtime_display()

    def draw(self):
        if not self.canvas.winfo_exists():
            return
        if self.transition.phase == "out":
            return
        self.transition.begin_layout()
        self.live.bindings.clear()
        self.visible_devices = {}
        self.reading_times.retain(set(self.readings))
        for key, reading in self.readings.items():
            self.reading_times.observe(key, reading)
        w, h = LAYOUT_WIDTH, LAYOUT_HEIGHT
        self.display_photos = []
        self.canvas.delete("all")
        self.canvas.create_rectangle(0, 0, w, h, fill=BG, outline="")

        # header
        self.canvas.create_image(24, 21, image=self.brand_photo, anchor="nw", tags="shape")
        self.canvas.create_text(69, 29, text="Nexus", anchor="w", fill=TEXT,
                               font=(FONT, 15, "bold"), tags="shape")
        self.canvas.create_text(69, 48, text="外设电量中心", anchor="w", fill=MUTED,
                               font=(FONT, 10), tags="shape")

        self.ui.place(self.receiver_button, x=w - 420, y=21, width=118, height=34)
        self.ui.place(self.refresh_button, x=w - 289, y=17, width=104, height=42)
        status_x = w - 179
        device = self.current_device()
        header = self.header_styles()
        self.live_round(self.canvas, "header", "badge_bg", status_x, 21, status_x + 90, 55, 17,
                         header["badge_bg"]["fill"])
        dot = self.canvas.create_oval(status_x + 13, 33, status_x + 19, 39,
                                      outline="", tags="shape", **header["dot"])
        self.live.link("header", "dot", self.canvas, (dot,), **header["dot"])
        self.live_text(self.canvas, "header", "badge", status_x + 28, 38,
                        anchor="w", font=(FONT, 9, "bold"), tags="shape", **header["badge"])

        self.canvas.create_image(24, 75, image=self.toolbar_photo, anchor="nw", tags="shape")
        self.canvas.create_line(272, 86, 272, 108, fill="#e5e9ef", width=1, tags="shape")
        self.ui.place(self.view_switcher, x=36, y=78, width=224, height=38)
        self.view_switcher.set_items((("all", "全部设备", "all"), ("single", "单个设备", "single")), self.view_mode)
        if not self.device_entries():
            self.overview_runtime_items = {}
            self.overview_hits = []
            self.device_switcher.place_forget()
            self.remove_device_button.place_forget()
            self.overview_label.place_forget()
            self.card_hover.suspend()
            self.overview_canvas.place_forget()
            self.overview_scrollbar.place_forget()
            draw_card_surface(self.canvas, 24, 120, w-24, h-52)
            self.canvas.create_text(w/2, 294, text="还没有加入设备", fill=TEXT, font=(FONT, 22, "bold"))
            self.canvas.create_text(w/2, 343, text="点击右上方“搜索设备”，选择需要查看的外设加入。", fill=MUTED, font=(FONT, 12))
            self.draw_footer(w, h)
            self.ui.canvas(self.canvas)
            self.transition.register(self.canvas, ())
            return
        if self.view_mode == "all":
            self.device_switcher.place_forget()
            self.remove_device_button.place_forget()
            self.overview_label.configure(text=f"共 {len(self.device_entries())} 台设备 · 点击卡片查看单个设备")
            self.ui.place(self.overview_label, x=284, y=82, width=410, height=30)
            self.ui.place(self.overview_canvas, x=0, y=120, width=w, height=h - 168)
            self.draw_overview(w, h - 168)
            self.draw_footer(w, h)
            self.ui.canvas(self.canvas)
            return
        self.overview_label.place_forget()
        self.card_hover.suspend()
        self.overview_canvas.place_forget()
        self.overview_scrollbar.place_forget()
        self.ui.place(self.device_switcher, x=284, y=78, width=600, height=38)
        self.ui.place(self.remove_device_button, x=w-124, y=82, width=90, height=30)

        # card and left information panel
        card_x1, card_y1, card_x2, card_y2 = 24, 120, w - 24, h - 52
        draw_card_surface(self.canvas, card_x1, card_y1, card_x2, card_y2)
        panel_x1, panel_y1, panel_x2, panel_y2 = card_x1 + 16, card_y1 + 12, card_x1 + 278, card_y2 - 10
        rounded_rect(self.canvas, panel_x1, panel_y1, panel_x2, panel_y2, 18,
                     "#fbfbfc", tags="shape")
        body_before = set(self.canvas.find_all())
        key = self.selected_device_id
        self.visible_devices[key] = device
        styles = self.reading_styles(key, self.reading)

        maker = device.manufacturer if device else "AULA"
        if is_bluetooth(device) and maker == "未提供":
            maker = "Beats" if product_kind(device) == "beats-studio-buds" else "Bluetooth"
        model = device.model if device else "F87 Pro"
        short_maker = maker[:14] + "…" if len(maker) > 14 else maker
        short_model = model[:18] + "…" if len(model) > 18 else model
        self.canvas.create_text(panel_x1 + 26, panel_y1 + 38, text=short_maker, anchor="w",
                               fill=TEXT, font=("Segoe UI", 22 if device else 31, "bold"), tags="shape")
        self.canvas.create_text(panel_x1 + 26, panel_y1 + 74, text=short_model, anchor="w",
                               fill="#5d6168", font=(FONT, 14 if device else 18), tags="shape")
        self.canvas.create_text(panel_x1 + 26, panel_y1 + 106,
                               text=device.hardware_id if device else "狼蛛无线键盘", anchor="w",
                               fill=MUTED, font=(FONT, 10), tags="shape")

        level = self.reading.level
        charging = self.reading.status in ("charging", "充电中")
        component_layout = product_kind(device) == "beats-studio-buds"
        shown_level = 100 if charging and level is None and self.reading.ok else level
        if component_layout:
            self.live_components(self.canvas, key, panel_x1 + 27, panel_y1 + 137, self.reading, size=12)
        else:
            self.live_battery(self.canvas, key, "battery", panel_x1 + 27, panel_y1 + 128, shown_level, charging)
            self.live_text(self.canvas, key, "level", panel_x1 + 68, panel_y1 + 137,
                             anchor="w", font=(FONT, 10, "bold"), tags="shape", **styles["level"])
        self.runtime_text_item = self.runtime_hint_item = self.runtime_hint_detail_item = None
        if key == DEFAULT_DEVICE_ID:
            self.canvas.create_text(panel_x1 + 27, panel_y1 + 165, text="预计剩余使用时长",
                                     anchor="w", fill=MUTED, font=(FONT, 10), tags="shape")
            self.runtime_text_item = self.live_text(self.canvas, key, "runtime", panel_x1 + 27, panel_y1 + 190,
                                                     anchor="w", font=(FONT, 11, "bold"), tags="shape", **styles["runtime"])
            self.runtime_hint_item = self.canvas.create_text(panel_x2 + 24, panel_y2 - 18,
                                                               anchor="w", fill=MUTED, font=(FONT, 9), tags="shape")
            self.runtime_hint_detail_item = self.canvas.create_text(panel_x2 + 24, panel_y2 - 18,
                                                                      anchor="w", fill=MUTED, font=(FONT, 9), tags="shape")
            self.update_runtime_display()

        # 跟随上方设备信息定位，避免窗口增高时被推到卡片底部。
        details_y = panel_y1 + (281 if component_layout else 230 if key == DEFAULT_DEVICE_ID else 190)
        self.canvas.create_text(panel_x1 + 27, details_y, text="连接方式",
                               anchor="w", fill=MUTED, font=(FONT, 10), tags="shape")
        draw_wireless_icon(self.canvas, panel_x1 + 27, details_y + 18.5)
        self.canvas.create_text(panel_x1 + 54, details_y + 27,
                               text=connection_text(device),
                               anchor="w", fill=TEXT, font=(FONT, 10, "bold"), tags="shape")
        self.canvas.create_text(panel_x1 + 27, details_y + 52, text="供电状态",
                               anchor="w", fill=MUTED, font=(FONT, 10), tags="shape")
        self.live_text(self.canvas, key, "power", panel_x1 + 27, details_y + 72,
                         anchor="w", font=(FONT, 11, "bold"), tags="shape", **styles["power"])
        self.live_text(self.canvas, key, "timestamp", panel_x1 + 27, panel_y2 - 48,
                         anchor="w", font=(FONT, 10), tags="shape", **styles["timestamp"])
        self.live_text(self.canvas, key, "status_dot", panel_x1 + 30, panel_y2 - 18,
                         text="●", font=("Segoe UI", 7), tags="shape", **styles["status_dot"])
        self.live_text(self.canvas, key, "status", panel_x1 + 40, panel_y2 - 18,
                         anchor="w", font=(FONT, 10, "bold"), tags="shape", **styles["status"])

        # 将产品图与说明作为整体，在右侧内容区水平、垂直居中。
        content_left, content_right = panel_x2 + 24, card_x2 - 24
        content_top, content_bottom = card_y1 + 40, card_y2 - 56
        available_width = content_right - content_left
        available_height = content_bottom - content_top
        content_center_x = (content_left + content_right) / 2
        self.detail_width = available_width - 24
        detail = clipped_label(self.canvas, styles["detail"]["text"].replace("\n", " "), (FONT, 10), self.detail_width)
        detail_item = self.live_text(self.canvas, key, "detail",
            content_center_x, 0, text=detail, anchor="n", justify="center",
            width=self.detail_width, fill=MUTED, font=(FONT, 10), tags="shape")
        detail_bounds = self.canvas.bbox(detail_item)
        detail_height = detail_bounds[3] - detail_bounds[1]
        detail_gap = 14
        kind = product_kind(device)
        photo = (self.fitted_product(product_image_kind(device), available_width - 18, available_height - detail_gap - detail_height)
                 if device and kind in ("mouse", "beats-studio-buds") else None)
        if product_image_kind(device) == "keyboard-v2d":
            photo = self.fitted_product("keyboard-v2d", available_width - 18,
                                        available_height - detail_gap - detail_height)
        elif product_image_kind(device) and product_image_kind(device).startswith("aula:"):
            photo = self.fitted_product(product_image_kind(device), available_width - 18,
                                        available_height - detail_gap - detail_height)
        if photo:
            image_y = content_top + max(0, (available_height - photo.height() - detail_gap - detail_height) / 2)
            self.canvas.create_image(content_center_x, image_y, image=photo, anchor="n", tags="shape")
            detail_y = image_y + photo.height() + detail_gap
        elif is_bluetooth(device) and product_kind(device) != "keyboard":
            center_y = (content_top + content_bottom) / 2 - 20
            draw_bluetooth_symbol(self.canvas, content_center_x, center_y - 20)
            self.canvas.create_text(content_center_x, center_y + 95, text=device.model,
                                    width=available_width - 24, font=(FONT, 16, "bold"), fill=TEXT, tags="shape")
            detail_y = center_y + 128
        elif device and product_kind(device) != "keyboard":
            center_y = (content_top + content_bottom) / 2 - 25
            rounded_rect(self.canvas, content_center_x - 38, center_y - 65,
                         content_center_x + 38, center_y + 35, 14, "#e5e8ed", outline="#c5cbd3")
            rounded_rect(self.canvas, content_center_x - 23, center_y - 88,
                         content_center_x + 23, center_y - 55, 4, "#c8ced7")
            for offset in (-10, 10):
                self.canvas.create_rectangle(content_center_x + offset - 3, center_y - 82,
                                             content_center_x + offset + 3, center_y - 68,
                                             fill="#f9fafb", outline="", tags="shape")
            self.canvas.create_text(content_center_x, center_y - 9, text="USB",
                                    font=("Segoe UI", 16, "bold"), fill="#7c8592", tags="shape")
            self.canvas.create_text(content_center_x, center_y + 62, text=device.model,
                                    width=available_width - 24, font=(FONT, 18, "bold"),
                                    fill=TEXT, tags="shape")
            detail_y = center_y + 94
        elif self.keyboard_photo_large:
            photo = (self.keyboard_photo_large
                     if available_width >= self.keyboard_photo_large.width() + 18
                     and available_height >= self.keyboard_photo_large.height() + detail_gap + detail_height
                     else self.keyboard_photo_small)
            image_x = content_center_x - photo.width() / 2
            group_height = photo.height() + detail_gap + detail_height
            image_y = content_top + max(0, (available_height - group_height) / 2)
            self.canvas.create_image(image_x, image_y, image=photo,
                                     anchor="nw", tags="shape")
            detail_y = image_y + photo.height() + detail_gap
        else:
            image_width = min(560, available_width - 18)
            image_height = min(210, available_height - detail_gap - detail_height - 14)
            group_height = image_height + 14 + detail_gap + detail_height
            image_y = content_top + (available_height - group_height) / 2
            draw_keyboard(self.canvas, content_center_x - image_width / 2,
                          image_y, image_width, image_height)
            detail_y = image_y + image_height + 14 + detail_gap
        self.canvas.coords(detail_item, content_center_x, detail_y)
        rounded_rect(self.canvas, card_x2 - 76, card_y2 - 48, card_x2 - 24, card_y2 - 20,
                     14, GREEN_SOFT, tags="shape")
        self.canvas.create_text(card_x2 - 50, card_y2 - 34, text="已加入" if device else "实时",
                               fill=GREEN, font=(FONT, 9, "bold"), tags="shape")

        body_items = set(self.canvas.find_all()) - body_before
        self.draw_footer(w, h)
        self.ui.canvas(self.canvas)
        self.transition.register(self.canvas, body_items)

    def draw_footer(self, w, h):
        footer = self.canvas.create_text(w / 2, h - 20,
                               text="Nexus 外设中心  ·  每台设备独立更新  ·",
                               fill=MUTED, font=(FONT, 10), tags="shape")
        from update_client import current_version
        self.canvas.create_text(self.canvas.bbox(footer)[2] + 8, h - 20, text="build " + current_version(), anchor="w",
                               fill=ORANGE, font=(FONT, 9, "bold"), tags="shape")
        self.canvas.create_text(w - 74, h - 20, text="设置",
                               fill=MUTED, font=(FONT, 10), tags="settings")

    def fitted_product(self, kind, max_width, max_height, clockwise=False):
        import tkinter as tk
        catalog_source = None
        if kind and kind.startswith("aula:"):
            from aula_catalog import image_path, model_by_key
            _prefix, model_key, variant = kind.split(":")
            variant = int(variant)
            if not hasattr(self, "aula_photos"):
                self.aula_photos = {}
            if kind not in self.aula_photos:
                path = image_path(model_key, variant)
                if path is not None:
                    source_photo = tk.PhotoImage(master=self.root, file=str(path))
                    photos = model_by_key(model_key)["photos"]
                    crop = photos[max(0, min(variant, len(photos)-1))].get("display_crop")
                    if crop:
                        cropped = tk.PhotoImage(master=self.root, width=crop[2]-crop[0], height=crop[3]-crop[1])
                        self.root.tk.call(str(cropped), "copy", str(source_photo), "-from", *crop, "-to", 0, 0)
                        source_photo = cropped
                    self.aula_photos[kind] = source_photo
                    while len(self.aula_photos) > 12:
                        self.aula_photos.pop(next(iter(self.aula_photos)))
            catalog_source = self.aula_photos.get(kind)
        source = {"keyboard": getattr(self, "_keyboard_crop", None), "mouse": self.mouse_crop,
                  "mouse-g304": getattr(self, "g304_crop", None),
                  "keyboard-v2d": getattr(self, "keyboard_v2d_crop", None),
                  "beats-studio-buds": self.beats_crop}.get(kind)
        source = catalog_source or source
        if source is None:
            return None
        size = (max(8, int(max_width) // 8 * 8), max(8, int(max_height) // 8 * 8))
        key = (kind, size, clockwise)
        if key not in self.display_image_cache:
            if kind not in self.display_rgb_cache:
                self.display_rgb_cache[kind] = self.product_png_sources.get(kind)
                if self.display_rgb_cache[kind] is None:
                    self.display_rgb_cache[kind] = source.tk.call(str(source), "data", "-format", "png")
            width, height = (source.height(), source.width()) if clockwise else (source.width(), source.height())
            ratio = min(size[0] / width, size[1] / height, 2 if kind == "mouse-g304" else 1)
            data = self.ui.native.resize(self.display_rgb_cache[kind], max(1, round(width * ratio)),
                                         max(1, round(height * ratio)), clockwise=clockwise)
            self.display_image_cache[key] = tk.PhotoImage(master=self.root, data=data, format="PNG")
            photo = self.display_image_cache[key]
            physical = self.ui.native.resize(data, max(1, self.ui.px(photo.width())), max(1, self.ui.px(photo.height())))
            self.ui.images[str(photo)] = tk.PhotoImage(master=self.root, data=physical, format="PNG")
            while len(self.display_image_cache) > 12:
                self.display_image_cache.pop(next(iter(self.display_image_cache)))
        photo = self.display_image_cache[key]
        self.display_photos.append(photo)
        return photo

    def draw_overview(self, width, viewport_height):
        canvas = self.overview_canvas
        canvas.delete("all")
        entries = self.device_entries()
        rectangles, content_height = card_rectangles(width, viewport_height, len(entries))
        canvas.configure(scrollregion=(0, 0, self.ui.px(width), self.ui.px(content_height)))
        self.overview_scrollable = content_height > viewport_height + 1
        if self.overview_scrollable:
            self.ui.place(self.overview_scrollbar, x=width - 14, y=132, width=12, height=viewport_height - 24)
        else:
            self.overview_scrollbar.place_forget()
            canvas.yview_moveto(0)
        self.overview_runtime_items = {}
        self.overview_hits = []
        motion_items = set()
        hover_cards = []
        for (device_id, device), rect in zip(entries, rectangles):
            x1, y1, x2, y2 = rect
            self.visible_devices[device_id] = device
            self.overview_hits.append((rect, device_id))
            reading = self.readings.get(device_id, Reading(device.label if device else KEYBOARD_TITLE, "正在读取…"))
            styles = self.reading_styles(device_id, reading)
            card_before = set(canvas.find_all())
            surface = draw_card_surface(canvas, x1, y1, x2, y2)
            body_before = set(canvas.find_all())
            maker, model = (device.manufacturer, device.model) if device else ("AULA", "F87 Pro")
            if is_bluetooth(device) and maker == "未提供":
                maker = "Beats" if product_kind(device) == "beats-studio-buds" else "Bluetooth"
            card_width, card_height = x2 - x1, y2 - y1
            compact = card_width < 440
            title_font = (FONT, 16 if compact else 18, "bold")
            canvas.create_text(x1 + 24, y1 + 28,
                               text=clipped_label(canvas, maker, (FONT, 10), card_width - 158),
                               anchor="w", fill=MUTED, font=(FONT, 10))
            canvas.create_text(x1 + 24, y1 + 65, text=clipped_label(canvas, model, title_font, card_width - 48),
                               anchor="w", fill=TEXT, font=title_font)
            self.live_round(canvas, device_id, "badge_bg", x2 - 110, y1 + 20, x2 - 24, y1 + 48, 14,
                              styles["badge_bg"]["fill"])
            self.live_text(canvas, device_id, "badge", x2 - 67, y1 + 34,
                             font=(FONT, 9, "bold"), **styles["badge"])
            image_width, image_height = card_width * .40, card_height - 164
            image_center_x, image_center_y = x1 + card_width * .23, y1 + 100 + image_height / 2
            kind = product_kind(device)
            photo = self.fitted_product(product_image_kind(device), image_width, image_height,
                                        clockwise=kind == "keyboard") if kind else None
            if photo:
                canvas.create_image(image_center_x, image_center_y, image=photo)
            elif is_bluetooth(device):
                draw_bluetooth_symbol(canvas, image_center_x, image_center_y)
            else:
                rounded_rect(canvas, image_center_x - 36, image_center_y - 60,
                             image_center_x + 36, image_center_y + 52, 16, "#e5e8ed")
                canvas.create_text(image_center_x, image_center_y, text="USB", fill="#8e97a4", font=(FONT, 15, "bold"))
            mx, my = x1 + card_width * .47, y1 + 118
            canvas.create_text(mx, my, text="设备电量", anchor="w", fill=MUTED, font=(FONT, 10))
            charging = not reading.error and reading.status in ("charging", "充电中")
            if kind == "beats-studio-buds":
                self.live_components(canvas, device_id, mx, my + 28, reading)
                status_offset, connection_y = 166, 204
            else:
                self.live_battery(canvas, device_id, "battery", mx, my + 30,
                                    reading.level if not reading.error else None, charging)
                self.live_text(canvas, device_id, "level", mx + 40, my + 41,
                                 anchor="w", font=(FONT, 20, "bold"), **styles["level"])
                status_offset, connection_y = 80, 133
            self.live_text(canvas, device_id, "state", mx, my + status_offset,
                             anchor="w", font=(FONT, 10), **styles["state"])
            canvas.create_text(mx, my + connection_y, text="连接方式", anchor="w", fill=MUTED, font=(FONT, 10))
            connection = connection_text(device)
            canvas.create_text(mx, my + connection_y + 25, text=connection, anchor="w", fill=TEXT, font=(FONT, 11, "bold"))
            if device_id == DEFAULT_DEVICE_ID:
                canvas.create_text(mx, my + 201, text="预计剩余使用时长", anchor="w", fill=MUTED, font=(FONT, 10))
                self.overview_runtime_items[device_id] = self.live_text(
                    canvas, device_id, "runtime", mx, my + 226, text="暂不可用", anchor="w",
                    font=(FONT, 11, "bold"), **styles["runtime"])
            self.live_text(canvas, device_id, "caption", mx, my + 263,
                             anchor="w", font=(FONT, 10), **styles["caption"])
            canvas.create_line(x1 + 24, y2 - 58, x2 - 24, y2 - 58, fill="#f0f1f4")
            self.live_text(canvas, device_id, "timestamp", x1 + 24, y2 - 31,
                             anchor="w", font=(FONT, 10), **styles["timestamp"])
            canvas.create_text(x2 - 24, y2 - 31, text="查看单个 →", anchor="e", fill="#5d6878", font=(FONT, 9))
            motion_items.update(set(canvas.find_all()) - body_before)
            hover_cards.append((device_id, rect, set(canvas.find_all()) - card_before, surface))
        self.update_runtime_display()
        self.ui.canvas(canvas)
        self.transition.register(canvas, motion_items)
        self.card_hover.register(hover_cards)

    def on_configure(self, event):
        # Moving the native window emits Configure too; its content is unchanged.
        if event.widget is self.root:
            size = (event.width, event.height)
            if size != getattr(self, "_layout_size", None):
                self._layout_size = size
                self.draw()

    def place_initial_window(self):
        """Place once in the primary work area, including native decorations."""
        import ctypes
        from ctypes import wintypes

        self.root.update_idletasks()
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.SystemParametersInfoW.argtypes = [wintypes.UINT, wintypes.UINT, ctypes.c_void_p, wintypes.UINT]
        user32.SystemParametersInfoW.restype = wintypes.BOOL
        user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
        user32.GetAncestor.restype = wintypes.HWND
        user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        user32.GetWindowRect.restype = wintypes.BOOL
        work = wintypes.RECT(0, 0, self.root.winfo_screenwidth(), self.root.winfo_screenheight())
        user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(work), 0)  # SPI_GETWORKAREA
        frame = wintypes.RECT()
        hwnd = user32.GetAncestor(self.root.winfo_id(), 2)  # GA_ROOT: includes title bar/borders
        width, height = WINDOW_WIDTH, WINDOW_HEIGHT
        if hwnd and user32.GetWindowRect(hwnd, ctypes.byref(frame)):
            width = max(width, frame.right - frame.left)
            height = max(height, frame.bottom - frame.top)
        x = max(work.left, work.right - width - 16)
        y = max(work.top, work.bottom - height - 16)
        self.root.geometry(f"{WINDOW_WIDTH}x{WINDOW_HEIGHT}+{x}+{y}")

    def reveal_transition(self):
        # Both views share the fixed minimum size; only their contents animate.
        self.draw()

    def set_view_mode(self, mode):
        if mode == self.view_mode:
            return
        try:
            self.device_store.set_view_mode(mode)
        except (OSError, ValueError) as exc:
            self.show_device_error(str(exc))
            return
        self.view_mode = mode
        view_switcher = getattr(self, "view_switcher", None)
        if view_switcher is not None:
            view_switcher.set_selected(mode)
        hover = getattr(self, "card_hover", None)
        if hover is not None:
            hover.suspend()
        if not self.busy:
            self.refresh_button.set_state("idle")
        self.transition.start()

    def overview_click(self, event):
        x = self.ui.logical(self.overview_canvas.canvasx(event.x))
        y = self.ui.logical(self.overview_canvas.canvasy(event.y))
        hovered = self.card_hover.hit(x, y)
        for (x1, y1, x2, y2), device_id in self.overview_hits:
            if device_id == hovered:
                try:
                    self.device_store.select(device_id)
                except (OSError, ValueError) as exc:
                    self.show_device_error(str(exc))
                    return
                self.selected_device_id = device_id
                self.reading = self.readings[device_id]
                self.update_device_choices()
                self.set_view_mode("single")
                return

    def click(self, event):
        w, h = LAYOUT_WIDTH, LAYOUT_HEIGHT
        x, y = self.ui.logical(event.x), self.ui.logical(event.y)
        if w - 118 <= x <= w - 28 and h - 42 <= y <= h:
            self.open_settings()
        self._drag = None

    def open_receiver_search(self):
        from receiver_search import ReceiverSearch
        if self.receiver_search and not self.receiver_search.closed:
            self.receiver_search.window.lift()
            self.receiver_search.window.focus_set()
            return
        self.receiver_search = ReceiverSearch(self.root, on_add=self.add_receiver,
                                              is_added=lambda receiver: self.device_store.id_for(receiver) is not None)

    def current_device(self):
        return next((d for d in self.saved_devices if d.id == self.selected_device_id), None)

    def device_entries(self):
        return ([(DEFAULT_DEVICE_ID, None)] if getattr(self, "default_visible", True) else []) + [
            (d.id, d) for d in getattr(self, "saved_devices", [])]

    def update_device_choices(self):
        self.device_ids = [key for key, _device in self.device_entries()]
        if self.selected_device_id not in self.device_ids:
            self.selected_device_id = next(iter(self.device_ids), None)
        entries = [(DEFAULT_DEVICE_ID, "F87 Pro", "keyboard")] if getattr(self, "default_visible", True) else []
        for device in self.saved_devices:
            kind = product_kind(device)
            entries.append((device.id, "Studio Buds" if kind == "beats-studio-buds" else device.model, kind))
        self.device_switcher.set_items(entries, self.selected_device_id)
        enabled = self.selected_device_id in self.device_ids
        self.remove_device_button.configure(state="normal" if enabled else "disabled", fg="#a45b65" if enabled else "#a4aab2")

    def show_device_error(self, message):
        from tkinter import messagebox
        messagebox.showerror("我的设备", message, parent=self.root)

    def device_changed(self):
        self.default_visible = self.device_store.default_visible()
        self.refresh_generation += 1  # Discard late reads from the previous device.
        for name in ("refresh_result_after_id", "refresh_feedback_after_id"):
            timer = getattr(self, name)
            if timer is not None:
                self.root.after_cancel(timer)
                setattr(self, name, None)
        self.busy = False
        device = self.current_device()
        valid_ids = {key for key, _device in self.device_entries()}
        self.readings = {key: value for key, value in self.readings.items() if key in valid_ids}
        if self.default_visible:
            self.readings.setdefault(DEFAULT_DEVICE_ID, Reading(KEYBOARD_TITLE, "正在读取…"))
        for saved in self.saved_devices:
            self.readings.setdefault(saved.id, Reading(saved.label, "正在读取…"))
        self.reading = self.readings.get(self.selected_device_id, Reading(device.label if device else KEYBOARD_TITLE, "正在检查…"))
        self.update_device_choices()
        # Device membership/selection changes the layout; normal readings do not.
        self.draw()
        self.refresh()

    def select_device(self, selected):
        if selected not in self.device_ids or selected == self.selected_device_id:
            return
        try:
            self.device_store.select(selected)
            self.saved_devices, self.selected_device_id = self.device_store.load()
        except (OSError, ValueError) as exc:
            self.update_device_choices()
            self.show_device_error(str(exc))
            return
        self.transition.start()
        self.device_changed()

    def add_receiver(self, receiver):
        _, created = self.device_store.add(receiver)
        discovery = getattr(self, "aula_discovery", None)
        if discovery is not None:
            try:
                discovery.set_ignored(receiver.instance_id, False)
            except OSError:
                pass
        self.saved_devices, self.selected_device_id = self.device_store.load()
        self.device_changed()
        self.root.lift()
        return created

    def remove_selected_device(self):
        if self.selected_device_id not in {key for key, _device in self.device_entries()}:
            return
        removed = self.current_device()
        try:
            self.device_store.remove(self.selected_device_id)
            discovery = getattr(self, "aula_discovery", None)
            if discovery is not None and removed is not None:
                try:
                    discovery.set_ignored(removed.instance_id)
                except OSError:
                    pass
            self.saved_devices, self.selected_device_id = self.device_store.load()
        except (OSError, ValueError) as exc:
            self.show_device_error(str(exc))
            return
        self.device_changed()

    def open_settings(self):
        if self.settings_window and self.settings_window.winfo_exists():
            self.settings_window.show()
        else:
            from app_dialogs import SettingsDialog
            self.settings_window = SettingsDialog(self)
        self.updates.open()


    def open_updates(self):
        if not getattr(self, "updates", None):
            from update_ui import UpdateManager
            self.updates = UpdateManager(self.root, on_install=self.close)
        self.updates.open()


def cli_once() -> None:
    for reading in read_all():
        if reading.ok:
            print(f"{reading.title}: {reading.display_level}   {reading.status}   {reading.detail}")
            if reading.extra:
                print(f"    {reading.extra}")
        else:
            print(f"{reading.title}: 读取失败 —— {reading.error}")


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description="狼蛛 F87 Pro 电量面板")
    parser.add_argument("--once", action="store_true", help="读取一次后退出")
    parser.add_argument("--install-links", action="store_true", help="重建桌面入口")
    parser.add_argument("--autostart", choices=["on", "off", "status"],
                        help="开机自启：on / off / status")
    parser.add_argument("--check-updates", action="store_true", help="在主窗口内检查软件更新")
    args = parser.parse_args(argv)

    if args.install_links:
        result = tc.install_desktop_links()
        print("桌面入口已处理")
        print(result)
        return
    if args.autostart:
        if args.autostart == "status":
            print(tc.autostart_status())
        else:
            print("开机自启", "已启用" if tc.set_autostart(args.autostart == "on") else "设置失败")
        return
    if args.once:
        cli_once()
        return

    if args.check_updates:
        from update_ui import request_existing_window
        if request_existing_window():
            return

    import tkinter as tk
    root = tk.Tk()
    root.withdraw()
    panel = NexusPanel(root)
    from update_ui import UpdateManager
    panel.updates = UpdateManager(root, on_install=panel.close)
    panel.updates.start()
    panel.place_initial_window()
    root.deiconify()
    if args.check_updates:
        root.after(50, panel.updates.open)
    root.mainloop()


if __name__ == "__main__":
    main()
