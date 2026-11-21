"""Settings and update cards hosted inside the Nexus window."""
from __future__ import annotations

from pathlib import Path
import tkinter as tk
from tkinter import ttk
from interval_picker import IntervalPicker
from search_window import bind_window_drag, match_owner_size
from toggle_switch import Switch
from ui_scale import UIScale
from view_switcher import ViewSwitcher
import theme

FONT = "Microsoft YaHei UI"


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


class Button(tk.Canvas):
    def __init__(self, master, text, command, *, scale=.85, width=104, height=38, primary=False, background=None):
        self.scale, self.text, self.command = scale, text, command
        self.primary, self.disabled, self.hover, self.focused = primary, False, False, False
        self.default_width, self.default_height = round(width * scale), round(height * scale)
        bg = theme.color(master, background) if background is not None else master.cget("bg")
        super().__init__(master, width=self.default_width, height=self.default_height, bg=bg,
                         highlightthickness=0, bd=0, takefocus=1, cursor="hand2")
        self.bind("<Configure>", lambda e: self.redraw())
        self.bind("<Enter>", lambda e: self._hover(True))
        self.bind("<Leave>", lambda e: self._hover(False))
        self.bind("<FocusIn>", lambda e: self._focus(True))
        self.bind("<FocusOut>", lambda e: self._focus(False))
        self.bind("<ButtonRelease-1>", lambda e: self.invoke())
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
        if self.disabled:
            surface, label = theme.DISABLED_SURFACE, theme.DISABLED
        elif self.primary:
            surface, label = (theme.PRIMARY_HOVER if self.hover else theme.PRIMARY), theme.ON_PRIMARY
        else:
            surface, label = (theme.SELECTED if self.hover else theme.SOFT), theme.TEXT
        fill = theme.color(self, surface)
        edge = theme.FOCUS if self.focused else theme.EDGE if not self.disabled else theme.LINE
        rounded(self, 1, 1, width-1, height-1, height/2, fill=fill, outline=theme.color(self, edge), width=1)
        self.create_text(width/2, height/2, text=self.text, fill=theme.color(self, label),
                         font=(FONT, -round(14*self.scale), "bold" if self.primary else "normal"))


class CloseButton(Button):
    def __init__(self, master, command, *, scale=.85):
        self.pressed = False
        super().__init__(master, "", command, scale=scale, width=42, height=42)
        self.bind("<ButtonPress-1>", self._press)
        self.bind("<ButtonRelease-1>", self._release)

    def _press(self, _event):
        self.pressed = True
        self.redraw()

    def _release(self, event):
        activate = self.pressed and 0 <= event.x < self.winfo_width() and 0 <= event.y < self.winfo_height()
        self.pressed = False
        if activate:
            self.invoke()
        else:
            self.redraw()

    def redraw(self):
        self.delete("all")
        width = max(self.default_width, self.winfo_width())
        height = max(self.default_height, self.winfo_height())
        cx, cy = width / 2, height / 2
        radius = min(width, height) / 2 - 3
        if self.focused:
            self.create_oval(cx-radius-2, cy-radius-2, cx+radius+2, cy+radius+2,
                             fill="", outline=theme.color(self, theme.FOCUS), width=1)
        surface = theme.SOFT
        if self.hover:
            surface = theme.PRIMARY_HOVER if self.pressed else theme.SELECTED
        self.create_oval(cx-radius, cy-radius, cx+radius, cy+radius,
                         fill=theme.color(self, surface), outline=theme.color(self, theme.EDGE))
        role = theme.ON_PRIMARY if self.pressed and self.hover else theme.ACCENT if self.hover else theme.MUTED
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
        return (FONT, -self.p(size), weight)


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


def setting_row(dialog, master, title, description, *, height=64, bg=theme.SURFACE):
    row = theme.widget(tk.Frame, master, bg=bg, height=dialog.p(height))
    row.pack(fill="x")
    row.pack_propagate(False)
    theme.widget(tk.Label, row, text=title, bg=bg, fg=theme.TEXT, font=dialog.font(16, "bold")).place(x=0, y=dialog.p(9))
    theme.widget(tk.Label, row, text=description, bg=bg, fg=theme.MUTED, font=dialog.font(13)).place(x=0, y=dialog.p(35))
    theme.widget(tk.Frame, row, bg=theme.LINE).place(x=0, rely=1, y=-1, relwidth=1, height=1)
    return row


class SettingsDialog(DialogMetrics, tk.Toplevel):
    def __init__(self, panel, manager=None):
        import tray_common as tc
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
        self.title("Nexus · 设置与偏好")
        self.transient(panel.root)
        self._nexus_ui = getattr(panel.root, "_nexus_ui", None) or UIScale(panel.root)
        self.scale = self._nexus_ui.factor
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self._nexus_dialog_escape = self.bind("<Escape>", self._escape)
        theme.style(self, bg=theme.BACKGROUND)
        theme.track_title_bar(self)

        self.header = theme.widget(tk.Frame, self, bg=theme.BACKGROUND)
        self.header.pack(fill="x", padx=self.p(13), pady=(self.p(8), self.p(5)))
        mark = brand_mark(self.header, self._nexus_ui)
        mark.pack(side="left", padx=(0, self.p(6)))
        title = theme.widget(tk.Label, self.header, text="设置", bg=theme.BACKGROUND, fg=theme.TEXT,
                             font=self.font(19, "bold"))
        title.pack(side="left", padx=(0, self.p(12)))
        self.close_button = CloseButton(self.header, self.destroy, scale=self.scale)
        self.close_button.pack(side="right")
        self.tabs = ViewSwitcher(self.header, ui=self._nexus_ui, command=self.select_page, show_icons=False)
        self.tabs.pack(side="left")
        bind_window_drag(self, self.header, mark, title)

        self.inner = theme.widget(tk.Frame, self, bg=theme.SURFACE)
        self.inner.pack(fill="both", expand=True, padx=self.p(13), pady=(0, self.p(9)))
        self.preferences = theme.widget(tk.Frame, self.inner, bg=theme.SURFACE)
        self.update_section = UpdateSection(self.inner, self, manager, compact=True)
        config = tc.load_config()
        self.interval = tk.StringVar(self, value=dict(tc.INTERVAL_CHOICES).get(panel.interval, "1 分钟"))
        self.auto = tk.BooleanVar(self, value=panel.auto)
        self.boot = tk.BooleanVar(self, value=tc.is_autostart_enabled())
        self.alert = tk.BooleanVar(self, value=bool(config.get("low_battery_alert", True)))
        self.gpu = tk.BooleanVar(self, value=bool(config.get("gpu_animation", True)))
        self.feedback = tk.StringVar(self)

        footer = theme.widget(tk.Frame, self.preferences, bg=theme.SURFACE)
        footer.pack(side="bottom", fill="x", padx=self.p(9), pady=(self.p(4), self.p(7)))
        Button(footer, "重建入口", self._links, scale=self.scale, width=86).pack(side="left")
        Button(footer, "测试提醒", self._test, scale=self.scale, width=86).pack(side="left", padx=self.p(6))
        self.save_button = Button(footer, "保存设置", self._save, scale=self.scale, width=106, primary=True)
        self.save_button.pack(side="right")
        Button(footer, "取消", self.destroy, scale=self.scale, width=70).pack(side="right", padx=self.p(6))
        self.feedback_label = theme.widget(tk.Label, self.preferences, textvariable=self.feedback,
                                           bg=theme.SURFACE, fg=theme.MUTED, font=self.font(11), anchor="w")
        self.feedback.trace_add("write", self._show_feedback)

        scroll_frame = theme.widget(tk.Frame, self.preferences, bg=theme.SURFACE)
        scroll_frame.pack(fill="both", expand=True, padx=self.p(9))
        self.preferences_canvas = theme.widget(tk.Canvas, scroll_frame, bg=theme.SURFACE,
                                               bd=0, highlightthickness=0, width=1, height=1)
        self.preferences_canvas.pack(side="left", fill="both", expand=True)
        scrollbar = ttk.Scrollbar(scroll_frame, command=self._scroll_preferences)
        scrollbar.pack(side="right", fill="y")
        self.preferences_canvas.configure(yscrollcommand=scrollbar.set, yscrollincrement=self.p(15))
        self.rows = theme.widget(tk.Frame, self.preferences_canvas, bg=theme.SURFACE)
        rows_window = self.preferences_canvas.create_window(0, 0, anchor="nw", window=self.rows)
        self.preferences_canvas.bind("<Configure>", lambda event: self.preferences_canvas.itemconfigure(rows_window, width=event.width))
        self.rows.bind("<Configure>", lambda event: self.preferences_canvas.configure(scrollregion=(0, 0, event.width, event.height)))
        self.bind("<MouseWheel>", self._wheel_preferences, add="+")
        self.bind("<FocusIn>", self._reveal_preference, add="+")

        row = setting_row(self, self.rows, "刷新间隔", "电量数据的自动读取频率", height=56)
        self.combo = IntervalPicker(row, self, self.interval,
                                    [label for _, label in tc.INTERVAL_CHOICES], scale=self.scale)
        self.combo.place(relx=1, rely=.5, x=-self.p(3), anchor="e")
        for title, detail, variable in (("自动刷新", "持续更新已连接设备的电量", self.auto),
                                        ("开机启动托盘", "登录 Windows 后在后台显示电量", self.boot),
                                        ("低电量提醒", "电量低于 10% 时发送系统通知", self.alert),
                                        ("GPU 动画加速", "加速视图切换，不支持时自动兼容", self.gpu)):
            row = setting_row(self, self.rows, title, detail, height=56)
            Switch(row, variable, scale=self.scale).place(relx=1, rely=.5, anchor="e", x=-self.p(3))
        self.select_page("settings")
        manager._attach(self)
        match_owner_size(self, panel.root)
        self.show()

    def select_page(self, page):
        pages = {"settings": self.preferences, "updates": self.update_section}
        for name, widget in pages.items():
            if name != page:
                widget.pack_forget()
        pages[page].pack(fill="both", expand=True, padx=self.p(5), pady=(self.p(4), 0))
        self.tabs.set_items((("settings", "常规设置", ""), ("updates", "软件更新", "")), page)

    def show_updates(self):
        self.select_page("updates")
        self.show()

    def _show_feedback(self, *_args):
        if self.feedback.get():
            self.feedback_label.pack(side="bottom", fill="x", padx=self.p(9))
        else:
            self.feedback_label.pack_forget()

    def _scroll_preferences(self, *args):
        self.combo._close()
        self.preferences_canvas.yview(*args)

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
        (active if active is not None else self).focus_set()

    def _escape(self, _event):
        active = getattr(self, "_nexus_active_dialog", None)
        if active is None:
            self.destroy()
        else:
            active.on_close()
        return "break"

    def confirm_install(self, callback):
        ConfirmDialog(self, "准备安装更新", "将关闭 Nexus 并打开安装向导。覆盖安装会保留设备列表、历史记录和设置。", callback)

    def destroy(self):
        if self.manager.window is self:
            self.manager.cancel.set()
            self.manager.window = None
        if getattr(self.panel, "settings_window", None) is self:
            self.panel.settings_window = None
        self._nexus_closing = True
        super().destroy()

    def _save(self):
        import tray_common as tc
        panel = self.panel
        try:
            if self.boot.get() != tc.is_autostart_enabled() and not tc.set_autostart(self.boot.get()):
                raise OSError("开机启动设置未能保存，请重试")
            interval = next(sec for sec, label in tc.INTERVAL_CHOICES if label == self.interval.get())
            cfg = tc.load_config()
            cfg.update(interval=interval, autostart=self.boot.get(), low_battery_alert=self.alert.get(),
                       gpu_animation=self.gpu.get())
            tc.save_config(cfg, strict=True)
            panel.auto, panel.interval = self.auto.get(), interval
            tc.post_to_tray(tc.MSG_RELOAD)
            panel.schedule()
            panel.transition.set_gpu_enabled(self.gpu.get())
            panel.draw()
            self.feedback.set("设置已保存")
        except (OSError, ValueError, StopIteration) as exc:
            self.feedback.set(str(exc) or "设置保存失败，请重试")

    def _links(self):
        import tray_common as tc
        result = tc.install_desktop_links()
        self.feedback.set("；".join(result.get("errors", [])) or "桌面入口已更新")

    def _test(self):
        result = self.panel.low_battery_notifier.preview()
        self.feedback.set("已发送测试提醒" if result else "测试提醒已提交，请查看系统通知")


class UpdateSection(tk.Frame):
    """Update controls shared by the preferences tab and standalone dialog."""
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
        status_surface = theme.Color("#edf8f2", "#203b36")
        status_box = theme.widget(tk.Frame, self, bg=status_surface, height=self.p(42 if compact else 66))
        status_box.pack(fill="x", pady=(0, self.p(5 if compact else 14)))
        status_box.pack_propagate(False)
        theme.widget(tk.Label, status_box, text="↻", bg=status_surface, fg=theme.Color("#16885b", "#76e0ad"),
                     font=self.font(20 if compact else 26, "bold")).pack(side="left", padx=self.p(8))
        status_label = theme.widget(tk.Label, status_box, textvariable=self.status, bg=status_surface,
                                    fg=theme.Color("#25805d", "#9de5bf"), font=self.font(12 if compact else 14),
                                    justify="left", anchor="w")
        status_label.pack(side="left", fill="both", expand=True, padx=(0, self.p(5)))
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
                     font=self.font(12 if compact else 14, "bold")).pack(
                         anchor="w", padx=self.p(12), pady=(self.p(3 if compact else 12), self.p(2 if compact else 8)))
        area = theme.widget(tk.Frame, notes_box, bg=theme.SURFACE)
        area.pack(fill="both", expand=True, padx=self.p(12), pady=(0, self.p(3 if compact else 10)))
        scroll = ttk.Scrollbar(area, orient="vertical")
        scroll.pack(side="right", fill="y")
        self.notes = theme.widget(tk.Text, area, height=1 if compact else 5, width=30, bg=theme.SURFACE,
                                  fg=theme.MUTED, bd=0, highlightthickness=0, font=self.font(12 if compact else 14),
                                  wrap="word", yscrollcommand=scroll.set, padx=2, pady=2)
        self.notes.pack(side="left", fill="both", expand=True)
        scroll.configure(command=self.notes.yview)


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
