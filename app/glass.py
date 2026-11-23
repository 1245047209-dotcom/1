"""Frosted-glass backdrop for the main window (Windows 11 Mica style).

Tk cannot see through its own window, so this does what Mica does: the part of
the desktop wallpaper behind the window is blurred, tinted towards the theme and
drawn as the window background. Cards on top are a second, lighter (dark mode:
deeper) layer of the same backdrop with rounded corners, so they read as glass.

The wallpaper is sampled once per change at 1/16 of the monitor resolution and
blurred there; every crop is upscaled by GDI+ with bicubic filtering, which is
what makes it look frosted. Other windows behind Nexus are not reflected.
"""
from __future__ import annotations

import math
import os
from pathlib import Path
import sys

from ios_art import _corner, png
import theme

SAMPLE = 16          # monitor pixels per backdrop sample
BLUR_RADIUS = 2      # box-blur radius, in samples (two passes)
MARGIN = 3           # extra samples around a crop so bicubic filtering never sees an edge

RIM_WIDTH = 1.25     # glass highlight rim, physical px
# Rim strength (top, bottom): white mixed into the card edge, strongest at the top like
# light falling on iOS glass.
RIM = {False: (.85, .30), True: (.24, .06)}

# (tint colour role, tint amount, minimum / maximum luminance) for each layer.
LAYERS = {
    "base": {False: (theme.BACKGROUND, .58, .80), True: (theme.BACKGROUND, .62, .14)},
    "card": {False: (theme.SURFACE, .74, .91), True: (theme.SURFACE, .66, .22)},
}


# ---- pure helpers (unit tested) ---------------------------------------------------
def wallpaper_rects(image_w, image_h, monitor_w, monitor_h, style="10", tile="0"):
    """(source rect in the image, target rect on the monitor) for a Windows wallpaper style.

    10 fill, 6 fit, 2 stretch, 0 centre (tile=1: tile, approximated by fill), 22 span
    (approximated by fill on this monitor).
    """
    style = str(style).strip()
    if style == "2":
        return (0, 0, image_w, image_h), (0, 0, monitor_w, monitor_h)
    if style == "6":
        scale = min(monitor_w / image_w, monitor_h / image_h)
        w, h = image_w * scale, image_h * scale
        return (0, 0, image_w, image_h), ((monitor_w - w) / 2, (monitor_h - h) / 2, w, h)
    if style == "0" and str(tile).strip() != "1":
        w, h = min(image_w, monitor_w), min(image_h, monitor_h)
        source = ((image_w - w) / 2, (image_h - h) / 2, w, h)
        return source, ((monitor_w - w) / 2, (monitor_h - h) / 2, w, h)
    scale = max(monitor_w / image_w, monitor_h / image_h)
    w, h = monitor_w / scale, monitor_h / scale
    return ((image_w - w) / 2, (image_h - h) / 2, w, h), (0, 0, monitor_w, monitor_h)


def box_blur(width, height, pixels, radius=BLUR_RADIUS, passes=2):
    """Separable box blur of a list of (r, g, b) tuples; edges are clamped."""
    if radius <= 0 or not pixels:
        return list(pixels)
    window = 2 * radius + 1
    for _ in range(passes):
        for horizontal in (True, False):
            out = [None] * len(pixels)
            lines, length = (height, width) if horizontal else (width, height)
            for line in range(lines):
                index = (lambda i: line * width + i) if horizontal else (lambda i: i * width + line)
                values = [pixels[index(min(length - 1, max(0, i)))] for i in range(-radius, length + radius)]
                r = sum(v[0] for v in values[:window])
                g = sum(v[1] for v in values[:window])
                b = sum(v[2] for v in values[:window])
                for i in range(length):
                    out[index(i)] = (r / window, g / window, b / window)
                    if i + window < len(values):
                        old, new = values[i], values[i + window]
                        r += new[0] - old[0]
                        g += new[1] - old[1]
                        b += new[2] - old[2]
            pixels = out
    return pixels


def luminance(rgb):
    return (.2126 * rgb[0] + .7152 * rgb[1] + .0722 * rgb[2]) / 255


def tint(rgb, tint_rgb, amount, bound, dark):
    """Blend towards the tint, then further until the luminance bound is met (text contrast)."""
    mixed = tuple(c + (t - c) * amount for c, t in zip(rgb, tint_rgb))
    current, target = luminance(mixed), luminance(tint_rgb)
    if (not dark and current < bound) or (dark and current > bound):
        # Fraction of the remaining way to the tint that reaches the bound.
        extra = 1. if abs(target - current) < 1e-6 else min(1., abs(bound - current) / abs(target - current))
        mixed = tuple(c + (t - c) * extra for c, t in zip(mixed, tint_rgb))
    return tuple(max(0, min(255, round(c))) for c in mixed)


def rounded_distance(px, py, width, height, radius):
    """Signed distance from a point to a rounded rectangle's edge (negative inside)."""
    qx = abs(px - width / 2) - (width / 2 - radius)
    qy = abs(py - height / 2) - (height / 2 - radius)
    return math.hypot(max(qx, 0.), max(qy, 0.)) + min(max(qx, qy), 0.) - radius


def rim_coverage(px, py, width, height, radius, rim=RIM_WIDTH):
    """How much of a pixel (centre px, py) lies on the inner highlight stroke."""
    return max(0., min(1., rounded_distance(px, py, width, height, radius) + rim + .5))


def bgra_to_png(width, height, bgra, stride, radius=0, rim=None):
    """RGBA PNG from top-down BGRA rows; ``radius`` > 0 rounds the four corners.

    ``rim=(top, bottom)`` lightens a thin inner stroke towards white, fading from
    ``top`` to ``bottom`` strength: the highlight edge of a pane of glass.
    """
    radius = max(0, min(int(radius), width // 2, height // 2))
    corner = _corner(radius) if radius else ()
    opaque = b"\xff" * width
    band = radius + 2
    scanlines = bytearray()
    for y in range(height):
        row = bgra[y * stride:y * stride + width * 4]
        rgba = bytearray(row)
        rgba[0::4], rgba[2::4] = row[2::4], row[0::4]
        alpha = bytearray(opaque)
        edge = y if y < radius else height - 1 - y if y >= height - radius else None
        if edge is not None:
            alpha[:radius] = corner[edge]
            alpha[width - radius:] = corner[edge][::-1]
        if rim is not None:
            strength = rim[0] + (rim[1] - rim[0]) * (y / max(1, height - 1))
            # Only pixels near an edge can touch the stroke.
            columns = range(width) if y < band or y >= height - band else (
                *range(min(band, width)), *range(max(band, width - band), width))
            for x in columns:
                amount = strength * rim_coverage(x + .5, y + .5, width, height, radius)
                if amount > 0:
                    i = x * 4
                    for channel in range(i, i + 3):
                        rgba[channel] = round(rgba[channel] + (255 - rgba[channel]) * amount)
        rgba[3::4] = alpha
        scanlines.append(0)
        scanlines += rgba
    return png(width, height, scanlines, level=1)


# ---- Windows wallpaper ----------------------------------------------------------
def wallpaper():
    """(path or None, style, tile, solid colour '#rrggbb')."""
    path, style, tile, solid = None, "10", "0", "#808080"
    if sys.platform != "win32":
        return path, style, tile, solid
    import ctypes
    import winreg
    buffer = ctypes.create_unicode_buffer(1024)
    if ctypes.windll.user32.SystemParametersInfoW(0x73, len(buffer), buffer, 0):  # SPI_GETDESKWALLPAPER
        path = buffer.value or None
    if not path or not os.path.isfile(path):
        cached = Path(os.environ.get("APPDATA", "")) / "Microsoft/Windows/Themes/TranscodedWallpaper"
        path = str(cached) if path and cached.is_file() else None
    for key_path, names in ((r"Control Panel\Desktop", ("WallpaperStyle", "TileWallpaper")),
                            (r"Control Panel\Colors", ("Background",))):
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
                for name in names:
                    try:
                        value = str(winreg.QueryValueEx(key, name)[0])
                    except OSError:
                        continue
                    if name == "WallpaperStyle":
                        style = value
                    elif name == "TileWallpaper":
                        tile = value
                    else:
                        parts = [int(p) for p in value.split()[:3]]
                        if len(parts) == 3:
                            solid = "#" + bytes(max(0, min(255, p)) for p in parts).hex()
        except OSError:
            pass
    return path, style, tile, solid


def monitor_rect(window):
    """Full monitor rectangle (left, top, right, bottom) that holds ``window``."""
    if sys.platform != "win32":
        return 0, 0, window.winfo_screenwidth(), window.winfo_screenheight()
    import ctypes
    from ctypes import wintypes

    class MonitorInfo(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("monitor", wintypes.RECT),
                    ("work", wintypes.RECT), ("flags", wintypes.DWORD)]

    user32 = ctypes.WinDLL("user32")
    user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    user32.GetAncestor.restype = wintypes.HWND
    user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
    user32.MonitorFromWindow.restype = wintypes.HMONITOR
    user32.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.POINTER(MonitorInfo)]
    monitor = user32.MonitorFromWindow(user32.GetAncestor(window.winfo_id(), 2), 2)
    info = MonitorInfo(size=ctypes.sizeof(MonitorInfo))
    if monitor and user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
        r = info.monitor
        return r.left, r.top, r.right, r.bottom
    return 0, 0, window.winfo_screenwidth(), window.winfo_screenheight()


class Backdrop:
    """Blurred wallpaper samples for one monitor plus cached glass crops."""

    def __init__(self, root, native):
        self.root, self.native = root, native
        self.signature = None
        self.samples = None          # (monitor rect, width, height, blurred pixels)
        self.error = ""
        self.cache = {}
        self.average = None          # mean colour of the last base crop

    def _signature(self, monitor):
        path, style, tile, solid = wallpaper()
        try:
            stamp = os.stat(path).st_mtime_ns if path else 0
        except OSError:
            path, stamp = None, 0
        return path, stamp, style, tile, solid, monitor

    def prepare(self):
        """Reload when the wallpaper, its style or the monitor changed. True when usable."""
        try:
            monitor = monitor_rect(self.root)
            signature = self._signature(monitor)
        except Exception as exc:  # registry / Win32 failures turn glass off, never the window
            self.error, self.samples = str(exc), None
            return False
        if signature == self.signature and self.samples is not None:
            return True
        self.signature, self.cache = signature, {}
        path, _stamp, style, tile, solid, (left, top, right, bottom) = signature
        mw, mh = max(1, right - left), max(1, bottom - top)
        sw, sh = max(1, math.ceil(mw / SAMPLE)), max(1, math.ceil(mh / SAMPLE))
        fill = 0xFF000000 | int(solid[1:], 16)
        try:
            data = Path(path).read_bytes() if path else None

            def place(iw, ih):
                source, target = wallpaper_rects(iw, ih, mw, mh, style, tile)
                return source, tuple(v / SAMPLE for v in target)
            bgra, stride = self.native.compose(data, sw, sh, fill=fill, place=place)
        except (OSError, RuntimeError, ValueError, MemoryError) as exc:
            if not path:
                self.error, self.samples = str(exc), None
                return False
            try:  # unreadable picture: fall back to the desktop colour
                bgra, stride = self.native.compose(None, sw, sh, fill=fill)
            except (OSError, RuntimeError, ValueError, MemoryError) as again:
                self.error, self.samples = str(again), None
                return False
        pixels = [(bgra[y * stride + x * 4 + 2], bgra[y * stride + x * 4 + 1], bgra[y * stride + x * 4])
                  for y in range(sh) for x in range(sw)]
        self.samples = (left, top), sw, sh, box_blur(sw, sh, pixels)
        self.error = ""
        return True

    def image_data(self, screen_x, screen_y, width, height, role, dark, radius=0):
        """PNG data of the glass for a screen rectangle (physical px), or None."""
        if self.samples is None or width < 1 or height < 1:
            return None
        key = (round(screen_x), round(screen_y), width, height, role, dark, radius, self.signature)
        if key in self.cache:
            return self.cache[key]
        (left, top), sw, sh, pixels = self.samples
        fx, fy = (screen_x - left) / SAMPLE, (screen_y - top) / SAMPLE
        fw, fh = width / SAMPLE, height / SAMPLE
        x0, y0 = math.floor(fx) - MARGIN, math.floor(fy) - MARGIN
        x1, y1 = math.ceil(fx + fw) + MARGIN, math.ceil(fy + fh) + MARGIN
        role_tint, amount, bound = LAYERS[role][dark]
        tint_rgb = tuple(bytes.fromhex((role_tint.dark if dark else role_tint.light)[1:]))
        cw, ch = x1 - x0, y1 - y0
        rows = bytearray()
        for y in range(y0, y1):
            rows.append(0)
            line = min(sh - 1, max(0, y)) * sw
            for x in range(x0, x1):
                rows.extend(tint(pixels[line + min(sw - 1, max(0, x))], tint_rgb, amount, bound, dark))
                rows.append(255)
        if role == "base":
            count = cw * ch
            self.average = "#" + bytes(round(sum(rows[1 + y * (cw * 4 + 1) + x * 4 + c]
                                                 for y in range(ch) for x in range(cw)) / count)
                                       for c in range(3)).hex()
        crop = png(cw, ch, rows, level=1)
        source = (fx - x0, fy - y0, fw, fh)
        bgra, stride = self.native.compose(crop, width, height,
                                           place=lambda _w, _h: (source, (0, 0, width, height)))
        data = bgra_to_png(width, height, bgra, stride, radius, rim=RIM[dark] if role == "card" else None)
        self.cache[key] = data
        while len(self.cache) > 16:
            self.cache.pop(next(iter(self.cache)))
        return data
