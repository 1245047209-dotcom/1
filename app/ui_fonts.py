"""Windows font discovery and the named fonts shared by the Tk windows."""
from __future__ import annotations

import atexit
import ctypes
from ctypes import wintypes
from functools import cache
import os
from pathlib import Path
import sys
from tkinter import font as tkfont
from typing import NamedTuple


DEFAULT = "system"
FONT_DIR = Path(__file__).with_name("assets") / "fonts"
_PRESETS = (
    (35, "Thin", "纤细"),
    (45, "Light", "细体"),
    (55, "Regular", "常规"),
    (65, "Medium", "中等"),
    (75, "SemiBold", "半粗"),
    (85, "Bold", "粗体"),
    (95, "ExtraBold", "特粗"),
    (105, "Heavy", "重体"),
    (115, "Black", "最粗"),
)
_NAMED_DEFAULTS = ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont")


class _Face(NamedTuple):
    path: Path
    family: str
    weight: str


def _font_directories():
    yield FONT_DIR
    if local_data := os.environ.get("LOCALAPPDATA"):
        yield Path(local_data) / "Microsoft" / "Windows" / "Fonts"
    if windows := os.environ.get("WINDIR"):
        yield Path(windows) / "Fonts"


def _find_font(filename, directories):
    for directory in directories:
        path = directory / filename
        if path.is_file():
            return path
    return None


@cache
def _register(path):
    if sys.platform != "win32" or not path.is_file():
        return False
    gdi = ctypes.WinDLL("gdi32")
    add_font, remove_font = gdi.AddFontResourceExW, gdi.RemoveFontResourceExW
    add_font.argtypes = remove_font.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_void_p)
    add_font.restype, remove_font.restype = ctypes.c_int, wintypes.BOOL
    private = 0x10  # FR_PRIVATE applies only for the current process.
    if not add_font(str(path), private, None):
        return False
    atexit.register(remove_font, str(path), private, None)
    return True


class UIFonts:
    def __init__(self, root, default_family, number_family):
        self.root = root
        self.default_family = default_family
        self.number_family = number_family
        self.choices = {DEFAULT: "系统默认"}
        self._faces = {}
        if sys.platform == "win32":
            directories = tuple(_font_directories())
            for number, name, label in _PRESETS:
                path = _find_font(f"AlibabaPuHuiTi-2-{number}-{name}.ttf", directories)
                if path is None:
                    continue
                key = f"alibaba-{name.lower()}"
                self.choices[key] = label
                # Windows groups the 85 Bold face with 55 Regular in GDI.
                family, weight = ("Alibaba PuHuiTi 2.0 55 Regular", "bold") if number == 85 else (
                    f"Alibaba PuHuiTi 2.0 {number} {name}", "normal")
                self._faces[key] = _Face(path, family, weight)
        self._order = tuple(self._faces)
        self._fonts, self._previews = {}, {}
        self._defaults = {name: tkfont.nametofont(name, root=root).cget("weight") for name in _NAMED_DEFAULTS}
        self.choice = None
        self.set_choice(DEFAULT)

    def _load_face(self, choice):
        face = self._faces.get(choice)
        if face is None or choice not in self.choices:
            return None
        if not _register(face.path):
            self.choices.pop(choice)
            return None
        return face

    def prepare_choices(self):
        """Register the available faces before displaying the selector previews."""
        for choice in self._order:
            self._load_face(choice)

    def _style(self, choice, weight, numeric):
        face = self._load_face(choice)
        if face is None:
            return (self.number_family if numeric else self.default_family, weight)
        if weight == "bold":
            index = self._order.index(choice)
            if index < len(self._order) - 1:
                face = self._load_face(self._order[index + 1]) or face
        return face.family, face.weight

    def set_choice(self, choice):
        if not isinstance(choice, str) or (choice != DEFAULT and self._load_face(choice) is None):
            choice = DEFAULT
        if choice == self.choice:
            return False
        self.choice = choice
        for name, weight in self._defaults.items():
            family, effective_weight = self._style(choice, weight, False)
            tkfont.nametofont(name, root=self.root).configure(family=family, weight=effective_weight)
        for (_size, weight, numeric), font in self._fonts.items():
            family, effective_weight = self._style(choice, weight, numeric)
            font.configure(family=family, weight=effective_weight)
        return True

    def font(self, size, weight, numeric=False):
        key = size, weight, numeric
        if key not in self._fonts:
            family, effective_weight = self._style(self.choice, weight, numeric)
            self._fonts[key] = tkfont.Font(root=self.root, family=family, size=size, weight=effective_weight)
        return self._fonts[key]

    def preview(self, choice, size, weight):
        key = choice, size, weight
        if key not in self._previews:
            family, effective_weight = self._style(choice, weight, False)
            self._previews[key] = tkfont.Font(root=self.root, family=family, size=size, weight=effective_weight)
        return self._previews[key]
