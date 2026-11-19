"""Nonlinear canvas transitions, using Tk scheduling and cached raster fades."""
import time
from collections import OrderedDict
from collections.abc import Iterator
from frame_pacer import FramePacer
from gpu_transition import create_backend

FADE_STEPS = 64
FADE_LOOKUPS = tuple(bytes(round(255 + (value - 255) * index / (FADE_STEPS-1)) for value in range(256))
                     for index in range(FADE_STEPS-1))


def smootherstep(progress):
    t = max(0.0, min(1.0, progress))
    return t * t * t * (t * (t * 6 - 15) + 10)


def ease_out_cubic(progress):
    t = max(0.0, min(1.0, progress))
    return 1 - (1 - t) ** 3


class DeviceTransition:
    def __init__(self, root, reveal, *, clock=time.perf_counter, scale=1, finished=lambda: None, gpu_enabled=True):
        self.root, self.reveal, self.clock = root, reveal, clock
        self.scale = scale
        self.phase, self.alpha, self.offset = "idle", 1.0, 0.0
        self.timer, self.token = None, 0
        self.layers, self.image_frames, self.colors = [], OrderedDict(), {}
        self.styles = {}
        self.frame_sizes = {}
        self.sources = OrderedDict()
        self.fade_background = "white"
        self.background_rgb = (255, 255, 255)
        self.channel_lookups = None
        self.finished = finished
        self.pacer = FramePacer(root, clock=clock)
        self.builder = None
        self.gpu_enabled = gpu_enabled
        self.gpu = create_backend(root) if gpu_enabled else None

    @property
    def active(self):
        return self.phase != "idle"

    def begin_layout(self):
        self.layers.clear()
        self.styles.clear()

    def register(self, canvas, items, *, region=None):
        entries = []
        for item in items:
            kind = canvas.type(item)
            if kind == "image":
                entries.append((item, {"image": canvas.itemcget(item, "image")}))
            else:
                options = ("fill", "outline") if kind in ("rectangle", "oval", "arc", "polygon") else ("fill",)
                entries.append((item, {key: canvas.itemcget(item, key) for key in options
                                       if canvas.itemcget(item, key)}))
        tag = f"device_motion_{self.token}_{len(self.layers)}"
        for item, _style in entries:
            canvas.addtag_withtag(tag, item)
        layer = {"canvas": canvas, "entries": entries, "offset": 0.0, "tag": tag,
                 "applied": {item: dict(style) for item, style in entries}, "region": region}
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
        if self.gpu is not None and not self.gpu.active:
            self.gpu.capture(self.layers, self.alpha, self.offset*self.scale)
        self.token += 1
        self.from_alpha, self.from_offset = self.alpha, self.offset
        self.phase = "out"
        self.pacer.start()
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
                self.phase, self.alpha, self.offset = "prepare", 0.0, 24.0
                work = self.reveal()
                if isinstance(work, Iterator):
                    self.builder = work
                    self.build_step(token)
                else:
                    self.ready(token)
                return
            else:
                self.phase, self.alpha, self.offset = "idle", 1.0, 0.0
                if self.gpu is not None:
                    self.gpu.clear()
                self.pacer.stop()
                self.finished()
                self.trim_frames()
                return
        self.timer = self.pacer.after(lambda: self.step(token))

    def build_step(self, token):
        if token != self.token or self.phase != "prepare":
            return
        self.timer = None
        started = self.clock()
        try:
            # Keep input and the other animations responsive during cold layout.
            for _ in range(8):
                next(self.builder)
                if self.clock()-started >= .004:
                    break
        except StopIteration:
            self.builder = None
            self.ready(token)
            return
        except Exception:
            self.cancel_timer()
            if self.gpu is not None:
                self.gpu.clear()
            self.phase = "idle"
            raise
        self.timer = self.root.after(1, lambda: self.build_step(token))

    def ready(self, token):
        self.phase, self.alpha, self.offset = "in", 0.0, 24.0
        if self.gpu is not None:
            self.gpu.capture(self.layers, 0., 24.*self.scale)
        if self.gpu is None or not self.gpu.active:
            self.paint()
        self.started = self.clock()
        self.pacer.start()
        self.timer = self.pacer.after(lambda: self.step(token))

    def paint(self):
        if self.gpu is not None and self.gpu.active and self.gpu.paint(self.alpha, self.offset*self.scale):
            return
        for layer in self.layers:
            self.paint_layer(layer)
        self.trim_frames()

    def paint_layer(self, layer):
        if self.gpu is not None and self.gpu.enabled and not self.gpu.failed and self.phase == "prepare":
            return
        canvas = layer["canvas"]
        delta = self.offset - layer["offset"]
        if delta:
            canvas.move(layer["tag"], delta * self.scale, 0)
        for item, style in layer["entries"]:
            if "image" in style:
                original = style["image"]
                options = {"image": self.fade_image(original)} if original else {}
            else:
                options = {key: self.fade_color(color) for key, color in style.items()}
            previous = layer["applied"][item]
            changed = {key: value for key, value in options.items() if str(previous.get(key)) != str(value)}
            if changed:
                canvas.itemconfigure(item, **changed)
                previous.update(changed)
        layer["offset"] = self.offset

    def fade_color(self, color):
        if self.alpha >= 1:
            return color
        if color not in self.colors:
            self.colors[color] = tuple(value // 257 for value in self.root.winfo_rgb(color))
        rgb = tuple(round(base + (value - base) * self.alpha)
                    for value, base in zip(self.colors[color], self.background_rgb))
        return "#%02x%02x%02x" % rgb

    def set_background(self, background):
        if background == self.fade_background:
            return
        self.cancel_timer()
        if self.gpu is not None:
            self.gpu.clear()
        self.token += 1
        self.phase, self.alpha, self.offset = "idle", 1., 0.
        self.fade_background = background
        self.background_rgb = tuple(value // 257 for value in self.root.winfo_rgb(background))
        self.channel_lookups = None
        if self.background_rgb != (255, 255, 255):
            self.channel_lookups = tuple(
                tuple(bytes(round(base + (value - base) * level / (FADE_STEPS - 1)) for value in range(256))
                      for base in self.background_rgb)
                for level in range(FADE_STEPS - 1)
            )
        self.image_frames.clear()
        self.frame_sizes.clear()
        self.sources.clear()

    def fade_image(self, image_name):
        level = round(self.alpha*(FADE_STEPS-1))
        if level >= FADE_STEPS-1:
            return image_name
        key = (image_name, level)
        if key not in self.image_frames:
            import tkinter as tk
            if image_name not in self.sources:
                data = self.root.tk.call(image_name, "data", "-format", "ppm", "-background", self.fade_background)
                magic, size, maximum, pixels = data.split(b"\n", 3)
                self.sources[image_name] = b"\n".join((magic, size, maximum))+b"\n", pixels
            self.sources.move_to_end(image_name)
            header, pixels = self.sources[image_name]
            if self.channel_lookups is None:
                faded = pixels.translate(FADE_LOOKUPS[level])
            else:
                faded = bytearray(len(pixels))
                for channel, lookup in enumerate(self.channel_lookups[level]):
                    faded[channel::3] = pixels[channel::3].translate(lookup)
            self.image_frames[key] = tk.PhotoImage(master=self.root, data=header+faded, format="PPM")
            self.frame_sizes[key] = len(pixels)//3*4
        self.image_frames.move_to_end(key)
        return self.image_frames[key]

    def trim_frames(self):
        # Currently painted images also have a strong reference in layer.applied.
        # Individual opacity frames can be evicted instead of retaining every
        # frame of every currently visible product image.
        total = sum(self.frame_sizes.values())
        for name in list(self.image_frames):
            if total <= 24 * 1024 * 1024 and len(self.image_frames) <= 256:
                break
            del self.image_frames[name]
            total -= self.frame_sizes.pop(name)
        source_bytes = sum(len(value[1]) for value in self.sources.values())
        while self.sources and (source_bytes > 4*1024*1024 or len(self.sources) > 32):
            _name, (_header, pixels) = self.sources.popitem(last=False)
            source_bytes -= len(pixels)

    def cancel_timer(self):
        if self.timer is not None:
            self.root.after_cancel(self.timer)
            self.timer = None
        self.pacer.stop()
        if self.builder is not None:
            self.builder.close()
            self.builder = None

    def close(self):
        self.cancel_timer()
        if self.gpu is not None:
            self.gpu.close()
        self.token += 1
        self.phase = "idle"
        self.layers.clear()
        self.styles.clear()
        self.image_frames.clear()
        self.frame_sizes.clear()
        self.sources.clear()

    def set_gpu_enabled(self, enabled):
        self.cancel_timer()
        if self.gpu is not None:
            self.gpu.close()
        self.gpu_enabled = bool(enabled)
        self.gpu = create_backend(self.root) if enabled else None
        self.phase, self.alpha, self.offset = "idle", 1., 0.
        self.paint()
