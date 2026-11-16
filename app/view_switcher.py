"""Compact view tabs with cached surfaces and a gently sliding selection."""
from pathlib import Path
import time
import tkinter as tk


class ViewSwitcher(tk.Canvas):
    WIDTH, HEIGHT = 224, 38
    PADDING, GAP, ICON_SIZE = 4, 4, 16
    DURATION = .18
    COLORS = {"active": "#ffffff", "idle": "#7b8695", "hover": "#303947"}

    def __init__(self, master, *, ui, command):
        super().__init__(master, width=ui.px(self.WIDTH), height=ui.px(self.HEIGHT),
                         bg="#ffffff", highlightthickness=0, borderwidth=0,
                         takefocus=True, cursor="hand2")
        self.ui, self.command = ui, command
        self.items, self.selected = (), None
        self.hover = self.pressed = None
        self.keyboard_focus = False
        self._pointer_focus = False
        self.position = self.origin = self.target = 0.0
        self.started, self.after_id, self.closed = 0.0, None, False
        self.labels, self.icons = [], []
        self.fonts = {state: ui.font(("Microsoft YaHei UI", -14,
                                     "bold" if state == "active" else "normal"))
                      for state in self.COLORS}
        self.photos = {}
        directory = Path(__file__).resolve().parent / "assets" / "view-switcher"
        names = ("track", "thumb", "hover", "focus") + tuple(
            f"{kind}-{state}" for kind in ("all", "single") for state in self.COLORS)
        dimensions = {"track": (224, 38), "thumb": (112, 36), "hover": (106, 30), "focus": (224, 38)}
        for name in names:
            source = tk.PhotoImage(master=self, file=str(directory / f"{name}.png"))
            size = tuple(ui.px(value) for value in dimensions.get(name, (16, 16)))
            if (source.width(), source.height()) == size:
                self.photos[name] = source
            else:
                encoded = source.tk.call(str(source), "data", "-format", "png")
                resized = ui.native.resize(encoded, *size)
                self.photos[name] = tk.PhotoImage(master=self, data=resized, format="PNG")
        self.bind("<Motion>", lambda event: self.set_hover(self.hit(event.x, event.y)))
        self.bind("<Leave>", lambda _event: self.set_hover(None))
        self.bind("<ButtonPress-1>", self.press)
        self.bind("<ButtonRelease-1>", self.release)
        self.bind("<FocusIn>", self.focus_in)
        self.bind("<FocusOut>", self.focus_out)
        for key in ("Left", "Right", "Home", "End", "space", "Return"):
            self.bind(f"<KeyPress-{key}>", self.key)
        self.bind("<Destroy>", lambda event: self.close() if event.widget is self else None)

    @property
    def cell_width(self):
        return (self.WIDTH - self.PADDING * 2 - self.GAP) / 2

    def set_items(self, items, selected):
        items = tuple(items)
        if (items, selected) == (self.items, self.selected):
            return
        if len(items) != 2 or selected not in (item[0] for item in items):
            raise ValueError("View switcher requires two items and a valid selection")
        target = next(index for index, item in enumerate(items) if item[0] == selected)
        rebuild = items != self.items
        self.items, self.selected = items, selected
        if rebuild:
            self.cancel_tick()
            self.position = self.origin = self.target = float(target)
            self.build()
        else:
            self.animate_to(target)
        self.update_content()

    def set_selected(self, selected):
        self.set_items(self.items, selected)

    def build(self):
        self._content_key = None
        self.delete("all")
        self.create_image(0, 0, image=self.photos["track"], anchor="nw")
        self.hover_image = self.create_image(0, self.ui.px(4), image=self.photos["hover"],
                                             anchor="nw", state="hidden")
        self.thumb_image = self.create_image(0, self.ui.px(1), image=self.photos["thumb"], anchor="nw")
        self.labels, self.icons = [], []
        for _identifier, label, kind in self.items:
            self.icons.append(self.create_image(0, self.ui.px(19),
                                               image=self.photos[f"{kind}-idle"], anchor="w"))
            self.labels.append(self.create_text(0, self.ui.px(19), text=label, anchor="w",
                                                 font=self.fonts["idle"], fill=self.COLORS["idle"]))
        self.focus_image = self.create_image(0, 0, image=self.photos["focus"], anchor="nw", state="hidden")
        self.place_thumb()

    def update_content(self):
        if not self.items:
            return
        # White content follows the dark surface rather than changing ahead of
        # it, keeping the incoming tab legible while the thumb is travelling.
        states = tuple("active" if abs(self.position-index) <= .28 else
                       "hover" if index == self.hover else "idle" for index in range(len(self.items)))
        content_key = (self.items, self.selected, self.hover, self.keyboard_focus, states)
        if content_key == getattr(self, "_content_key", None):
            return
        self._content_key = content_key
        for index, (identifier, label, kind) in enumerate(self.items):
            state = states[index]
            font = self.fonts["active" if identifier == self.selected else "idle"]
            group_width = self.ui.px(self.ICON_SIZE + 8) + font.measure(label)
            center = self.ui.px(self.PADDING + self.cell_width / 2 + index * (self.cell_width + self.GAP))
            x = center - group_width / 2
            self.coords(self.icons[index], x, self.ui.px(19))
            self.coords(self.labels[index], x + self.ui.px(self.ICON_SIZE + 8), self.ui.px(19))
            self.itemconfigure(self.icons[index], image=self.photos[f"{kind}-{state}"])
            self.itemconfigure(self.labels[index], font=font, fill=self.COLORS[state])
        visible_hover = self.hover is not None and self.items[self.hover][0] != self.selected
        self.itemconfigure(self.hover_image, state="normal" if visible_hover else "hidden")
        if visible_hover:
            x = self.PADDING + self.hover * (self.cell_width + self.GAP)
            self.coords(self.hover_image, self.ui.px(x), self.ui.px(4))
        self.itemconfigure(self.focus_image, state="normal" if self.keyboard_focus else "hidden")

    def place_thumb(self):
        # Only move this existing image per frame. All PNGs and text are reused.
        x = self.PADDING + self.position * (self.cell_width + self.GAP) - 3
        self.coords(self.thumb_image, x * self.ui.factor, self.ui.px(1))

    def sample(self, now):
        progress = min(1.0, max(0.0, (now - self.started) / self.DURATION))
        self.position = self.origin + (self.target - self.origin) * (1 - (1-progress)**3)
        return progress

    def animate_to(self, target):
        now = time.perf_counter()
        if self.after_id is not None:
            self.sample(now)
        self.cancel_tick()
        self.origin, self.target, self.started = self.position, float(target), now
        if abs(self.target - self.origin) < .0001:
            self.position = self.target
            self.place_thumb()
            return
        self.tick()

    def tick(self):
        self.after_id = None
        if self.closed:
            return
        progress = self.sample(time.perf_counter())
        self.place_thumb()
        self.update_content()
        if progress < 1:
            self.after_id = self.after(16, self.tick)

    def cancel_tick(self):
        if self.after_id is not None:
            try:
                self.after_cancel(self.after_id)
            except tk.TclError:
                pass
            self.after_id = None

    def hit(self, x, y):
        if 0 <= x < self.ui.px(self.WIDTH) and 0 <= y < self.ui.px(self.HEIGHT):
            return 0 if x < self.ui.px(self.WIDTH / 2) else 1
        return None

    def set_hover(self, index):
        if index != self.hover:
            self.hover = index
            self.update_content()

    def press(self, event):
        self.pressed = self.hit(event.x, event.y)
        self.keyboard_focus = False
        self._pointer_focus = True
        self.focus_set()
        self.update_content()

    def release(self, event):
        index, pressed = self.hit(event.x, event.y), self.pressed
        self.pressed = None
        if index is not None and index == pressed and self.items[index][0] != self.selected:
            self.command(self.items[index][0])

    def focus_in(self, _event):
        self.keyboard_focus = not self._pointer_focus
        self._pointer_focus = False
        self.update_content()

    def focus_out(self, _event):
        self.pressed, self.keyboard_focus = None, False
        self._pointer_focus = False
        self.update_content()

    def key(self, event):
        self.keyboard_focus = True
        self.update_content()
        if self.items:
            current = next(index for index, item in enumerate(self.items) if item[0] == self.selected)
            index = {"Home": 0, "End": 1, "Left": max(0, current-1),
                     "Right": min(1, current+1)}.get(event.keysym, current)
            if index != current:
                self.command(self.items[index][0])
        return "break"

    def close(self):
        self.closed = True
        self.cancel_tick()
