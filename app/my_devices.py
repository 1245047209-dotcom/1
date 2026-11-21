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
from device_layout import (
    CARD_MARGIN, CARD_PADDING, FOOTER_HEIGHT, OVERVIEW_TOP,
    card_rectangles, content_height, read_device_batch,
)
from search_window import window_work_area

ASSETS = Path(__file__).resolve().parent / "assets"
OFFLINE_BADGE = theme.Color("#eeebf2", "#302e36")
FONT = "Microsoft YaHei UI"
UI_SCALE = .85
MIN_WIDTH, DEFAULT_WIDTH, MAX_WIDTH = 540, 640, 880
WINDOW_MARGIN = 16
BADGE_WIDTH = 86
HEADER_BADGE_HEIGHT = 28
DEVICE_BADGE_HEIGHT = 26


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


def draw_card_surface(canvas, x1, y1, x2, y2, *, product=False):
    """Stretch the card artwork while preserving its rim, corners and shadow."""
    import tkinter as tk

    name, padding = ("product-surface.png", 12) if product else ("card-surface.png", 32)
    width, height = round(x2 - x1) + 2*padding, round(y2 - y1) + 2*padding
    asset = theme.asset(canvas, ASSETS / name)
    if not hasattr(canvas, "_card_surfaces"):
        canvas._card_surfaces = {}
    cached_path, source, size, surface = canvas._card_surfaces.get(name, (None, None, None, None))
    if cached_path != asset:
        source = tk.PhotoImage(master=canvas, file=str(asset))
        size = None
    if size != (width, height):
        surface = tk.PhotoImage(master=canvas, width=width, height=height)
        cx, cy = source.width() // 2, source.height() // 2
        columns = ((0, cx, 0, cx), (cx, 1, cx, width-2*cx), (cx+1, cx, width-cx, cx))
        rows = ((0, cy, 0, cy), (cy, 1, cy, height-2*cy), (cy+1, cy, height-cy, cy))
        for sx, sw, dx, dw in columns:
            for sy, sh, dy, dh in rows:
                tile = tk.PhotoImage(master=canvas, width=sw, height=sh)
                tile.tk.call(str(tile), "copy", str(source), "-from", sx, sy, sx+sw, sy+sh, "-to", 0, 0)
                if sw == 1 or sh == 1:
                    tile = tile.zoom(dw if sw == 1 else 1, dh if sh == 1 else 1)
                surface.tk.call(str(surface), "copy", str(tile), "-to", dx, dy)
        canvas._card_surfaces[name] = asset, source, (width, height), surface
    tags = ("shape", "product_surface") if product else "shape"
    return canvas.create_image(x1-padding, y1-padding, image=surface, anchor="nw", tags=tags)


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


def draw_bluetooth_symbol(canvas, x, y, scale=1):
    canvas.create_oval(x - 78*scale, y - 92*scale, x + 78*scale, y + 92*scale,
                       fill=theme.color(canvas, theme.Color("#edf3ff", "#293653")), outline="", tags="shape")
    for line in ((0, -58, 0, 58), (0, -58, 34, -28, -30, 29), (0, 58, 34, 28, -30, -29)):
        points = [(x if index % 2 == 0 else y) + value*scale for index, value in enumerate(line)]
        canvas.create_line(*points, fill=theme.color(canvas, theme.Color("#7196da", "#9dbbff")),
                           width=max(2, 7*scale), capstyle="round", joinstyle="round", tags="shape")


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
        from app_dialogs import Button
        from refresh_button import RefreshButton
        from interval_picker import IntervalPicker

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
        self.chrome_images = {}
        self.badge_images = {}
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
        self.default_visible = self.device_store.default_visible()
        self.readings = {DEFAULT_DEVICE_ID: Reading(KEYBOARD_TITLE, "正在读取")} if self.default_visible else {}
        self.readings.update({d.id: Reading(d.label, "正在读取") for d in self.saved_devices})
        self.reading = self.readings.get(self.selected_device_id, Reading("暂无设备", ""))
        from aula_discovery import AulaDiscovery
        self.aula_discovery = AulaDiscovery()
        self.discovery_refresh_pending = False
        self.overview_runtime_items = {}
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
        theme.style(self.refresh_button, bg=theme.SURFACE)
        self.auto = True
        self.interval = int(cfg.get("interval", 60))
        self.after_id = None
        self.settings_window = None
        self.receiver_search = None
        self.theme_choice = tk.StringVar(root, value=theme.MODE_LABELS[self.appearance.mode])
        self.theme_picker = IntervalPicker(
            self.canvas, root, self.theme_choice, theme.MODE_LABELS.values(),
            scale=self.ui.factor, show_clock=False, command=self.select_theme,
        )
        self.receiver_button = Button(self.canvas, "搜索设备", self.open_receiver_search,
                                      scale=self.ui.factor, width=101, height=36, background=theme.SURFACE)
        self.settings_button = Button(self.canvas, "设置", self.open_settings,
                                      scale=self.ui.factor, width=68, height=28)
        self.overview_canvas = theme.widget(SceneCanvas, self.canvas, bg=theme.BACKGROUND,
                                           highlightthickness=0, yscrollincrement=self.ui.px(24))
        self.overview_scrollbar = ttk.Scrollbar(self.canvas, orient="vertical", command=self.overview_canvas.yview)
        self.overview_canvas.configure(yscrollcommand=self.overview_scrollbar.set)
        self.overview_scrollable = False
        self._content_mode = None
        self.device_menu = theme.widget(tk.Menu, self.overview_canvas, tearoff=False,
                                        bg=theme.SURFACE, fg=theme.TEXT, activebackground=theme.SELECTED,
                                        activeforeground=theme.TEXT)
        self.overview_canvas.bind("<ButtonRelease-1>", self.overview_click)
        self.overview_canvas.bind("<ButtonRelease-3>", self.overview_click)
        self.overview_canvas.bind("<MouseWheel>", lambda event: self.overview_canvas.yview_scroll(
            -int(event.delta / 120) * 3, "units") if self.overview_scrollable else None)
        from card_hover import CardHover
        self.card_hover = CardHover(root, self.overview_canvas, scale=UI_SCALE, assets=brand_path.parent,
                                   blocked=lambda: self.transition.active)
        self.overview_canvas.bind("<MouseWheel>", lambda _event: self.root.after_idle(self.card_hover.refresh_pointer), add="+")
        self._drag = None

        root.title(tc.UI_TITLE)
        self._layout_size = self.ui.px(DEFAULT_WIDTH), 0
        self._size_limits = self._window_work_area = self._geometry_after_id = None
        self._positioned = False
        root.resizable(True, False)
        root.bind("<Configure>", self.on_configure)
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.bind("<F5>", self.refresh_shortcut)
        root.bind("<Control-r>", self.refresh_shortcut)
        self.canvas.bind("<ButtonPress-1>", self.start_drag)
        self.canvas.bind("<ButtonRelease-1>", self.end_drag)
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
        return self.color(theme.BACKGROUND)

    def chrome_image(self, filename):
        import tkinter as tk

        path = theme.asset(self.root, ASSETS / filename)
        if path not in self.chrome_images:
            self.chrome_images[path] = tk.PhotoImage(master=self.root, file=str(path))
        return self.chrome_images[path]

    def apply_theme(self):
        self.transition.set_background(self.fade_background())
        self.card_hover.reset_assets()
        self.draw()

    def select_theme(self, choice):
        mode = next(mode for mode, label in theme.MODES if choice == label)
        if mode == self.appearance.mode:
            return
        try:
            config = tc.load_config()
            config["theme"] = mode
            tc.save_config(config, strict=True)
        except OSError as exc:
            from tkinter import messagebox

            self.theme_choice.set(theme.MODE_LABELS[self.appearance.mode])
            messagebox.showerror("主题设置失败", str(exc) or "无法保存主题设置，请重试", parent=self.root)
            return
        self.appearance.set_mode(mode)

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
        else:
            self.reading = reading
            self.readings[self.selected_device_id] = reading
            if self.selected_device_id == DEFAULT_DEVICE_ID:
                self.low_battery_notifier.check(reading)
            success = reading.ok or (reading.connected is True and reading.status in ("待适配电量", "未上报电量"))
        self.busy = False
        self.refresh_button.set_state("success" if success else "error")
        if isinstance(reading, dict) and identity_changed:
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
        discovery = getattr(self, "aula_discovery", None)
        if discovery is not None:
            discovery.close()
        updates = getattr(self, "updates", None)
        if updates is not None:
            updates.close()
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
                      self.refresh_feedback_after_id, self.startup_after_id, self.pump_after_id,
                      self.runtime_after_id, getattr(self, "_geometry_after_id", None)):
            if timer is not None:
                self.root.after_cancel(timer)
        self.root.destroy()

    def update_runtime_display(self):
        transition = getattr(self, "transition", None)
        if transition is not None and transition.phase == "out":
            return
        item = self.overview_runtime_items.get(DEFAULT_DEVICE_ID)
        if item is None:
            return
        reading = self.readings.get(DEFAULT_DEVICE_ID)
        text = "暂不可用"
        if reading and reading.ok:
            charging = reading.status in ("charging", "充电中")
            view = get_runtime_view(reading.level, charging=charging, valid=True)
            text = "充电中，暂不估算" if charging else format_remaining(view["hours"], live=True)
        if self.overview_canvas.itemcget(item, "text") != text:
            self.overview_canvas.itemconfigure(item, text=text)

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

    def badge_style(self, fill, height):
        import tkinter as tk
        from toggle_switch import capsule_png

        width, height = self.ui.px(BADGE_WIDTH), self.ui.px(height)
        key = width, height, fill
        if key not in self.badge_images:
            image = tk.PhotoImage(master=self.root, data=capsule_png(width, height, fill), format="PNG")
            name = str(image)
            self.badge_images[key] = image
            # Keep the exact pixel coverage when the rest of the canvas is scaled.
            self.ui.images[name] = image
            self.transition.opacity_sources[name] = (
                lambda opacity: capsule_png(width, height, fill, opacity=opacity)
            )
        return {"image": str(self.badge_images[key])}

    def live_badge(self, canvas, group, x, y, height, fill):
        style = self.badge_style(fill, height)
        item = canvas.create_image(x, y, tags="status_badge", **style)
        self.live.link(group, "badge_bg", canvas, (item,), **style)

    def live_battery(self, canvas, group, name, x, y, level, charging=False):
        item = battery_icon(canvas, x, y, level, charging)
        physical = str(self.ui.image(canvas.itemcget(item, "image")))
        self.live.link(group, name, canvas, (item,), image=physical)

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
            "badge_bg": {"fill": self.color(theme.ONLINE_SURFACE if online else OFFLINE_BADGE)},
            "badge": {"text": "已连接" if online else "未连接" if reading.error else "读取中",
                      "fill": self.color(theme.HEALTHY if online else theme.MUTED)},
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
        count = sum(reading.ok or reading.connected is True for reading in self.readings.values())
        online, label = count > 0, f"{count}/{len(self.device_entries())} 在线"
        return {"badge_bg": {"fill": self.color(theme.ONLINE_SURFACE if online else OFFLINE_BADGE)},
                "dot": {"fill": self.color(theme.HEALTHY if online else theme.MUTED)},
                "badge": {"text": label, "fill": self.color(theme.HEALTHY if online else theme.MUTED)}}

    def refresh_readings(self):
        if not getattr(self, "live", None) or not self.live.bindings:
            self.draw()
            return
        for key, reading in self.readings.items():
            self.reading_times.observe(key, reading)
        if self.transition.active:
            return
        header = self.header_styles()
        header["badge_bg"] = self.badge_style(header["badge_bg"]["fill"], HEADER_BADGE_HEIGHT)
        self.live.apply("header", header)
        for key in self.visible_devices:
            reading = self.readings.get(key)
            if reading is None:
                continue
            values = self.reading_styles(key, reading)
            values["badge_bg"] = self.badge_style(values["badge_bg"]["fill"], DEVICE_BADGE_HEIGHT)
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
                gauge = getattr(self, "battery_gauges", {}).get((key, part))
                if gauge is not None:
                    self.update_battery_gauge(gauge, level, charging)
            self.live.apply(key, values)
        self.update_runtime_display()

    def draw(self):
        if self.transition.phase == "prepare":
            self.transition.start()
            return
        for _ in self.draw_steps():
            pass

    def draw_steps(self):
        if not self.canvas.winfo_exists() or self.transition.phase == "out":
            return
        entries = self.device_entries()
        top = OVERVIEW_TOP
        body_height = content_height(len(entries))
        self.resize_to_content(top + body_height + FOOTER_HEIGHT)
        w, h = (self.ui.logical(value) for value in self._layout_size)
        viewport = h - top - FOOTER_HEIGHT
        self.transition.begin_layout()
        self.live.bindings.clear()
        self.battery_gauges = {}
        self.visible_devices = {}
        self.reading_times.retain(set(self.readings))
        for key, reading in self.readings.items():
            self.reading_times.observe(key, reading)
            yield
        self.display_photos = []
        self.overview_runtime_items.clear()
        self.canvas.delete("all")
        self.overview_canvas.delete("all")
        yield

        canvas = self.canvas
        canvas.create_rectangle(0, 0, w, h, fill=self.color(theme.BACKGROUND), outline="")
        canvas.create_image(0, 0, image=self.chrome_image("ambient-header.png"), anchor="nw")
        canvas.create_image(17, 15, image=self.chrome_image("nexus-chrome.png"), anchor="nw")
        canvas.create_text(79, 30, text="Nexus", anchor="w", fill=self.color(theme.TEXT),
                           font=("Segoe UI", 21, "bold"))
        canvas.create_text(80, 52, text="外设电量中心", anchor="w", fill=self.color(theme.MUTED), font=(FONT, 10))
        canvas.create_text(w - 271, 39, text="主题", anchor="e", fill=self.color(theme.MUTED), font=(FONT, 10))
        self.ui.place(self.theme_picker, x=w - 259, y=20)
        yield

        rounded_rect(canvas, 16, 71, w - 16, 118, 16, self.color(theme.SURFACE),
                     outline=self.color(theme.LINE), tags="view_toolbar")
        canvas.create_text(32, 94, text="设备总览", anchor="w", fill=self.color(theme.TEXT),
                           font=(FONT, 13, "bold"))
        self.ui.place(self.receiver_button, x=w - 231, y=77)
        self.ui.place(self.refresh_button, x=w - 124, y=74, width=104, height=42)
        header = self.header_styles()
        status_x = w - 103
        self.live_badge(canvas, "header", status_x + BADGE_WIDTH/2, 39,
                        HEADER_BADGE_HEIGHT, header["badge_bg"]["fill"])
        dot = canvas.create_oval(status_x + 10, 36, status_x + 16, 42, outline="", **header["dot"])
        self.live.link("header", "dot", canvas, (dot,), **header["dot"])
        self.live_text(canvas, "header", "badge", status_x + 22, 39,
                       anchor="w", font=(FONT, 9, "bold"), **header["badge"])
        yield
        self.ui.place(self.overview_canvas, x=0, y=top, width=w, height=viewport)
        self.set_content_scroll(w, top, viewport, body_height, "empty" if not entries else "overview")
        self.draw_footer(w, h)
        yield from self.ui.canvas_steps(canvas)
        yield

        body = self.overview_canvas
        if not entries:
            self.card_hover.suspend()
            draw_card_surface(body, CARD_MARGIN, CARD_PADDING, w - CARD_MARGIN, body_height - CARD_PADDING)
            body.create_text(w/2, body_height/2 - 15, text="还没有加入设备",
                             fill=self.color(theme.TEXT), font=(FONT, 18, "bold"))
            body.create_text(w/2, body_height/2 + 18, text="点击上方「搜索设备」添加外设",
                             fill=self.color(theme.MUTED), font=(FONT, 11))
            yield from self.ui.canvas_steps(body)
            return
        yield from self.draw_overview_steps(w, viewport, entries)

    def set_content_scroll(self, width, top, viewport, content, mode):
        canvas = self.overview_canvas
        viewport_pixels = self.ui.px(viewport)
        content_pixels = self.ui.px(content)
        # Separate rounding of scaled rows can differ by one pixel.
        self.overview_scrollable = content_pixels > viewport_pixels + 1
        canvas.configure(scrollregion=(0, 0, self.ui.px(width),
                                       content_pixels if self.overview_scrollable else viewport_pixels))
        if self.overview_scrollable:
            self.ui.place(self.overview_scrollbar, x=width - 13, y=top + 5, width=12, height=viewport - 10)
        else:
            self.overview_scrollbar.place_forget()
        if not self.overview_scrollable or mode != self._content_mode:
            canvas.yview_moveto(0)
        self._content_mode = mode

    def draw_footer(self, w, h):
        from update_client import current_version
        y = h - FOOTER_HEIGHT / 2
        self.canvas.create_text(20, y, text=f"Nexus · {current_version()}", anchor="w",
                                fill=self.color(theme.MUTED), font=(FONT, 10))
        self.ui.place(self.settings_button, x=w-86, y=y-14)

    def draw_product(self, canvas, device, x, y, width, height):
        kind = product_kind(device)
        halo = "keyboard" if kind == "keyboard" else "mouse" if kind == "mouse" else "audio"
        canvas.create_image(x, y, image=self.chrome_image(f"device-halo-{halo}.png"))
        photo = self.fitted_product(product_image_kind(device), width, height)
        if photo is not None:
            canvas.create_image(x, y, image=photo, tags="product_photo")
        elif is_bluetooth(device):
            draw_bluetooth_symbol(canvas, x, y, min(width, height) / 190)
        elif kind == "keyboard":
            keyboard_width = min(width, height * 2.7)
            draw_keyboard(canvas, x - keyboard_width/2, y - height*.28, keyboard_width, height*.56)
        else:
            rounded_rect(canvas, x - 20, y - 24, x + 20, y + 24, 11,
                         self.color(theme.Color("#e5e8ed", "#424d67")))
            canvas.create_text(x, y, text="USB", fill=self.color(theme.MUTED), font=(FONT, 11, "bold"))

    def draw_card_name(self, canvas, device, key, styles, rect, *, info_right=None):
        x1, y1, x2, _ = rect
        badge_right = x2 - 19 if info_right is None else info_right
        maker, model = (device.manufacturer, device.model) if device else ("AULA", "F87 Pro")
        if is_bluetooth(device) and maker == "未提供":
            maker = "Beats" if product_kind(device) == "beats-studio-buds" else "Bluetooth"
        title_font = ("Segoe UI", 18, "bold")
        maker_width = badge_right - x1 - 115
        title_width = x2 - x1 - 134 if info_right is None else info_right - x1 - 20
        canvas.create_text(x1 + 20, y1 + 23, anchor="w", font=(FONT, 10), fill=self.color(theme.MUTED),
                           text=clipped_label(canvas, maker, (FONT, 10), maker_width))
        canvas.create_text(x1 + 20, y1 + 51, anchor="w", font=title_font, fill=self.color(theme.TEXT),
                           text=clipped_label(canvas, model, title_font, title_width))
        badge_y = y1 + (31 if info_right is None else 24)
        badge_center = badge_right - BADGE_WIDTH/2
        self.live_badge(canvas, key, badge_center, badge_y, DEVICE_BADGE_HEIGHT, styles["badge_bg"]["fill"])
        self.live_text(canvas, key, "badge", badge_center, badge_y, font=(FONT, 9, "bold"), **styles["badge"])

    def draw_card_battery(self, canvas, key, reading, styles, x, y, width, *, component_layout):
        if component_layout:
            for index, (part, label) in enumerate((("left", "左耳"), ("right", "右耳"), ("case", "充电仓"))):
                px = x + index * (width / 3)
                value = ((reading.components or {}).get(part) or {}) if not reading.error else {}
                canvas.create_text(px, y, text=label, anchor="w", fill=self.color(theme.MUTED), font=(FONT, 9))
                self.live_battery(canvas, key, part + "_battery", px, y + 16,
                                  value.get("level"), value.get("charging") is True)
                self.live_text(canvas, key, part + "_level", px + 38, y + 24, anchor="w",
                               font=("Segoe UI", 13, "bold"), **styles[part + "_level"])
                self.live_text(canvas, key, part + "_time", px, y + 45, anchor="w",
                               font=(FONT, 8), **styles[part + "_time"])
                self.draw_battery_gauge(canvas, key, part, px, y + 55, width / 3 - 12,
                                        value.get("level"), value.get("charging") is True)
        else:
            canvas.create_text(x, y, text="剩余电量", anchor="w", fill=self.color(theme.MUTED), font=(FONT, 10))
            charging = not reading.error and reading.status in ("charging", "充电中")
            self.live_battery(canvas, key, "battery", x, y + 17, reading.level if not reading.error else None, charging)
            self.live_text(canvas, key, "level", x + 45, y + 24, anchor="w",
                           font=("Segoe UI", 26, "bold"), **styles["level"])
            self.draw_battery_gauge(canvas, key, None, x, y+47, min(width, 185),
                                    reading.level if not reading.error else None, charging)

    def battery_tone(self, level, charging):
        role = theme.ACCENT if charging else theme.DANGER if level < 20 else theme.WARNING if level < 40 else theme.HEALTHY
        return self.color(role)

    def draw_battery_gauge(self, canvas, key, part, x, y, width, level, charging):
        canvas.create_line(x, y, x+width, y, fill=self.color(theme.LINE), width=3, capstyle="round")
        span = width * (level or 0) / 100
        fill = self.battery_tone(level or 0, charging)
        item = canvas.create_line(x, y, x+span, y, fill=fill, width=3, capstyle="round",
                                  state="normal" if level else "hidden")
        self.battery_gauges[key, part] = canvas, item, width

    def update_battery_gauge(self, gauge, level, charging):
        canvas, item, width = gauge
        x, y, old_end, _ = canvas.coords(item)
        fill = self.battery_tone(level or 0, charging)
        end = x + self.ui.px(width * (level or 0) / 100)
        if abs(old_end - end) >= 1:
            canvas.coords(item, x, y, end, y)
        state = "normal" if level else "hidden"
        if canvas.itemcget(item, "state") != state:
            canvas.itemconfigure(item, state=state)
        if canvas.itemcget(item, "fill") != fill:
            canvas.itemconfigure(item, fill=fill)
            self.transition.update_style(canvas, item, fill=fill)

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

    def draw_overview_steps(self, width, viewport_height, entries):
        canvas = self.overview_canvas
        hover_cards = []
        motion_items = set()
        for (key, device), rect in zip(entries, card_rectangles(width, len(entries))):
            x1, y1, x2, y2 = rect
            art_width = min(274, (x2 - x1) * .39)
            art_right = x2 - 14
            art_left = art_right - art_width
            art_top, art_bottom = y1 + 12, y2 - 43
            info_right = art_left - 14
            self.visible_devices[key] = device
            reading = self.readings.get(key, Reading(device.label if device else KEYBOARD_TITLE, "正在读取…"))
            styles = self.reading_styles(key, reading)
            previous = set(canvas.find_all())
            surface = draw_card_surface(canvas, x1, y1, x2, y2)
            self.draw_card_name(canvas, device, key, styles, rect, info_right=info_right)
            yield
            x = x1 + 20
            component_layout = product_kind(device) == "beats-studio-buds"
            meter_width = info_right - x
            self.draw_card_battery(canvas, key, reading, styles, x, y1 + 81, meter_width,
                                   component_layout=component_layout)
            if not component_layout:
                self.live_text(canvas, key, "state", x, y1+147, anchor="w",
                               font=(FONT, 9), **styles["state"])
            yield
            if theme.is_dark(canvas):
                draw_card_surface(canvas, art_left, art_top, art_right, art_bottom, product=True)
            else:
                rounded_rect(canvas, art_left, art_top, art_right, art_bottom, 17, "#f3eff7")
            self.draw_product(canvas, device, (art_left+art_right)/2, (art_top+art_bottom)/2,
                              art_width - 22, art_bottom - art_top - 10)
            caption_x = x if component_layout else x + 110
            self.live_text(canvas, key, "caption", caption_x, y1 + 149, anchor="w",
                           font=(FONT, 9), **styles["caption"])
            yield
            canvas.create_line(x, y2 - 36, x2 - 20, y2 - 36, fill=self.color(theme.LINE))
            self.live_text(canvas, key, "timestamp", x, y2 - 18, anchor="w",
                           font=(FONT, 9), **styles["timestamp"])
            canvas.create_text(x1 + (x2-x1)*.43, y2 - 18, text=connection_text(device),
                               fill=self.color(theme.MUTED), font=(FONT, 9))
            if key == DEFAULT_DEVICE_ID:
                self.overview_runtime_items[key] = self.live_text(
                    canvas, key, "runtime", x2 - 100, y2 - 18, anchor="e", font=(FONT, 9), **styles["runtime"])
            else:
                self.live_text(canvas, key, "power", x2 - 100, y2 - 18, anchor="e",
                               font=(FONT, 9), **styles["power"])
            canvas.create_text(x2 - 20, y2 - 18, text="设备操作 ⋯", anchor="e",
                               fill=self.color(theme.ACCENT), font=(FONT, 9, "bold"))
            items = set(canvas.find_all()) - previous
            motion_items.update(items - {surface})
            hover_cards.append((key, rect, items, surface))
            yield
        self.update_runtime_display()
        yield from self.ui.canvas_steps(canvas)
        self.transition.register(canvas, motion_items,
                                 region=(0, 0, self.ui.px(width), self.ui.px(viewport_height)))
        self.card_hover.register(hover_cards)

    def resize_to_content(self, wanted_height):
        root = self.root
        work, frame_width, frame_height = window_work_area(root, root)
        left, top, right, bottom = work
        max_width = max(1, min(self.ui.px(MAX_WIDTH), right-left-frame_width-2*WINDOW_MARGIN))
        min_width = min(self.ui.px(MIN_WIDTH), max_width)
        width = max(min_width, min(max_width, self._layout_size[0]))
        height = min(self.ui.px(wanted_height), max(1, bottom-top-frame_height-2*WINDOW_MARGIN))
        limits = min_width, max_width, height
        if limits != self._size_limits:
            root.minsize(min_width, height)
            root.maxsize(max_width, height)
            self._size_limits = limits
        self._window_work_area = work
        if (width, height) == self._layout_size:
            return
        self._layout_size = width, height
        geometry = f"{width}x{height}"
        if self._positioned:
            x, y = root.winfo_x(), root.winfo_y()
            right_edge = right - width - frame_width - WINDOW_MARGIN
            bottom_edge = bottom - height - frame_height - WINDOW_MARGIN
            old_bottom = y + root.winfo_height() + frame_height
            if abs(old_bottom - (bottom - WINDOW_MARGIN)) <= WINDOW_MARGIN:
                y = bottom_edge
            x = max(left, min(x, right_edge))
            y = max(top, min(y, bottom_edge))
            geometry += f"+{x}+{y}"
        root.geometry(geometry)

    def on_configure(self, event):
        if event.widget is not self.root:
            return
        size = event.width, event.height
        work = window_work_area(self.root, self.root)[0]
        if size == self._layout_size and work == self._window_work_area:
            return
        self._layout_size = size
        self._window_work_area = work
        if self._geometry_after_id is not None:
            self.root.after_cancel(self._geometry_after_id)
        self._geometry_after_id = self.root.after(32, self.redraw_after_resize)

    def redraw_after_resize(self):
        self._geometry_after_id = None
        self.transition.cancel()
        self.card_hover.reset_assets()
        self.draw()

    def place_initial_window(self):
        """Anchor the initial panel inside the monitor's usable desktop."""
        self.root.update_idletasks()
        self.draw()
        self.root.update_idletasks()
        (left, top, right, bottom), frame_width, frame_height = window_work_area(self.root, self.root)
        width, height = self._layout_size
        x = max(left, right - width - frame_width - WINDOW_MARGIN)
        y = max(top, bottom - height - frame_height - WINDOW_MARGIN)
        self.root.geometry(f"{width}x{height}+{x}+{y}")
        self._positioned = True

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

    def overview_click(self, event):
        if self.transition.active:
            return
        x = self.ui.logical(self.overview_canvas.canvasx(event.x))
        y = self.ui.logical(self.overview_canvas.canvasy(event.y))
        device_id = self.card_hover.hit(x, y)
        if device_id is None:
            return
        self.device_menu.delete(0, "end")
        self.device_menu.add_command(label="查看设备信息", command=lambda: self.show_device_information(device_id))
        self.device_menu.add_command(label="移除设备", command=lambda: self.remove_device(device_id))
        try:
            self.device_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.device_menu.grab_release()

    def show_device_information(self, device_id):
        from tkinter import messagebox

        entries = dict(self.device_entries())
        if device_id not in entries:
            return
        device = entries[device_id]
        model = device.model if device else "AULA F87 Pro"
        hardware = device.hardware_id if device else ""
        reading = self.readings.get(device_id)
        lines = [connection_text(device)]
        if hardware:
            lines.append(hardware)
        if reading is not None:
            styles = self.reading_styles(device_id, reading)
            if reading.ok:
                lines.append("电量 " + reading.display_level)
            lines.extend((styles["status"]["text"], styles["power"]["text"], styles["detail"]["text"]))
        messagebox.showinfo(model, "\n\n".join(lines), parent=self.root)

    def end_drag(self, _event):
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
        # Ordinary battery readings update in place.
        if self.root.winfo_viewable():
            self.card_hover.suspend()
            self.transition.start()
        else:
            self.draw()
        self.refresh()

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

    def remove_device(self, device_id):
        entries = dict(self.device_entries())
        if device_id not in entries:
            return
        removed = entries[device_id]
        try:
            self.device_store.remove(device_id)
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
            self.settings_window.select_page("settings")
            self.settings_window.show()
        else:
            from app_dialogs import SettingsDialog
            self.settings_window = SettingsDialog(self)
        self.updates.check(manual=True)


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
