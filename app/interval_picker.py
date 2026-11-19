"""The refresh interval selector and its inline option list."""
from __future__ import annotations

import tkinter as tk
import theme


FONT = "Microsoft YaHei UI"
HOVER = theme.Color("#f1effc", "#302d4b")
HIGHLIGHT = theme.Color("#e0ddfb", "#403967")
MENU_SHADOW = theme.Color("#e3e1f3", "#10131e")
FOCUS_EDGE = theme.Color("#9d99eb", theme.FOCUS.dark)


def _rounded(canvas, x1, y1, x2, y2, radius, **options):
    return canvas.create_polygon(
        x1 + radius, y1, x2 - radius, y1, x2, y1, x2, y1 + radius,
        x2, y2 - radius, x2, y2, x2 - radius, y2, x1 + radius, y2,
        x1, y2, x1, y2 - radius, x1, y1 + radius, x1, y1,
        smooth=True, splinesteps=12, **options,
    )


class IntervalPicker(tk.Canvas):
    def __init__(self, master, overlay, variable, values, *, scale=.85, show_clock=True):
        self.scale = scale
        self.show_clock = show_clock
        self.overlay, self.variable, self.values = overlay, variable, tuple(values)
        self.root = overlay.winfo_toplevel()
        self.default_width = self.p(142)
        self.default_height = self.p(38)
        self.row_height = self.p(34)
        self.popup_width = self.p(154)
        self.padding = self.p(6)
        self.popup = None
        self.outside_binding = None
        self.active = 0
        self.pointer_row = self.pressed_row = None
        self.hovered = self.focused = self.pressed = False
        super().__init__(master, width=self.default_width, height=self.default_height,
                         bg=master.cget("bg"), highlightthickness=0, bd=0,
                         takefocus=1, cursor="hand2")
        self.bind("<Configure>", lambda event: self._draw_trigger())
        self.bind("<Enter>", lambda event: self._hover(True))
        self.bind("<Leave>", lambda event: self._hover(False))
        self.bind("<FocusIn>", self._focus_in)
        self.bind("<FocusOut>", self._focus_out)
        self.bind("<Unmap>", lambda event: self._close())
        self.bind("<Destroy>", self._destroy)
        self.bind("<ButtonPress-1>", self._press)
        self.bind("<ButtonRelease-1>", self._release)
        for sequence in ("Up", "Down", "Home", "End", "Return", "space", "Escape", "Alt-Down"):
            self.bind(f"<{sequence}>", self._key)
        self.trace = variable.trace_add("write", self._value_changed)
        theme.watch(self, self.apply_theme)
        self.apply_theme()

    def p(self, value):
        return round(value * self.scale)

    def apply_theme(self):
        self.configure(bg=self.master.cget("bg"))
        if self.popup is not None:
            self.popup.configure(bg=self.cget("bg"))
        self._value_changed()

    def _draw_trigger(self):
        self.delete("all")
        width = max(self.winfo_width(), self.default_width)
        height = max(self.winfo_height(), self.default_height)
        mid_y = height / 2
        expanded = self.popup is not None
        fill = theme.color(self, theme.SELECTED if expanded else HOVER if self.hovered else theme.SOFT)
        border = theme.color(self, FOCUS_EDGE if self.focused or expanded else theme.LINE)
        _rounded(self, 1, 1, width - 1, height - 1, self.p(12), fill=fill, outline=border)
        if self.show_clock:
            clock_x, radius = self.p(18), self.p(7)
            self.create_oval(clock_x - radius, mid_y - radius, clock_x + radius, mid_y + radius,
                             outline=theme.color(self, theme.ACCENT), width=self.p(1.4))
            self.create_line(clock_x, mid_y - self.p(4), clock_x, mid_y, clock_x + self.p(3), mid_y,
                             fill=theme.color(self, theme.ACCENT), width=self.p(1.4), capstyle="round", joinstyle="round")
        self.create_text(self.p(32 if self.show_clock else 14), mid_y, text=self.variable.get(), anchor="w", fill=theme.color(self, theme.TEXT),
                         font=(FONT, -self.p(14), "bold"))
        arrow_x, half_width = width - self.p(16), self.p(4)
        side_y, center_y = (mid_y + 2, mid_y - 2) if expanded else (mid_y - 2, mid_y + 2)
        self.create_line(arrow_x - half_width, side_y, arrow_x, center_y, arrow_x + half_width, side_y,
                         fill=theme.color(self, theme.ACCENT), width=self.p(1.7), capstyle="round", joinstyle="round")

    def _draw_popup(self):
        popup = self.popup
        popup.delete("all")
        width, height = self.popup_width, self.row_height * len(self.values) + 2 * self.padding
        _rounded(popup, self.p(2), self.p(3), width - 1, height - 1, self.p(12),
                 fill=theme.color(self, MENU_SHADOW), outline="")
        _rounded(popup, 1, 1, width - self.p(3), height - self.p(4), self.p(12),
                 fill=theme.color(self, theme.SURFACE), outline=theme.color(self, theme.LINE))
        highlight = self.active if self.pointer_row is None else self.pointer_row
        for index, value in enumerate(self.values):
            top = self.padding + index * self.row_height
            mid_y = top + self.row_height / 2
            selected = self.variable.get() == value
            if selected or index == highlight:
                _rounded(popup, self.padding, top + 1, width - self.padding - self.p(2),
                         top + self.row_height - 1, self.p(8),
                         fill=theme.color(self, HIGHLIGHT if index == highlight else theme.SOFT), outline="")
            popup.create_text(self.p(15), mid_y, text=value, anchor="w",
                              fill=theme.color(self, theme.ACCENT if selected else theme.TEXT),
                              font=(FONT, -self.p(14), "bold" if selected else "normal"))
            if selected:
                check_x = width - self.p(23)
                popup.create_line(check_x - self.p(4), mid_y, check_x - self.p(1), mid_y + self.p(3),
                                  check_x + self.p(5), mid_y - self.p(4), fill=theme.color(self, theme.ACCENT),
                                  width=self.p(2), capstyle="round", joinstyle="round")

    def _open(self):
        if not self.values or not self.winfo_ismapped():
            return
        self.active = self.values.index(self.variable.get()) if self.variable.get() in self.values else 0
        self.pointer_row = self.pressed_row = None
        self.popup = popup = tk.Canvas(self.overlay, bg=self.cget("background"), bd=0,
                                       highlightthickness=0, takefocus=0, cursor="hand2")
        height = self.row_height * len(self.values) + 2 * self.padding
        x = self.winfo_rootx() - self.overlay.winfo_rootx() + self.winfo_width() - self.popup_width
        trigger_y = self.winfo_rooty() - self.overlay.winfo_rooty()
        y = trigger_y + self.winfo_height() + self.p(5)
        if y + height > self.overlay.winfo_height():
            y = max(0, trigger_y - height - self.p(5))
        popup.place(x=max(0, x), y=y, width=self.popup_width, height=height)
        tk.Misc.tkraise(popup)
        popup.bind("<Motion>", lambda event: self._hover_row(self._row_at(event)))
        popup.bind("<Leave>", lambda event: self._hover_row(None))
        popup.bind("<ButtonPress-1>", self._popup_press)
        popup.bind("<ButtonRelease-1>", self._popup_release)
        self.outside_binding = self.root.bind("<ButtonPress-1>", self._outside_press, add="+")
        self._draw_popup()
        self._draw_trigger()

    def _close(self, redraw=True):
        if self.popup is None:
            return
        popup, self.popup = self.popup, None
        if self.outside_binding is not None:
            self.root.unbind("<ButtonPress-1>", self.outside_binding)
            self.outside_binding = None
        if popup.winfo_exists():
            popup.destroy()
        if redraw:
            self._draw_trigger()

    def _outside_press(self, event):
        if event.widget not in (self, self.popup):
            self._close()

    def _row_at(self, event):
        index = (event.y - self.padding) // self.row_height
        if 0 <= index < len(self.values) and self.padding <= event.x < self.popup_width - self.padding:
            return index
        return None

    def _hover_row(self, row):
        if self.popup is not None and row != self.pointer_row:
            self.pointer_row = row
            self._draw_popup()

    def _popup_press(self, event):
        self.pressed_row = self._row_at(event)
        return "break"

    def _popup_release(self, event):
        row = self._row_at(event)
        pressed, self.pressed_row = self.pressed_row, None
        if row is not None and row == pressed:
            self._select(row)
        return "break"

    def _select(self, index):
        self._close()
        self.variable.set(self.values[index])

    def _press(self, _event):
        self.pressed = True
        self.focus_set()
        return "break"

    def _release(self, event):
        if self.pressed and 0 <= event.x < self.winfo_width() and 0 <= event.y < self.winfo_height():
            if self.popup is None:
                self._open()
            else:
                self._close()
        self.pressed = False
        return "break"

    def _hover(self, hovered):
        self.hovered = hovered
        self._draw_trigger()

    def _focus_in(self, _event):
        self.focused = True
        self._draw_trigger()

    def _focus_out(self, _event):
        self.focused = False
        self._close(redraw=False)
        self._draw_trigger()

    def _key(self, event):
        key = event.keysym
        if key == "Escape":
            if self.popup is None:
                return None
            self._close()
        elif self.popup is None:
            self._open()
        elif key in ("Return", "space"):
            self._select(self.active)
        else:
            if key == "Home":
                self.active = 0
            elif key == "End":
                self.active = len(self.values) - 1
            else:
                direction = 1 if key == "Down" else -1
                self.active = max(0, min(len(self.values) - 1, self.active + direction))
            self.pointer_row = None
            self._draw_popup()
        return "break"

    def _value_changed(self, *_args):
        self._draw_trigger()
        if self.popup is not None:
            self._draw_popup()

    def _destroy(self, _event):
        self._close(redraw=False)
        self.variable.trace_remove("write", self.trace)
