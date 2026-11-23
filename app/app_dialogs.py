"""Settings and update cards hosted inside the Nexus window."""
from __future__ import annotations

from pathlib import Path
import tkinter as tk
from tkinter import ttk
from interval_picker import IntervalPicker
from search_window import attach_dialog_owner, bind_window_drag, match_owner_size
from toggle_switch import Switch, capsule_png, glass_capsule_png
from ui_scale import UIScale
import theme

SETTINGS_ICONS = ("clock", "refresh", "screen", "battery", "motion", "appearance", "update")


def brand_mark(parent, ui):
    size = ui.px(42)
    photo = ui.load_asset(Path(__file__).with_name("assets") / "nexus-chrome.png", (size, size))
    return theme.widget(tk.Label, parent, image=photo, bg=theme.BACKGROUND, bd=0)


def rounded(canvas, x1, y1, x2, y2, radius, **options):
    r = min(radius, (x2 - x1) / 2, (y2 - y1) / 2)
    return canvas.create_polygon(
        x1+r, y1, x2-r, y1, x2, y1, x2, y1+r,
        x2, y2-r, x2, y2, x2-r, y2, x1+r, y2,
        x1, y2, x1, y2-r, x1, y1+r, x1, y1,
        smooth=True, splinesteps=24, **options)


class GlassPanel(tk.Canvas):
    """A rounded surface with an inset area for native controls."""
    def __init__(self, master, *, scale, fit_content=False):
        self.scale = scale
        super().__init__(master, width=1, height=1, bg=theme.color(master, theme.BACKGROUND),
                         highlightthickness=0, bd=0)
        self.body = theme.widget(tk.Frame, self, bg=theme.SURFACE)
        inset = round(8 * scale)
        if fit_content:
            self.body.pack(fill="x", padx=inset, pady=(inset, inset+1))
        else:
            self.body.place(x=inset, y=inset, relwidth=1, relheight=1,
                            width=-2*inset, height=-2*inset-1)
        theme.style(self, bg=theme.BACKGROUND)
        theme.watch(self, self.redraw)
        self.bind("<Configure>", self.redraw)

    def redraw(self, _event=None):
        from ios_art import rounded_surface_png

        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        if min(w, h) < 10:
            return
        radius = round(14 * self.scale)
        self._surface = tk.PhotoImage(master=self, format="PNG",
                                      data=rounded_surface_png(w, h, radius, theme.color(self, theme.SURFACE)))
        self.create_image(0, 0, image=self._surface, anchor="nw")


class Button(tk.Canvas):
    def __init__(self, master, text, command, *, scale=.85, width=104, height=38, primary=False, background=None):
        self.scale, self.text, self.command = scale, text, command
        self.primary, self.disabled, self.hover, self.focused = primary, False, False, False
        self.pressed = False
        self.default_width, self.default_height = round(width * scale), round(height * scale)
        bg = theme.color(master, background) if background is not None else master.cget("bg")
        super().__init__(master, width=self.default_width, height=self.default_height, bg=bg,
                         highlightthickness=0, bd=0, takefocus=1, cursor="hand2")
        self.bind("<Configure>", lambda e: self.redraw())
        self.bind("<Enter>", lambda e: self._hover(True))
        self.bind("<Leave>", lambda e: self._hover(False))
        self.bind("<FocusIn>", lambda e: self._focus(True))
        self.bind("<FocusOut>", lambda e: self._focus(False))
        self.bind("<ButtonPress-1>", self._press)
        self.bind("<ButtonRelease-1>", self._release)
        self.bind("<Return>", lambda e: self.invoke())
        self.bind("<space>", lambda e: self.invoke())
        if background is None:
            theme.watch(self, lambda: self.configure(bg=self.master.cget("bg")))
        else:
            theme.style(self, bg=background)
        self.redraw()

    def _hover(self, value):
        self.hover = value
        self.redraw()

    def _focus(self, value):
        self.focused = value
        self.redraw()

    def _press(self, _event):
        if not self.disabled:
            self.pressed = True
            self.redraw()

    def _release(self, event):
        pressed, self.pressed = self.pressed, False
        self.redraw()
        if pressed and 0 <= event.x < self.winfo_width() and 0 <= event.y < self.winfo_height():
            self.invoke()

    def invoke(self):
        if not self.disabled and self.command:
            self.command()

    def configure(self, cnf=None, **options):
        values = dict(cnf or {}, **options)
        if "text" in values:
            self.text = values.pop("text")
        if "command" in values:
            self.command = values.pop("command")
        if "state" in values:
            self.disabled = values.pop("state") == "disabled"
            if self.disabled:
                self.pressed = False
            super().configure(cursor="arrow" if self.disabled else "hand2")
        if values:
            super().configure(**values)
        if hasattr(self, "_w"):
            self.redraw()

    config = configure

    def redraw(self):
        self.delete("all")
        width = self.winfo_width() if self.winfo_width() > 1 else self.default_width
        height = self.winfo_height() if self.winfo_height() > 1 else self.default_height
        background = theme.control_background(self)
        if self.disabled:
            label = theme.DISABLED
            fill = theme.color(self, theme.DISABLED_SURFACE) if self.primary else background
        elif self.primary:
            label = theme.ON_PRIMARY
            fill = theme.color(self, theme.ACTION_HOVER if self.hover else theme.ACTION)
            if self.pressed and self.hover:
                fill = theme.blend(fill, "#000000", .16)
        else:
            label = theme.TEXT
            amount = (.12 if self.pressed else .06) if self.hover else 0
            fill = theme.blend(background, theme.color(self, label), amount)
        if fill != background or self.focused:
            plate_width, plate_height = max(4, width-2), max(4, height-2)
            if self.focused:
                edge = theme.color(self, theme.FOCUS)
                data = glass_capsule_png(plate_width, plate_height, fill, edge, edge, reflection=0)
            else:
                data = capsule_png(plate_width, plate_height, fill)
            self.plate = tk.PhotoImage(master=self, format="png", data=data)
            self.create_image(width/2, height/2, image=self.plate)
        self.create_text(width/2, height/2, text=self.text, fill=theme.color(self, label),
                         font=(theme.FONT, -round(15*self.scale)))


class CloseButton(Button):
    def __init__(self, master, command, *, scale=.85):
        super().__init__(master, "", command, scale=scale, width=42, height=42)

    def redraw(self):
        self.delete("all")
        width = max(self.default_width, self.winfo_width())
        height = max(self.default_height, self.winfo_height())
        cx, cy = width / 2, height / 2
        radius = min(width, height) / 2 - 3
        if self.focused:
            self.create_oval(cx-radius-2, cy-radius-2, cx+radius+2, cy+radius+2,
                             fill="", outline=theme.color(self, theme.FOCUS), width=1)
        if self.hover and not self.disabled:
            diameter = round(radius * 2)
            fill = theme.blend(theme.control_background(self), theme.color(self, theme.TEXT),
                               .12 if self.pressed else .06)
            self.plate = tk.PhotoImage(master=self, format="png", data=capsule_png(diameter, diameter, fill))
            self.create_image(cx, cy, image=self.plate)
        role = theme.DISABLED if self.disabled else theme.TEXT if self.hover else theme.MUTED
        color = theme.color(self, role)
        arm = 5 * self.scale
        for direction in (-1, 1):
            self.create_line(cx-arm, cy-direction*arm, cx+arm, cy+direction*arm,
                             fill=color, width=1.8*self.scale, capstyle=tk.ROUND)


class Progress(tk.Canvas):
    def __init__(self, master, *, scale=.85):
        self.value, self.scale = 0, scale
        super().__init__(master, bg=master.cget("bg"), height=round(7*scale), bd=0, highlightthickness=0)
        self.bind("<Configure>", lambda e: self.redraw())
        theme.watch(self, self.apply_theme)

    def apply_theme(self):
        self.configure(bg=self.master.cget("bg"))
        self.redraw()

    def __setitem__(self, key, value):
        if key == "value":
            self.value = max(0, min(100, float(value)))
            self.redraw()
        else:
            super().__setitem__(key, value)

    def redraw(self):
        self.delete("all")
        width, height = max(2, self.winfo_width()), max(4, round(7*self.scale))
        track = theme.color(self, theme.LINE)
        rounded(self, 0, 0, width, height, height/2, fill=track, outline=track)
        if self.value:
            fill = theme.color(self, theme.ACCENT)
            rounded(self, 0, 0, max(height, width*self.value/100), height, height/2, fill=fill, outline=fill)


class DialogMetrics:
    def p(self, value):
        return round(value*self.scale)

    def font(self, size, weight="normal"):
        return (theme.FONT, -self.p(size), weight)


class OverlayDialog(DialogMetrics, tk.Canvas):
    """A child widget, so it cannot detach from or outlive its main window."""
    def __init__(self, root, title, subtitle, *, width=820, height=590, on_close=None):
        self.root = root
        import tray_common as tc
        theme.install(root._root(), tc.load_config().get("theme"))
        self.scale = getattr(getattr(root, "_nexus_ui", None), "factor", .85)
        self.card_width, self.card_height = width, height
        self.on_close = on_close or self.destroy
        self.previous = getattr(root, "_nexus_active_dialog", None)
        super().__init__(root, highlightthickness=0, bd=0, takefocus=1)
        theme.style(self, bg=theme.BACKGROUND)
        theme.watch(self, self._layout)
        self.inner = theme.widget(tk.Frame, self, bg=theme.SURFACE)
        self.bind("<Configure>", self._layout)
        if not getattr(root, "_nexus_dialog_escape", None):
            def escape(event):
                dialog = getattr(root, "_nexus_active_dialog", None)
                if dialog is not None and dialog.winfo_exists():
                    dialog.on_close()
                    return "break"
            root._nexus_dialog_escape = root.bind("<Escape>", escape, add="+")
        self.header = theme.widget(tk.Frame, self.inner, bg=theme.SURFACE)
        self.header.pack(fill="x", pady=(0, self.p(16)))
        badge = theme.widget(tk.Label, self.header, text="N", bg=theme.SELECTED, fg=theme.ACCENT, font=self.font(21, "bold"),
                         width=2, padx=self.p(3), pady=self.p(4))
        badge.pack(side="left", padx=(0, self.p(12)))
        heading = theme.widget(tk.Frame, self.header, bg=theme.SURFACE)
        heading.pack(side="left", fill="x", expand=True)
        theme.widget(tk.Label, heading, text=title, bg=theme.SURFACE, fg=theme.TEXT, font=self.font(25, "bold")).pack(anchor="w")
        theme.widget(tk.Label, heading, text=subtitle, bg=theme.SURFACE, fg=theme.MUTED, font=self.font(13)).pack(anchor="w", pady=(self.p(3), 0))
        self.close_button = CloseButton(self.header, self.on_close, scale=self.scale)
        self.close_button.pack(side="right", anchor="n")
        self.show()

    def _layout(self, event=None):
        width = self.winfo_width() if self.winfo_width() > 1 else 952
        height = self.winfo_height() if self.winfo_height() > 1 else 553
        cw, ch = min(self.p(self.card_width), width-28), min(self.p(self.card_height), height-24)
        left, top = (width-cw)/2, (height-ch)/2
        self.card_bounds = (left, top, cw, ch)
        self.delete("surface")
        rounded(self, left+2, top+5, left+cw+2, top+ch+5, self.p(22),
                fill=theme.color(self, theme.SHADOW), outline="", tags="surface")
        rounded(self, left, top, left+cw, top+ch, self.p(22),
                fill=theme.color(self, theme.SURFACE), outline=theme.color(self, theme.LINE), tags="surface")
        self.inner.place(x=round(left)+self.p(26), y=round(top)+self.p(24),
                         width=cw-self.p(52), height=ch-self.p(48))

    def show(self):
        active = getattr(self.root, "_nexus_active_dialog", None)
        if active is not None and active is not self and active.winfo_exists():
            active.place_forget()
        self.root._nexus_active_dialog = self
        self.place(x=0, y=0, relwidth=1, relheight=1)
        tk.Misc.tkraise(self)
        self._layout()
        self.focus_set()

    def destroy(self):
        active = getattr(self.root, "_nexus_active_dialog", None)
        previous = self.previous
        if active is self:
            self.root._nexus_active_dialog = None
        super().destroy()
        if active is self and not getattr(self.root, "_nexus_closing", False) and previous is not None:
            try:
                if previous.winfo_exists():
                    previous.show()
            except tk.TclError:
                pass


def setting_icon(dialog, row, symbol, bg):
    size = dialog.p(32)
    index = SETTINGS_ICONS.index(symbol)
    atlas_path = Path(__file__).with_name("assets") / "settings-icons.png"
    icon = theme.widget(tk.Label, row, bg=bg, bd=0, highlightthickness=0, padx=0, pady=0)
    icon.place(x=dialog.p(7), rely=.5, anchor="w")

    def draw():
        atlas = dialog._nexus_ui.load_asset(atlas_path, (size * len(SETTINGS_ICONS), size))
        photo = tk.PhotoImage(master=icon, width=size, height=size)
        left = index * size
        icon.tk.call(str(photo), "copy", str(atlas), "-from", left, 0, left + size, size)
        icon.configure(image=photo)
        icon.photo = photo

    theme.watch(icon, draw)
    draw()


def setting_row(dialog, master, title, description, *, height=53, bg=theme.SURFACE,
                separator=True, icon=None):
    row = theme.widget(tk.Frame, master, bg=bg)
    row.pack(fill="x")
    half = dialog.p(height / 2)
    row.rowconfigure(0, minsize=half)
    row.rowconfigure(1, minsize=dialog.p(height) - half)
    if icon:
        setting_icon(dialog, row, icon, bg)
    label_x = dialog.p(48 if icon else 7)
    title_label = theme.widget(tk.Label, row, text=title, bg=bg, fg=theme.TEXT,
                               font=dialog.font(19, "bold"), bd=0, padx=2, pady=0)
    title_label.grid(row=0, column=0, sticky="sw", padx=(label_x, 0))
    detail_label = theme.widget(tk.Label, row, text=description, bg=bg, fg=theme.MUTED,
                                font=dialog.font(16), bd=0, padx=2, pady=0)
    detail_label.grid(row=1, column=0, sticky="nw", padx=(label_x, 0))
    if separator:
        inset = dialog.p(7)
        theme.widget(tk.Frame, row, bg=theme.LINE).place(x=label_x, rely=1, y=-1, relwidth=1,
                                                       width=-label_x-inset, height=1)
    return row


class SettingsDialog(DialogMetrics, tk.Toplevel):
    """iOS-style settings sheet: 取消 / 设置 / 完成, inset-grouped lists, live theme preview."""

    def __init__(self, panel, manager=None):
        import tray_common as tc
        from update_client import current_version
        from ios_controls import AppearancePicker, Group, TextButton, action_row, link_row, row
        self.panel = panel
        if manager is None:
            manager = getattr(panel, "updates", None)
        if manager is None:
            from update_ui import UpdateManager
            panel.updates = manager = UpdateManager(panel.root, on_install=getattr(panel, "close", None))
        self.manager = manager
        theme.install(panel.root._root(), tc.load_config().get("theme"))
        super().__init__(panel.root)
        self.withdraw()
        self.title("Nexus · 设置")
        attach_dialog_owner(self, panel.root)
        self._nexus_ui = getattr(panel.root, "_nexus_ui", None) or UIScale(panel.root)
        self.scale = self._nexus_ui.factor
        self.protocol("WM_DELETE_WINDOW", self.cancel)
        self._nexus_dialog_escape = self.bind("<Escape>", self._escape)
        theme.style(self, bg=theme.BACKGROUND)
        theme.track_title_bar(self)
        self.original_theme = panel.appearance.mode
        self.theme_mode = self.original_theme
        self._committed = False
        self.current_page = None

        # iOS navigation bar.
        self.header = theme.widget(tk.Frame, self, bg=theme.BACKGROUND, height=self.p(50))
        self.header.pack(fill="x")
        self.header.pack_propagate(False)
        self.header_title = theme.widget(tk.Label, self.header, text="设置", bg=theme.BACKGROUND,
                                         fg=theme.TEXT, font=self.font(16, "bold"), bd=0)
        self.header_title.place(relx=.5, rely=.5, anchor="center")
        self.cancel_button = TextButton(self.header, "取消", self.cancel, font=self.font(15))
        self.done_button = TextButton(self.header, "完成", self.done, font=self.font(15, "bold"))
        self.back_button = TextButton(self.header, "‹ 设置", lambda: self.select_page("settings"),
                                      font=self.font(15))
        self.done_button.place(relx=1, x=-self.p(12), rely=.5, anchor="e")
        bind_window_drag(self, self.header, self.header_title)

        self.inner = theme.widget(tk.Frame, self, bg=theme.BACKGROUND)
        self.inner.pack(fill="both", expand=True)
        self.preferences = theme.widget(tk.Frame, self.inner, bg=theme.BACKGROUND)
        self.update_page = theme.widget(tk.Frame, self.inner, bg=theme.BACKGROUND)
        update_group = Group(self.update_page, p=self.p, expand=True)
        update_group.pack(fill="both", expand=True, padx=self.p(16), pady=(self.p(6), self.p(16)))
        self.update_section = UpdateSection(update_group.body, self, manager, compact=True)
        self.update_section.pack(fill="both", expand=True, padx=self.p(10), pady=self.p(8))

        config = tc.load_config()
        self.interval = tk.StringVar(self, value=dict(tc.INTERVAL_CHOICES).get(panel.interval, "1 分钟"))
        self.auto = tk.BooleanVar(self, value=panel.auto)
        self.boot = tk.BooleanVar(self, value=tc.is_autostart_enabled())
        self.alert = tk.BooleanVar(self, value=bool(config.get("low_battery_alert", True)))
        self.gpu = tk.BooleanVar(self, value=bool(config.get("gpu_animation", True)))
        self.auto_theme = tk.BooleanVar(self, value=self.original_theme == "system")
        self.glass = tk.BooleanVar(self, value=bool(config.get("glass_backdrop", True)))
        self.feedback = tk.StringVar(self)

        scroll_frame = theme.widget(tk.Frame, self.preferences, bg=theme.BACKGROUND)
        scroll_frame.pack(fill="both", expand=True)
        self.preferences_canvas = theme.widget(tk.Canvas, scroll_frame, bg=theme.BACKGROUND,
                                               bd=0, highlightthickness=0, width=1, height=1)
        self.preferences_canvas.pack(side="left", fill="both", expand=True)
        from ios_controls import ThinScrollbar
        self.preferences_scrollbar = ThinScrollbar(scroll_frame, self._scroll_preferences, p=self.p)
        self.preferences_canvas.configure(yscrollcommand=self._update_preferences_scrollbar,
                                          yscrollincrement=self.p(15))
        self.rows = theme.widget(tk.Frame, self.preferences_canvas, bg=theme.BACKGROUND)
        rows_window = self.preferences_canvas.create_window(0, 0, anchor="nw", window=self.rows)
        self.preferences_canvas.bind("<Configure>", lambda event: self.preferences_canvas.itemconfigure(rows_window, width=event.width))
        self.rows.bind("<Configure>", lambda event: self.preferences_canvas.configure(scrollregion=(0, 0, event.width, event.height)))
        self.bind("<MouseWheel>", self._wheel_preferences, add="+")
        self.bind("<FocusIn>", self._reveal_preference, add="+")

        # 外观
        body = self._group("外观")
        self.appearance_picker = AppearancePicker(body, self, self.effective_theme, self.pick_theme)
        self.appearance_picker.pack(fill="x")
        body.rows = 1  # the next row draws a separator under the picker
        line = row(self, body, "自动", detail="跟随 Windows 的浅色 / 深色设置")
        self.auto_theme_switch = Switch(line, self.auto_theme, scale=self.scale, command=self._auto_theme_changed)
        self.auto_theme_switch.place(relx=1, rely=.5, anchor="e", x=-self.p(14))
        glass_detail = ("主窗口实时虚化后方的内容" if getattr(panel.root, "_nexus_native_hwnd", 0)
                        else "主窗口透出模糊的桌面壁纸")
        line = row(self, body, "毛玻璃背景", detail=glass_detail)
        self.glass_switch = Switch(line, self.glass, scale=self.scale)
        self.glass_switch.place(relx=1, rely=.5, anchor="e", x=-self.p(14))
        # 刷新
        body = self._group("刷新")
        line = row(self, body, "刷新间隔", icon="timer", swatch=theme.ACCENT)
        self.combo = IntervalPicker(line, self, self.interval, [label for _, label in tc.INTERVAL_CHOICES],
                                    scale=self.scale, show_clock=False, flat=True)
        self.combo.place(relx=1, rely=.5, x=-self.p(6), anchor="e")
        line = row(self, body, "自动刷新", detail="仅主窗口", icon="refresh", swatch=theme.SWITCH_ON)
        Switch(line, self.auto, scale=self.scale).place(relx=1, rely=.5, anchor="e", x=-self.p(14))
        # 通知与系统
        body = self._group("通知与系统", footer="低电量提醒会在设备电量低于 10% 时发送 Windows 通知。")
        for title, detail, variable, icon, swatch in (
            ("开机启动托盘", None, self.boot, "monitor", theme.GRAY),
            ("低电量提醒", None, self.alert, "battery", theme.LOW),
            ("GPU 动画加速", "不支持时自动兼容", self.gpu, "speed", theme.INDIGO),
        ):
            line = row(self, body, title, detail=detail, icon=icon, swatch=swatch)
            Switch(line, variable, scale=self.scale).place(relx=1, rely=.5, anchor="e", x=-self.p(14))
        # 软件更新
        body = self._group(None)
        self.update_button = link_row(self, body, "软件更新", current_version(),
                                      lambda: self.select_page("updates"), icon="update", swatch=theme.GRAY)
        # 工具
        body = self._group("工具")
        action_row(self, body, "发送测试提醒", self._test)
        action_row(self, body, "重建桌面入口", self._links)
        self.feedback_label = theme.widget(tk.Label, self.rows, textvariable=self.feedback, bg=theme.BACKGROUND,
                                           fg=theme.MUTED, font=self.font(12), anchor="w", justify="left")
        self.feedback_label.pack(fill="x", padx=self.p(30), pady=(self.p(8), self.p(18)))
        self.feedback_label.bind("<Configure>", lambda e: self.feedback_label.configure(wraplength=e.width))

        self.select_page("settings")
        manager._attach(self)
        match_owner_size(self, panel.root)
        self.show()

    def _group(self, title, *, footer=None):
        from ios_controls import Group
        if title:
            theme.widget(tk.Label, self.rows, text=title, bg=theme.BACKGROUND, fg=theme.MUTED,
                         font=self.font(12.5), bd=0).pack(anchor="w", padx=self.p(30), pady=(self.p(16), self.p(6)))
        else:
            theme.widget(tk.Frame, self.rows, bg=theme.BACKGROUND, height=self.p(20)).pack(fill="x")
        group = Group(self.rows, p=self.p)
        group.pack(fill="x", padx=self.p(16))
        if footer:
            label = theme.widget(tk.Label, self.rows, text=footer, bg=theme.BACKGROUND, fg=theme.MUTED,
                                 font=self.font(12), anchor="w", justify="left", bd=0)
            label.pack(fill="x", padx=self.p(30), pady=(self.p(7), 0))
            label.bind("<Configure>", lambda e: label.configure(wraplength=e.width))
        return group.body

    # ---- appearance (applied live, saved by 完成, reverted by 取消) -------------
    def effective_theme(self):
        return "dark" if theme.is_dark(self) else "light"

    def _preview_theme(self, mode):
        self.theme_mode = mode
        self.panel.appearance.set_mode(mode)
        self.appearance_picker.redraw()

    def pick_theme(self, mode):
        self.auto_theme.set(False)
        self._preview_theme(mode)

    def _auto_theme_changed(self):
        self._preview_theme("system" if self.auto_theme.get() else self.effective_theme())

    # ---- navigation -----------------------------------------------------------
    def select_page(self, page):
        if page == "appearance":  # the appearance page is now part of the main list
            page = "settings"
        previous = self.current_page
        if previous == "settings" and page != previous:
            self.combo._close()
        self.current_page = page
        pages = {"settings": self.preferences, "updates": self.update_page}
        for name, widget in pages.items():
            if name != page:
                widget.pack_forget()
        pages[page].pack(fill="both", expand=True)
        self.header_title.configure(text={"settings": "设置", "updates": "软件更新"}[page])
        if page == "settings":
            self.back_button.place_forget()
            self.cancel_button.place(x=self.p(12), rely=.5, anchor="w")
            if previous == "updates":
                self.update_button.focus_set()
        else:
            self.cancel_button.place_forget()
            self.back_button.place(x=self.p(8), rely=.5, anchor="w")
            self.back_button.focus_set()

    def show_updates(self):
        self.select_page("updates")
        self.show()

    def _scroll_preferences(self, *args):
        self.combo._close()
        self.preferences_canvas.yview(*args)

    def _update_preferences_scrollbar(self, first, last):
        bar = self.preferences_scrollbar
        bar.set(first, last)
        fits = float(first) <= 0 and float(last) >= 1
        if fits and bar.winfo_manager():
            bar.pack_forget()
        elif not fits and not bar.winfo_manager():
            bar.pack(side="right", fill="y", before=self.preferences_canvas)

    def _wheel_preferences(self, event):
        if event.widget is self.preferences_canvas or str(event.widget).startswith(str(self.rows)):
            steps = int(event.delta / 120)
            if steps:
                self._scroll_preferences("scroll", -steps * 2, "units")
                return "break"

    def _reveal_preference(self, event):
        widget = event.widget
        if not str(widget).startswith(f"{self.rows}."):
            return
        canvas = self.preferences_canvas
        y = widget.winfo_rooty() - self.rows.winfo_rooty()
        bottom = y + widget.winfo_height()
        visible_top = canvas.canvasy(0)
        height = canvas.winfo_height()
        if y < visible_top:
            canvas.yview_moveto(y / self.rows.winfo_height())
        elif bottom > visible_top + height:
            canvas.yview_moveto((bottom-height) / self.rows.winfo_height())

    def show(self):
        self.deiconify()
        self.lift()
        active = getattr(self, "_nexus_active_dialog", None)
        if active is not None:
            active.focus_set()
        else:
            (self if self.current_page == "settings" else self.back_button).focus_set()

    def _escape(self, _event):
        active = getattr(self, "_nexus_active_dialog", None)
        if active is not None:
            active.on_close()
        elif self.current_page != "settings":
            self.select_page("settings")
        else:
            self.cancel()
        return "break"

    def confirm_install(self, callback):
        ConfirmDialog(self, "准备安装更新", "将关闭 Nexus 并打开安装向导。覆盖安装会保留设备列表、历史记录和设置。", callback)

    def cancel(self):
        self.destroy()

    def done(self):
        if self._save():
            self._committed = True
            self.destroy()

    def destroy(self):
        if self.manager.window is self:
            self.manager.cancel.set()
            self.manager.window = None
        if getattr(self.panel, "settings_window", None) is self:
            self.panel.settings_window = None
        closing = getattr(self.panel.root, "_nexus_closing", False)
        self._nexus_closing = True
        if not self._committed and not closing and self.panel.appearance.mode != self.original_theme:
            try:
                self.panel.appearance.set_mode(self.original_theme)  # 取消: undo the live preview
            except tk.TclError:
                pass
        super().destroy()

    def _save(self):
        import tray_common as tc
        panel = self.panel
        try:
            if self.boot.get() != tc.is_autostart_enabled() and not tc.set_autostart(self.boot.get()):
                raise OSError("开机启动设置未能保存，请重试")
            interval = next(sec for sec, label in tc.INTERVAL_CHOICES if label == self.interval.get())
            cfg = {"interval": interval, "panel_auto_refresh": self.auto.get(), "autostart": self.boot.get(),
                   "low_battery_alert": self.alert.get(), "gpu_animation": self.gpu.get(),
                   "theme": self.theme_mode, "glass_backdrop": self.glass.get()}
            tc.save_config(cfg, strict=True)
            panel.auto, panel.interval = cfg["panel_auto_refresh"], interval
            panel.glass_enabled = self.glass.get()
            panel.theme_choice.set(theme.MODE_LABELS[self.theme_mode])
            tc.post_to_tray(tc.MSG_RELOAD)
            panel.schedule()
            panel.transition.set_gpu_enabled(self.gpu.get())
            panel.draw()
            self.feedback.set("设置已保存")
            return True
        except (OSError, ValueError, StopIteration) as exc:
            self.feedback.set(str(exc) or "设置保存失败，请重试")
            return False

    def _links(self):
        import tray_common as tc
        result = tc.install_desktop_links()
        self.feedback.set("；".join(result.get("errors", [])) or "桌面入口已更新")

    def _test(self):
        result = self.panel.low_battery_notifier.preview()
        self.feedback.set("已发送测试提醒" if result else "测试提醒已提交，请查看系统通知")


class UpdateSection(tk.Frame):
    """Update controls shared by the settings page and standalone dialog."""
    def __init__(self, master, dialog, manager, *, compact=False):
        import update_client
        self.manager = manager
        self.scale, self.p, self.font = dialog.scale, dialog.p, dialog.font
        super().__init__(master)
        theme.style(self, bg=theme.SURFACE)
        if compact:
            theme.widget(tk.Label, self, text="当前版本 " + update_client.current_version(),
                         bg=theme.SURFACE, fg=theme.MUTED, font=self.font(11), anchor="w").pack(fill="x", pady=(0, 2))
        self.status = tk.StringVar(value="点击“检查更新”获取最新正式版本。")
        status_box = theme.widget(tk.Frame, self, bg=theme.SOFT, height=self.p(42 if compact else 66))
        status_box.pack(fill="x", pady=(0, self.p(5 if compact else 14)))
        status_box.pack_propagate(False)
        status_label = theme.widget(tk.Label, status_box, textvariable=self.status, bg=theme.SOFT,
                                    fg=theme.TEXT, font=self.font(12 if compact else 14),
                                    justify="left", anchor="w")
        status_label.pack(fill="both", expand=True, padx=self.p(12))
        status_label.bind("<Configure>", lambda event: status_label.configure(wraplength=event.width))
        footer = theme.widget(tk.Frame, self, bg=theme.SURFACE)
        footer.pack(side="bottom", fill="x", pady=(self.p(6 if compact else 14), self.p(5) if compact else 0))
        import webbrowser
        Button(footer, "发布记录", lambda: webbrowser.open("https://github.com/" + update_client.DEFAULT_REPOSITORY + "/releases"),
               scale=self.scale, width=92).pack(side="left")
        self.ignore = Button(footer, "忽略此版", manager._ignore, scale=self.scale, width=92)
        self.ignore.configure(state="disabled")
        self.ignore.pack(side="left", padx=self.p(8))
        self.primary = Button(footer, "检查更新", lambda: manager.check(manual=True), scale=self.scale, width=126, primary=True)
        self.primary.pack(side="right")
        auto_row = theme.widget(tk.Frame, self, bg=theme.SURFACE, height=self.p(31 if compact else 42))
        auto_row.pack(side="bottom", fill="x", pady=(self.p(3 if compact else 6), 0))
        auto_row.pack_propagate(False)
        self.auto = tk.BooleanVar(value=bool(update_client.load_state().get("auto_check")))
        theme.widget(tk.Label, auto_row, text="自动检查更新并提醒", bg=theme.SURFACE, fg=theme.MUTED, font=self.font(13)).place(x=0, rely=.5, anchor="w")
        Switch(auto_row, self.auto, scale=self.scale,
               command=lambda: update_client.save_state(auto_check=self.auto.get())).place(relx=1, rely=.5, anchor="e")
        self.progress = Progress(self, scale=self.scale)
        self.progress.pack(side="bottom", fill="x", pady=(self.p(5 if compact else 12), self.p(3 if compact else 4)))
        notes_box = theme.widget(tk.Frame, self, bg=theme.SURFACE, highlightbackground=theme.LINE, highlightthickness=1)
        notes_box.pack(fill="both", expand=True)
        theme.widget(tk.Label, notes_box, text="更新说明", bg=theme.SURFACE, fg=theme.TEXT,
                     font=self.font(13 if compact else 14)).pack(
                         anchor="w", padx=self.p(12), pady=(self.p(3 if compact else 12), self.p(2 if compact else 8)))
        area = theme.widget(tk.Frame, notes_box, bg=theme.SURFACE)
        area.pack(fill="both", expand=True, padx=self.p(12), pady=(0, self.p(3 if compact else 10)))
        from ios_controls import ThinScrollbar
        scroll = ThinScrollbar(area, lambda *args: self.notes.yview(*args), p=self.p, bg=theme.SURFACE)
        scroll.pack(side="right", fill="y")
        self.notes = theme.widget(tk.Text, area, height=1 if compact else 5, width=30, bg=theme.SURFACE,
                                  fg=theme.MUTED, bd=0, highlightthickness=0, font=self.font(12 if compact else 14),
                                  wrap="word", yscrollcommand=scroll.set, padx=2, pady=2)
        self.notes.pack(side="left", fill="both", expand=True)


class UpdateDialog(OverlayDialog):
    """Compatibility host for a caller without a Nexus settings panel."""
    def __init__(self, manager):
        import update_client
        self.manager = manager
        super().__init__(manager.root, "软件更新", "当前版本 " + update_client.current_version() + " · 获取最新功能与设备支持",
                         width=850, height=590, on_close=manager._dismiss)
        self.update_section = UpdateSection(self.inner, self, manager)
        self.update_section.pack(fill="both", expand=True)

    def confirm_install(self, callback):
        ConfirmDialog(self.root, "准备安装更新", "将关闭 Nexus 并打开安装向导。覆盖安装会保留设备列表、历史记录和设置。", callback)


class ConfirmDialog(OverlayDialog):
    def __init__(self, root, title, text, confirm):
        super().__init__(root, title, "确认后再继续", width=650, height=300)
        body = theme.widget(tk.Label, self.inner, text=text, bg=theme.SURFACE, fg=theme.MUTED, font=self.font(15),
                            wraplength=self.p(520), justify="left", anchor="w")
        body.pack(fill="x", pady=self.p(12))
        self.inner.bind("<Configure>", lambda event: body.configure(wraplength=event.width))
        footer = theme.widget(tk.Frame, self.inner, bg=theme.SURFACE)
        footer.pack(side="bottom", fill="x")
        def accept():
            self.destroy()
            confirm()
        Button(footer, "确认安装", accept, scale=self.scale, width=114, primary=True).pack(side="right")
        Button(footer, "返回", self.destroy, scale=self.scale, width=90).pack(side="right", padx=self.p(8))
