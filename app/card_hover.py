"""Reversible overview-card lift; animate existing canvas groups, not layouts."""
from pathlib import Path
import time
import tkinter as tk

from ui_motion import ease_out_cubic


class CardHover:
    LIFT = 8
    RISE_SECONDS, FALL_SECONDS = .20, .24

    def __init__(self, root, canvas, *, scale=1, assets=None, blocked=lambda: False, clock=time.monotonic):
        self.root, self.canvas, self.scale = root, canvas, scale
        self.assets = Path(assets or Path(__file__).with_name("assets"))
        self.blocked, self.clock = blocked, clock
        self.states, self.layers, self.shadow_cache = {}, {}, {}
        self.shadow_sources = []
        self.target, self.timer = None, None
        self.enabled, self.closed = True, False
        canvas.bind("<Motion>", self.motion, add="+")
        canvas.bind("<Enter>", self.motion, add="+")
        canvas.bind("<Leave>", lambda _e: self.set_target(None), add="+")

    def shadow_frames(self, width, height):
        key = (width, height)
        if key in self.shadow_cache:
            return self.shadow_cache[key]
        if not self.shadow_sources:
            self.shadow_sources = [tk.PhotoImage(master=self.root, file=str(self.assets / f"card-hover-{i}.png"))
                                   for i in range(1, 6)]
        frames = []
        for source in self.shadow_sources:
            corner = (source.width()-1)//2
            columns = ((0, corner, 0, corner), (corner, 1, corner, width-2*corner),
                       (corner+1, corner, width-corner, corner))
            rows = ((0, corner, 0, corner), (corner, 1, corner, height-2*corner),
                    (corner+1, corner, height-corner, corner))
            frame = tk.PhotoImage(master=self.root, width=width, height=height)
            for sx, sw, dx, dw in columns:
                for sy, sh, dy, dh in rows:
                    tile = tk.PhotoImage(master=self.root, width=sw, height=sh)
                    tile.tk.call(str(tile), "copy", str(source), "-from", sx, sy, sx+sw, sy+sh)
                    if sw == 1 or sh == 1:
                        tile = tile.zoom(dw if sw == 1 else 1, dh if sh == 1 else 1)
                    frame.tk.call(str(frame), "copy", str(tile), "-to", dx, dy)
            frames.append(frame)
        self.shadow_cache[key] = frames
        return frames

    def register(self, cards):
        """Rebind after a data redraw while retaining current lift and easing."""
        self.sample(self.clock())
        self.enabled = True
        layers = {}
        for index, (key, rect, items, surface) in enumerate(cards):
            tag = f"hover_card_{index}"
            for item in items:
                self.canvas.addtag_withtag(tag, item)
            normal = self.canvas.itemcget(surface, "image")
            width = int(self.canvas.tk.call("image", "width", normal))
            height = int(self.canvas.tk.call("image", "height", normal))
            layers[key] = {"tag": tag, "rect": rect, "surface": surface, "normal": normal,
                           "frames": self.shadow_frames(width, height), "applied": 0, "level": 0}
            self.states.setdefault(key, {"value": 0, "from": 0, "to": 0, "started": 0, "duration": 1})
        self.layers = layers
        self.states = {key: self.states[key] for key in layers}
        if self.target not in layers:
            self.target = None
        self.paint()
        self.refresh_pointer()

    def sample(self, now):
        moving = False
        for state in self.states.values():
            progress = max(0, min(1, (now-state["started"])/state["duration"]))
            state["value"] = state["from"] + (state["to"]-state["from"])*ease_out_cubic(progress)
            moving |= progress < 1 and state["from"] != state["to"]
        return moving

    def set_target(self, key):
        if self.closed or not self.enabled:
            return
        if self.blocked():
            key = None
        if key == self.target:
            return
        now = self.clock()
        self.sample(now)
        self.target = key
        self.canvas.configure(cursor="hand2" if key is not None else "")
        for identifier, state in self.states.items():
            target = 1 if identifier == key else 0
            state.update({"from": state["value"], "to": target, "started": now,
                          "duration": self.RISE_SECONDS if target else self.FALL_SECONDS})
        if self.timer is None:
            self.timer = self.root.after(16, self.tick)

    def paint(self):
        for key, layer in self.layers.items():
            value = self.states[key]["value"]
            delta = -(value-layer["applied"])*self.LIFT*self.scale
            if delta:
                self.canvas.move(layer["tag"], 0, delta)
            level = round(value*5)
            if level != layer["level"]:
                self.canvas.itemconfigure(layer["surface"], image=layer["normal"] if not level else layer["frames"][level-1])
                layer["level"] = level
            layer["applied"] = value

    def tick(self):
        self.timer = None
        if self.closed or not self.enabled:
            return
        moving = self.sample(self.clock())
        self.paint()
        if moving:
            self.timer = self.root.after(16, self.tick)

    def hit(self, x, y):
        # The original footprint remains interactive as a card rises, preventing
        # a stationary pointer at its lower edge from repeatedly entering/leaving.
        for key, layer in self.layers.items():
            x1, y1, x2, y2 = layer["rect"]
            if x1 <= x <= x2 and y1-layer["applied"]*self.LIFT <= y <= y2:
                return key
        return None

    def motion(self, event):
        x, y = self.canvas.canvasx(event.x)/self.scale, self.canvas.canvasy(event.y)/self.scale
        self.set_target(self.hit(x, y))

    def refresh_pointer(self):
        if self.closed or not self.enabled:
            return
        if not self.canvas.winfo_ismapped():
            self.set_target(None)
            return
        x, y = self.canvas.winfo_pointerxy()
        if self.canvas.winfo_containing(x, y) is not self.canvas:
            self.set_target(None)
            return
        x = self.canvas.canvasx(x-self.canvas.winfo_rootx())/self.scale
        y = self.canvas.canvasy(y-self.canvas.winfo_rooty())/self.scale
        self.set_target(self.hit(x, y))

    def suspend(self):
        if self.timer is not None:
            self.root.after_cancel(self.timer)
            self.timer = None
        self.enabled = False
        self.target = None
        self.states.clear()
        self.layers.clear()
        self.canvas.configure(cursor="")

    def close(self):
        self.suspend()
        self.closed = True
        self.shadow_cache.clear()
        self.shadow_sources.clear()
