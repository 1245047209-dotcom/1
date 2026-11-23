"""Round iOS toolbar buttons (tinted circle with a blue glyph)."""
import tkinter as tk

from ios_art import ring_png, rounded_surface_png
import theme
from toggle_switch import capsule_png, glass_capsule_png


class IconButton(tk.Canvas):
    """A keyboard-accessible circular button like the iOS navigation-bar buttons."""

    def __init__(self, master, name, command, *, ui, size=34, label=""):
        self.ui, self.name, self.command, self.label = ui, name, command, label
        self.size = ui.px(size) if ui else size
        self.hover = self.pressed = self.focused = False
        super().__init__(master, width=self.size, height=self.size, bg=master.cget("bg"),
                         highlightthickness=0, bd=0, takefocus=1, cursor="hand2")
        self.backdrop_item = self.create_image(0, 0, anchor="nw")
        self.plate_item = self.create_image(self.size / 2, self.size / 2)
        self.glyph_item = self.create_text(self.size / 2, self.size / 2)
        self.backdrop = None
        self.bind("<Enter>", lambda _e: self._set(hover=True))
        self.bind("<Leave>", lambda _e: self._set(hover=False, pressed=False))
        self.bind("<FocusIn>", lambda _e: self._set(focused=True))
        self.bind("<FocusOut>", lambda _e: self._set(focused=False))
        self.bind("<ButtonPress-1>", lambda _e: self._set(pressed=True))
        self.bind("<ButtonRelease-1>", self._release)
        self.bind("<Return>", lambda _e: self.invoke())
        self.bind("<space>", lambda _e: self.invoke())
        theme.watch(self, self.apply_theme)
        self.redraw()

    def apply_theme(self):
        self.configure(bg=self.master.cget("bg"))
        self.redraw()

    def set_backdrop(self, photo):
        """Frosted-glass pixels behind the button (None: plain background colour)."""
        self.backdrop = photo
        self.itemconfigure(self.backdrop_item, image=photo or "")

    def _set(self, **state):
        for key, value in state.items():
            setattr(self, key, value)
        self.redraw()

    def _release(self, event):
        pressed = self.pressed
        self._set(pressed=False)
        if pressed and 0 <= event.x < self.winfo_width() and 0 <= event.y < self.winfo_height():
            self.invoke()

    def invoke(self):
        if self.command:
            self.command()

    def redraw(self):
        dark = theme.is_dark(self)
        face = theme.color(self, theme.TOOLBAR_FACE)
        if self.pressed and self.hover:
            face = theme.blend(face, theme.color(self, theme.TEXT), .14)
        elif self.hover:
            face = theme.blend(face, theme.color(self, theme.TEXT), .06)
        if self.focused or dark:
            edge = theme.color(self, theme.FOCUS if self.focused else theme.EDGE)
            data = glass_capsule_png(self.size, self.size, face, edge, edge, reflection=0)
        else:
            data = capsule_png(self.size, self.size, face)
        self.plate = tk.PhotoImage(master=self, data=data, format="PNG")
        self.itemconfigure(self.plate_item, image=self.plate)
        text, family = theme.glyph(self, self.name)
        glyph_size = max(8, round(self.size * (.60 if dark else .44)))
        self.itemconfigure(self.glyph_item, text=text, fill=theme.color(self, theme.TOOLBAR_ICON),
                           font=(family, -glyph_size))


def opaque_photo(widget, photo, background):
    """A copy of a Tk photo with its transparency blended onto ``background`` (see ios_art)."""
    data = widget.tk.call(str(photo), "data", "-format", "ppm", "-background", background)
    return tk.PhotoImage(master=widget, data=data, format="PPM")


class TextButton(tk.Label):
    """iOS text button (取消 / 完成 / ‹ 设置 / action rows): tinted text, no plate."""

    def __init__(self, master, text, command, *, font, bg=theme.BACKGROUND, fg=theme.ACCENT, anchor="center"):
        self.command, self._fg = command, fg
        super().__init__(master, text=text, font=font, cursor="hand2", takefocus=1, bd=0, padx=4, pady=2,
                         highlightthickness=1, anchor=anchor)
        theme.style(self, bg=bg, fg=fg, highlightbackground=bg, highlightcolor=theme.FOCUS,
                    activebackground=bg)
        self.bind("<ButtonPress-1>", lambda _e: self.configure(fg=theme.blend(
            theme.color(self, self._fg), theme.color(self, bg), .45)))
        self.bind("<ButtonRelease-1>", self._release)
        self.bind("<Return>", lambda _e: self.invoke())
        self.bind("<space>", lambda _e: self.invoke())

    def _release(self, event):
        self.configure(fg=theme.color(self, self._fg))
        if 0 <= event.x < self.winfo_width() and 0 <= event.y < self.winfo_height():
            self.invoke()

    def invoke(self):
        if self.command:
            self.command()


class SquareIcon(tk.Canvas):
    """The coloured rounded-square icon tile of an iOS settings row."""

    def __init__(self, master, name, swatch, *, p, bg=theme.SURFACE):
        self.name, self.swatch, self.size = name, swatch, p(29)
        self.radius, self.glyph_size = p(7), p(15)
        super().__init__(master, width=self.size, height=self.size, highlightthickness=0, bd=0)
        theme.style(self, bg=bg)
        self.plate_item = self.create_image(0, 0, anchor="nw")
        self.glyph_item = self.create_text(self.size / 2, self.size / 2, fill="#ffffff")
        theme.watch(self, self.redraw)
        self.redraw()

    def redraw(self):
        data = rounded_surface_png(self.size, self.size, self.radius, theme.color(self, self.swatch))
        self.plate = tk.PhotoImage(master=self, data=data, format="PNG")
        self.itemconfigure(self.plate_item, image=self.plate)
        text, family = theme.glyph(self, self.name)
        self.itemconfigure(self.glyph_item, text=text, font=(family, -self.glyph_size))


class Group(tk.Canvas):
    """An iOS inset-grouped list section: antialiased rounded white surface + body frame."""

    def __init__(self, master, *, p, radius=12, expand=False, bg=theme.BACKGROUND, surface=theme.SURFACE):
        self.radius, self.surface_swatch, self.bg_swatch = p(radius), surface, bg
        super().__init__(master, highlightthickness=0, bd=0, width=1, height=1)
        theme.style(self, bg=bg)
        self.body = theme.widget(tk.Frame, self, bg=surface)
        self.body.rows = 0
        # 0.3 r keeps the body's corners inside the rounded outline.
        inset = max(2, -(-self.radius * 3 // 10))
        if expand:
            self.body.place(x=inset, y=inset, relwidth=1, relheight=1, width=-2*inset, height=-2*inset)
        else:
            self.body.pack(fill="x", padx=inset, pady=inset)
        self.bind("<Configure>", lambda _e: self.redraw())
        theme.watch(self, self.redraw)

    def redraw(self):
        width, height = self.winfo_width(), self.winfo_height()
        if min(width, height) < 4:
            return
        data = rounded_surface_png(width, height, self.radius, theme.color(self, self.surface_swatch),
                                   theme.color(self, self.bg_swatch))
        self.surface = tk.PhotoImage(master=self, data=data, format="PNG")
        self.delete("surface")
        self.create_image(0, 0, image=self.surface, anchor="nw", tags="surface")
        self.tag_lower("surface")


def row(dialog, group, title, *, detail=None, icon=None, swatch=None, title_fg=theme.TEXT):
    """One iOS list row; returns the row frame. Controls are placed at its right edge."""
    p = dialog.p
    height = p(58 if detail else 46)
    frame = theme.widget(tk.Frame, group, bg=theme.SURFACE, height=height)
    frame.pack(fill="x")
    frame.pack_propagate(False)
    label_x = p(14)
    if icon:
        SquareIcon(frame, icon, swatch or theme.ACCENT, p=p).place(x=p(12), rely=.5, anchor="w")
        label_x = p(12 + 29 + 12)
    if group.rows:
        theme.widget(tk.Frame, frame, bg=theme.LINE, height=1).place(x=label_x, y=0, relwidth=1,
                                                                      width=-label_x, height=1)
    group.rows += 1
    frame.title = theme.widget(tk.Label, frame, text=title, bg=theme.SURFACE, fg=title_fg,
                               font=dialog.font(15), bd=0, padx=0, pady=0)
    if detail:
        frame.title.place(x=label_x, rely=.5, y=-p(9), anchor="w")
        theme.widget(tk.Label, frame, text=detail, bg=theme.SURFACE, fg=theme.MUTED, font=dialog.font(12),
                     bd=0, padx=0, pady=0).place(x=label_x, rely=.5, y=p(10), anchor="w")
    else:
        frame.title.place(x=label_x, rely=.5, anchor="w")
    return frame


def link_row(dialog, group, title, value, command, *, icon=None, swatch=None, detail=None):
    """A row that opens a sub-page: grey value and a chevron, whole row clickable."""
    frame = row(dialog, group, title, detail=detail, icon=icon, swatch=swatch)
    p = dialog.p
    chevron, family = theme.glyph(frame, "chevron")
    arrow = theme.widget(tk.Label, frame, text=chevron, bg=theme.SURFACE, fg=theme.CHEVRON,
                         font=(family, -p(12)), bd=0)
    arrow.place(relx=1, x=-p(14), rely=.5, anchor="e")
    label = theme.widget(tk.Label, frame, text=value, bg=theme.SURFACE, fg=theme.MUTED,
                         font=dialog.font(15), bd=0)
    label.place(relx=1, x=-p(32), rely=.5, anchor="e")
    frame.configure(takefocus=1, highlightthickness=1, cursor="hand2")
    theme.style(frame, highlightbackground=theme.SURFACE, highlightcolor=theme.FOCUS)
    for target in (frame, *frame.winfo_children()):
        target.configure(cursor="hand2")
        target.bind("<ButtonRelease-1>", lambda _e: command())
    frame.bind("<Return>", lambda _e: command())
    frame.bind("<space>", lambda _e: command())
    frame.invoke = command
    return frame


def action_row(dialog, group, title, command):
    """An iOS action row: the whole title is a blue text button."""
    frame = row(dialog, group, "", title_fg=theme.ACCENT)
    frame.title.destroy()
    frame.title = TextButton(frame, title, command, font=dialog.font(15), bg=theme.SURFACE, anchor="w")
    frame.title.place(x=dialog.p(10), rely=.5, anchor="w")
    return frame


class AppearancePicker(tk.Canvas):
    """iOS Display & Brightness picker: light / dark phone previews with radio checks."""

    MODES = (("light", "浅色", "#f2f2f7", "#ffffff"), ("dark", "深色", "#000000", "#2c2c2e"))

    def __init__(self, master, dialog, selected, command):
        self.p, self.selected, self.command = dialog.p, selected, command
        self.font = dialog.font(13)
        super().__init__(master, height=self.p(158), width=1, highlightthickness=0, bd=0,
                         takefocus=1, cursor="hand2")
        theme.style(self, bg=theme.SURFACE)
        self.photos, self.hits = [], []
        self.focused = self.keyboard = False
        self.bind("<Configure>", lambda _e: self.redraw())
        self.bind("<ButtonRelease-1>", self._click)
        self.bind("<Left>", lambda _e: self._key("light"))
        self.bind("<Right>", lambda _e: self._key("dark"))
        self.bind("<FocusIn>", lambda _e: self._focus(True))
        self.bind("<FocusOut>", lambda _e: self._focus(False))
        self.bind("<<TraverseIn>>", lambda _e: self._key(None))
        theme.watch(self, self.redraw)

    def _key(self, mode):
        self.keyboard = True
        if mode is None:
            self.redraw()
        else:
            self.command(mode)

    def _focus(self, focused):
        self.focused = focused
        if not focused:
            self.keyboard = False
        self.redraw()

    def _image(self, data):
        photo = tk.PhotoImage(master=self, data=data, format="PNG")
        self.photos.append(photo)
        return photo

    def redraw(self):
        width = self.winfo_width()
        if width < 40:
            return
        self.delete("all")
        self.photos, self.hits = [], []
        p = self.p
        preview_w, preview_h, radius, border = p(54), p(88), p(11), max(2, p(2))
        current = self.selected()
        accent, line = theme.color(self, theme.ACCENT), theme.color(self, theme.LINE)
        spacing = p(118)
        for index, (mode, label, background, bar) in enumerate(self.MODES):
            cx = width / 2 + (index - .5) * spacing
            top = p(14)
            chosen = mode == current
            edge = accent if chosen else line
            thickness = border if chosen else 1
            outer = (preview_w + 2*thickness, preview_h + 2*thickness)
            self.create_image(cx, top + preview_h/2 + thickness, image=self._image(
                rounded_surface_png(*outer, radius + thickness, edge)))
            self.create_image(cx, top + preview_h/2 + thickness, image=self._image(
                rounded_surface_png(preview_w, preview_h, radius, background)))
            for row_index in range(3):
                y = top + thickness + p(12) + row_index * p(17)
                self.create_image(cx, y + p(6), image=self._image(
                    rounded_surface_png(preview_w - p(14), p(12), p(4), bar)))
            label_y = top + preview_h + 2*thickness + p(15)
            self.create_text(cx, label_y, text=label, fill=theme.color(self, theme.TEXT), font=self.font)
            radio_y, size = label_y + p(24), p(22)
            if chosen:
                self.create_image(cx, radio_y, image=self._image(rounded_surface_png(size, size, size // 2, accent)))
                text, family = theme.glyph(self, "check")
                self.create_text(cx, radio_y, text=text, fill="#ffffff", font=(family, -p(12)))
            else:
                self.create_image(cx, radio_y, image=self._image(
                    ring_png(size, max(1.2, p(1.5)), None, accent, line)))
            self.hits.append((mode, cx - spacing / 2, cx + spacing / 2))
        if self.focused and self.keyboard:
            self.create_rectangle(1, 1, width - 2, self.winfo_height() - 2,
                                  outline=theme.color(self, theme.FOCUS), dash=(2, 2))

    def _click(self, event):
        self.focus_set()
        mode = next((mode for mode, left, right in self.hits if left <= event.x <= right), None)
        if mode is not None:
            self.command(mode)


def thumb_geometry(height, first, last, pad, minimum):
    """(top, bottom) of a scroll thumb in pixels, or None when the content fits."""
    first, last = max(0., float(first)), min(1., float(last))
    span = max(0., last - first)
    if span >= .999 or height <= 2 * pad:
        return None
    track = height - 2 * pad
    size = min(track, max(minimum, track * span))
    movable = track - size
    top = pad + movable * max(0., min(1., first / (1 - span)))
    return top, top + size


class ThinScrollbar(tk.Canvas):
    """iOS-style scroll indicator: a thin rounded thumb, no track or arrows.

    Same protocol as a Tk scrollbar: ``set(first, last)`` from the scrolled
    widget and ``command("moveto", fraction)`` back to it.
    """

    def __init__(self, master, command, *, p, bg=theme.BACKGROUND):
        self.command, self.p = command, p
        self.first, self.last = 0., 1.
        self.hover = False
        self._grab = None
        super().__init__(master, width=p(12), height=1, highlightthickness=0, bd=0)
        theme.style(self, bg=bg)
        self.bind("<Configure>", lambda _e: self.redraw())
        self.bind("<Enter>", lambda _e: self._set_hover(True))
        self.bind("<Leave>", lambda _e: self._set_hover(False))
        self.bind("<ButtonPress-1>", self._press)
        self.bind("<B1-Motion>", self._motion)
        self.bind("<ButtonRelease-1>", self._release)
        theme.watch(self, self.redraw)

    def set(self, first, last):
        self.first, self.last = float(first), float(last)
        self.redraw()

    def get(self):
        return self.first, self.last

    def _geometry(self):
        return thumb_geometry(self.winfo_height(), self.first, self.last, self.p(3), self.p(28))

    def _set_hover(self, hover):
        self.hover = hover
        self.redraw()

    def _fraction(self, top):
        pad, height = self.p(3), self.winfo_height()
        geometry = self._geometry()
        if geometry is None:
            return 0.
        size = geometry[1] - geometry[0]
        movable = max(1., height - 2 * pad - size)
        span = self.last - self.first
        return max(0., min(1. - span, (top - pad) / movable * (1 - span)))

    def _press(self, event):
        geometry = self._geometry()
        if geometry is None:
            return
        top, bottom = geometry
        if not top <= event.y <= bottom:  # click on the track: centre the thumb there
            top = event.y - (bottom - top) / 2
            self.command("moveto", self._fraction(top))
            geometry = self._geometry() or geometry
        self._grab = event.y - geometry[0]
        self.redraw()

    def _motion(self, event):
        if self._grab is not None:
            self.command("moveto", self._fraction(event.y - self._grab))

    def _release(self, _event):
        self._grab = None
        self.redraw()

    def redraw(self):
        self.delete("all")
        geometry = self._geometry()
        if geometry is None:
            return
        top, bottom = geometry
        active = self.hover or self._grab is not None
        thickness = self.p(7) if active else self.p(5)
        background = theme.color(self, theme.BACKGROUND) if self.cget("bg") == "" else self.cget("bg")
        color = theme.blend(background, theme.color(self, theme.TEXT), .42 if active else .28)
        height = max(thickness, round(bottom - top))
        data = rounded_surface_png(thickness, height, thickness // 2, color, background)
        self.thumb = tk.PhotoImage(master=self, data=data, format="PNG")
        self.create_image(self.winfo_width() / 2, top, image=self.thumb, anchor="n")


class SegmentedControl(tk.Canvas):
    """iOS segmented control: grey track, raised white (dark: grey) segment for the selection."""

    def __init__(self, master, items, selected, command, *, p, font, bold, bg=theme.BACKGROUND):
        self.items, self.selected, self.command, self.p = list(items), selected, command, p
        self.font, self.bold = font, bold
        self.focused = False
        self.keyboard = False  # focus ring only after Tab or arrow keys, never after a click
        super().__init__(master, height=p(32), width=1, highlightthickness=0, bd=0, takefocus=1, cursor="hand2")
        theme.style(self, bg=bg)
        self.bind("<Configure>", lambda _e: self.redraw())
        self.bind("<ButtonRelease-1>", self._click)
        self.bind("<Left>", lambda _e: self._step(-1))
        self.bind("<Right>", lambda _e: self._step(1))
        self.bind("<FocusIn>", lambda _e: self._focus(True))
        self.bind("<FocusOut>", lambda _e: self._focus(False))
        self.bind("<<TraverseIn>>", lambda _e: self._keyboard_focus())
        theme.watch(self, self.redraw)

    @property
    def ring_visible(self):
        return self.focused and self.keyboard

    def _keyboard_focus(self):
        self.keyboard = True
        self.redraw()

    def set_selected(self, key):
        self.selected = key
        self.redraw()

    def _focus(self, focused):
        self.focused = focused
        if not focused:
            self.keyboard = False
        self.redraw()

    def _choose(self, key):
        if key != self.selected:
            self.selected = key
            self.redraw()
            self.command(key)

    def _step(self, direction):
        self.keyboard = True
        keys = [key for key, _label in self.items]
        index = keys.index(self.selected) if self.selected in keys else 0
        self._choose(keys[max(0, min(len(keys) - 1, index + direction))])
        return "break"

    def _click(self, event):
        self.focus_set()
        width = max(1, self.winfo_width())
        index = min(len(self.items) - 1, max(0, int(event.x / width * len(self.items))))
        self._choose(self.items[index][0])

    def redraw(self):
        width, height = self.winfo_width(), self.winfo_height()
        if width < 20 or height < 10:
            return
        self.delete("all")
        p = self.p
        track = theme.color(self, theme.FILL)
        if self.ring_visible:
            self._ring = tk.PhotoImage(master=self, format="PNG", data=rounded_surface_png(
                width, height, p(9), theme.color(self, theme.FOCUS)))
            self.create_image(0, 0, image=self._ring, anchor="nw")
            inset = 2
        else:
            inset = 0
        self._track = tk.PhotoImage(master=self, format="PNG", data=rounded_surface_png(
            width - 2 * inset, height - 2 * inset, p(9), track))
        self.create_image(inset, inset, image=self._track, anchor="nw")
        cell = width / len(self.items)
        for index, (key, label) in enumerate(self.items):
            chosen = key == self.selected
            if chosen:
                pad = inset + 2
                self._thumb = tk.PhotoImage(master=self, format="PNG", data=rounded_surface_png(
                    round(cell - 2 * pad), height - 2 * pad, p(7), theme.color(self, theme.SEGMENT)))
                self.create_image(index * cell + pad, pad, image=self._thumb, anchor="nw")
            self.create_text(index * cell + cell / 2, height / 2, text=label, fill=theme.color(self, theme.TEXT),
                             font=self.bold if chosen else self.font)


class PillButton(tk.Canvas):
    """iOS tinted capsule button (添加 / 更新)."""

    def __init__(self, master, text, command, *, p, font, bg=theme.SURFACE):
        self.text, self.command, self.font, self.p = text, command, font, p
        self.hover = self.pressed = self.focused = False
        probe = tkfont_measure(master, font, text)
        self.w, self.h = probe + p(26), p(28)
        super().__init__(master, width=self.w, height=self.h, highlightthickness=0, bd=0, takefocus=1,
                         cursor="hand2")
        theme.style(self, bg=bg)
        for sequence, state in (("<Enter>", {"hover": True}), ("<Leave>", {"hover": False, "pressed": False}),
                                ("<FocusIn>", {"focused": True}), ("<FocusOut>", {"focused": False}),
                                ("<ButtonPress-1>", {"pressed": True})):
            self.bind(sequence, lambda _e, state=state: self._set(**state))
        self.bind("<ButtonRelease-1>", self._release)
        self.bind("<Return>", lambda _e: self.invoke())
        self.bind("<space>", lambda _e: self.invoke())
        theme.watch(self, self.redraw)
        self.redraw()

    def _set(self, **state):
        for key, value in state.items():
            setattr(self, key, value)
        self.redraw()

    def _release(self, event):
        pressed = self.pressed
        self._set(pressed=False)
        if pressed and 0 <= event.x < self.winfo_width() and 0 <= event.y < self.winfo_height():
            self.invoke()

    def invoke(self):
        if self.command:
            self.command()

    def redraw(self):
        self.delete("all")
        accent, surface = theme.color(self, theme.ACCENT), theme.color(self, theme.SURFACE)
        amount = .26 if self.pressed else .18 if self.hover else .12
        face = theme.blend(surface, accent, amount)
        if self.focused:
            data = glass_capsule_png(self.w, self.h, face, accent, accent, reflection=0)
        else:
            data = capsule_png(self.w, self.h, face)
        self.plate = tk.PhotoImage(master=self, data=data, format="PNG")
        self.create_image(0, 0, image=self.plate, anchor="nw")
        self.create_text(self.w / 2, self.h / 2, text=self.text, fill=accent, font=self.font)


def tkfont_measure(widget, font, text):
    from tkinter import font as tkfont
    return tkfont.Font(root=widget._root(), font=font).measure(text)
