"""Shared capsule switch with antialiased edges and a sliding white thumb."""
from __future__ import annotations

from functools import lru_cache
import math
import struct
import time
import tkinter as tk
import zlib

ON = "#21ba79"
OFF = "#dce3eb"
THUMB = "#ffffff"
FOCUS = "#91b6df"


def _rgb(color):
    return tuple(int(color[i:i+2], 16) for i in (1, 3, 5))


@lru_cache(maxsize=192)
def _image(width, height, position, on, focused):
    """Render to PNG with subpixel edge coverage, preserving the theme colors."""
    track = _rgb(ON if on else OFF)
    thumb, focus = _rgb(THUMB), _rgb(FOCUS)
    radius, cy = (height - 2) / 2, height / 2
    left, right = 1 + radius, width - 1 - radius
    thumb_x, thumb_radius = left + (right - left) * position / 1000, (height - 6) / 2
    scanlines = bytearray()
    for y in range(height):
        scanlines.append(0)
        for x in range(width):
            px, py = x + .5, y + .5
            distance = math.hypot(px - min(right, max(left, px)), py - cy) - radius
            layers = []
            if focused:
                layers.append((focus, max(0, min(1, 1.5 - distance))))
            layers.append((track, max(0, min(1, .5 - distance))))
            thumb_distance = math.hypot(px - thumb_x, py - cy) - thumb_radius
            layers.append((thumb, max(0, min(1, .5 - thumb_distance))))
            alpha, rgb = 0, [0., 0., 0.]
            for color, coverage in layers:
                rgb = [channel * coverage + old * (1 - coverage) for channel, old in zip(color, rgb)]
                alpha = coverage + alpha * (1 - coverage)
            scanlines.extend(round(channel / alpha) if alpha else 0 for channel in rgb)
            scanlines.append(round(alpha * 255))

    def chunk(kind, data):
        return struct.pack("!I", len(data)) + kind + data + struct.pack("!I", zlib.crc32(kind + data))

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack("!IIBBBBB", width, height, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(bytes(scanlines))) + chunk(b"IEND", b""))


class Switch(tk.Canvas):
    def __init__(self, master, variable, *, scale=.85, command=None):
        self.variable, self.command, self.scale = variable, command, scale
        self.default_width, self.default_height = round(46 * scale), round(26 * scale)
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
        self.photo = tk.PhotoImage(master=self, data=_image(width, height, round(self.position * 1000),
                                                          bool(self.variable.get()), self.focused), format="PNG")
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
