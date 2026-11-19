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

import theme
import tray_common as tc
import win_link
from device_readers import KEYBOARD_TITLE, Reading, read_all
from battery_runtime import format_remaining, get_runtime_view
from low_battery_alert import LowBatteryNotifier
from saved_devices import DeviceStore, DeviceStoreError, DEFAULT_DEVICE_ID, read_saved_device
from device_layout import read_device_batch, card_rectangles

ASSETS = Path(__file__).resolve().parent / "assets"
OFFLINE_BADGE = theme.Color("#f0f1f3", "#292f41")
SECONDARY_TEXT = theme.Color("#848b9d", theme.MUTED.dark)
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
        return theme.MUTED.light
    if level >= 60:
        return theme.ACCENT.light
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
    dark = theme.is_dark(canvas)
    if getattr(canvas, "_card_surface_dark", None) != dark:
        asset = theme.asset(canvas, ASSETS / "card-surface.png", dark=dark)
        canvas._card_surface_source = tk.PhotoImage(master=canvas, file=str(asset))
        canvas._card_surface_dark = dark
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

    root = canvas._root()
    if not hasattr(root, "_battery_icons"):
        root._battery_icons = {}
    dark = theme.is_dark(canvas)
    if dark not in root._battery_icons:
        atlas = ASSETS / "optimized" / "battery-icons.png"
        if not atlas.exists():
            atlas = ASSETS / "battery-icons.png"
        sheet = tk.PhotoImage(master=root, file=str(theme.asset(canvas, atlas, dark=dark)))
        root._battery_icons[dark] = sheet, {}
    sheet, cache = root._battery_icons[dark]
    index = 101 if level is None else max(0, min(100, int(level)))
    if charging:
        # 部分设备充电时只报告状态，不提供百分比。
        index = (100 if level is None else index) + 102
    width, height = 34, 17
    if index not in cache:
        image = tk.PhotoImage(master=canvas, width=width, height=height)
        columns = sheet.width() // width
        sx, sy = index % columns * width, index // columns * height
        image.tk.call(str(image), "copy", str(sheet),
                      "-from", sx, sy, sx + width, sy + height, "-to", 0, 0)
        cache[index] = image
    return cache[index]


def battery_icon(canvas, x, y, level: Optional[int], charging: bool = False):
    return canvas.create_image(x, y + 9, anchor="w",
                               image=battery_photo(canvas, level, charging), tags="shape")


def draw_wireless_icon(canvas, x, y):
    """显示参考图样式的黑色双弧 Wi-Fi 图标，背景透明。"""
    import tkinter as tk

    dark = theme.is_dark(canvas)
    if getattr(canvas, "_wireless_dark", None) != dark:
        asset = theme.asset(canvas, ASSETS / "wireless-icon.png", dark=dark)
        canvas._wireless_icon = tk.PhotoImage(master=canvas, file=str(asset))
        canvas._wireless_dark = dark
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
    canvas.create_oval(x - 78, y - 92, x + 78, y + 92, fill=theme.color(canvas, theme.Color("#edf3ff", "#293653")), outline="", tags="shape")
    for points in ((x, y - 58, x, y + 58),
                   (x, y - 58, x + 34, y - 28, x - 30, y + 29),
                   (x, y + 58, x + 34, y + 28, x - 30, y - 29)):
        canvas.create_line(*points, fill=theme.color(canvas, theme.Color("#7196da", "#9dbbff")), width=7, capstyle="round", joinstyle="round", tags="shape")


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
        canvas.create_text(x, row_y, text=label, anchor="w", fill=theme.color(canvas, theme.MUTED), font=(FONT, 9))
        battery_icon(canvas, x + 46, row_y - 7, level, charging)
        value = f"{level}%" if level is not None else "--"
        font = (FONT, size, "bold")
        canvas.create_text(x + 86, row_y, text=value,
                           anchor="w", fill=theme.color(canvas, theme.ACCENT) if charging else theme.color(canvas, theme.TEXT) if level is not None else theme.color(canvas, theme.MUTED), font=font)


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
        cfg = tc.load_config()
        self.appearance = theme.install(root, cfg.get("theme"))
        theme.style(root, bg=theme.BACKGROUND)
        theme.track_title_bar(root)
        from ui_scale import UIScale
        from staged_canvas import SceneCanvas
        self.ui = UIScale(root, UI_SCALE)
        root._nexus_ui = self.ui
        root._nexus_panel = self
        self.canvas = theme.widget(SceneCanvas, root, bg=theme.BACKGROUND, highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        from ui_motion import DeviceTransition
        self.transition = DeviceTransition(root, self.reveal_transition, scale=UI_SCALE,
                                           finished=self.finish_transition,
                                           gpu_enabled=bool(cfg.get("gpu_animation", True)))
        self.transition.set_background(self.fade_background())
        from reading_ui import LiveItems, ReadingTimes
        self.live = LiveItems(self.transition.update_style)
        self.reading_times = ReadingTimes()
        self.visible_devices = {}
        brand_path = ASSETS / "nexus-brand.png"
        self.toolbar_photos = {}
        self.load_toolbar()
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
        self.interval = int(cfg.get("interval", 60))
        self.after_id = None
        self.settings_window = None
        self.receiver_search = None
        self.receiver_button = theme.widget(tk.Button,
            self.canvas, text="搜索设备", command=self.open_receiver_search,
            font=(FONT, 9, "bold"), bg=theme.SURFACE, fg=theme.TEXT, activebackground=theme.Color("#e9ebee", "#333b51"),
            activeforeground=theme.TEXT, relief="flat", bd=0, cursor="hand2",
            highlightthickness=0, takefocus=True)
        self.device_switcher = DeviceNavigation(self.canvas, ui=self.ui, command=self.select_device, width=600)
        self.remove_device_button = theme.widget(tk.Button,
            self.canvas, text="移除设备", command=self.remove_selected_device,
            font=(FONT, 9), bg=theme.SURFACE, fg=theme.Color("#929ba8", theme.MUTED.dark),
            activebackground=theme.Color("#f5f7fa", theme.SOFT.dark),
            activeforeground=theme.Color("#b45e68", theme.DANGER.dark),
            disabledforeground=theme.Color("SystemDisabledText", theme.DISABLED.dark),
            relief="flat", bd=0, cursor="hand2", highlightthickness=0)
        self.view_switcher = ViewSwitcher(self.canvas, ui=self.ui, command=self.set_view_mode)
        self.overview_label = theme.widget(tk.Label, self.canvas, font=(FONT, 10), bg=theme.SURFACE, fg=theme.MUTED, anchor="w")
        for widget in (self.receiver_button, self.remove_device_button, self.overview_label):
            widget.configure(font=self.ui.font(widget.cget("font")))
        self.overview_canvas = theme.widget(SceneCanvas, self.canvas, bg=theme.BACKGROUND,
                                           highlightthickness=0, yscrollincrement=self.ui.px(24))
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
        self.appearance.on_change = self.apply_theme
        self.draw()
        self.startup_after_id = self.root.after(200, self.refresh)
        self.schedule()
        self.pump_after_id = self.root.after(100, self.pump)
        self.runtime_after_id = self.root.after(1000, self.runtime_tick)

    def color(self, swatch):
        return theme.color(getattr(self, "root", None), swatch)

    def fade_background(self):
        return theme.BACKGROUND.dark if theme.is_dark(self.root) else "white"

    def load_toolbar(self):
        import tkinter as tk

        directory = ASSETS / "navigation"
        dark = theme.is_dark(self.root)
        if dark not in self.toolbar_photos:
            source = tk.PhotoImage(master=self.canvas, file=str(theme.asset(self.root, directory / "toolbar.png")))
            scaled = tk.PhotoImage(master=self.canvas, file=str(theme.asset(self.root, directory / "toolbar-85.png")))
            self.ui.images[str(source)] = scaled
            self.toolbar_photos[dark] = source
        self.toolbar_photo = self.toolbar_photos[dark]

    def apply_theme(self):
        self.transition.set_background(self.fade_background())
        self.card_hover.reset_assets()
        self.load_toolbar()
        self.update_device_choices()
        self.draw()

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
                                  fill=self.color(theme.ACCENT) if active else self.color(theme.MUTED))
        self.transition.update_style(self.canvas, self.runtime_hint_item,
                                     fill=self.color(theme.ACCENT) if active else self.color(theme.MUTED))
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
            canvas.create_text(x, row_y, text=label, anchor="w", fill=self.color(theme.MUTED), font=(FONT, 10))
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
            state, color = "电量待适配", self.color(theme.WARNING)
        elif reading.error:
            state, color = ("读取暂不可用", self.color(theme.DANGER)) if online else ("未连接", self.color(theme.MUTED))
        elif reading.ok or known_parts:
            state, color = component_charging_text(reading.components) or reading.status or "设备在线", self.color(theme.ACCENT)
        elif reading.status in ("待适配电量", "未上报电量"):
            state, color = reading.status, self.color(theme.WARNING)
        else:
            state, color = "读取中", self.color(theme.MUTED)
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
            "badge_bg": {"fill": self.color(theme.SELECTED) if online else self.color(OFFLINE_BADGE)},
            "badge": {"text": "已连接" if online else "未连接" if reading.error else "读取中",
                      "fill": self.color(theme.ACCENT) if online else self.color(theme.MUTED)},
            "state": {"text": "●  " + state, "fill": color},
            "status": {"text": state, "fill": color},
            "status_dot": {"fill": color},
            "level": {"text": reading.display_level if reading.ok else "--", "fill": self.color(theme.TEXT) if reading.ok else self.color(theme.MUTED)},
            "power": {"text": power, "fill": self.color(theme.TEXT)},
            "caption": {"text": caption, "fill": self.color(theme.MUTED)},
            "timestamp": {"text": self.reading_times.label(key, reading), "fill": self.color(theme.MUTED)},
            "runtime": {"fill": self.color(theme.ACCENT) if reading.ok else self.color(theme.MUTED)},
            "detail": {"text": reading.error or reading.detail or "等待设备电量回复", "fill": self.color(theme.MUTED)},
        }
        for part in ("left", "right", "case"):
            value = ((reading.components or {}).get(part) or {}) if not reading.error else {}
            level = value.get("level")
            values[part+"_level"] = {"text": f"{level}%" if level is not None else "--",
                                     "fill": self.color(theme.ACCENT) if value.get("charging") is True else self.color(theme.TEXT) if level is not None else self.color(theme.MUTED)}
            values[part+"_time"] = {"text": self.reading_times.label(key, reading, part), "fill": self.color(theme.MUTED)}
        return values

    def header_styles(self):
        if not self.device_entries():
            return {"badge_bg": {"fill": self.color(OFFLINE_BADGE)}, "dot": {"fill": self.color(theme.MUTED)},
                    "badge": {"text": "暂无设备", "fill": self.color(theme.MUTED)}}
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
        return {"badge_bg": {"fill": self.color(theme.SELECTED) if online else self.color(OFFLINE_BADGE)},
                "dot": {"fill": self.color(theme.ACCENT) if online else self.color(theme.MUTED)},
                "badge": {"text": label, "fill": self.color(theme.ACCENT) if online else self.color(theme.MUTED)}}

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
        if self.transition.phase == "prepare":
            self.transition.start()
            return
        for _ in self.draw_steps():
            pass

    def draw_steps(self):
        if not self.canvas.winfo_exists():
            return
        if self.transition.phase == "out":
            return
        self.transition.begin_layout()
        yield
        self.live.bindings.clear()
        yield
        self.visible_devices = {}
        self.reading_times.retain(set(self.readings))
        yield
        for key, reading in self.readings.items():
            self.reading_times.observe(key, reading)
            yield
        w, h = LAYOUT_WIDTH, LAYOUT_HEIGHT
        self.display_photos = []
        self.canvas.delete("all")
        yield
        self.canvas.create_rectangle(0, 0, w, h, fill=self.color(theme.BACKGROUND), outline="")
        yield

        # header
        self.canvas.create_image(24, 21, image=self.brand_photo, anchor="nw", tags="shape")
        yield
        self.canvas.create_text(69, 29, text="Nexus", anchor="w", fill=self.color(theme.TEXT),
                               font=(FONT, 15, "bold"), tags="shape")
        yield
        self.canvas.create_text(69, 48, text="外设电量中心", anchor="w", fill=self.color(theme.MUTED),
                               font=(FONT, 10), tags="shape")
        yield

        self.ui.place(self.receiver_button, x=w - 420, y=21, width=118, height=34)
        yield
        self.ui.place(self.refresh_button, x=w - 289, y=17, width=104, height=42)
        yield
        status_x = w - 179
        device = self.current_device()
        yield
        header = self.header_styles()
        yield
        self.live_round(self.canvas, "header", "badge_bg", status_x, 21, status_x + 90, 55, 17,
                         header["badge_bg"]["fill"])
        yield
        dot = self.canvas.create_oval(status_x + 13, 33, status_x + 19, 39,
                                      outline="", tags="shape", **header["dot"])
        yield
        self.live.link("header", "dot", self.canvas, (dot,), **header["dot"])
        yield
        self.live_text(self.canvas, "header", "badge", status_x + 28, 38,
                        anchor="w", font=(FONT, 9, "bold"), tags="shape", **header["badge"])
        yield

        self.canvas.create_image(24, 75, image=self.toolbar_photo, anchor="nw", tags="shape")
        yield
        self.canvas.create_line(272, 86, 272, 108, fill=self.color(theme.Color("#e5e9ef", theme.LINE.dark)), width=1, tags="shape")
        yield
        self.ui.place(self.view_switcher, x=36, y=78, width=224, height=38)
        yield
        self.view_switcher.set_items((("all", "全部设备", "all"), ("single", "单个设备", "single")), self.view_mode)
        yield
        if not self.device_entries():
            self.overview_runtime_items = {}
            self.overview_hits = []
            self.device_switcher.place_forget()
            yield
            self.remove_device_button.place_forget()
            yield
            self.overview_label.place_forget()
            yield
            self.card_hover.suspend()
            yield
            self.overview_canvas.place_forget()
            yield
            self.overview_scrollbar.place_forget()
            yield
            draw_card_surface(self.canvas, 24, 120, w-24, h-52)
            yield
            self.canvas.create_text(w/2, 294, text="还没有加入设备", fill=self.color(theme.TEXT), font=(FONT, 22, "bold"))
            yield
            self.canvas.create_text(w/2, 343, text="点击右上方“搜索设备”，选择需要查看的外设加入。", fill=self.color(theme.MUTED), font=(FONT, 12))
            yield
            self.draw_footer(w, h)
            yield
            yield from self.ui.canvas_steps(self.canvas)
            yield
            self.transition.register(self.canvas, ())
            yield
            return
        if self.view_mode == "all":
            self.device_switcher.place_forget()
            yield
            self.remove_device_button.place_forget()
            yield
            self.overview_label.configure(text=f"共 {len(self.device_entries())} 台设备 · 点击卡片查看单个设备")
            yield
            self.ui.place(self.overview_label, x=284, y=82, width=410, height=30)
            yield
            self.ui.place(self.overview_canvas, x=0, y=120, width=w, height=h - 168)
            yield
            yield from self.draw_overview_steps(w, h - 168)
            yield
            self.draw_footer(w, h)
            yield
            yield from self.ui.canvas_steps(self.canvas)
            yield
            return
        self.overview_label.place_forget()
        yield
        self.card_hover.suspend()
        yield
        self.overview_canvas.place_forget()
        yield
        self.overview_scrollbar.place_forget()
        yield
        self.ui.place(self.device_switcher, x=284, y=78, width=600, height=38)
        yield
        self.ui.place(self.remove_device_button, x=w-124, y=82, width=90, height=30)
        yield

        # card and left information panel
        card_x1, card_y1, card_x2, card_y2 = 24, 120, w - 24, h - 52
        draw_card_surface(self.canvas, card_x1, card_y1, card_x2, card_y2)
        yield
        panel_x1, panel_y1, panel_x2, panel_y2 = card_x1 + 16, card_y1 + 12, card_x1 + 278, card_y2 - 10
        rounded_rect(self.canvas, panel_x1, panel_y1, panel_x2, panel_y2, 18,
                     self.color(theme.Color("#f7f8fc", "#252b3e")), tags="shape")
        rounded_rect(self.canvas, panel_x2 + 20, card_y1 + 24, card_x2 - 20, card_y2 - 66,
                     22, self.color(theme.Color("#fafafe", "#24293a")), tags="shape")
        yield
        body_before = set(self.canvas.find_all())
        yield
        key = self.selected_device_id
        self.visible_devices[key] = device
        styles = self.reading_styles(key, self.reading)
        yield

        maker = device.manufacturer if device else "AULA"
        if is_bluetooth(device) and maker == "未提供":
            maker = "Beats" if product_kind(device) == "beats-studio-buds" else "Bluetooth"
        model = device.model if device else "F87 Pro"
        short_maker = maker[:14] + "…" if len(maker) > 14 else maker
        short_model = model[:18] + "…" if len(model) > 18 else model
        self.canvas.create_text(panel_x1 + 26, panel_y1 + 38, text=short_maker, anchor="w",
                               fill=self.color(theme.TEXT), font=("Segoe UI", 22 if device else 31, "bold"), tags="shape")
        yield
        self.canvas.create_text(panel_x1 + 26, panel_y1 + 74, text=short_model, anchor="w",
                               fill=self.color(theme.Color("#5d6168", theme.MUTED.dark)), font=(FONT, 14 if device else 18), tags="shape")
        yield
        self.canvas.create_text(panel_x1 + 26, panel_y1 + 106,
                               text=device.hardware_id if device else "狼蛛无线键盘", anchor="w",
                               fill=self.color(theme.MUTED), font=(FONT, 10), tags="shape")
        yield

        level = self.reading.level
        charging = self.reading.status in ("charging", "充电中")
        component_layout = product_kind(device) == "beats-studio-buds"
        shown_level = 100 if charging and level is None and self.reading.ok else level
        if component_layout:
            self.live_components(self.canvas, key, panel_x1 + 27, panel_y1 + 137, self.reading, size=12)
            yield
        else:
            self.live_battery(self.canvas, key, "battery", panel_x1 + 24, panel_y1 + 147, shown_level, charging)
            yield
            self.live_text(self.canvas, key, "level", panel_x1 + 72, panel_y1 + 155,
                             anchor="w", font=("Segoe UI", 26, "bold"), tags="shape", **styles["level"])
            yield
        self.runtime_text_item = self.runtime_hint_item = self.runtime_hint_detail_item = None
        if key == DEFAULT_DEVICE_ID:
            self.canvas.create_text(panel_x1 + 27, panel_y1 + 202, text="预计剩余使用时长",
                                     anchor="w", fill=self.color(theme.MUTED), font=(FONT, 10), tags="shape")
            yield
            self.runtime_text_item = self.live_text(self.canvas, key, "runtime", panel_x1 + 27, panel_y1 + 225,
                                                     anchor="w", font=(FONT, 11, "bold"), tags="shape", **styles["runtime"])
            yield
            self.runtime_hint_item = self.canvas.create_text(panel_x2 + 24, panel_y2 - 18,
                                                               anchor="w", fill=self.color(theme.MUTED), font=(FONT, 9), tags="shape")
            yield
            self.runtime_hint_detail_item = self.canvas.create_text(panel_x2 + 24, panel_y2 - 18,
                                                                      anchor="w", fill=self.color(theme.MUTED), font=(FONT, 9), tags="shape")
            yield
            self.update_runtime_display()
            yield

        # 跟随上方设备信息定位，避免窗口增高时被推到卡片底部。
        details_y = panel_y1 + (281 if component_layout else 270 if key == DEFAULT_DEVICE_ID else 220)
        self.canvas.create_text(panel_x1 + 27, details_y, text="连接方式",
                               anchor="w", fill=self.color(theme.MUTED), font=(FONT, 10), tags="shape")
        yield
        draw_wireless_icon(self.canvas, panel_x1 + 27, details_y + 18.5)
        yield
        self.canvas.create_text(panel_x1 + 54, details_y + 27,
                               text=connection_text(device),
                               anchor="w", fill=self.color(theme.TEXT), font=(FONT, 10, "bold"), tags="shape")
        yield
        self.canvas.create_text(panel_x1 + 27, details_y + 52, text="供电状态",
                               anchor="w", fill=self.color(theme.MUTED), font=(FONT, 10), tags="shape")
        yield
        self.live_text(self.canvas, key, "power", panel_x1 + 27, details_y + 72,
                         anchor="w", font=(FONT, 11, "bold"), tags="shape", **styles["power"])
        yield
        self.live_text(self.canvas, key, "timestamp", panel_x1 + 27, panel_y2 - 48,
                         anchor="w", font=(FONT, 10), tags="shape", **styles["timestamp"])
        yield
        self.live_text(self.canvas, key, "status_dot", panel_x1 + 30, panel_y2 - 18,
                         text="●", font=("Segoe UI", 7), tags="shape", **styles["status_dot"])
        yield
        self.live_text(self.canvas, key, "status", panel_x1 + 40, panel_y2 - 18,
                         anchor="w", font=(FONT, 10, "bold"), tags="shape", **styles["status"])
        yield

        # 将产品图与说明作为整体，在右侧内容区水平、垂直居中。
        content_left, content_right = panel_x2 + 24, card_x2 - 24
        content_top, content_bottom = card_y1 + 40, card_y2 - 56
        available_width = content_right - content_left
        available_height = content_bottom - content_top
        content_center_x = (content_left + content_right) / 2
        self.detail_width = available_width - 24
        detail = clipped_label(self.canvas, styles["detail"]["text"].replace("\n", " "), (FONT, 10), self.detail_width)
        yield
        detail_item = self.live_text(self.canvas, key, "detail",
            content_center_x, 0, text=detail, anchor="n", justify="center",
            width=self.detail_width, fill=self.color(theme.MUTED), font=(FONT, 10), tags="shape")
        yield
        detail_bounds = self.canvas.bbox(detail_item)
        yield
        detail_height = detail_bounds[3] - detail_bounds[1]
        detail_gap = 14
        kind = product_kind(device)
        yield
        photo = (self.fitted_product(product_image_kind(device), available_width - 18, available_height - detail_gap - detail_height)
                 if device and kind in ("mouse", "beats-studio-buds") else None)
        if product_image_kind(device) == "keyboard-v2d":
            photo = self.fitted_product("keyboard-v2d", available_width - 18,
                                        available_height - detail_gap - detail_height)
            yield
        elif product_image_kind(device) and product_image_kind(device).startswith("aula:"):
            photo = self.fitted_product(product_image_kind(device), available_width - 18,
                                        available_height - detail_gap - detail_height)
            yield
        if photo:
            image_y = content_top + max(0, (available_height - photo.height() - detail_gap - detail_height) / 2)
            self.canvas.create_image(content_center_x, image_y, image=photo, anchor="n", tags="shape")
            yield
            detail_y = image_y + photo.height() + detail_gap
        elif is_bluetooth(device) and product_kind(device) != "keyboard":
            center_y = (content_top + content_bottom) / 2 - 20
            draw_bluetooth_symbol(self.canvas, content_center_x, center_y - 20)
            yield
            self.canvas.create_text(content_center_x, center_y + 95, text=device.model,
                                    width=available_width - 24, font=(FONT, 16, "bold"), fill=self.color(theme.TEXT), tags="shape")
            yield
            detail_y = center_y + 128
        elif device and product_kind(device) != "keyboard":
            center_y = (content_top + content_bottom) / 2 - 25
            rounded_rect(self.canvas, content_center_x - 38, center_y - 65,
                         content_center_x + 38, center_y + 35, 14, self.color(theme.Color("#e5e8ed", "#424d67")), outline=self.color(theme.Color("#c5cbd3", "#687594")))
            yield
            rounded_rect(self.canvas, content_center_x - 23, center_y - 88,
                         content_center_x + 23, center_y - 55, 4, self.color(theme.Color("#c8ced7", "#5a6785")))
            yield
            for offset in (-10, 10):
                self.canvas.create_rectangle(content_center_x + offset - 3, center_y - 82,
                                             content_center_x + offset + 3, center_y - 68,
                                             fill=self.color(theme.Color("#f9fafb", "#465271")), outline="", tags="shape")
                yield
            self.canvas.create_text(content_center_x, center_y - 9, text="USB",
                                    font=("Segoe UI", 16, "bold"), fill=self.color(theme.Color("#7c8592", theme.MUTED.dark)), tags="shape")
            yield
            self.canvas.create_text(content_center_x, center_y + 62, text=device.model,
                                    width=available_width - 24, font=(FONT, 18, "bold"),
                                    fill=self.color(theme.TEXT), tags="shape")
            yield
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
            yield
            detail_y = image_y + photo.height() + detail_gap
        else:
            image_width = min(560, available_width - 18)
            yield
            image_height = min(210, available_height - detail_gap - detail_height - 14)
            yield
            group_height = image_height + 14 + detail_gap + detail_height
            image_y = content_top + (available_height - group_height) / 2
            draw_keyboard(self.canvas, content_center_x - image_width / 2,
                          image_y, image_width, image_height)
            yield
            detail_y = image_y + image_height + 14 + detail_gap
        self.canvas.coords(detail_item, content_center_x, detail_y)
        yield
        rounded_rect(self.canvas, card_x2 - 76, card_y2 - 48, card_x2 - 24, card_y2 - 20,
                     14, self.color(theme.SELECTED), tags="shape")
        yield
        self.canvas.create_text(card_x2 - 50, card_y2 - 34, text="已加入" if device else "实时",
                               fill=self.color(theme.ACCENT), font=(FONT, 9, "bold"), tags="shape")
        yield

        body_items = set(self.canvas.find_all()) - body_before
        self.draw_footer(w, h)
        yield
        yield from self.ui.canvas_steps(self.canvas)
        yield
        self.transition.register(self.canvas, body_items,
                                 region=(0, self.ui.px(120), self.ui.px(w), self.ui.px(h-172)))
        yield

    def draw_footer(self, w, h):
        footer = self.canvas.create_text(w / 2, h - 20,
                               text="Nexus 外设中心  ·  每台设备独立更新  ·",
                               fill=self.color(theme.MUTED), font=(FONT, 10), tags="shape")
        from update_client import current_version
        self.canvas.create_text(self.canvas.bbox(footer)[2] + 8, h - 20, text="build " + current_version(), anchor="w",
                               fill=self.color(SECONDARY_TEXT), font=(FONT, 9, "bold"), tags="shape")
        self.canvas.create_text(w - 74, h - 20, text="设置",
                               fill=self.color(theme.MUTED), font=(FONT, 10), tags="settings")

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
        for _ in self.draw_overview_steps(width, viewport_height):
            pass

    def draw_overview_steps(self, width, viewport_height):
        canvas = self.overview_canvas
        canvas.delete("all")
        yield
        entries = self.device_entries()
        yield
        rectangles, content_height = card_rectangles(width, viewport_height, len(entries))
        yield
        canvas.configure(scrollregion=(0, 0, self.ui.px(width), self.ui.px(content_height)))
        yield
        self.overview_scrollable = content_height > viewport_height + 1
        if self.overview_scrollable:
            self.ui.place(self.overview_scrollbar, x=width - 14, y=132, width=12, height=viewport_height - 24)
            yield
        else:
            self.overview_scrollbar.place_forget()
            yield
            canvas.yview_moveto(0)
            yield
        self.overview_runtime_items = {}
        self.overview_hits = []
        motion_items = set()
        yield
        hover_cards = []
        for (device_id, device), rect in zip(entries, rectangles):
            x1, y1, x2, y2 = rect
            self.visible_devices[device_id] = device
            self.overview_hits.append((rect, device_id))
            yield
            reading = self.readings.get(device_id, Reading(device.label if device else KEYBOARD_TITLE, "正在读取…"))
            yield
            styles = self.reading_styles(device_id, reading)
            yield
            card_before = set(canvas.find_all())
            yield
            surface = draw_card_surface(canvas, x1, y1, x2, y2)
            yield
            body_before = set(canvas.find_all())
            yield
            maker, model = (device.manufacturer, device.model) if device else ("AULA", "F87 Pro")
            if is_bluetooth(device) and maker == "未提供":
                maker = "Beats" if product_kind(device) == "beats-studio-buds" else "Bluetooth"
            card_width, card_height = x2 - x1, y2 - y1
            compact = card_width < 440
            title_font = (FONT, 16 if compact else 18, "bold")
            canvas.create_text(x1 + 24, y1 + 28,
                               text=clipped_label(canvas, maker, (FONT, 10), card_width - 158),
                               anchor="w", fill=self.color(theme.MUTED), font=(FONT, 10))
            yield
            canvas.create_text(x1 + 24, y1 + 65, text=clipped_label(canvas, model, title_font, card_width - 48),
                               anchor="w", fill=self.color(theme.TEXT), font=title_font)
            yield
            self.live_round(canvas, device_id, "badge_bg", x2 - 110, y1 + 20, x2 - 24, y1 + 48, 14,
                              styles["badge_bg"]["fill"])
            yield
            self.live_text(canvas, device_id, "badge", x2 - 67, y1 + 34,
                             font=(FONT, 9, "bold"), **styles["badge"])
            yield
            # 横向产品舞台与底部数据区各自占用固定区域。
            image_width, image_height = card_width - 70, 112
            image_center_x, image_center_y = (x1 + x2) / 2, y1 + 160
            kind = product_kind(device)
            if not compact and kind == "keyboard":
                image_width, image_height = min(card_width - 360, 620), 260
                image_center_x, image_center_y = x1 + card_width * .58, y1 + 205
            yield
            photo = self.fitted_product(product_image_kind(device), image_width, image_height,
                                        clockwise=False) if kind else None
            if photo:
                canvas.create_image(image_center_x, image_center_y, image=photo)
                yield
            elif is_bluetooth(device):
                draw_bluetooth_symbol(canvas, image_center_x, image_center_y)
                yield
            else:
                rounded_rect(canvas, image_center_x - 36, image_center_y - 60,
                             image_center_x + 36, image_center_y + 52, 16, self.color(theme.Color("#e5e8ed", "#424d67")))
                yield
                canvas.create_text(image_center_x, image_center_y, text="USB", fill=self.color(theme.Color("#8e97a4", theme.TEXT.dark)), font=(FONT, 15, "bold"))
                yield
            mx, my = x1 + 24, y1 + 252
            charging = not reading.error and reading.status in ("charging", "充电中")
            if kind == "beats-studio-buds":
                column_width = (card_width - 48) / 3
                for index, (part, label) in enumerate((("left", "左耳"), ("right", "右耳"), ("case", "充电仓"))):
                    px = mx + index * column_width
                    value = ((reading.components or {}).get(part) or {}) if not reading.error else {}
                    canvas.create_text(px, my, text=label, anchor="w", fill=self.color(theme.MUTED), font=(FONT, 9))
                    self.live_text(canvas, device_id, part + "_level", px + 38, my + 34,
                                   anchor="w", font=("Segoe UI", 16, "bold"), **styles[part + "_level"])
                    self.live_battery(canvas, device_id, part + "_battery", px, my + 25,
                                      value.get("level"), value.get("charging") is True)
                    self.live_text(canvas, device_id, part + "_time", px, my + 82,
                                   anchor="w", font=(FONT, 8), **styles[part + "_time"])
                    yield
            else:
                canvas.create_text(mx, my, text="剩余电量", anchor="w", fill=self.color(theme.MUTED), font=(FONT, 10))
                self.live_text(canvas, device_id, "level", mx + 48, my + 39,
                               anchor="w", font=("Segoe UI", 26, "bold"), **styles["level"])
                self.live_battery(canvas, device_id, "battery", mx, my + 31,
                                  reading.level if not reading.error else None, charging)
                self.live_text(canvas, device_id, "state", mx, my + 82,
                               anchor="w", font=(FONT, 9), **styles["state"])
                yield
            info_y = y2 - 100
            canvas.create_text(mx, info_y, text=connection_text(device), anchor="w",
                               fill=self.color(theme.TEXT), font=(FONT, 10, "bold"))
            yield
            if device_id == DEFAULT_DEVICE_ID:
                self.overview_runtime_items[device_id] = self.live_text(
                    canvas, device_id, "runtime", x2 - 24, info_y, text="暂不可用", anchor="e",
                    font=(FONT, 10), **styles["runtime"])
                yield
            else:
                self.live_text(canvas, device_id, "power", x2 - 24, info_y,
                               anchor="e", font=(FONT, 9), **styles["power"])
                yield
            self.live_text(canvas, device_id, "caption", mx, y2 - 77,
                             anchor="w", font=(FONT, 9), **styles["caption"])
            yield
            canvas.create_line(x1 + 24, y2 - 58, x2 - 24, y2 - 58, fill=self.color(theme.Color("#f0f1f4", theme.LINE.dark)))
            yield
            self.live_text(canvas, device_id, "timestamp", x1 + 24, y2 - 31,
                             anchor="w", font=(FONT, 10), **styles["timestamp"])
            yield
            canvas.create_text(x2 - 24, y2 - 31, text="查看单个 →", anchor="e", fill=self.color(theme.Color("#5d6878", theme.ACCENT.dark)), font=(FONT, 9))
            yield
            motion_items.update(set(canvas.find_all()) - body_before)
            yield
            hover_cards.append((device_id, rect, set(canvas.find_all()) - card_before, surface))
            yield
        self.update_runtime_display()
        yield
        yield from self.ui.canvas_steps(canvas)
        yield
        self.transition.register(canvas, motion_items,
                                 region=(0, 0, self.ui.px(width), self.ui.px(viewport_height)))
        yield
        self.card_hover.register(hover_cards)
        yield

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
        canvases = self.canvas, self.overview_canvas
        for canvas in canvases:
            canvas.begin_scene()
        try:
            yield from self.draw_steps()
            for canvas in canvases:
                canvas.commit_scene()
        finally:
            for canvas in canvases:
                canvas.abort_scene()

    def finish_transition(self):
        self.refresh_readings()
        self.card_hover.refresh_pointer()

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
        self.remove_device_button.configure(state="normal" if enabled else "disabled", fg=self.color(theme.Color("#a45b65", theme.DANGER.dark)) if enabled else self.color(theme.Color("#a4aab2", theme.DISABLED.dark)))

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
