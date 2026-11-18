"""Settings and update cards hosted inside the Nexus window."""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from toggle_switch import Switch

BG = "#f4f5f8"
WHITE = "#ffffff"
TEXT = "#202536"
MUTED = "#798294"
LINE = "#e8eaf1"
SOFT = "#f6f6fb"
GREEN = "#5755d9"
FONT = "Microsoft YaHei UI"


def rounded(canvas, x1, y1, x2, y2, radius, **options):
    r = min(radius, (x2 - x1) / 2, (y2 - y1) / 2)
    return canvas.create_polygon(
        x1+r, y1, x2-r, y1, x2, y1, x2, y1+r,
        x2, y2-r, x2, y2, x2-r, y2, x1+r, y2,
        x1, y2, x1, y2-r, x1, y1+r, x1, y1,
        smooth=True, splinesteps=24, **options)


class Button(tk.Canvas):
    def __init__(self, master, text, command, *, scale=.85, width=104, height=38, primary=False):
        self.scale, self.text, self.command = scale, text, command
        self.primary, self.disabled, self.hover, self.focused = primary, False, False, False
        self.default_width, self.default_height = round(width * scale), round(height * scale)
        super().__init__(master, width=self.default_width, height=self.default_height,
                         bg=master.cget("bg"), highlightthickness=0, bd=0, takefocus=1, cursor="hand2")
        self.bind("<Configure>", lambda e: self.redraw())
        self.bind("<Enter>", lambda e: self._hover(True))
        self.bind("<Leave>", lambda e: self._hover(False))
        self.bind("<FocusIn>", lambda e: self._focus(True))
        self.bind("<FocusOut>", lambda e: self._focus(False))
        self.bind("<ButtonRelease-1>", lambda e: self.invoke())
        self.bind("<Return>", lambda e: self.invoke())
        self.bind("<space>", lambda e: self.invoke())
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
        fill = "#e7ebf0" if self.disabled else "#4643c2" if self.primary and self.hover else GREEN if self.primary else "#eeedfc" if self.hover else SOFT
        color = "#a2acb8" if self.disabled else WHITE if self.primary else TEXT
        rounded(self, 1, 1, width-1, height-1, 9*self.scale, fill=fill,
                outline="#91b6df" if self.focused else fill, width=1)
        self.create_text(width/2, height/2, text=self.text, fill=color,
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
                             fill="", outline="#b7b5f4", width=1)
        fill = "#e2e0ff" if self.pressed and self.hover else "#eeedfc" if self.hover else "#f4f5fa"
        self.create_oval(cx-radius, cy-radius, cx+radius, cy+radius, fill=fill, outline=LINE)
        color = GREEN if self.hover else "#505973"
        arm = 5 * self.scale
        for direction in (-1, 1):
            self.create_line(cx-arm, cy-direction*arm, cx+arm, cy+direction*arm,
                             fill=color, width=1.8*self.scale, capstyle=tk.ROUND)


class Progress(tk.Canvas):
    def __init__(self, master, *, scale=.85):
        self.value, self.scale = 0, scale
        super().__init__(master, bg=master.cget("bg"), height=round(7*scale), bd=0, highlightthickness=0)
        self.bind("<Configure>", lambda e: self.redraw())

    def __setitem__(self, key, value):
        if key == "value":
            self.value = max(0, min(100, float(value)))
            self.redraw()
        else:
            super().__setitem__(key, value)

    def redraw(self):
        self.delete("all")
        width, height = max(2, self.winfo_width()), max(4, round(7*self.scale))
        rounded(self, 0, 0, width, height, height/2, fill=LINE, outline=LINE)
        if self.value:
            rounded(self, 0, 0, max(height, width*self.value/100), height, height/2, fill=GREEN, outline=GREEN)


class OverlayDialog(tk.Canvas):
    """A child widget, so it cannot detach from or outlive its main window."""
    def __init__(self, root, title, subtitle, *, width=820, height=590, on_close=None):
        self.root = root
        self.scale = getattr(getattr(root, "_nexus_ui", None), "factor", .85)
        self.card_width, self.card_height = width, height
        self.on_close = on_close or self.destroy
        self.previous = getattr(root, "_nexus_active_dialog", None)
        super().__init__(root, bg=BG, highlightthickness=0, bd=0, takefocus=1)
        self.inner = tk.Frame(self, bg=WHITE)
        self.bind("<Configure>", self._layout)
        if not getattr(root, "_nexus_dialog_escape", None):
            def escape(event):
                dialog = getattr(root, "_nexus_active_dialog", None)
                if dialog is not None and dialog.winfo_exists():
                    dialog.on_close()
                    return "break"
            root._nexus_dialog_escape = root.bind("<Escape>", escape, add="+")
        self.header = tk.Frame(self.inner, bg=WHITE)
        self.header.pack(fill="x", pady=(0, self.p(16)))
        badge = tk.Label(self.header, text="N", bg="#eeedfc", fg="#5755d9", font=self.font(21, "bold"),
                         width=2, padx=self.p(3), pady=self.p(4))
        badge.pack(side="left", padx=(0, self.p(12)))
        heading = tk.Frame(self.header, bg=WHITE)
        heading.pack(side="left", fill="x", expand=True)
        tk.Label(heading, text=title, bg=WHITE, fg=TEXT, font=self.font(25, "bold")).pack(anchor="w")
        tk.Label(heading, text=subtitle, bg=WHITE, fg=MUTED, font=self.font(13)).pack(anchor="w", pady=(self.p(3), 0))
        self.close_button = CloseButton(self.header, self.on_close, scale=self.scale)
        self.close_button.pack(side="right", anchor="n")
        self.show()

    def p(self, value):
        return round(value*self.scale)

    def font(self, size, weight="normal"):
        return (FONT, -self.p(size), weight)

    def _layout(self, event=None):
        width = self.winfo_width() if self.winfo_width() > 1 else 952
        height = self.winfo_height() if self.winfo_height() > 1 else 553
        cw, ch = min(self.p(self.card_width), width-28), min(self.p(self.card_height), height-24)
        left, top = (width-cw)/2, (height-ch)/2
        self.card_bounds = (left, top, cw, ch)
        self.delete("surface")
        rounded(self, left+2, top+5, left+cw+2, top+ch+5, self.p(22), fill="#dfe5ec", outline="", tags="surface")
        rounded(self, left, top, left+cw, top+ch, self.p(22), fill=WHITE, outline=LINE, tags="surface")
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


def setting_row(dialog, master, title, description, *, height=64, bg=WHITE):
    row = tk.Frame(master, bg=bg, height=dialog.p(height))
    row.pack(fill="x")
    row.pack_propagate(False)
    tk.Label(row, text=title, bg=bg, fg=TEXT, font=dialog.font(16, "bold")).place(x=0, y=dialog.p(9))
    tk.Label(row, text=description, bg=bg, fg=MUTED, font=dialog.font(13)).place(x=0, y=dialog.p(35))
    tk.Frame(row, bg=LINE).place(x=0, rely=1, y=-1, relwidth=1, height=1)
    return row


def section_heading(dialog, master, title, subtitle, *, close=None):
    header = tk.Frame(master, bg=WHITE)
    header.pack(fill="x", pady=(0, dialog.p(18)))
    tk.Label(header, text="N", bg="#eeedfc", fg="#5755d9", font=dialog.font(21, "bold"),
             width=2, padx=dialog.p(3), pady=dialog.p(4)).pack(side="left", padx=(0, dialog.p(12)))
    if close is not None:
        dialog.close_button = CloseButton(header, close, scale=dialog.scale)
        dialog.close_button.pack(side="right", anchor="n")
    heading = tk.Frame(header, bg=WHITE)
    heading.pack(side="left", fill="x", expand=True)
    tk.Label(heading, text=title, bg=WHITE, fg=TEXT, font=dialog.font(25, "bold")).pack(anchor="w")
    tk.Label(heading, text=subtitle, bg=WHITE, fg=MUTED, font=dialog.font(13)).pack(anchor="w", pady=(dialog.p(3), 0))


class SettingsDialog(OverlayDialog):
    def __init__(self, panel, manager=None):
        import tray_common as tc
        self.panel = panel
        if manager is None:
            manager = getattr(panel, "updates", None)
        if manager is None:
            from update_ui import UpdateManager
            panel.updates = manager = UpdateManager(panel.root, on_install=getattr(panel, "close", None))
        self.manager = manager
        super().__init__(panel.root, "设置与偏好", "设备状态、提醒与软件更新", width=1088, height=600)
        self.header.pack_forget()
        self.columns = tk.Frame(self.inner, bg=WHITE)
        self.columns.pack(fill="both", expand=True)
        self.columns.grid_columnconfigure(0, minsize=self.p(410))
        self.columns.grid_columnconfigure(2, weight=1)
        self.columns.grid_rowconfigure(0, weight=1)
        self.preferences = tk.Frame(self.columns, bg=WHITE)
        self.preferences.grid(row=0, column=0, sticky="nsew")
        tk.Frame(self.columns, bg=LINE, width=1).grid(row=0, column=1, sticky="ns", padx=self.p(14))
        self.update_section = UpdateSection(self.columns, self, manager, heading=True)
        self.update_section.grid(row=0, column=2, sticky="nsew")
        section_heading(self, self.preferences, "设置与偏好", "设备状态与通知偏好")
        self.interval = tk.StringVar(value=dict(tc.INTERVAL_CHOICES).get(panel.interval, "1 分钟"))
        self.auto = tk.BooleanVar(value=panel.auto)
        self.boot = tk.BooleanVar(value=tc.is_autostart_enabled())
        self.alert = tk.BooleanVar(value=bool(tc.load_config().get("low_battery_alert", True)))
        self.gpu = tk.BooleanVar(value=bool(tc.load_config().get("gpu_animation", True)))
        self.feedback = tk.StringVar()
        footer = tk.Frame(self.preferences, bg=WHITE)
        footer.pack(side="bottom", fill="x")
        actions = tk.Frame(footer, bg=WHITE)
        actions.pack(fill="x", pady=(0, self.p(12)))
        Button(actions, "重建入口", self._links, scale=self.scale, width=92).pack(side="left")
        Button(actions, "测试提醒", self._test, scale=self.scale, width=92).pack(side="left", padx=self.p(8))
        save_row = tk.Frame(footer, bg=WHITE)
        save_row.pack(fill="x")
        Button(save_row, "保存设置", self._save, scale=self.scale, width=112, primary=True).pack(side="right")
        Button(save_row, "取消", self.destroy, scale=self.scale, width=76).pack(side="right", padx=self.p(8))
        tk.Label(self.preferences, textvariable=self.feedback, bg=WHITE, fg=MUTED,
                 font=self.font(12), wraplength=self.p(400), anchor="w").pack(side="bottom", fill="x", pady=self.p(8))
        rows = tk.Frame(self.preferences, bg=WHITE)
        rows.pack(fill="x")
        row = setting_row(self, rows, "刷新间隔", "电量数据的自动读取频率")
        from interval_picker import IntervalPicker
        self.combo = IntervalPicker(row, self, self.interval,
                                    [label for _, label in tc.INTERVAL_CHOICES], scale=self.scale)
        self.combo.place(relx=1, rely=.5, x=-self.p(2), anchor="e")
        for title, detail, variable in (("自动刷新", "持续更新已连接设备的电量", self.auto),
                                        ("开机启动托盘", "登录 Windows 后在后台显示电量", self.boot),
                                        ("低电量提醒", "电量低于 10% 时发送系统通知", self.alert),
                                        ("GPU 动画加速", "加速视图切换，不支持时自动兼容", self.gpu)):
            row = setting_row(self, rows, title, detail)
            Switch(row, variable, scale=self.scale).place(relx=1, rely=.5, anchor="e", x=-self.p(2))
        manager._attach(self)

    def confirm_install(self, callback):
        ConfirmDialog(self.root, "准备安装更新", "将关闭 Nexus 并打开安装向导。覆盖安装会保留设备列表、历史记录和设置。", callback)

    def destroy(self):
        if self.manager.window is self:
            self.manager.cancel.set()
            self.manager.window = None
        if getattr(self.panel, "settings_window", None) is self:
            self.panel.settings_window = None
        super().destroy()

    def _save(self):
        import tray_common as tc
        panel = self.panel
        try:
            if self.boot.get() != tc.is_autostart_enabled() and not tc.set_autostart(self.boot.get()):
                raise OSError("开机启动设置未能保存，请重试")
            panel.auto = self.auto.get()
            panel.interval = next(sec for sec, label in tc.INTERVAL_CHOICES if label == self.interval.get())
            cfg = tc.load_config()
            cfg.update(interval=panel.interval, autostart=self.boot.get(), low_battery_alert=self.alert.get(),
                       gpu_animation=self.gpu.get())
            tc.save_config(cfg)
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
    """Reusable right column; preferences and update actions remain independent."""
    def __init__(self, master, dialog, manager, *, heading=False):
        import update_client
        self.manager = manager
        self.scale, self.p, self.font = dialog.scale, dialog.p, dialog.font
        super().__init__(master, bg=WHITE)
        if heading:
            section_heading(dialog, self, "软件更新", "当前版本 " + update_client.current_version(), close=dialog.destroy)
        self.status = tk.StringVar(value="点击“检查更新”获取最新正式版本。")
        status_box = tk.Frame(self, bg="#edf8f2", height=self.p(66))
        status_box.pack(fill="x", pady=(0, self.p(14)))
        status_box.pack_propagate(False)
        tk.Label(status_box, text="↻", bg="#edf8f2", fg="#16885b", font=self.font(26, "bold")).place(x=self.p(16), rely=.5, anchor="w")
        tk.Label(status_box, textvariable=self.status, bg="#edf8f2", fg="#25805d", font=self.font(14),
                 justify="left", wraplength=self.p(480 if heading else 690)).place(x=self.p(60), rely=.5, anchor="w")
        footer = tk.Frame(self, bg=WHITE)
        footer.pack(side="bottom", fill="x", pady=(self.p(14), 0))
        import webbrowser
        Button(footer, "发布记录", lambda: webbrowser.open("https://github.com/" + update_client.DEFAULT_REPOSITORY + "/releases"),
               scale=self.scale, width=92).pack(side="left")
        self.ignore = Button(footer, "忽略此版", manager._ignore, scale=self.scale, width=92)
        self.ignore.configure(state="disabled")
        self.ignore.pack(side="left", padx=self.p(8))
        self.primary = Button(footer, "检查更新", lambda: manager.check(manual=True), scale=self.scale, width=126, primary=True)
        self.primary.pack(side="right")
        auto_row = tk.Frame(self, bg=WHITE, height=self.p(42))
        auto_row.pack(side="bottom", fill="x", pady=(self.p(6), 0))
        auto_row.pack_propagate(False)
        self.auto = tk.BooleanVar(value=bool(update_client.load_state().get("auto_check")))
        tk.Label(auto_row, text="自动检查更新并提醒", bg=WHITE, fg=MUTED, font=self.font(13)).place(x=0, rely=.5, anchor="w")
        Switch(auto_row, self.auto, scale=self.scale,
               command=lambda: update_client.save_state(auto_check=self.auto.get())).place(relx=1, rely=.5, anchor="e")
        self.progress = Progress(self, scale=self.scale)
        self.progress.pack(side="bottom", fill="x", pady=(self.p(12), self.p(4)))
        notes_box = tk.Frame(self, bg=WHITE, highlightbackground=LINE, highlightthickness=1)
        notes_box.pack(fill="both", expand=True)
        tk.Label(notes_box, text="更新说明", bg=WHITE, fg=TEXT, font=self.font(14, "bold")).pack(anchor="w", padx=self.p(14), pady=(self.p(12), self.p(8)))
        area = tk.Frame(notes_box, bg=WHITE)
        area.pack(fill="both", expand=True, padx=self.p(12), pady=(0, self.p(10)))
        scroll = ttk.Scrollbar(area, orient="vertical")
        scroll.pack(side="right", fill="y")
        self.notes = tk.Text(area, height=5, width=30, bg=WHITE, fg=MUTED, bd=0, highlightthickness=0,
                             font=self.font(14), wrap="word", yscrollcommand=scroll.set, padx=2, pady=2)
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
        tk.Label(self.inner, text=text, bg=WHITE, fg=MUTED, font=self.font(15),
                 wraplength=self.p(580), justify="left").pack(fill="x", anchor="w", pady=self.p(12))
        footer = tk.Frame(self.inner, bg=WHITE)
        footer.pack(side="bottom", fill="x")
        def accept():
            self.destroy()
            confirm()
        Button(footer, "确认安装", accept, scale=self.scale, width=114, primary=True).pack(side="right")
        Button(footer, "返回", self.destroy, scale=self.scale, width=90).pack(side="right", padx=self.p(8))
