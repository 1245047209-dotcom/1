"""Shared capsule switch with antialiased edges and a sliding white thumb."""
from __future__ import annotations

from functools import lru_cache
import math
import struct
import time
import tkinter as tk
import zlib
import theme

def _rgb(color):
    return tuple(int(color[i:i+2], 16) for i in (1, 3, 5))


def _rgba_png(width, height, scanlines):
    def chunk(kind, data):
        return struct.pack("!I", len(data)) + kind + data + struct.pack("!I", zlib.crc32(kind + data))

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack("!IIBBBBB", width, height, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(bytes(scanlines))) + chunk(b"IEND", b""))


def _edge_coverage(distance):
    return max(0., min(1., .5 - distance))


def _capsule_distance(px, py, left, right, center_y, radius):
    return math.hypot(px - min(right, max(left, px)), py - center_y) - radius


@lru_cache(maxsize=24)
def _capsule_alpha(width, height):
    radius = height / 2
    return bytes(round(255 * _edge_coverage(_capsule_distance(
        x + .5, y + .5, radius, width - radius, radius, radius)))
        for y in range(height) for x in range(width))


def capsule_png(width, height, color, *, opacity=1.):
    """Draw a pill at its destination size with partially transparent edge pixels."""
    alpha = _capsule_alpha(width, height)
    if opacity < 1.:
        alpha = alpha.translate(bytes(round(value * opacity) for value in range(256)))
    row = bytearray(bytes((*_rgb(color), 0)) * width)
    scanlines = bytearray()
    for y in range(height):
        row[3::4] = alpha[y*width:(y+1)*width]
        scanlines.append(0)
        scanlines.extend(row)
    return _rgba_png(width, height, scanlines)


@lru_cache(maxsize=128)
def glass_capsule_png(width, height, face, rim, highlight, *, reflection=.42, opacity=1.):
    """Render an antialiased rim and a soft highlight across the upper face."""
    outer_alpha = _capsule_alpha(width, height)
    inner_width, inner_height = width - 2, height - 2
    inner_alpha = _capsule_alpha(inner_width, inner_height)
    face, rim, highlight = map(_rgb, (face, rim, highlight))
    scanlines = bytearray()
    for y in range(height):
        scanlines.append(0)
        glint = reflection * max(0., 1. - y / (height * .72)) ** 2
        tint = tuple(round(a + (b-a)*glint) for a, b in zip(face, highlight))
        edge = rim if y >= height / 2 else tuple(round(a + (b-a) * .42) for a, b in zip(rim, highlight))
        for x in range(width):
            alpha = outer_alpha[y * width + x]
            coverage = inner_alpha[(y-1)*inner_width + x-1] / alpha if alpha and 0 < x < width-1 and 0 < y < height-1 else 0
            coverage = min(coverage, 1.)
            scanlines.extend(round(a + (b-a)*coverage) for a, b in zip(edge, tint))
            scanlines.append(round(alpha * opacity))
    return _rgba_png(width, height, scanlines)


@lru_cache(maxsize=192)
def _image(width, height, position, track_color, focus_color, focused):
    """Render to PNG with subpixel edge coverage, preserving the theme colors."""
    # iOS switch: flat track, round white thumb inset ~2 px, soft drop shadow.
    track = _rgb(track_color)
    thumb, focus = _rgb(theme.ON_PRIMARY.light), _rgb(focus_color)
    radius, cy = (height - 2) / 2, height / 2
    left, right = 1 + radius, width - 1 - radius
    thumb_radius = radius - max(1.5, height * .07)
    thumb_x = left + (right - left) * position / 1000
    scanlines = bytearray()
    for y in range(height):
        scanlines.append(0)
        for x in range(width):
            px, py = x + .5, y + .5
            distance = _capsule_distance(px, py, left, right, cy, radius)
            layers = []
            if focused:
                layers.append((focus, max(0, min(1, 1.5 - distance))))
            layers.append((track, _edge_coverage(distance)))
            thumb_distance = math.hypot(px - thumb_x, py - cy) - thumb_radius
            thumb_shadow = math.hypot(px - thumb_x, py - cy - .9) - thumb_radius - .6
            layers.append(((0, 0, 0), .16 * _edge_coverage(thumb_shadow) * _edge_coverage(distance)))
            layers.append((thumb, _edge_coverage(thumb_distance)))
            alpha, rgb = 0, [0., 0., 0.]
            for color, coverage in layers:
                rgb = [channel * coverage + old * (1 - coverage) for channel, old in zip(color, rgb)]
                alpha = coverage + alpha * (1 - coverage)
            scanlines.extend(round(channel / alpha) if alpha else 0 for channel in rgb)
            scanlines.append(round(alpha * 255))

    return _rgba_png(width, height, scanlines)


class Switch(tk.Canvas):
    def __init__(self, master, variable, *, scale=.85, command=None):
        self.variable, self.command, self.scale = variable, command, scale
        self.default_width, self.default_height = round(51 * scale), round(31 * scale)
        self.position = float(bool(variable.get()))
        self.focused, self.pressed, self.closed = False, False, False
        self.animation_id = None
        super().__init__(master, width=self.default_width, height=self.default_height,
                         bg=master.cget("bg"), highlightthickness=0, bd=0, takefocus=1, cursor="hand2")
        self.bind("<Configure>", lambda e: self.redraw())
        self.bind("<ButtonPress-1>", self._press)
        self.bind("<ButtonRelease-1>", self._release)
        self.bind("<space>", self._key)
        self.bind("<Return>", self._key)
        self.bind("<FocusIn>", lambda e: self._focus(True))
        self.bind("<FocusOut>", lambda e: self._focus(False))
        self.trace = variable.trace_add("write", self._changed)
        self.bind("<Destroy>", self._destroy_trace)
        theme.watch(self, self.apply_theme)
        self.redraw()

    def apply_theme(self):
        self.configure(bg=self.master.cget("bg"))
        self.redraw()

    def _press(self, _event):
        self.pressed = True
        self.focus_set()

    def _release(self, event):
        pressed, self.pressed = self.pressed, False
        if pressed and 0 <= event.x < self.winfo_width() and 0 <= event.y < self.winfo_height():
            self.invoke()

    def _key(self, _event):
        self.invoke()
        return "break"

    def _focus(self, focused):
        self.focused = focused
        self.redraw()

    def invoke(self):
        if self.closed:
            return
        self.variable.set(not self.variable.get())
        if self.command:
            self.command()

    def _changed(self, *_args):
        if self.animation_id is not None:
            self.after_cancel(self.animation_id)
            self.animation_id = None
        self.start_position = self.position
        self.target_position = float(bool(self.variable.get()))
        self.started = time.perf_counter()
        if not self.winfo_ismapped():
            self.position = self.target_position
            self.redraw()
        else:
            self._animate()

    def _animate(self):
        self.animation_id = None
        progress = min(1, (time.perf_counter() - self.started) / .14)
        eased = 1 - (1 - progress) ** 3
        self.position = self.start_position + (self.target_position - self.start_position) * eased
        self.redraw()
        if progress < 1:
            self.animation_id = self.after(15, self._animate)

    def redraw(self):
        if self.closed:
            return
        width = max(12, self.winfo_width() if self.winfo_width() > 1 else self.default_width)
        height = max(8, self.winfo_height() if self.winfo_height() > 1 else self.default_height)
        track = theme.blend(theme.color(self, theme.SWITCH_OFF), theme.color(self, theme.SWITCH_ON), self.position)
        image = _image(width, height, round(self.position * 1000), track,
                       theme.color(self, theme.FOCUS), self.focused)
        self.photo = tk.PhotoImage(master=self, data=image, format="PNG")
        if not hasattr(self, "image_item"):
            self.image_item = self.create_image(0, 0, anchor="nw", image=self.photo)
        else:
            self.itemconfigure(self.image_item, image=self.photo)

    def _destroy_trace(self, event):
        if event.widget is self:
            self.closed = True
            if self.animation_id is not None:
                self.after_cancel(self.animation_id)
                self.animation_id = None
            try:
                self.variable.trace_remove("write", self.trace)
            except tk.TclError:
                pass
