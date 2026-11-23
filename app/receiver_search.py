"""iOS-style 添加设备 window for the Nexus panel (replaces the old table + details layout)."""
from __future__ import annotations

from dataclasses import replace
import queue
import re
import threading
import tkinter as tk

import theme
from app_dialogs import DialogMetrics
from bluetooth_devices import scan_bluetooth
from receiver_scan import scan_receivers
from search_window import bind_window_drag, match_owner_size
from toggle_switch import Switch
from ui_scale import UIScale


SEARCH_MESSAGES = {
    "usb": "正在扫描 USB 接收器…",
    "bluetooth": "正在读取已配对蓝牙设备…",
}
AULA_IDS = ("3554:FA09", "258A:010C", "0C45:FEFE", "0C45:FEF9", "38A6:2908")
HEX_COLOR = re.compile(r"#[0-9a-fA-F]{6}")
AUTO = "自动识别"


def scan_devices(mode):
    """One search pass; patched by the demo so it never touches real hardware."""
    if mode == "bluetooth":
        return scan_bluetooth(force=True)
    from aula_protocols import identify_receivers
    return identify_receivers(scan_receivers())


def aula_eligible(device):
    from aula_catalog import model_for_device
    return getattr(device, "transport", "usb") == "usb" and (
        model_for_device(device) is not None or device.hardware_id.upper() in AULA_IDS
        or (device.manufacturer or "").casefold() == "aula")


def variant_swatches(model):
    """[(label, [colours])] for a catalog model; invalid colour values are skipped."""
    result = []
    for index, photo in enumerate(model.get("photos", [])):
        colors = [value for value in photo.get("swatches") or [] if isinstance(value, str) and HEX_COLOR.fullmatch(value)]
        result.append((f"配色 {index + 1}", colors))
    return result


def row_subtitle(device, bluetooth):
    parts = [device.hardware_id]
    if bluetooth:
        connected = getattr(device, "connected", None)
        parts.append("已连接" if connected is True else "未连接" if connected is False else "状态未知")
        battery = getattr(device, "battery", None)
        if connected is True and battery is not None:
            parts.append(f"{battery}%")
    else:
        if not device.wireless:
            parts.append("待确认")
        if device.category:
            parts.append(device.category)
    return " · ".join(part for part in parts if part)


def status_text(bluetooth, devices, visible, show_other):
    if bluetooth:
        connected = sum(getattr(device, "connected", None) is True for device in devices)
        return f"{len(visible)} 台已配对 · {connected} 台已连接"
    return f"显示 {len(visible)} 个 USB 设备" if show_other else f"找到 {len(visible)} 个无线接收器 / 外设"


class ReceiverSearch(DialogMetrics):
    def __init__(self, parent, *, on_add=None, is_added=None):
        from ios_controls import SegmentedControl, TextButton, ThinScrollbar, row
        self.on_add = on_add
        self.is_added = is_added or (lambda _: False)
        win = self.window = tk.Toplevel(parent)
        win.withdraw()
        win.title("Nexus · 添加设备")
        theme.style(win, bg=theme.BACKGROUND)
        theme.track_title_bar(win)
        win.transient(parent)
        win.resizable(False, False)
        self.ui = getattr(parent, "_nexus_ui", None) or UIScale(parent)
        self.scale = self.ui.factor
        self.panel = getattr(parent._root(), "_nexus_panel", None)
        self.results = queue.Queue()
        self.devices = []
        self.busy = False
        self.scanning_mode = None
        self.scan_error = None
        self.closed = False
        self.timer = None
        self.expanded = None       # instance id of the row showing its details
        self.pending = set()       # rows whose model / colour was changed by the user
        self.actions = {}          # instance id -> action text, for checks
        self.thumbs = []
        win.protocol("WM_DELETE_WINDOW", self.close)
        win.bind("<Escape>", lambda _: self.close())
        win.bind("<F5>", lambda _: self.search())

        header = theme.widget(tk.Frame, win, bg=theme.BACKGROUND, height=self.p(50))
        header.pack(fill="x")
        header.pack_propagate(False)
        title = theme.widget(tk.Label, header, text="添加设备", bg=theme.BACKGROUND, fg=theme.TEXT,
                             font=self.font(16, "bold"), bd=0)
        title.place(relx=.5, rely=.5, anchor="center")
        self.search_button = TextButton(header, "重新搜索", self.search, font=self.font(15))
        self.search_button.place(x=self.p(12), rely=.5, anchor="w")
        self.done_button = TextButton(header, "完成", self.close, font=self.font(15, "bold"))
        self.done_button.place(relx=1, x=-self.p(12), rely=.5, anchor="e")
        bind_window_drag(win, header, title)

        self.mode = tk.StringVar(master=win, value="usb")
        self.mode_tabs = SegmentedControl(win, (("usb", "2.4G / USB"), ("bluetooth", "蓝牙设备")), "usb",
                                          self.change_mode, p=self.p, font=self.font(13),
                                          bold=self.font(13, "bold"))
        self.mode_tabs.pack(fill="x", padx=self.p(16), pady=(0, self.p(4)))

        area = theme.widget(tk.Frame, win, bg=theme.BACKGROUND)
        area.pack(fill="both", expand=True)
        self.canvas = theme.widget(tk.Canvas, area, bg=theme.BACKGROUND, bd=0, highlightthickness=0,
                                   width=1, height=1, yscrollincrement=self.p(15))
        self.canvas.pack(side="left", fill="both", expand=True)
        self.scrollbar = ThinScrollbar(area, self.canvas.yview, p=self.p)
        self.canvas.configure(yscrollcommand=self._scrolled)
        self.content = theme.widget(tk.Frame, self.canvas, bg=theme.BACKGROUND)
        window = self.canvas.create_window(0, 0, anchor="nw", window=self.content)
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(window, width=e.width))
        self.content.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=(0, 0, e.width, e.height)))
        win.bind("<MouseWheel>", self._wheel, add="+")

        self.status = tk.StringVar(master=win, value="准备搜索…")
        theme.widget(tk.Label, self.content, textvariable=self.status, bg=theme.BACKGROUND, fg=theme.MUTED,
                     font=self.font(12.5), bd=0, anchor="w").pack(fill="x", padx=self.p(30), pady=(self.p(12), self.p(6)))
        self.list_holder = theme.widget(tk.Frame, self.content, bg=theme.BACKGROUND)
        self.list_holder.pack(fill="x", padx=self.p(16))
        self.feedback = tk.StringVar(master=win)
        self.feedback_label = theme.widget(tk.Label, self.content, textvariable=self.feedback, bg=theme.BACKGROUND,
                                           fg=theme.MUTED, font=self.font(12), anchor="w", justify="left", bd=0)
        self.feedback_label.pack(fill="x", padx=self.p(30), pady=(self.p(7), 0))
        self.feedback_label.bind("<Configure>", lambda e: self.feedback_label.configure(wraplength=max(1, e.width)))

        from ios_controls import Group
        self.options = theme.widget(tk.Frame, self.content, bg=theme.BACKGROUND)
        self.options.pack(fill="x", padx=self.p(16), pady=(self.p(18), 0))
        group = Group(self.options, p=self.p)
        group.pack(fill="x")
        self.show_other = tk.BooleanVar(master=win, value=False)
        line = row(self, group.body, "显示待确认的 USB 设备")
        self.other_switch = Switch(line, self.show_other, scale=self.scale, command=self.render)
        self.other_switch.place(relx=1, rely=.5, anchor="e", x=-self.p(14))
        self.hint = tk.StringVar(master=win)
        hint = theme.widget(tk.Label, self.content, textvariable=self.hint, bg=theme.BACKGROUND, fg=theme.MUTED,
                            font=self.font(12), anchor="w", justify="left", bd=0)
        hint.pack(fill="x", padx=self.p(30), pady=(self.p(7), self.p(18)))
        hint.bind("<Configure>", lambda e: hint.configure(wraplength=max(1, e.width)))

        self.menu = theme.widget(tk.Menu, win, tearoff=False, bg=theme.SURFACE, fg=theme.TEXT,
                                 activebackground=theme.SELECTED, activeforeground=theme.TEXT)
        from aula_catalog import catalog
        self.aula_models = {model["model"]: model for model in catalog()["models"]}
        self._update_hint()
        match_owner_size(win, parent)
        win.focus_set()  # Esc / F5 work at once; Tab reaches the segmented control
        self.timer = win.after(100, self.pump)
        self.search()

    # ---- scrolling ----------------------------------------------------------------
    def _scrolled(self, first, last):
        bar = self.scrollbar
        bar.set(first, last)
        fits = float(first) <= 0 and float(last) >= 1
        if fits and bar.winfo_manager():
            bar.pack_forget()
        elif not fits and not bar.winfo_manager():
            bar.pack(side="right", fill="y", before=self.canvas)

    def _wheel(self, event):
        first, last = self.canvas.yview()
        if first > 0 or last < 1:
            self.canvas.yview_scroll(-int(event.delta / 120) * 2, "units")
            return "break"

    # ---- searching ----------------------------------------------------------------
    def _update_hint(self):
        if self.mode.get() == "bluetooth":
            self.options.pack_forget()
            self.hint.set("列表来自 Windows 已配对的蓝牙设备。新设备请先在 Windows「蓝牙和其他设备」里配对。")
        else:
            if not self.options.winfo_manager():
                self.options.pack(fill="x", padx=self.p(16), pady=(self.p(18), 0), before=self.content.pack_slaves()[-1])
            self.hint.set("打开后也会列出无法确认能否读取电量的 USB 设备。点开狼蛛键盘一行可以更改型号和配色。")

    def _set_busy_ui(self):
        button = self.search_button
        button.command = None if self.busy else self.search
        button._fg = theme.MUTED if self.busy else theme.ACCENT
        button.configure(text="搜索中…" if self.busy else "重新搜索", fg=theme.color(button, button._fg))

    def change_mode(self, mode=None):
        if mode is not None:
            if mode == self.mode.get():
                return
            self.mode.set(mode)
        mode = self.mode.get()
        self.mode_tabs.set_selected(mode)
        self.expanded = None
        self.feedback.set("")
        self._update_hint()
        if self.busy:
            message = SEARCH_MESSAGES[mode] if self.scanning_mode == mode else "等待开始搜索…"
            self.clear_results(message)
        else:
            self.search()

    def clear_results(self, message):
        self.status.set(message)
        self.devices = []
        self._placeholder("正在查找设备…", None)

    def search(self):
        if self.busy or self.closed:
            return
        self.busy = True
        mode = self.mode.get()
        self.scanning_mode = mode
        self.scan_error = None
        self.devices = []
        self.pending.clear()
        self._set_busy_ui()
        self.clear_results(SEARCH_MESSAGES[mode])

        def worker():
            try:
                self.results.put((mode, scan_devices(mode), None))
            except Exception as exc:
                self.results.put((mode, [], str(exc) or type(exc).__name__))
        threading.Thread(target=worker, daemon=True).start()

    def pump(self):
        if self.closed:
            return
        try:
            mode, devices, error = self.results.get_nowait()
        except queue.Empty:
            pass
        else:
            self.busy = False
            self.scanning_mode = None
            if mode != self.mode.get():
                self.search()
                self.timer = self.window.after(100, self.pump)
                return
            self.scan_error = error
            self._set_busy_ui()
            self.devices = devices
            self.render()
        self.timer = self.window.after(100, self.pump)

    # ---- list -----------------------------------------------------------------------
    def visible_indexes(self):
        bluetooth = self.mode.get() == "bluetooth"
        return [i for i, d in enumerate(self.devices) if bluetooth or d.wireless or self.show_other.get()]

    def index_of(self, instance_id):
        return next(i for i, d in enumerate(self.devices) if d.instance_id == instance_id)

    def added(self, device):
        try:
            return bool(self.is_added(device))
        except (OSError, ValueError):
            return False

    def action_label(self, index):
        device = self.devices[index]
        pending = device.instance_id in self.pending
        if self.added(device):
            return "更新" if pending else "已添加"
        if not device.wireless and getattr(device, "transport", "usb") == "usb":
            return "仍然添加"
        return "添加"

    def _new_group(self):
        from ios_controls import Group
        for child in self.list_holder.winfo_children():
            child.destroy()
        self.thumbs, self.actions = [], {}
        group = Group(self.list_holder, p=self.p)
        group.pack(fill="x")
        return group.body

    def _placeholder(self, title, detail):
        body = self._new_group()
        frame = theme.widget(tk.Frame, body, bg=theme.SURFACE, height=self.p(64 if detail else 50))
        frame.pack(fill="x")
        frame.pack_propagate(False)
        theme.widget(tk.Label, frame, text=title, bg=theme.SURFACE, fg=theme.TEXT if detail else theme.MUTED,
                     font=self.font(15), bd=0).place(x=self.p(14), rely=.5, y=-self.p(9) if detail else 0, anchor="w")
        if detail:
            label = theme.widget(tk.Label, frame, text=detail, bg=theme.SURFACE, fg=theme.MUTED,
                                 font=self.font(12), bd=0, anchor="w", justify="left")
            label.place(x=self.p(14), rely=.5, y=self.p(11), anchor="w", relwidth=1, width=-self.p(28))

    def render(self):
        if self.busy or self.closed:
            return
        if self.scan_error is not None:
            self.status.set("搜索失败")
            self._placeholder("搜索失败，请点「重新搜索」", "无法读取设备列表：" + self.scan_error)
            return
        bluetooth = self.mode.get() == "bluetooth"
        visible = self.visible_indexes()
        self.status.set(status_text(bluetooth, self.devices, visible, self.show_other.get()))
        if not visible:
            if bluetooth:
                self._placeholder("未找到已配对的蓝牙设备", "请先在 Windows 蓝牙设置中完成配对，再点「重新搜索」。")
            else:
                self._placeholder("未找到可显示的 USB 设备", "插入接收器后点「重新搜索」，或打开下方的「显示待确认的 USB 设备」。")
            return
        top = self.canvas.yview()[0]
        body = self._new_group()
        for index in visible:
            self._device_row(body, index, bluetooth)
        self.content.update_idletasks()
        self.canvas.yview_moveto(top)

    def _thumbnail(self, frame, device):
        p = self.p
        size = p(38)
        canvas = theme.widget(tk.Canvas, frame, bg=theme.SURFACE, width=size, height=size, highlightthickness=0, bd=0)
        from ios_art import rounded_surface_png
        plate = tk.PhotoImage(master=canvas, format="PNG", data=rounded_surface_png(
            size, size, p(9), theme.color(canvas, theme.SOFT)))
        canvas.create_image(0, 0, image=plate, anchor="nw")
        images = [plate]
        photo = None
        if self.panel is not None:
            try:
                from my_devices import product_image_kind
                logical = self.panel.fitted_product(product_image_kind(device), 36, 32)
                if logical is not None:
                    photo = self.panel.ui.images.get(str(logical)) or self.panel.ui.image(str(logical))
            except Exception:  # a missing picture must not break the list
                photo = None
        if photo is not None:
            images.append(photo)
            canvas.create_image(size / 2, size / 2, image=photo)
        else:
            name = "bluetooth" if getattr(device, "transport", "usb") == "bluetooth" else "usb"
            text, family = theme.glyph(canvas, name)
            canvas.create_text(size / 2, size / 2, text=text, fill=theme.color(canvas, theme.ACCENT),
                               font=(family, -p(16)))
        self.thumbs.append(images)
        return canvas

    def _device_row(self, body, index, bluetooth):
        from ios_controls import PillButton
        p = self.p
        device = self.devices[index]
        frame = theme.widget(tk.Frame, body, bg=theme.SURFACE, height=p(60), cursor="hand2")
        frame.pack(fill="x")
        frame.pack_propagate(False)
        label_x = p(12 + 38 + 12)
        if body.rows:
            theme.widget(tk.Frame, frame, bg=theme.LINE, height=1).place(x=label_x, y=0, relwidth=1,
                                                                          width=-label_x, height=1)
        body.rows += 1
        thumb = self._thumbnail(frame, device)
        thumb.place(x=p(12), rely=.5, anchor="w")
        title = theme.widget(tk.Label, frame, text=device.model, bg=theme.SURFACE, fg=theme.TEXT,
                             font=self.font(15), bd=0, anchor="w")
        title.place(x=label_x, rely=.5, y=-p(9), anchor="w", relwidth=1, width=-label_x - p(110))
        subtitle = theme.widget(tk.Label, frame, text=row_subtitle(device, bluetooth), bg=theme.SURFACE,
                                fg=theme.MUTED, font=self.font(12), bd=0, anchor="w")
        subtitle.place(x=label_x, rely=.5, y=p(11), anchor="w", relwidth=1, width=-label_x - p(110))
        action = self.action_label(index)
        self.actions[device.instance_id] = action
        if action == "已添加":
            check, family = theme.glyph(frame, "check")
            done = theme.widget(tk.Label, frame, text=check + " 已添加", bg=theme.SURFACE, fg=theme.MUTED,
                                font=self.font(13), bd=0)
            done.configure(font=self.font(13))
            done.place(relx=1, x=-p(14), rely=.5, anchor="e")
        else:
            PillButton(frame, action, lambda i=index: self.add(i), p=p,
                       font=self.font(13, "bold")).place(relx=1, x=-p(12), rely=.5, anchor="e")
        for target in (frame, thumb, title, subtitle):
            target.bind("<ButtonRelease-1>", lambda _e, i=index: self.toggle(i))
            target.bind("<ButtonRelease-3>", lambda e, i=index: self.row_menu(e, i))
        if self.expanded == device.instance_id:
            self._details(body, index, label_x)

    def _details(self, body, index, label_x):
        from ios_controls import TextButton
        p = self.p
        device = self.devices[index]
        holder = theme.widget(tk.Frame, body, bg=theme.SURFACE)
        holder.pack(fill="x")
        if aula_eligible(device):
            from aula_catalog import model_for_device
            from aula_protocols import legacy_default
            model = model_for_device(device)
            self.model_value = tk.StringVar(holder, model["model"] if model else AUTO)
            self._value_row(holder, label_x, "狼蛛型号", self.model_value,
                            lambda widget: self.model_menu(widget, index))
            variants = variant_swatches(model) if model and not legacy_default(device) else []
            if variants:
                current = variants[min(getattr(device, "product_variant", 0), len(variants) - 1)][0]
                self.variant_value = tk.StringVar(holder, current)
                self._value_row(holder, label_x, "配色", self.variant_value,
                                lambda widget: self.variant_menu(widget, index, variants))
        text = device.details().split("\n\n")[0]
        details = theme.widget(tk.Label, holder, text=text, bg=theme.SURFACE, fg=theme.MUTED, font=self.font(12),
                               bd=0, anchor="w", justify="left")
        details.pack(fill="x", padx=(label_x, p(14)), pady=(p(8), 0))
        details.bind("<Configure>", lambda e: details.configure(wraplength=max(1, e.width)))
        copy = TextButton(holder, "复制设备信息", lambda: self.copy(index), font=self.font(14), bg=theme.SURFACE)
        copy.pack(anchor="w", padx=(label_x - p(4), 0), pady=(p(6), p(10)))

    def _value_row(self, holder, label_x, title, variable, open_menu):
        p = self.p
        frame = theme.widget(tk.Frame, holder, bg=theme.SURFACE, height=p(42), cursor="hand2")
        frame.pack(fill="x")
        frame.pack_propagate(False)
        theme.widget(tk.Frame, frame, bg=theme.LINE, height=1).place(x=label_x, y=0, relwidth=1,
                                                                      width=-label_x, height=1)
        theme.widget(tk.Label, frame, text=title, bg=theme.SURFACE, fg=theme.TEXT, font=self.font(14),
                     bd=0).place(x=label_x, rely=.5, anchor="w")
        arrow, family = theme.glyph(frame, "down")
        theme.widget(tk.Label, frame, text=arrow, bg=theme.SURFACE, fg=theme.ACCENT, font=(family, -p(10)),
                     bd=0).place(relx=1, x=-p(14), rely=.5, anchor="e")
        value = theme.widget(tk.Label, frame, textvariable=variable, bg=theme.SURFACE, fg=theme.ACCENT,
                             font=self.font(14), bd=0)
        value.place(relx=1, x=-p(30), rely=.5, anchor="e")
        for target in (frame, *frame.winfo_children()):
            target.configure(cursor="hand2")
            target.bind("<ButtonRelease-1>", lambda _e, widget=value: open_menu(widget))

    # ---- interactions -------------------------------------------------------------
    def toggle(self, index):
        key = self.devices[index].instance_id
        self.expanded = None if self.expanded == key else key
        self.render()

    def _popup(self, widget):
        try:
            self.menu.tk_popup(widget.winfo_rootx(), widget.winfo_rooty() + widget.winfo_height())
        finally:
            self.menu.grab_release()

    def row_menu(self, event, index):
        self.menu.delete(0, "end")
        expanded = self.expanded == self.devices[index].instance_id
        self.menu.add_command(label="收起识别信息" if expanded else "查看识别信息", command=lambda: self.toggle(index))
        self.menu.add_command(label="复制设备信息", command=lambda: self.copy(index))
        try:
            self.menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.menu.grab_release()

    def model_menu(self, widget, index):
        self.menu.delete(0, "end")
        for name in [AUTO, *self.aula_models]:
            self.menu.add_radiobutton(label=name, variable=self.model_value, value=name,
                                      command=lambda name=name: self.choose_model(index, name))
        self._popup(widget)

    def variant_menu(self, widget, index, variants):
        self.menu.delete(0, "end")
        self.swatch_images = []
        size = self.p(14)
        for number, (label, colors) in enumerate(variants):
            image = tk.PhotoImage(master=self.window, width=size * 2, height=size)
            fill = colors or [theme.color(self.window, theme.SOFT)]
            image.put(fill[0], to=(0, 0, size, size))
            image.put(fill[-1], to=(size, 0, size * 2, size))
            self.swatch_images.append(image)
            self.menu.add_radiobutton(label="  " + label, image=image, compound="left", variable=self.variant_value,
                                      value=label, command=lambda n=number: self.choose_variant(index, n))
        self._popup(widget)

    def choose_model(self, index, name):
        if name == AUTO:
            self.search()  # re-identify from the hardware
            return
        model = self.aula_models.get(name)
        if model is None:
            return
        device = self.devices[index]
        self.devices[index] = replace(device, model=model["model"], manufacturer="AULA", category="键盘", product_variant=0,
                                      identification="用户指定型号；电量支持仍按实际硬件版本校验")
        self.pending.add(device.instance_id)
        self.feedback.set(f"型号改为 {model['model']}，点「{self.action_label(index)}」生效。")
        self.render()

    def choose_variant(self, index, number):
        device = self.devices[index]
        self.devices[index] = replace(device, product_variant=number)
        self.pending.add(device.instance_id)
        self.feedback.set(f"已选择配色 {number + 1}，点「{self.action_label(index)}」生效。")
        self.render()

    def add(self, index):
        if not self.on_add:
            return
        device = self.devices[index]
        updating = self.added(device)
        try:
            created = self.on_add(device)
        except (OSError, ValueError) as exc:
            self.feedback_label.configure(fg=theme.color(self.window, theme.DANGER))
            self.feedback.set("添加失败：" + (str(exc) or type(exc).__name__))
            return
        self.feedback_label.configure(fg=theme.color(self.window, theme.MUTED))
        self.pending.discard(device.instance_id)
        if updating:
            message = f"已更新 {device.model}。"
        elif created:
            message = f"已添加 {device.model}，可以继续添加，或点「完成」返回。"
        else:
            message = f"{device.model} 已在设备列表中。"
        self.feedback.set(message)
        self.render()

    def copy(self, index):
        self.window.clipboard_clear()
        self.window.clipboard_append(self.devices[index].details())
        self.feedback_label.configure(fg=theme.color(self.window, theme.MUTED))
        self.feedback.set("已复制设备信息。")

    def close(self):
        if self.closed:
            return
        self.closed = True
        if self.timer is not None:
            self.window.after_cancel(self.timer)
            self.timer = None
        self.window.destroy()
