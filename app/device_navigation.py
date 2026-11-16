"""Device tabs on a shared navigation surface; move only the selection mark."""
from pathlib import Path
import time
import tkinter as tk

FONT = "Microsoft YaHei UI"


class DeviceNavigation(tk.Canvas):
    HEIGHT, DURATION = 38, .20
    COLORS = {"active": "#26313f", "idle": "#7b8696", "hover": "#46556a"}
    KINDS = ("keyboard", "mouse", "beats-studio-buds", "headphones", "usb")

    def __init__(self, master, *, ui, command, width, visible=3):
        super().__init__(master, width=ui.px(width), height=ui.px(self.HEIGHT),
                         bg="white", highlightthickness=0, borderwidth=0,
                         takefocus=True, cursor="hand2")
        self.ui, self.command, self.width, self.limit = ui, command, width, visible
        self.items, self.selected, self.start = (), None, 0
        self.hover, self.pressed, self.keyboard_focus = None, None, False
        self._pointer_focus = False
        self.cells, self.hits, self.labels, self.icons = [], [], [], []
        self.photos, self.surfaces = {}, {}
        self.position = self.origin = self.target = 0.0
        self.started, self.after_id, self.closed = 0.0, None, False
        self.fonts = {state: ui.font((FONT, -14, "bold" if state == "active" else "normal"))
                      for state in self.COLORS}
        directory = Path(__file__).resolve().parent / "assets" / "navigation"
        dimensions = {"hover": (48, 38), "focus": (48, 38), "selection": (30, 3)}
        names = tuple(dimensions) + tuple(f"{kind}-{state}" for kind in self.KINDS for state in self.COLORS)
        for name in names:
            photo = tk.PhotoImage(master=self, file=str(directory / f"{name}.png"))
            size = tuple(ui.px(value) for value in dimensions.get(name, (24, 24)))
            if (photo.width(), photo.height()) != size:
                data = photo.tk.call(str(photo), "data", "-format", "png")
                photo = tk.PhotoImage(master=self, data=ui.native.resize(data, *size), format="PNG")
            self.photos[name] = photo
        self.bind("<Motion>", self.motion)
        self.bind("<Leave>", lambda _event: self.set_hover(None))
        self.bind("<ButtonPress-1>", self.press)
        self.bind("<ButtonRelease-1>", self.click)
        self.bind("<FocusIn>", self.focus_in)
        self.bind("<FocusOut>", self.focus_out)
        for key in ("Left", "Right", "Home", "End", "space", "Return"):
            self.bind(f"<KeyPress-{key}>", self.key)
        self.bind("<Destroy>", lambda event: self.close() if event.widget is self else None)

    def surface(self, name, logical_width):
        """Stretch only the flat middle; retain antialiased corners."""
        width = max(1, self.ui.px(logical_width))
        key = (name, width)
        if key not in self.surfaces:
            source = self.photos[name]
            sw, height = source.width(), source.height()
            corner = min(self.ui.px(12), (width-1)//2)
            result = tk.PhotoImage(master=self, width=width, height=height)
            if corner:
                result.tk.call(str(result), "copy", str(source), "-from", 0, 0, corner, height, "-to", 0, 0)
                result.tk.call(str(result), "copy", str(source), "-from", sw-corner, 0, sw, height, "-to", width-corner, 0)
            strip = tk.PhotoImage(master=self, width=1, height=height)
            strip.tk.call(str(strip), "copy", str(source), "-from", sw//2, 0, sw//2+1, height)
            middle = strip.zoom(width-2*corner, 1)
            result.tk.call(str(result), "copy", str(middle), "-to", corner, 0)
            self.surfaces[key] = result
        return self.surfaces[key]

    def set_items(self, items, selected):
        items = tuple(items)
        if (items, selected) == (self.items, self.selected):
            return
        changed, previous_page = items != self.items, self.start
        self.items, self.selected = items, selected
        selected_index = next((i for i, item in enumerate(items) if item[0] == selected), None)
        if selected_index is not None and not self.start <= selected_index < self.start+self.limit:
            self.start = selected_index//self.limit*self.limit
        self.start = min(self.start, max(0, (len(items)-1)//self.limit*self.limit))
        self.hover = None
        if changed or self.start != previous_page:
            self.build()
        else:
            target = self.selected_center()
            if target is not None:
                self.itemconfigure(self.marker, state="normal")
                self.animate_to(target)
            else:
                self.cancel_tick()
                self.itemconfigure(self.marker, state="hidden")
            self.paint()

    def selected_center(self):
        return next(((left+right)/2 for left, right, index in self.cells
                     if self.items[index][0] == self.selected), None)

    def build(self):
        self.cancel_tick()
        self.delete("all")
        self.hits, self.cells, self.labels, self.icons = [], [], [], []
        overflow = len(self.items) > self.limit
        left, right = (32, self.width-32) if overflow else (4, self.width-4)
        shown = self.items[self.start:self.start+self.limit]
        widths = [max(132, min(206, self.fonts["active"].measure(item[1])/self.ui.factor+64)) for item in shown]
        available = max(1, right-left-8*max(0, len(shown)-1))
        ratio = min(1, available/max(1, sum(widths)))
        x = left
        for slot, width in enumerate(widths):
            width *= ratio
            self.cells.append((x, x+width, self.start+slot))
            x += width+8
        self.hover_image = self.create_image(0, 0, anchor="nw", state="hidden")
        self.marker = self.create_image(0, self.ui.px(34), image=self.photos["selection"], anchor="n", state="hidden")
        for left, right, index in self.cells:
            self.hits.append((left, right, ("item", index)))
            self.icons.append(self.create_image(0, self.ui.px(18), anchor="w"))
            self.labels.append(self.create_text(0, self.ui.px(18), anchor="w", font=self.fonts["idle"]))
            self.surface("hover", right-left)
            self.surface("focus", right-left)
        self.focus_image = self.create_image(0, 0, anchor="nw", state="hidden")
        if overflow:
            for x, direction, enabled in ((14, -1, self.start > 0), (self.width-14, 1, self.start+self.limit < len(self.items))):
                color = "#7b8696" if enabled else "#d5dbe3"
                self.create_line(*[self.ui.px(v) for v in (x-direction*2, 14, x+direction*2, 18, x-direction*2, 22)],
                                 fill=color, width=1.2, capstyle="round", joinstyle="round")
                if enabled:
                    self.hits.append((x-12, x+12, ("page", direction)))
        center = self.selected_center()
        if center is not None:
            self.position = self.origin = self.target = center
            self.itemconfigure(self.marker, state="normal")
            self.place_marker()
        self.paint()

    def paint(self):
        if self.closed or not self.cells:
            return
        hover_cell = focus_cell = None
        for slot, (left, right, index) in enumerate(self.cells):
            identifier, label, kind = self.items[index]
            active = identifier == self.selected
            state = "active" if active else "hover" if self.hover == ("item", index) else "idle"
            kind = kind if kind in self.KINDS else "usb"
            font = self.fonts[state]
            allowed, title = self.ui.px(right-left-64), label
            while title and font.measure(title) > allowed:
                title = title[:-2]+"…" if len(title) > 2 else ""
            group_width = self.ui.px(34)+font.measure(title)
            x = self.ui.px((left+right)/2)-group_width/2
            self.coords(self.icons[slot], x, self.ui.px(18))
            self.coords(self.labels[slot], x+self.ui.px(34), self.ui.px(18))
            self.itemconfigure(self.icons[slot], image=self.photos[f"{kind}-{state}"])
            self.itemconfigure(self.labels[slot], text=title, font=font, fill=self.COLORS[state])
            if self.hover == ("item", index) and not active:
                hover_cell = (left, right)
            if active and self.keyboard_focus:
                focus_cell = (left, right)
        for item, name, cell in ((self.hover_image, "hover", hover_cell), (self.focus_image, "focus", focus_cell)):
            self.itemconfigure(item, state="normal" if cell else "hidden")
            if cell:
                self.coords(item, self.ui.px(cell[0]), 0)
                self.itemconfigure(item, image=self.surface(name, cell[1]-cell[0]))

    def hit(self, x, y):
        x, y = self.ui.logical(x), self.ui.logical(y)
        if not 1 <= y <= self.HEIGHT-1:
            return None
        return next((value for left, right, value in self.hits if left <= x <= right), None)

    def motion(self, event):
        self.set_hover(self.hit(event.x, event.y))

    def set_hover(self, value):
        if value != self.hover:
            self.hover = value
            self.paint()

    def press(self, event):
        self.pressed = self.hit(event.x, event.y)
        self.keyboard_focus = False
        self._pointer_focus = True
        self.focus_set()
        self.paint()

    def click(self, event):
        value, pressed = self.hit(event.x, event.y), self.pressed
        self.pressed = None
        if value is None or value != pressed:
            return
        kind, index = value
        if kind == "page":
            self.start = max(0, min(self.start+index*self.limit, (len(self.items)-1)//self.limit*self.limit))
            self.hover = None
            self.build()
        elif self.items[index][0] != self.selected:
            self.command(self.items[index][0])

    def focus_in(self, _event):
        self.keyboard_focus = not self._pointer_focus
        self._pointer_focus = False
        self.paint()

    def focus_out(self, _event):
        self.pressed, self.keyboard_focus = None, False
        self._pointer_focus = False
        self.paint()

    def key(self, event):
        self.keyboard_focus = True
        self.paint()
        if self.items:
            current = next((i for i, item in enumerate(self.items) if item[0] == self.selected), 0)
            index = {"Home": 0, "End": len(self.items)-1, "Left": max(0, current-1),
                     "Right": min(len(self.items)-1, current+1)}.get(event.keysym, current)
            if index != current:
                self.command(self.items[index][0])
        return "break"

    def sample(self, now):
        progress = max(0, min(1, (now-self.started)/self.DURATION))
        self.position = self.origin+(self.target-self.origin)*(1-(1-progress)**3)
        return progress

    def place_marker(self):
        self.coords(self.marker, self.position*self.ui.factor, self.ui.px(34))

    def animate_to(self, target):
        now = time.perf_counter()
        if self.after_id is not None:
            self.sample(now)
        self.cancel_tick()
        self.origin, self.target, self.started = self.position, target, now
        if abs(self.target-self.origin) < .001:
            self.position = target
            self.place_marker()
            return
        self.tick()

    def tick(self):
        self.after_id = None
        if self.closed:
            return
        progress = self.sample(time.perf_counter())
        self.place_marker()
        if progress < 1:
            self.after_id = self.after(16, self.tick)

    def cancel_tick(self):
        if self.after_id is not None:
            try:
                self.after_cancel(self.after_id)
            except tk.TclError:
                pass
            self.after_id = None

    def close(self):
        self.closed = True
        self.cancel_tick()
