"""A smooth, keyboard-accessible refresh control for the Tk panel."""
from pathlib import Path
import tkinter as tk


class RefreshButton(tk.Canvas):
    WIDTH, HEIGHT = 104, 42
    LABELS = {"idle": "刷新", "loading": "刷新中", "success": "已更新", "error": "重试"}

    def __init__(self, master, command, ui=None):
        self.ui = ui
        super().__init__(master, width=ui.px(self.WIDTH) if ui else self.WIDTH,
                         height=ui.px(self.HEIGHT) if ui else self.HEIGHT,
                         bg="#f3f4f6", highlightthickness=0, takefocus=True,
                         cursor="hand2")
        self.command = command
        self.phase = "idle"
        self._hover = self._pressed = self._focused = False
        self._key_held = False
        self._frame = 0
        self._animation_id = None
        assets = Path(__file__).resolve().parent / "assets"
        self._surfaces = self._load_frames(assets / "refresh-button.png", 104, 42, 12)
        self._symbols = self._load_frames(assets / "refresh-symbols.png", 18, 18, 27)
        self._surface_item = self.create_image(0, 0, anchor="nw")
        self._symbol_item = self.create_image(16, 12, anchor="nw")
        self._label_item = self.create_text(63, 21, fill="white",
                                            font=("Microsoft YaHei UI", 9, "bold"))
        self.bind("<Enter>", self._motion)
        self.bind("<Motion>", self._motion)
        self.bind("<Leave>", self._leave)
        self.bind("<ButtonPress-1>", self._press)
        self.bind("<ButtonRelease-1>", self._release)
        self.bind("<FocusIn>", lambda _e: self._focus(True))
        self.bind("<FocusOut>", lambda _e: self._focus(False))
        for key in ("space", "Return"):
            self.bind(f"<KeyPress-{key}>", self._key_press)
            self.bind(f"<KeyRelease-{key}>", self._key_release)
        self._render()

    def _load_frames(self, path, width, height, count):
        source = tk.PhotoImage(master=self, file=str(path))
        frames = []
        for index in range(count):
            frame = tk.PhotoImage(master=self, width=width, height=height)
            frame.tk.call(str(frame), "copy", str(source), "-from",
                          0, index * height, width, (index + 1) * height, "-to", 0, 0)
            frames.append(frame)
        return frames

    @staticmethod
    def _inside(x, y):
        # Match the capsule itself, excluding its transparent corner padding.
        if not (4 <= x <= 100 and 4 <= y <= 38):
            return False
        center_x = max(21, min(83, x))
        return (x - center_x) ** 2 + (y - 21) ** 2 <= 17 ** 2

    def _motion(self, event):
        hover = self._hit(event.x, event.y)
        if hover != self._hover:
            self._hover = hover
            self._render()

    def _leave(self, _event):
        self._hover = False
        self._render()

    def _focus(self, focused):
        self._focused = focused
        if not focused:
            self._key_held = self._pressed = False
        self._render()

    def _press(self, event):
        if self.phase != "loading" and self._hit(event.x, event.y):
            self.focus_set()
            self._hover = self._pressed = True
            self._render()
        return "break"

    def _release(self, event):
        activate = self._pressed and self._hit(event.x, event.y)
        self._pressed = False
        self._hover = self._hit(event.x, event.y)
        self._render()
        if activate:
            self.invoke()
        return "break"

    def _key_press(self, _event):
        if self.phase != "loading" and not self._key_held:
            self._key_held = self._pressed = True
            self._render()
        return "break"

    def _hit(self, x, y):
        if self.ui:
            x, y = self.ui.logical(x), self.ui.logical(y)
        return self._inside(x, y)

    def _key_release(self, _event):
        activate = self._key_held
        self._key_held = self._pressed = False
        self._render()
        if activate:
            self.invoke()
        return "break"

    def invoke(self):
        if self.phase != "loading":
            self.command()

    def set_state(self, phase):
        if phase not in self.LABELS:
            raise ValueError(f"Unknown refresh state: {phase}")
        self._stop_animation()
        self.phase = phase
        self._pressed = self._key_held = False
        self._frame = 0
        self.configure(cursor="watch" if phase == "loading" else "hand2")
        self._render()
        if phase == "loading":
            self._animation_id = self.after(40, self._animate)

    def _animate(self):
        self._animation_id = None
        if self.phase == "loading":
            self._frame = (self._frame + 1) % 24
            self._render()
            self._animation_id = self.after(40, self._animate)

    def _render(self):
        if self.phase == "loading":
            surface, symbol = 3, 3 + self._frame
        else:
            surface = {"idle": 0, "success": 4, "error": 5}[self.phase]
            symbol = {"idle": 0, "success": 1, "error": 2}[self.phase]
            if self._pressed and (self._hover or self._key_held):
                surface = 2
            elif self._hover and self.phase == "idle":
                surface = 1
        self.itemconfigure(self._surface_item, image=self._surfaces[surface + 6 * self._focused])
        self.itemconfigure(self._symbol_item, image=self._symbols[symbol])
        self.itemconfigure(self._label_item, text=self.LABELS[self.phase],
                           font=("Microsoft YaHei UI", 9, "bold"))
        offset = int(surface == 2)
        self.coords(self._symbol_item, 16, 12 + offset)
        self.coords(self._label_item, 63, 21 + offset)
        if self.ui:
            self.ui.canvas(self)

    def _stop_animation(self):
        if self._animation_id is not None:
            self.after_cancel(self._animation_id)
            self._animation_id = None

    def destroy(self):
        self._stop_animation()
        super().destroy()
