"""Nexus color roles and the theme for one Tk application."""
from __future__ import annotations

import atexit
import ctypes
from ctypes import wintypes
from functools import cache
from pathlib import Path
import sys
import tkinter as tk
from tkinter import font as tkfont, ttk
from typing import NamedTuple


FONT = "Microsoft YaHei UI"          # ships with Windows and has a real bold
NUM_FONT = "Segoe UI Semibold"       # percentages; tabular figures
ICON_FONTS = ("Segoe Fluent Icons", "Segoe MDL2 Assets")
GLYPHS = {"add": "\uE710", "settings": "\uE713", "refresh": "\uE72C", "more": "\uE712",
          "chevron": "\uE76C", "keyboard": "\uE765", "mouse": "\uE962", "headphones": "\uE7F6",
          "bluetooth": "\uE702", "usb": "\uE88E", "case": "\uE83F",
          "timer": "\uE916", "monitor": "\uE7F4", "battery": "\uE851", "speed": "\uEC4A",
          "update": "\uE895", "check": "\uE73E", "back": "\uE76B", "down": "\uE70D"}
FALLBACK_GLYPHS = {"add": "+", "settings": "⚙", "refresh": "↻", "more": "⋯", "chevron": "›",
                   "keyboard": "⌨", "mouse": "●", "headphones": "♫", "bluetooth": "ᛒ", "usb": "⇄", "case": "▭",
                   "timer": "◷", "monitor": "▭", "battery": "▮", "speed": "»", "update": "⟳",
                   "check": "✓", "back": "‹", "down": "▾"}
FONT_PATH = Path(__file__).with_name("assets") / "fonts" / "SourceHanSansCN-Normal.otf"


@cache
def _load_bundled_font():
    if sys.platform != "win32" or not FONT_PATH.is_file():
        return
    gdi = ctypes.WinDLL("gdi32")
    add_font = gdi.AddFontResourceExW
    remove_font = gdi.RemoveFontResourceExW
    add_font.argtypes = remove_font.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_void_p)
    add_font.restype, remove_font.restype = ctypes.c_int, wintypes.BOOL
    private = 0x10  # FR_PRIVATE restricts the font to this process.
    path = str(FONT_PATH)
    if add_font(path, private, None):
        atexit.register(remove_font, path, private, None)


def configure_fonts(root):
    # v73-iOS uses system fonts; the bundled Source Han Sans is no longer registered.
    for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont"):
        tkfont.nametofont(name, root=root).configure(family=FONT)


class Color(NamedTuple):
    light: str
    dark: str


# iOS system colours (light, dark); role names are unchanged so every window follows.
BACKGROUND = Color("#f2f2f7", "#000000")        # systemGroupedBackground
SURFACE = Color("#ffffff", "#1c1c1e")           # secondarySystemGroupedBackground
TEXT = Color("#000000", "#ffffff")              # label
MUTED = Color("#6e6e73", "#98989d")             # secondaryLabel, darkened for small Windows text
LINE = Color("#c6c6c8", "#38383a")              # separator
SOFT = Color("#eeeef0", "#2c2c2e")              # tertiarySystemFill on a grouped row
THUMBNAIL_BACKDROP = Color(SOFT.light, "#e4edff")
ACCENT = Color("#007aff", "#0a84ff")            # systemBlue
SELECTED = Color("#e5f0ff", "#1c2f4a")
FOCUS = Color("#007aff", "#0a84ff")
PRIMARY = Color("#007aff", "#0a84ff")
PRIMARY_HOVER = Color("#0068d9", "#3395ff")
ACTION = Color("#007aff", "#0a84ff")            # filled (prominent) buttons
ACTION_HOVER = Color("#0068d9", "#3395ff")
ON_PRIMARY = Color("#ffffff", "#ffffff")
DISABLED = Color("#8e8e93", "#8e8e93")
DISABLED_SURFACE = Color("#e5e5ea", "#2c2c2e")
WARNING = Color("#c93400", "#ff9f0a")           # systemOrange (accessible variant in light)
DANGER = Color("#d70015", "#ff453a")            # systemRed for text
LOW = Color("#ff3b30", "#ff453a")               # systemRed for rings and percentages
SHADOW = Color("#dcdce8", "#0d0d10")
EDGE = Color("#d1d1d6", "#48484a")
HIGHLIGHT = Color("#ffffff", "#55555f")
HEALTHY = Color("#248a3d", "#30d158")           # systemGreen (accessible variant in light)
RING = Color("#34c759", "#30d158")              # battery ring
RING_TRACK = Color("#e5e5ea", "#2c2c2e")
FILL = Color("#e3e3e8", "#1c1c1e")
TOOLBAR_FACE = Color(FILL.light, "#303036")
TOOLBAR_ICON = Color(ACCENT.light, "#aed7ff")
CHEVRON = Color("#c4c4c7", "#5a5a5e")
SEGMENT = Color("#ffffff", "#636366")           # selected segment of a segmented control
ONLINE_SURFACE = Color("#e4f5e8", "#203c2a")
SHEET = Color("#f2f2f7", "#1c1c1e")            # bottom sheet (iOS elevated background in dark)
SHEET_GROUP = Color("#ffffff", "#2c2c2e")
SHEET_LINE = Color("#c6c6c8", "#48484a")
DIM = Color("#9c9ca1", "#000000")              # main window dimmed behind a sheet
INDIGO = Color("#5856d6", "#5e5ce6")           # iOS settings icon tiles
ORANGE = Color("#ff9500", "#ff9f0a")
GRAY = Color("#8e8e93", "#8e8e93")
SWITCH_ON = Color("#34c759", "#30d158")
SWITCH_OFF = Color("#e3e3e8", "#39393d")

MODES = (("system", "跟随系统"), ("light", "浅色"), ("dark", "深色"))
MODE_LABELS = dict(MODES)


def normalize(mode):
    return mode if isinstance(mode, str) and mode in MODE_LABELS else "system"


def windows_dark():
    if sys.platform == "win32":
        import winreg

        path = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as key:
                value, kind = winreg.QueryValueEx(key, "AppsUseLightTheme")
                return kind == winreg.REG_DWORD and value == 0
        except OSError:
            pass
    return False


def is_dark(widget):
    root = widget._root() if widget is not None else None
    return getattr(getattr(root, "_nexus_theme", None), "dark", False) is True


def color(widget, swatch):
    return swatch.dark if is_dark(widget) else swatch.light


def control_background(widget):
    return "#" + bytes(channel // 257 for channel in widget.winfo_rgb(widget.cget("bg"))).hex()


def glass_value(value):
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return .48


def blend(first, second, amount):
    a, b = bytes.fromhex(first[1:]), bytes.fromhex(second[1:])
    return "#" + bytes(round(x+(y-x)*amount) for x, y in zip(a, b)).hex()


def icon_font(widget):
    """Segoe Fluent Icons (Windows 11) or Segoe MDL2 Assets (Windows 10), else None."""
    root = widget._root()
    if not hasattr(root, "_nexus_icon_font"):
        families = set(tkfont.families(root))
        root._nexus_icon_font = next((name for name in ICON_FONTS if name in families), None)
    return root._nexus_icon_font


def glyph(widget, name):
    """(text, font family) for a named icon, falling back to plain text."""
    family = icon_font(widget)
    if family is None:
        return FALLBACK_GLYPHS[name], FONT
    return GLYPHS[name], family


def material(widget, level=None):
    if level is None:
        root = widget._root()
        level = getattr(getattr(root, "_nexus_theme", None), "glass_level", .48)
    return blend(color(widget, BACKGROUND), color(widget, SURFACE), .52 + .48 * level)


def asset(widget, path, *, dark=None):
    """Use the dark version of UI art when one was supplied."""
    if dark is None:
        dark = is_dark(widget)
    if dark:
        assets = Path(__file__).with_name("assets")
        if path.is_relative_to(assets):
            candidate = assets / "dark" / path.relative_to(assets)
            if candidate.is_file():
                return candidate
    return path


def watch(widget, callback):
    if not hasattr(widget, "_nexus_theme_callbacks"):
        widget._nexus_theme_callbacks = []
    widget._nexus_theme_callbacks.append(callback)


def style(widget, **swatches):
    def apply():
        widget.configure(**{key: color(widget, swatch) for key, swatch in swatches.items()})

    apply()
    watch(widget, apply)
    return widget


def widget(factory, parent, **options):
    """Apply named color roles and keep those options up to date."""
    swatches = {key: value for key, value in options.items() if isinstance(value, Color)}
    options.update({key: color(parent, value) for key, value in swatches.items()})
    result = factory(parent, **options)
    if swatches:
        watch(result, lambda: result.configure(**{key: color(result, value) for key, value in swatches.items()}))
    return result


def title_bar(window):
    if sys.platform != "win32":
        return
    try:
        user = ctypes.WinDLL("user32")
        user.GetAncestor.argtypes = (wintypes.HWND, wintypes.UINT)
        user.GetAncestor.restype = wintypes.HWND
        hwnd = user.GetAncestor(window.winfo_id(), 2)
        if not hwnd:
            return
        set_attribute = ctypes.WinDLL("dwmapi").DwmSetWindowAttribute
        set_attribute.argtypes = (wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD)
        set_attribute.restype = ctypes.c_long
        dark = is_dark(window)
        enabled = wintypes.BOOL(dark)
        if set_attribute(hwnd, 20, ctypes.byref(enabled), ctypes.sizeof(enabled)) != 0:
            set_attribute(hwnd, 19, ctypes.byref(enabled), ctypes.sizeof(enabled))
        # Windows 11 supports matching the system caption to the selected Nexus theme.
        for attribute, swatch in ((35, BACKGROUND), (36, TEXT)):
            value = int.from_bytes(bytes.fromhex(color(window, swatch)[1:]), "little")
            setting = wintypes.DWORD(value)
            set_attribute(hwnd, attribute, ctypes.byref(setting), ctypes.sizeof(setting))
    except (AttributeError, OSError, tk.TclError):
        pass


def track_title_bar(window):
    watch(window, lambda: title_bar(window))
    window.bind("<Map>", lambda event: title_bar(window) if event.widget is window else None, add="+")


def style_combobox_popup(combo):
    dark = is_dark(combo)
    defaults = getattr(combo, "_nexus_list_colors", None)
    if not dark and defaults is None:
        return
    popup = combo.tk.call("ttk::combobox::PopdownWindow", combo)
    listbox = f"{popup}.f.l"
    if defaults is None:
        keys = ("background", "foreground", "selectbackground", "selectforeground")
        defaults = {key: combo.tk.call(listbox, "cget", f"-{key}") for key in keys}
        combo._nexus_list_colors = defaults
    options = dict(background=SOFT.dark, foreground=TEXT.dark,
                   selectbackground=SELECTED.dark, selectforeground=TEXT.dark) if dark else defaults
    combo.tk.call(listbox, "configure", *(arg for key, value in options.items() for arg in (f"-{key}", value)))


class Theme:
    def __init__(self, root, mode):
        self.root = root
        self.mode, self.dark, self.timer = None, False, None
        self.glass_level = .48
        self.on_change = None
        configure_fonts(root)
        self.ttk = ttk.Style(root)
        self.native_theme = self.ttk.theme_use()
        root._nexus_theme = self
        root.bind("<Destroy>", self._destroy, add="+")
        self.set_mode(mode)

    def set_mode(self, mode):
        self.mode = normalize(mode)
        if self.timer is not None:
            self.root.after_cancel(self.timer)
            self.timer = None
        changed = self._refresh()
        if self.mode == "system":
            self.timer = self.root.after(1000, self._poll)
        return changed

    def _poll(self):
        self.timer = None
        self._refresh()
        if self.mode == "system":
            self.timer = self.root.after(1000, self._poll)

    def _refresh(self):
        dark = windows_dark() if self.mode == "system" else self.mode == "dark"
        if dark == self.dark:
            return False
        self.dark = dark
        self.ttk.theme_use("clam" if dark else self.native_theme)
        if dark:
            self.ttk.configure("TScrollbar", background=LINE.dark, troughcolor=BACKGROUND.dark,
                               bordercolor=SURFACE.dark, arrowcolor=MUTED.dark,
                               lightcolor=LINE.dark, darkcolor=LINE.dark)
            self.ttk.map("TScrollbar", background=[("active", EDGE.dark)])
            self.ttk.configure("TCombobox", background=LINE.dark, fieldbackground=SOFT.dark,
                               foreground=TEXT.dark, arrowcolor=TEXT.dark, bordercolor=LINE.dark,
                               lightcolor=SOFT.dark, darkcolor=SOFT.dark)
            self.ttk.map("TCombobox", fieldbackground=[("readonly", SOFT.dark), ("disabled", SURFACE.dark)],
                         foreground=[("disabled", DISABLED.dark), ("readonly", TEXT.dark)],
                         selectbackground=[("readonly", SELECTED.dark)], selectforeground=[("readonly", TEXT.dark)])
        self._notify(self.root)
        if self.on_change is not None:
            self.on_change()
        return True

    def _notify(self, widget):
        for callback in getattr(widget, "_nexus_theme_callbacks", ()):
            callback()
        for child in widget.winfo_children():
            self._notify(child)

    def _destroy(self, event):
        if event.widget is self.root and self.timer is not None:
            self.root.after_cancel(self.timer)
            self.timer = None


def install(root, mode="system"):
    return getattr(root, "_nexus_theme", None) or Theme(root, mode)
