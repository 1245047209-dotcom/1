"""Nexus color roles and the theme for one Tk application."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
from pathlib import Path
import sys
import tkinter as tk
from tkinter import ttk
from typing import NamedTuple


class Color(NamedTuple):
    light: str
    dark: str


BACKGROUND = Color("#f1eff5", "#17171b")
SURFACE = Color("#fcfbfd", "#232328")
TEXT = Color("#251f2c", "#f9f7fa")
MUTED = Color("#635b6d", "#b3afb9")
LINE = Color("#dfdbe6", "#3d3b46")
SOFT = Color("#f0edf5", "#2c2b34")
ACCENT = Color("#624786", "#e3d0fb")
SELECTED = Color("#ebe4f5", "#35303f")
FOCUS = Color("#815fa7", "#ddc6fc")
PRIMARY = Color("#3b304b", "#46414f")
PRIMARY_HOVER = Color("#51415f", "#585060")
ON_PRIMARY = Color("#ffffff", "#ffffff")
DISABLED = Color("#8b8494", "#918b99")
DISABLED_SURFACE = Color("#eeebf2", "#28272e")
WARNING = Color("#805c20", "#f2cb90")
DANGER = Color("#9b4554", "#fbb0b6")
SHADOW = Color("#ded9e7", "#0d0d10")
EDGE = Color("#cbc1d5", "#64606e")
HEALTHY = Color("#276b62", "#b7ebd9")
ONLINE_SURFACE = Color("#e1eee9", "#2a3533")

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
        # On Windows 11, explicit colors also cover forced dark when Windows is light.
        for attribute, swatch in ((35, SURFACE), (36, TEXT)):
            value = int.from_bytes(bytes.fromhex(swatch.dark[1:]), "little") if dark else 0xffffffff
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
        self.on_change = None
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
            self.ttk.map("TScrollbar", background=[("active", "#535e79")])
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
