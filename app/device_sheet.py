"""iOS-style device details sheet over the main window (replaces the old message box)."""
from __future__ import annotations

import sys
import time
import tkinter as tk

import theme
from app_dialogs import DialogMetrics
from device_readers import KEYBOARD_TITLE, Reading
from ios_art import ring_png
from saved_devices import DEFAULT_DEVICE_ID

PARTS = (("left", "左耳"), ("right", "右耳"), ("case", "充电仓"))


def _set_window_pos():
    """user32.SetWindowPos, or None off Windows."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes
        function = ctypes.WinDLL("user32").SetWindowPos
        function.argtypes = (wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                             ctypes.c_int, ctypes.c_int, wintypes.UINT)
        function.restype = wintypes.BOOL
        return function
    except (AttributeError, OSError):
        return None


# SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE. Without SWP_NOREDRAW / SWP_NOCOPYBITS,
# Windows moves the already painted pixels and only asks for the strip that becomes
# visible to be painted. Tk's own placement uses MoveWindow(..., TRUE), which makes every
# label, list and picture in the sheet repaint on every frame: that was the stutter.
SWP_MOVE_ONLY = 0x0001 | 0x0004 | 0x0010


def _runtime_text(reading):
    if not reading.ok:
        return "—"
    if reading.status in ("charging", "充电中"):
        return "充电中"
    if reading.level is None:
        return "—"
    try:
        from battery_runtime import format_remaining, get_runtime_view
        return format_remaining(get_runtime_view(reading.level, charging=False)["hours"], live=True)
    except Exception:  # an estimate must never break the details sheet
        return "—"


def sheet_values(panel, key):
    """Texts and colour roles shown by the sheet for one device (no Tk needed)."""
    device = dict(panel.device_entries()).get(key)
    reading = panel.readings.get(key) or Reading(device.label if device else KEYBOARD_TITLE, "正在读取…")
    return format_sheet_values(panel, key, device, reading, panel.reading_styles(key, reading))


def format_sheet_values(panel, key, device, reading, styles):
    """Build detail data from the reading and styles already used by the overview."""
    from my_devices import component_charging_text, connection_text, product_kind

    level, charging = panel.ring_state(reading)
    low = panel.needs_charge(reading)
    if low:
        status, tone = "电量低，建议尽快充电", theme.DANGER
    elif reading.error:
        status, tone = styles["status"]["text"], theme.WARNING if reading.connected else theme.MUTED
    elif charging:
        status, tone = "充电中", theme.HEALTHY
    elif reading.components and component_charging_text(reading.components):
        # Earbuds: say which part charges ("充电仓充电中"), never a bare "充电中".
        status, tone = component_charging_text(reading.components), theme.HEALTHY
    else:
        status, tone = styles["card_status"]["text"], theme.MUTED
    ago = panel.reading_times.ago(key, reading)
    # The time is a separate grey label, as in the main window's header.
    status_time = " · " + ago if ago and ago != status else ""
    rows = [("connection", "连接方式", connection_text(device)),
            ("power", "供电状态", styles["power"]["text"]),
            ("source", "读数来源", styles["caption"]["text"] or reading.source or "—"),
            ("time", "读取时间", styles["timestamp"]["text"])]
    if key == DEFAULT_DEVICE_ID:
        rows.append(("runtime", "续航", _runtime_text(reading)))
    hardware = getattr(device, "hardware_id", "") if device is not None else ""
    if hardware:
        rows.append(("hardware", "硬件 ID", hardware))
    parts = []
    if product_kind(device) == "beats-studio-buds":
        components = (reading.components or {}) if not reading.error else {}
        for part, label in PARTS:
            text = styles[part + "_level"]["text"]
            if (components.get(part) or {}).get("charging") is True:
                text += " · 充电中"
            parts.append((part, label, text, styles[part + "_time"]["text"]))
    return {"title": device.model if device else "AULA F87 Pro",
            "level": f"{level}%" if level is not None else "--",
            "level_tone": theme.LOW if low else theme.TEXT if level is not None else theme.MUTED,
            "raw_level": level, "charging": charging, "low": low,
            "status": status, "status_tone": tone, "status_time": status_time, "rows": rows, "parts": parts,
            "footer": styles["detail"]["text"]}


class DeviceSheet(DialogMetrics):
    """A large-detent iOS sheet: the dimmed main window peeks out as a strip at the top."""
    GAP = 12
    NAV_TOP, NAV = 16, 44
    DURATION = .24

    def __init__(self, panel, key):
        from ios_controls import Group, TextButton, ThinScrollbar
        self.panel, self.key, self.root = panel, key, panel.root
        self.scale = panel.ui.factor
        self.closed = False
        self.confirm_after = self.anim_after = None
        from frame_pacer import FramePacer
        self.pacer = FramePacer(panel.root)
        self.animating = False
        self.surface_key = self.hero_key = None
        self.surface_renders = 0          # checked by the demo: the sheet surface is drawn once
        self.frame_times = []
        self.frame_log = []       # (gap ms, move ms, overview redraws so far) per frame
        self.sheet_height = None
        self.native_move = None   # SetWindowPos while sliding (Windows)
        self.vars, self.part_names, self.photos = {}, [], []
        self.Group, self.TextButton = Group, TextButton
        self.open_times, mark = {}, time.perf_counter()   # where the time before the slide goes
        values = self.values = sheet_values(panel, key)
        root = self.root

        self.strip = theme.widget(tk.Frame, root, bg=theme.DIM, cursor="hand2")
        self.strip.bind("<ButtonRelease-1>", lambda _e: self.close())
        self.card = theme.widget(tk.Canvas, root, bg=theme.DIM, highlightthickness=0, bd=0, takefocus=1)
        self.card.bind("<Configure>", lambda _e: self.redraw())
        theme.watch(self.card, self.redraw)

        nav = theme.widget(tk.Frame, self.card, bg=theme.SHEET)
        nav.place(x=self.p(18), y=self.p(self.NAV_TOP), relwidth=1, width=-self.p(36), height=self.p(self.NAV))
        self.title_label = theme.widget(tk.Label, nav, text=values["title"], bg=theme.SHEET, fg=theme.TEXT,
                                        font=self.font(15, "bold"), bd=0)
        self.title_label.place(relx=.5, rely=.5, anchor="center")
        self.done_button = TextButton(nav, "完成", self.close, font=self.font(15, "bold"), bg=theme.SHEET)
        self.done_button.place(relx=1, x=self.p(6), rely=.5, anchor="e")

        self.body_top = self.p(self.NAV_TOP + self.NAV)
        self.scroll = theme.widget(tk.Canvas, self.card, bg=theme.SHEET, highlightthickness=0, bd=0,
                                   width=1, height=1, yscrollincrement=self.p(15))
        self.scroll.place(x=0, y=self.body_top, relwidth=1, relheight=1, height=-self.body_top)
        self.scrollbar = ThinScrollbar(self.card, self.scroll.yview, p=self.p, bg=theme.SHEET)
        self.scroll.configure(yscrollcommand=self._scrolled)
        self.body = theme.widget(tk.Frame, self.scroll, bg=theme.SHEET)
        window = self.scroll.create_window(0, 0, anchor="nw", window=self.body)
        self.scroll.bind("<Configure>", lambda e: self.scroll.itemconfigure(window, width=e.width))
        self.body.bind("<Configure>", lambda e: self.scroll.configure(scrollregion=(0, 0, e.width, e.height)))
        self._build(values)
        self.open_times["建控件"] = time.perf_counter() - mark

        self.previous = getattr(root, "_nexus_active_dialog", None)
        root._nexus_active_dialog = self
        if not getattr(root, "_nexus_dialog_escape", None):
            def escape(_event):
                dialog = getattr(root, "_nexus_active_dialog", None)
                if dialog is not None and dialog.winfo_exists():
                    dialog.on_close()
                    return "break"
            root._nexus_dialog_escape = root.bind("<Escape>", escape, add="+")
        self.wheel = root.bind("<MouseWheel>", self._wheel, add="+")
        mark = time.perf_counter()
        self._fit_window()
        self.open_times["加高窗口"] = time.perf_counter() - mark
        self._open()

    # ---- compatibility with OverlayDialog's active-dialog protocol ----------------
    def winfo_exists(self):
        return not self.closed and bool(self.card.winfo_exists())

    def on_close(self):
        self.close()

    def place_forget(self):  # another overlay takes over the window
        self.close()

    def show(self):
        tk.Misc.tkraise(self.card)

    # ---- layout ---------------------------------------------------------------
    def _build(self, values):
        p, Group = self.p, self.Group
        self.hero = theme.widget(tk.Canvas, self.body, bg=theme.SHEET, highlightthickness=0, bd=0,
                                 width=1, height=p(122))
        self.hero.pack(fill="x", pady=(p(4), 0))
        self.hero.bind("<Configure>", lambda _e: self.draw_hero())
        self.vars["level"] = tk.StringVar(self.body, values["level"])
        self.level_label = theme.widget(tk.Label, self.body, textvariable=self.vars["level"], bg=theme.SHEET,
                                        font=(theme.NUM_FONT, -p(40)), bd=0)
        self.level_label.pack(pady=(p(4), 0))
        status_line = theme.widget(tk.Frame, self.body, bg=theme.SHEET)
        status_line.pack(pady=(p(2), p(16)))
        self.vars["status"] = tk.StringVar(self.body, values["status"])
        self.status_label = theme.widget(tk.Label, status_line, textvariable=self.vars["status"], bg=theme.SHEET,
                                         font=self.font(13), bd=0, padx=0)
        self.status_label.pack(side="left")
        self.vars["status_time"] = tk.StringVar(self.body, values["status_time"])
        self.status_time_label = theme.widget(tk.Label, status_line, textvariable=self.vars["status_time"],
                                              bg=theme.SHEET, fg=theme.MUTED, font=self.font(13), bd=0, padx=0)
        self.status_time_label.pack(side="left")

        group = Group(self.body, p=p, bg=theme.SHEET, surface=theme.SHEET_GROUP)
        group.pack(fill="x", padx=p(16))
        for name, title, value in values["rows"]:
            self._value_row(group.body, name, title, value)
        if values["parts"]:
            self._section("耳机电量")
            group = Group(self.body, p=p, bg=theme.SHEET, surface=theme.SHEET_GROUP)
            group.pack(fill="x", padx=p(16))
            for part, label, text, when in values["parts"]:
                self._value_row(group.body, part, label, text, detail=when)
                self.part_names.append(part)
        self.vars["footer"] = tk.StringVar(self.body, values["footer"])
        footer = theme.widget(tk.Label, self.body, textvariable=self.vars["footer"], bg=theme.SHEET,
                              fg=theme.MUTED, font=self.font(12), anchor="w", justify="left", bd=0)
        footer.pack(fill="x", padx=p(30), pady=(p(7), 0))
        footer.bind("<Configure>", lambda e: footer.configure(wraplength=max(1, e.width)))

        group = Group(self.body, p=p, bg=theme.SHEET, surface=theme.SHEET_GROUP)
        group.pack(fill="x", padx=p(16), pady=(p(22), 0))
        self.refresh_button = self._action_row(group.body, "立即读取", self.read_now, theme.ACCENT, "w")
        group = Group(self.body, p=p, bg=theme.SHEET, surface=theme.SHEET_GROUP)
        group.pack(fill="x", padx=p(16), pady=(p(22), p(26)))
        self.remove_button = self._action_row(group.body, "移除设备", self.remove, theme.LOW, "center")
        self._busy_state()

    def _section(self, title):
        theme.widget(tk.Label, self.body, text=title, bg=theme.SHEET, fg=theme.MUTED, font=self.font(12.5),
                     bd=0).pack(anchor="w", padx=self.p(30), pady=(self.p(18), self.p(6)))

    def _value_row(self, body, name, title, value, *, detail=None):
        p = self.p
        frame = theme.widget(tk.Frame, body, bg=theme.SHEET_GROUP, height=p(54 if detail else 44))
        frame.pack(fill="x")
        frame.pack_propagate(False)
        if body.rows:
            theme.widget(tk.Frame, frame, bg=theme.SHEET_LINE, height=1).place(
                x=p(16), y=0, relwidth=1, width=-p(16), height=1)
        body.rows += 1
        title_label = theme.widget(tk.Label, frame, text=title, bg=theme.SHEET_GROUP, fg=theme.TEXT,
                                   font=self.font(15), bd=0)
        self.vars[name] = tk.StringVar(frame, value)
        theme.widget(tk.Label, frame, textvariable=self.vars[name], bg=theme.SHEET_GROUP, fg=theme.MUTED,
                     font=self.font(15), bd=0).place(relx=1, x=-p(16), rely=.5, anchor="e")
        if detail is None:
            title_label.place(x=p(16), rely=.5, anchor="w")
            return
        title_label.place(x=p(16), rely=.5, y=-p(9), anchor="w")
        self.vars[name + "_time"] = tk.StringVar(frame, detail)
        theme.widget(tk.Label, frame, textvariable=self.vars[name + "_time"], bg=theme.SHEET_GROUP,
                     fg=theme.MUTED, font=self.font(12), bd=0).place(x=p(16), rely=.5, y=p(10), anchor="w")

    def _action_row(self, body, text, command, fg, anchor):
        frame = theme.widget(tk.Frame, body, bg=theme.SHEET_GROUP, height=self.p(44))
        frame.pack(fill="x")
        frame.pack_propagate(False)
        button = self.TextButton(frame, text, command, font=self.font(15), bg=theme.SHEET_GROUP, fg=fg)
        if anchor == "center":
            button.place(relx=.5, rely=.5, anchor="center")
        else:
            button.place(x=self.p(12), rely=.5, anchor="w")
        frame.configure(cursor="hand2")
        frame.bind("<ButtonRelease-1>", lambda _e: button.invoke())
        return button

    def redraw(self):
        from ios_art import rounded_surface_png
        if self.closed:
            return
        width, height = self.card.winfo_width(), self.card.winfo_height()
        if width < 4 or height < 4:
            return
        key = (width, height, theme.is_dark(self.card))
        if key == self.surface_key:
            return  # moving the sheet must not re-render its surface
        self.surface_key = key
        self.surface_renders += 1
        radius = self.p(12)
        self.card.delete("chrome")
        # The canvas behind the corners is DIM, so blending onto DIM looks identical.
        self._surface = tk.PhotoImage(master=self.card, format="PNG", data=rounded_surface_png(
            width, height + radius, radius, theme.color(self.card, theme.SHEET), theme.color(self.card, theme.DIM)))
        self.card.create_image(0, 0, image=self._surface, anchor="nw", tags="chrome")
        grab_w, grab_h = self.p(36), max(4, self.p(5))
        self._grabber = tk.PhotoImage(master=self.card, format="PNG", data=rounded_surface_png(
            grab_w, grab_h, grab_h // 2, theme.color(self.card, theme.SHEET_LINE), theme.color(self.card, theme.SHEET)))
        self.card.create_image(width / 2, self.p(7), image=self._grabber, anchor="n", tags="chrome")

    def draw_hero(self, force=False):
        from my_devices import product_image_kind
        if self.closed:
            return
        canvas = self.hero
        width, height = canvas.winfo_width(), canvas.winfo_height()
        if width < 10:
            return
        key = (width, height, theme.is_dark(canvas))
        if key == self.hero_key and not force:
            return
        self.hero_key = key
        canvas.delete("all")
        device = dict(self.panel.device_entries()).get(self.key)
        photo = self.panel.fitted_product(product_image_kind(device), 220, 118)
        self.hero_has_photo = photo is not None
        if photo is not None:
            from ios_controls import opaque_photo
            physical = self.panel.ui.images.get(str(photo)) or self.panel.ui.image(str(photo))
            flat = opaque_photo(canvas, physical, theme.color(canvas, theme.SHEET))
            self.photos = [flat]
            canvas.create_image(width / 2, height / 2, image=flat)
            return
        values, p = self.values, self.p
        size, stroke = p(96), max(4, p(9))
        level = values["raw_level"]
        progress = theme.color(canvas, theme.LOW if values["low"] else theme.RING)
        badge = (theme.color(canvas, theme.SHEET), progress, "#ffffff") if values["charging"] else None
        from ios_controls import opaque_photo
        ring = opaque_photo(canvas, tk.PhotoImage(master=canvas, format="PNG", data=ring_png(
            size, stroke, None if level is None else int(level), progress,
            theme.color(canvas, theme.RING_TRACK), badge)), theme.color(canvas, theme.SHEET))
        self.photos = [ring]
        canvas.create_image(width / 2, height / 2, image=ring)
        from my_devices import is_bluetooth
        text, family = theme.glyph(canvas, "bluetooth" if is_bluetooth(device) else "usb")
        canvas.create_text(width / 2, height / 2, text=text, fill=theme.color(canvas, theme.TEXT),
                           font=(family, -p(26)))

    def _fit_window(self):
        """The window keeps its size; a taller sheet scrolls inside it (as on iOS).

        Growing the window used to repaint the whole main window in the middle of the
        slide-in, which was the one long frame (about 80 ms) that made it stutter.
        """
        self.draws_at_open = getattr(self.panel, "draw_count", 0)

    def _scrolled(self, first, last):
        bar = self.scrollbar
        bar.set(first, last)
        fits = float(first) <= 0 and float(last) >= 1
        if fits:
            bar.place_forget()
        elif not bar.winfo_manager():
            bar.place(relx=1, x=-self.p(2), y=self.body_top, anchor="ne", relheight=1,
                      height=-self.body_top - self.p(6))

    def _wheel(self, event):
        if self.closed or not str(event.widget).startswith(str(self.card)):
            return None
        first, last = self.scroll.yview()
        if first > 0 or last < 1:
            self.scroll.yview_scroll(-int(event.delta / 120) * 2, "units")
        return "break"

    # ---- motion -----------------------------------------------------------------
    def _move(self, y):
        """Slide only: the sheet keeps its final size, so nothing inside is laid out again."""
        y = round(y)
        if self.native_move is not None:
            if self.native_move(self.card.winfo_id(), None, 0, y, 0, 0, SWP_MOVE_ONLY):
                return
            self.native_move = None  # fall back to Tk for the rest of this animation
        self.card.place_configure(y=y)

    def _settle(self):
        """Final position; from now on the sheet follows the window's size."""
        top = self.p(self.GAP)
        self.card.place(x=0, y=top, relwidth=1, relheight=1, height=-top)

    def _open(self):
        root = self.root
        top, bottom = self.p(self.GAP), max(1, root.winfo_height())
        self.strip.place(x=0, y=0, relwidth=1, height=top)
        self.strip.tkraise()
        animate = bool(root.winfo_viewable()) and bottom > top
        # Lay the sheet out once, at its final size and below the window, and render it
        # completely before the first frame.
        self.sheet_height = max(1, bottom - top)
        self.card.place(x=0, y=bottom if animate else top, relwidth=1, height=self.sheet_height)
        tk.Misc.tkraise(self.card)
        mark = time.perf_counter()
        root.update_idletasks()
        self.open_times["排版"] = time.perf_counter() - mark
        mark = time.perf_counter()
        self.redraw()
        self.open_times["画背景"] = time.perf_counter() - mark
        mark = time.perf_counter()
        self.draw_hero()
        self.open_times["画产品图"] = time.perf_counter() - mark
        self.card.focus_set()
        if not animate:
            self._settle()
            return
        self.animating = True
        self.native_move = _set_window_pos()
        self.started, self.start_y, self.last_frame = time.perf_counter(), bottom, None
        self.pacer.start()
        self._step()

    def _step(self):
        self.anim_after = None
        if self.closed:
            return
        now = time.perf_counter()
        gap = None
        if self.last_frame is not None:
            gap = now - self.last_frame
            self.frame_times.append(gap)
        self.last_frame = now
        progress = min(1., (now - self.started) / self.DURATION)
        eased = 1 - (1 - progress) ** 3
        top = self.p(self.GAP)
        self._move(self.start_y + (top - self.start_y) * eased)
        if gap is not None:
            self.frame_log.append((gap * 1000, (time.perf_counter() - now) * 1000,
                                   getattr(self.panel, "draw_count", 0) - self.draws_at_open))
        if progress < 1:
            self.anim_after = self.pacer.after(self._step)
            return
        self.animating = False
        self.pacer.stop()
        self.native_move = None
        # Tk still records the start position; this moves nothing on screen (the window is
        # already there), it only brings Tk's geometry up to date.
        self._settle()

    # ---- data -------------------------------------------------------------------
    def refresh(self):
        if self.closed:
            return
        if self.key not in dict(self.panel.device_entries()):
            self.close()
            return
        values = sheet_values(self.panel, self.key)
        changed_ring = (values["raw_level"], values["low"], values["charging"]) != (
            self.values["raw_level"], self.values["low"], self.values["charging"])
        self.values = values
        for name in ("level", "status", "status_time", "footer"):
            self._set(name, values[name])
        for name, _title, value in values["rows"]:
            self._set(name, value)
        for part, _label, text, when in values["parts"]:
            self._set(part, text)
            self._set(part + "_time", when)
        self.level_label.configure(fg=theme.color(self.card, values["level_tone"]))
        self.status_label.configure(fg=theme.color(self.card, values["status_tone"]))
        self._busy_state()
        if changed_ring and not getattr(self, "hero_has_photo", True):
            self.draw_hero(force=True)

    def _set(self, name, value):
        variable = self.vars.get(name)
        if variable is not None and variable.get() != value:
            variable.set(value)

    def _busy_state(self):
        busy = bool(getattr(self.panel, "busy", False))
        self.refresh_button._fg = theme.MUTED if busy else theme.ACCENT
        self.refresh_button.configure(text="正在读取…" if busy else "立即读取",
                                      fg=theme.color(self.card, self.refresh_button._fg))
        if not hasattr(self, "_toned"):
            self._toned = True
            self.level_label.configure(fg=theme.color(self.card, self.values["level_tone"]))
            self.status_label.configure(fg=theme.color(self.card, self.values["status_tone"]))

    def read_now(self):
        if not getattr(self.panel, "busy", False):
            self.panel.refresh()
        self._busy_state()

    def remove(self):
        if self.confirm_after is None:
            self.remove_button.configure(text="再点一次以移除设备", font=self.font(15, "bold"))
            self.confirm_after = self.card.after(3000, self._reset_remove)
            return
        key = self.key
        self.close()
        self.panel.remove_device(key)

    def _reset_remove(self):
        self.confirm_after = None
        if not self.closed:
            self.remove_button.configure(text="移除设备", font=self.font(15))

    def close(self, redraw=True):
        if self.closed:
            return
        self.closed = True
        self.animating = False
        self.pacer.stop()
        root, panel = self.root, self.panel
        for timer in (self.anim_after, self.confirm_after):
            if timer is not None:
                try:
                    self.card.after_cancel(timer)
                except tk.TclError:
                    pass
        if getattr(root, "_nexus_active_dialog", None) is self:
            previous = self.previous
            alive = previous is not None and previous is not self and previous.winfo_exists()
            root._nexus_active_dialog = previous if alive else None
        try:
            root.unbind("<MouseWheel>", self.wheel)
        except tk.TclError:
            pass
        for widget in (self.card, self.strip):
            try:
                widget.destroy()
            except tk.TclError:
                pass
        if getattr(panel, "device_sheet", None) is self:
            panel.device_sheet = None
        if getattr(panel, "sheet_min_height", 0):
            panel.sheet_min_height = 0
            if redraw:
                if getattr(panel, "draw_count", 0) != getattr(self, "draws_at_open", None):
                    panel.draw()               # the overview was rebuilt for the taller window
                else:
                    panel.apply_sheet_height()  # untouched overview: just shrink back
        try:
            root.focus_set()
        except tk.TclError:
            pass
