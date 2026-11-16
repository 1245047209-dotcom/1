"""Small canvas tabs for device and view selection, without native popup lists."""
import tkinter as tk

FONT = "Microsoft YaHei UI"


def pill(canvas, x1, y1, x2, y2, radius, fill, outline=""):
    points = (x1+radius, y1, x2-radius, y1, x2, y1, x2, y1+radius,
              x2, y2-radius, x2, y2, x2-radius, y2, x1+radius, y2,
              x1, y2, x1, y2-radius, x1, y1+radius, x1, y1)
    return canvas.create_polygon(points, smooth=True, splinesteps=24,
                                 fill=fill, outline=outline, width=1)


class SegmentedControl(tk.Canvas):
    HEIGHT = 38

    def __init__(self, master, *, ui, command, width, visible=3, dark=False):
        super().__init__(master, width=ui.px(width), height=ui.px(self.HEIGHT),
                         bg="#f3f4f6", highlightthickness=0, takefocus=True, cursor="hand2")
        self.ui, self.command, self.width = ui, command, width
        self.limit, self.dark = visible, dark
        self.items, self.selected = (), None
        self.start, self.hover, self.focused = 0, None, False
        self.hits = []
        self.bind("<Motion>", self.motion)
        self.bind("<Leave>", lambda _e: self.set_hover(None))
        self.bind("<ButtonRelease-1>", self.click)
        self.bind("<FocusIn>", lambda _e: self.focus(True))
        self.bind("<FocusOut>", lambda _e: self.focus(False))
        for key in ("Left", "Right", "Home", "End"):
            self.bind(f"<KeyPress-{key}>", self.key)

    def set_items(self, items, selected):
        items = tuple(items)
        if (items, selected) == (self.items, self.selected):
            return
        self.items, self.selected = items, selected
        selected_index = next((i for i, item in enumerate(items) if item[0] == selected), 0)
        if not self.start <= selected_index < self.start + self.limit:
            self.start = selected_index // self.limit * self.limit
        self.start = min(self.start, max(0, (len(items)-1) // self.limit * self.limit))
        self.hover = None
        self.render()

    def focus(self, focused):
        self.focused = focused
        self.render()

    def set_hover(self, value):
        if value != self.hover:
            self.hover = value
            self.render()

    def hit(self, x, y):
        x, y = self.ui.logical(x), self.ui.logical(y)
        return next((value for x1, x2, value in self.hits if x1 <= x <= x2 and 3 <= y <= self.HEIGHT-3), None)

    def motion(self, event):
        self.set_hover(self.hit(event.x, event.y))

    def click(self, event):
        value = self.hit(event.x, event.y)
        if value is None:
            return
        self.focus_set()
        kind, index = value
        if kind == "page":
            self.start = max(0, min(self.start + index*self.limit, (len(self.items)-1)//self.limit*self.limit))
            self.hover = None
            self.render()
        elif self.items[index][0] != self.selected:
            self.command(self.items[index][0])

    def key(self, event):
        if not self.items:
            return "break"
        current = next((i for i, item in enumerate(self.items) if item[0] == self.selected), 0)
        index = {"Home": 0, "End": len(self.items)-1,
                 "Left": max(0, current-1), "Right": min(len(self.items)-1, current+1)}[event.keysym]
        if index != current:
            self.command(self.items[index][0])
        return "break"

    def icon(self, kind, x, y, color):
        line = lambda *coords: self.create_line(*coords, fill=color, width=1.5, capstyle="round", joinstyle="round")
        if kind == "keyboard":
            pill(self, x, y-6, x+19, y+6, 3, "", color)
            for row in (-2, 2):
                for col in (4, 8, 12, 16):
                    line(x+col-1, y+row, x+col, y+row)
        elif kind == "mouse":
            pill(self, x+3, y-9, x+16, y+9, 6, "", color)
            line(x+9.5, y-8, x+9.5, y-2)
        elif kind in ("beats-studio-buds", "headphones"):
            self.create_arc(x+2, y-8, x+18, y+8, start=0, extent=180, style="arc", outline=color, width=1.5)
            for dx in (1, 14):
                pill(self, x+dx, y-1, x+dx+5, y+8, 2, color)
        elif kind == "all":
            for dx, dy in ((1, -7), (11, -7), (1, 3), (11, 3)):
                pill(self, x+dx, y+dy, x+dx+6, y+dy+6, 1.5, "", color)
        elif kind == "single":
            pill(self, x+2, y-7, x+18, y+7, 3, "", color)
            line(x+7, y-6, x+7, y+6)
        else:
            pill(self, x+3, y-7, x+16, y+7, 3, "", color)

    def render(self):
        self.delete("all")
        self.hits = []
        width, height = self.width, self.HEIGHT
        pill(self, 0.5, 0.5, width-.5, height-.5, 12, "#e9ecf1", "#bdc7d5" if self.focused else "")
        overflow = len(self.items) > self.limit
        left, right = (28, width-28) if overflow else (4, width-4)
        if overflow:
            for x, direction, enabled in ((14, -1, self.start > 0), (width-14, 1, self.start+self.limit < len(self.items))):
                color = "#687485" if enabled else "#c4cbd4"
                self.create_line(x-direction*2, 15, x+direction*2, 19, x-direction*2, 23,
                                 fill=color, width=1.6, capstyle="round", joinstyle="round")
                if enabled:
                    self.hits.append((x-12, x+12, ("page", direction)))
        shown = self.items[self.start:self.start+self.limit]
        cell = (right-left) / max(1, len(shown))
        for slot, (identifier, label, kind) in enumerate(shown):
            index = self.start + slot
            x1, x2 = left+slot*cell, left+(slot+1)*cell
            value = ("item", index)
            active = identifier == self.selected
            if active:
                fill, text = ("#17191d", "white") if self.dark else ("white", "#292e38")
                pill(self, x1, 4, x2, height-4, 8, fill, "" if self.dark else "#d7dde5")
            else:
                text = "#737d8b"
                if self.hover == value:
                    pill(self, x1+1, 4, x2-1, height-4, 8, "#f6f7f9")
            font = (FONT, 9, "bold" if active else "normal")
            measure = lambda title: self.tk.call("font", "measure", font, title)
            title = label
            while len(title) > 1 and measure(title) > cell-52:
                title = title[:-2] + "…"
            text_width = measure(title)
            icon_x = (x1+x2-text_width-28)/2
            self.icon(kind, icon_x, height/2, text)
            self.create_text(icon_x+28, height/2, text=title, anchor="w", fill=text, font=font)
            self.hits.append((x1, x2, value))
        self.ui.canvas(self)
