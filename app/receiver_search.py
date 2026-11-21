"""Asynchronous receiver search dialog for the Nexus panel."""
from __future__ import annotations

from dataclasses import replace
import queue
import threading
import tkinter as tk
from tkinter import ttk, messagebox

from app_dialogs import FONT, Button, brand_mark
import theme
from bluetooth_devices import scan_bluetooth
from receiver_scan import scan_receivers
from refresh_button import RefreshButton
from search_window import bind_window_drag, match_owner_size
from toggle_switch import Switch
from ui_scale import UIScale
from view_switcher import ViewSwitcher


SEARCH_MESSAGES = {
    "usb": "正在扫描 USB 接收器…",
    "bluetooth": "正在读取已配对蓝牙设备…",
}


class SearchRefreshButton(RefreshButton):
    LABELS = {**RefreshButton.LABELS, "idle": "重新搜索", "loading": "搜索中"}


def configure_search_styles(window):
    style = ttk.Style(window)
    surface = theme.color(window, theme.SURFACE)
    text = theme.color(window, theme.TEXT)
    style.layout("Receiver.Treeview", [("Treeview.treearea", {"sticky": "nsew"})])
    style.configure("Receiver.Treeview", rowheight=23, font=(FONT, -12), borderwidth=0,
                    background=surface, fieldbackground=surface, foreground=text)
    selection_text = theme.color(window, theme.Color("#3e3a93", theme.TEXT.dark))
    style.map("Receiver.Treeview", background=[("selected", theme.color(window, theme.SELECTED))],
              foreground=[("selected", selection_text)])
    # The native Windows heading ignores background colors. Use a local
    # heading element so other tables and dialogs keep their existing theme.
    element = "Receiver.heading.border"
    if element not in style.element_names():
        style.element_create(element, "from", "clam", "Treeheading.border")
    style.layout("Receiver.Treeview.Heading", [(element, {"sticky": "nsew", "children": [
        ("Treeheading.padding", {"sticky": "nsew", "children": [
            ("Treeheading.text", {"sticky": "w"}),
        ]}),
    ]})])
    style.configure("Receiver.Treeview.Heading", background=theme.color(window, theme.SOFT),
                    foreground=theme.color(window, theme.MUTED),
                    borderwidth=0, relief="flat", padding=(5, 4), font=(FONT, -11, "bold"))


class ReceiverSearch:
    def __init__(self, parent, *, on_add=None, is_added=None):
        self.on_add = on_add
        self.is_added = is_added or (lambda _: False)
        win = self.window = tk.Toplevel(parent)
        win.withdraw()
        win.title("Nexus · 搜索设备")
        theme.style(win, bg=theme.BACKGROUND)
        theme.track_title_bar(win)
        win.transient(parent)
        win.resizable(False, False)
        self.ui = getattr(parent, "_nexus_ui", None) or UIScale(parent)
        self.results = queue.Queue()
        self.devices = []
        self.busy = False
        self.scanning_mode = None
        self.scan_error = None
        self.closed = False
        self.timer = None
        win.protocol("WM_DELETE_WINDOW", self.close)
        win.bind("<Escape>", lambda _: self.close())
        win.bind("<F5>", lambda _: self.search())
        win.columnconfigure(0, weight=1)
        win.rowconfigure(3, weight=1)

        header = theme.widget(tk.Frame, win, bg=theme.BACKGROUND)
        header.grid(row=0, column=0, sticky="ew", padx=12, pady=(8, 5))
        brand = brand_mark(header, self.ui)
        brand.pack(side="left", padx=(0, 8))
        title = theme.widget(tk.Label, header, text="搜索设备", bg=theme.BACKGROUND, fg=theme.TEXT,
                             font=(FONT, -18, "bold"), bd=0)
        title.pack(side="left")
        self.search_button = SearchRefreshButton(header, self.search, ui=self.ui)
        theme.style(self.search_button, bg=theme.BACKGROUND)
        self.search_button.pack(side="right")
        bind_window_drag(win, header, brand, title)

        toolbar = theme.widget(tk.Frame, win, bg=theme.SURFACE)
        toolbar.grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 5))
        self.mode = tk.StringVar(master=win, value="usb")
        self.mode_tabs = ViewSwitcher(toolbar, ui=self.ui, command=self.change_mode, show_icons=False)
        self.mode_tabs.set_items((("usb", "2.4G / USB", ""), ("bluetooth", "蓝牙设备", "")), "usb")
        self.mode_tabs.pack(side="left", padx=(3, 0), pady=1)
        self.show_other = tk.BooleanVar(master=win, value=False)
        self.other_option = theme.widget(tk.Frame, toolbar, bg=theme.SURFACE)
        self.other_option.pack(side="right", padx=6)
        self.other_switch = Switch(self.other_option, self.show_other, scale=self.ui.factor, command=self.render)
        self.other_switch.pack(side="right")
        other_label = theme.widget(tk.Label, self.other_option, text="显示待确认 USB",
                                   bg=theme.SURFACE, fg=theme.TEXT, font=(FONT, -11), cursor="hand2")
        other_label.pack(side="right", padx=(0, 5))
        other_label.bind("<Button-1>", lambda _: self.other_switch.invoke())

        self.status = tk.StringVar(master=win, value="准备搜索…")
        status_label = theme.widget(tk.Label, win, textvariable=self.status, bg=theme.BACKGROUND,
                                    fg=theme.MUTED, font=(FONT, -11), anchor="w")
        status_label.grid(row=2, column=0, sticky="ew", padx=12)
        self.body = theme.widget(tk.Frame, win, bg=theme.BACKGROUND)
        self.body.grid(row=3, column=0, sticky="nsew", padx=12)
        self.body.columnconfigure(0, weight=1)
        self.body.rowconfigure(0, weight=1)
        self.table_frame = theme.widget(tk.Frame, self.body, bg=theme.SURFACE,
                                        highlightbackground=theme.LINE, highlightthickness=1)
        columns = ("model", "maker", "id", "type", "state", "level")
        self.table = ttk.Treeview(self.table_frame, columns=columns, displaycolumns=("model", "state", "level"),
                                  show="headings", selectmode="browse", height=2, style="Receiver.Treeview")
        for column, label, width in (("model", "设备名称", 245), ("state", "状态", 69), ("level", "电量", 52)):
            align = "w" if column == "model" else "center"
            self.table.heading(column, text=label, anchor=align)
            self.table.column(column, width=width, minwidth=width if column != "model" else 80,
                              stretch=column == "model", anchor=align)
        scroll = ttk.Scrollbar(self.table_frame, orient="vertical", command=self.table.yview)
        self.table.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.table.pack(fill="both", expand=True)
        self.table.bind("<<TreeviewSelect>>", self.selection_changed)
        self.table.bind("<Double-Button-1>", self.open_selected_details)
        self.empty_label = theme.widget(tk.Label, self.table_frame, bg=theme.SURFACE, fg=theme.MUTED,
                                        font=(FONT, -12))

        self.details_panel = theme.widget(tk.Frame, self.body, bg=theme.SURFACE,
                                          highlightbackground=theme.LINE, highlightthickness=1)
        theme.widget(tk.Label, self.details_panel, text="设备详情", bg=theme.SURFACE, fg=theme.TEXT,
                     font=(FONT, -12, "bold")).pack(anchor="w", padx=8, pady=(4, 0))
        from aula_catalog import catalog
        self.aula_models = {model["model"]: model for model in catalog()["models"]}
        self.model_row = theme.widget(tk.Frame, self.details_panel, bg=theme.SURFACE)
        theme.widget(tk.Label, self.model_row, text="狼蛛型号", bg=theme.SURFACE,
                     fg=theme.MUTED, font=(FONT, -11)).pack(side="left")
        self.aula_choice = tk.StringVar(master=win, value="自动识别")
        self.aula_combo = ttk.Combobox(self.model_row, textvariable=self.aula_choice, state="disabled",
                                      values=["自动识别"] + list(self.aula_models), width=12, font=(FONT, -12))
        self.aula_combo.pack(side="left", padx=(4, 8))
        self.aula_combo.bind("<<ComboboxSelected>>", self.choose_aula_model)
        theme.widget(tk.Label, self.model_row, text="配色", bg=theme.SURFACE,
                     fg=theme.MUTED, font=(FONT, -11)).pack(side="left")
        self.variant_choice = tk.StringVar(master=win)
        self.variant_combo = ttk.Combobox(self.model_row, textvariable=self.variant_choice,
                                         state="disabled", width=11, font=(FONT, -12))
        self.variant_combo.pack(side="left", padx=(4, 0))
        self.variant_combo.bind("<<ComboboxSelected>>", self.choose_aula_variant)
        self.details_frame = theme.widget(tk.Frame, self.details_panel, bg=theme.SOFT)
        self.details_frame.pack(side="bottom", fill="both", expand=True, padx=6, pady=5)
        self.detail = theme.widget(tk.Text, self.details_frame, height=1, wrap="word", font=(FONT, -12),
                                  bg=theme.SOFT, fg=theme.TEXT, relief="flat", highlightthickness=0,
                                  padx=7, pady=3, spacing3=2)
        detail_scroll = ttk.Scrollbar(self.details_frame, orient="vertical", command=self.detail.yview)
        self.detail.configure(yscrollcommand=detail_scroll.set)
        detail_scroll.pack(side="right", fill="y")
        self.detail.pack(side="left", fill="both", expand=True)

        footer = theme.widget(tk.Frame, win, bg=theme.BACKGROUND)
        footer.grid(row=4, column=0, sticky="ew", padx=12, pady=(6, 8))
        self.details_button = Button(footer, "查看详情", self.toggle_details,
                                     scale=self.ui.factor, width=93, height=36)
        self.add_button = Button(footer, "加入设备", self.add_selected, scale=self.ui.factor,
                                 width=134, height=36, primary=True)
        self.add_button.pack(side="right", padx=(7, 0))
        self.copy_button = Button(footer, "复制设备信息", self.copy, scale=self.ui.factor, width=116, height=36)
        self.copy_button.pack(side="right")
        self.details_only = False
        self.stacked = None
        win.bind("<Configure>", self.on_configure, add="+")
        theme.watch(win, self.apply_theme)
        self.apply_theme()
        self.layout_panels(False)
        match_owner_size(win, parent)
        self.mode_tabs.focus_set()
        self.timer = win.after(100, self.pump)
        self.search()

    def on_configure(self, event):
        if event.widget is self.window:
            self.layout_panels(event.height >= self.ui.px(460))

    def layout_panels(self, stacked):
        if stacked == self.stacked:
            return
        self.stacked = stacked
        self.arrange_panels()

    def arrange_panels(self):
        self.table_frame.grid_remove()
        self.details_panel.grid_remove()
        if self.stacked:
            self.table_frame.grid(row=0, column=0, sticky="nsew")
            self.details_panel.grid(row=1, column=0, sticky="nsew", pady=(6, 0))
            self.body.rowconfigure(1, weight=1, minsize=102)
            self.details_button.pack_forget()
        else:
            self.body.rowconfigure(1, weight=0, minsize=0)
            target = self.details_panel if self.details_only else self.table_frame
            target.grid(row=0, column=0, sticky="nsew")
            self.details_button.configure(text="返回列表" if self.details_only else "查看详情")
            self.details_button.pack(side="left")

    def toggle_details(self):
        if not self.stacked:
            self.details_only = not self.details_only
            self.arrange_panels()

    def open_selected_details(self, event):
        if not self.stacked and self.table.identify_row(event.y):
            self.details_only = True
            self.arrange_panels()

    def apply_theme(self):
        configure_search_styles(self.window)
        alternate = theme.color(self.window, theme.Color("#f6f2f9", "#28262f"))
        self.table.tag_configure("alternate", background=alternate)
        for combo in (self.aula_combo, self.variant_combo):
            theme.style_combobox_popup(combo)

    def change_mode(self, mode=None):
        if mode is not None:
            if mode == self.mode.get():
                return
            self.mode.set(mode)
        mode = self.mode.get()
        self.mode_tabs.set_selected(mode)
        if mode == "bluetooth":
            self.other_option.pack_forget()
        else:
            self.other_option.pack(side="right")
        if self.busy:
            message = SEARCH_MESSAGES[mode] if self.scanning_mode == mode else "等待开始搜索…"
            self.clear_results(message)
        else:
            self.search()

    def clear_selection(self):
        self.model_row.pack_forget()
        self.aula_combo.configure(state="disabled")
        self.variant_combo.configure(state="disabled")
        self.copy_button.configure(state="disabled")
        self.add_button.configure(state="disabled", text="加入设备")

    def clear_results(self, message):
        self.table.delete(*self.table.get_children())
        self.clear_selection()
        self.status.set(message)
        self.show_placeholder("正在查找设备", "找到设备后，选中一行查看详情。")

    def show_placeholder(self, title, detail):
        self.empty_label.configure(text=title)
        self.empty_label.place(relx=.5, rely=.58, anchor="center")
        self.set_detail(detail)

    def search(self):
        if self.busy or self.closed:
            return
        self.busy = True
        mode = self.mode.get()
        self.scanning_mode = mode
        self.scan_error = None
        self.devices = []
        self.search_button.set_state("loading")
        self.clear_results(SEARCH_MESSAGES[mode])

        def worker():
            try:
                if mode == "bluetooth":
                    devices = scan_bluetooth(force=True)
                else:
                    from aula_protocols import identify_receivers
                    devices = identify_receivers(scan_receivers())
                self.results.put((mode, devices, None))
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
            self.search_button.set_state("error" if error is not None else "idle")
            self.devices = devices
            self.render()
        self.timer = self.window.after(100, self.pump)

    def render(self):
        if self.busy:
            return
        if self.scan_error is not None:
            self.status.set("搜索失败，请点击“重试”")
            self.show_placeholder("搜索失败", "无法读取设备列表：" + self.scan_error)
            return
        selected = self.table.selection()
        self.table.delete(*self.table.get_children())
        bluetooth = self.mode.get() == "bluetooth"
        visible = [i for i, d in enumerate(self.devices) if bluetooth or d.wireless or self.show_other.get()]
        if bluetooth:
            connected_count = sum(device.connected is True for device in self.devices)
            self.status.set(f"{len(visible)} 台已配对 · {connected_count} 台已连接")
        else:
            self.status.set(f"显示 {len(visible)} 个 USB 设备" if self.show_other.get()
                            else f"找到 {len(visible)} 个无线接收器 / 外设")
        for row, index in enumerate(visible):
            d = self.devices[index]
            connected = getattr(d, "connected", None)
            state = ("已连接" if connected is True else "未连接" if connected is False else "状态未知") if bluetooth else "已检测"
            battery = getattr(d, "battery", None)
            level = f"{battery}%" if connected is True and battery is not None else "--"
            self.table.insert("", "end", iid=str(index),
                              values=(d.model, d.manufacturer, d.hardware_id, d.category, state, level),
                              tags=("alternate",) if row % 2 else ())
        if visible:
            self.empty_label.place_forget()
            current = selected[0] if selected and self.table.exists(selected[0]) else str(visible[0])
            self.table.selection_set(current)
            self.table.focus(current)
            self.table.see(current)
            self.selection_changed()
        else:
            self.clear_selection()
            if bluetooth:
                self.show_placeholder("未找到已配对的蓝牙设备",
                                      "请先在 Windows 蓝牙设置中完成配对，再点击“重新搜索”。")
            else:
                self.show_placeholder("未找到可显示的 USB 设备",
                                      "请插入接收器后重新搜索，也可开启“显示待确认的 USB 外设”。")

    def selection_changed(self, _event=None):
        selected = self.table.selection()
        if not selected:
            return
        device = self.devices[int(selected[0])]
        from aula_catalog import model_for_device
        model = model_for_device(device)
        eligible = (getattr(device, "transport", "usb") == "usb" and
                    (model is not None or device.hardware_id.upper() in ("3554:FA09", "258A:010C", "0C45:FEFE", "0C45:FEF9", "38A6:2908")
                     or device.manufacturer.casefold() == "aula"))
        if eligible:
            self.model_row.pack(fill="x", padx=8, pady=(3, 0), before=self.details_frame)
        else:
            self.model_row.pack_forget()
        self.aula_combo.configure(state="readonly" if eligible else "disabled")
        self.aula_choice.set(model["model"] if model else "自动识别")
        from aula_protocols import legacy_default
        variants = [f"官网配色 {i+1}" for i in range(len(model["photos"]))] if model and eligible and not legacy_default(device) else []
        self.variant_combo.configure(values=variants, state="readonly" if variants else "disabled")
        self.variant_choice.set(variants[min(getattr(device, "product_variant", 0), len(variants)-1)] if variants else "")
        self.set_detail(device.details())
        self.copy_button.configure(state="normal")
        try:
            added = self.is_added(device)
        except (OSError, ValueError):
            added = False
        self.add_button.configure(text="已加入 · 查看" if added else "加入设备",
                                  state="normal" if self.on_add else "disabled")

    def choose_aula_model(self, _event=None):
        selected = self.table.selection()
        model = self.aula_models.get(self.aula_choice.get())
        if selected and self.aula_choice.get() == "自动识别":
            self.search()
            return
        if not selected or model is None:
            return
        index = int(selected[0])
        device = self.devices[index]
        self.devices[index] = replace(device, model=model["model"], manufacturer="AULA", category="键盘", product_variant=0,
                                      identification="用户指定型号；电量支持仍按实际硬件版本校验")
        values = list(self.table.item(selected[0], "values"))
        values[:2] = [model["model"], "AULA"]
        self.table.item(selected[0], values=values)
        self.selection_changed()
        self.add_button.configure(text="应用型号并加入")

    def choose_aula_variant(self, _event=None):
        selected = self.table.selection()
        if selected and self.variant_combo.current() >= 0:
            index = int(selected[0])
            self.devices[index] = replace(self.devices[index], product_variant=self.variant_combo.current())
            self.add_button.configure(text="应用配色并加入")

    def add_selected(self):
        selected = self.table.selection()
        if not selected or not self.on_add:
            return
        try:
            created = self.on_add(self.devices[int(selected[0])])
        except (OSError, ValueError) as exc:
            messagebox.showerror("加入设备失败", str(exc), parent=self.window)
            return
        self.selection_changed()
        self.status.set("设备已加入，可在主面板的“我的设备”中切换" if created else "设备已在列表中，已切换到主面板")
        self.close()

    def set_detail(self, text):
        self.detail.configure(state="normal")
        self.detail.delete("1.0", "end")
        self.detail.insert("1.0", text)
        self.detail.configure(state="disabled")

    def copy(self):
        selected = self.table.selection()
        if selected:
            self.window.clipboard_clear()
            self.window.clipboard_append(self.devices[int(selected[0])].details())
            self.status.set("已复制所选设备信息")

    def close(self):
        if self.closed:
            return
        self.closed = True
        if self.timer is not None:
            self.window.after_cancel(self.timer)
            self.timer = None
        self.window.destroy()
