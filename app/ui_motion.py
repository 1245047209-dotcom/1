"""Nonlinear canvas transitions, using Tk scheduling and cached raster fades."""
import time
from collections import OrderedDict

FADE_LOOKUPS = tuple(bytes(round(255 + (value - 255) * index / 11) for value in range(256))
                     for index in range(11))


def smootherstep(progress):
    t = max(0.0, min(1.0, progress))
    return t * t * t * (t * (t * 6 - 15) + 10)


def ease_out_cubic(progress):
    t = max(0.0, min(1.0, progress))
    return 1 - (1 - t) ** 3


class DeviceTransition:
    def __init__(self, root, reveal, *, clock=time.monotonic, scale=1):
        self.root, self.reveal, self.clock = root, reveal, clock
        self.scale = scale
        self.phase, self.alpha, self.offset = "idle", 1.0, 0.0
        self.timer, self.token = None, 0
        self.layers, self.image_frames, self.colors = [], OrderedDict(), {}
        self.styles = {}
        self.frame_sizes = {}

    @property
    def active(self):
        return self.phase != "idle"

    def begin_layout(self):
        self.layers.clear()
        self.styles.clear()

    def register(self, canvas, items):
        entries = []
        for item in items:
            kind = canvas.type(item)
            if kind == "image":
                entries.append((item, {"image": canvas.itemcget(item, "image")}))
            else:
                options = ("fill", "outline") if kind in ("rectangle", "oval", "arc", "polygon") else ("fill",)
                entries.append((item, {key: canvas.itemcget(item, key) for key in options
                                       if canvas.itemcget(item, key)}))
        layer = {"canvas": canvas, "entries": entries, "offset": 0.0}
        self.styles.update({(canvas, item): style for item, style in entries})
        self.layers.append(layer)
        if self.active:
            self.paint_layer(layer)

    def update_style(self, canvas, item, **options):
        # A subsequent transition must fade the current battery/status, not the
        # values captured when the static layout was first constructed.
        style = self.styles.get((canvas, item))
        if style is not None:
            style.update({key: value for key, value in options.items() if key in style})

    def start(self):
        self.cancel_timer()
        self.token += 1
        self.from_alpha, self.from_offset = self.alpha, self.offset
        self.phase = "out"
        # Prepare C-level lookup-table fades before starting the animation clock.
        for layer in self.layers:
            for _, style in layer["entries"]:
                if style.get("image"):
                    self.fade_frames(style["image"])
        self.started = self.clock()
        self.step(self.token)

    def step(self, token):
        if token != self.token or not self.active:
            return
        self.timer = None
        duration = .085 if self.phase == "out" else .32
        progress = min(1.0, max(0.0, (self.clock() - self.started) / duration))
        if self.phase == "out":
            eased = smootherstep(progress)
            self.alpha = self.from_alpha * (1 - eased)
            self.offset = self.from_offset + (-8 - self.from_offset) * eased
        else:
            self.alpha = smootherstep(progress)
            self.offset = 24 * (1 - ease_out_cubic(progress))
        self.paint()
        if progress >= 1:
            if self.phase == "out":
                self.phase, self.alpha, self.offset = "in", 0.0, 24.0
                self.reveal()
                self.started = self.clock()
            else:
                self.phase, self.alpha, self.offset = "idle", 1.0, 0.0
                self.paint()
                self.reveal()
                self.trim_frames()
                return
        self.timer = self.root.after(16, lambda: self.step(token))

    def paint(self):
        for layer in self.layers:
            self.paint_layer(layer)

    def paint_layer(self, layer):
        canvas = layer["canvas"]
        delta = self.offset - layer["offset"]
        for item, style in layer["entries"]:
            if delta:
                canvas.move(item, delta * self.scale, 0)
            if "image" in style:
                original = style["image"]
                if original:
                    image = original if self.alpha >= 1 else self.fade_frames(original)[round(self.alpha * 11)]
                    canvas.itemconfigure(item, image=image)
            else:
                canvas.itemconfigure(item, **{key: self.fade_color(color) for key, color in style.items()})
        layer["offset"] = self.offset

    def fade_color(self, color):
        if self.alpha >= 1:
            return color
        if color not in self.colors:
            self.colors[color] = tuple(value // 257 for value in self.root.winfo_rgb(color))
        rgb = tuple(round(255 + (value - 255) * self.alpha) for value in self.colors[color])
        return "#%02x%02x%02x" % rgb

    def fade_frames(self, image_name):
        if image_name not in self.image_frames:
            import tkinter as tk
            data = self.root.tk.call(image_name, "data", "-format", "ppm", "-background", "white")
            magic, size, maximum, pixels = data.split(b"\n", 3)
            header = b"\n".join((magic, size, maximum)) + b"\n"
            frames = []
            for lookup in FADE_LOOKUPS:
                frames.append(tk.PhotoImage(master=self.root, data=header + pixels.translate(lookup), format="PPM"))
            frames.append(image_name)
            self.image_frames[image_name] = frames
            self.frame_sizes[image_name] = len(pixels) // 3 * 4 * 11
        self.image_frames.move_to_end(image_name)
        return self.image_frames[image_name]

    def trim_frames(self):
        # Keep recent transitions warm without unbounded battery-icon/frame growth.
        protected = {style.get("image") for layer in self.layers for _, style in layer["entries"]}
        total = sum(self.frame_sizes.values())
        for name in list(self.image_frames):
            if total <= 24 * 1024 * 1024 and len(self.image_frames) <= 64:
                break
            if name not in protected:
                del self.image_frames[name]
                total -= self.frame_sizes.pop(name)

    def cancel_timer(self):
        if self.timer is not None:
            self.root.after_cancel(self.timer)
            self.timer = None

    def close(self):
        self.cancel_timer()
        self.token += 1
        self.phase = "idle"
        self.layers.clear()
        self.styles.clear()
        self.image_frames.clear()
        self.frame_sizes.clear()
