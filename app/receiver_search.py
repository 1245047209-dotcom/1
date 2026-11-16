"""Asynchronous receiver search dialog for the Nexus panel."""
from __future__ import annotations

from dataclasses import replace
import queue
import threading
import tkinter as tk
from tkinter import ttk, messagebox

from receiver_scan import scan_receivers
from bluetooth_devices import scan_bluetooth
from toggle_switch import Switch


class ReceiverSearch:
    def __init__(self, parent, *, on_add=None, is_added=None):
        self.on_add = on_add
        self.is_added = is_added or (lambda _: False)
        self.window = tk.Toplevel(parent)
        win = self.window
        win.title("Nexus · 搜索设备")
        win.geometry("980x660")
        win.minsize(820, 620)
        win.configure(bg="#f3f4f6")
        win.transient(parent)
        self.results = queue.Queue()
        self.devices = []
        self.busy = False
        self.closed = False
        self.timer = None
        win.protocol("WM_DELETE_WINDOW", self.close)
        win.bind("<Escape>", lambda _: self.close())
        win.bind("<F5>", lambda _: self.search())
        style = ttk.Style(win)
        style.configure("Receiver.Treeview", rowheight=34, font=("Microsoft YaHei UI", 10))
        style.configure("Receiver.Treeview.Heading", font=("Microsoft YaHei UI", 9, "bold"))

        header = tk.Frame(win, bg="#f3f4f6")
        header.pack(fill="x", padx=22, pady=(20, 8))
        tk.Label(header, text="搜索设备", bg="#f3f4f6", fg="#292c32",
                 font=("Microsoft YaHei UI", 17, "bold")).pack(side="left")
        self.search_button = ttk.Button(header, text="重新搜索", command=self.search)
        self.search_button.pack(side="right")
        self.subtitle = tk.Label(win, text="查找已插入电脑的无线接收器，显示型号与硬件 ID。",
                                 bg="#f3f4f6", fg="#6e747e", font=("Microsoft YaHei UI", 10))
        self.subtitle.pack(anchor="w", padx=22)
        modes = tk.Frame(win, bg="#f3f4f6")
        modes.pack(fill="x", padx=22, pady=(10, 0))
        self.mode = tk.StringVar(value="usb")
        for text, value in (("2.4G / USB 接收器", "usb"), ("蓝牙设备", "bluetooth")):
            ttk.Radiobutton(modes, text=text, variable=self.mode, value=value,
                            command=self.change_mode).pack(side="left", padx=(0, 22))
        options = tk.Frame(win, bg="#f3f4f6")
        options.pack(fill="x", padx=22, pady=12)
        self.status = tk.StringVar(value="准备搜索…")
        tk.Label(options, textvariable=self.status, bg="#f3f4f6", fg="#4a5059",
                 font=("Microsoft YaHei UI", 9)).pack(side="left")
        self.show_other = tk.BooleanVar(value=False)
        self.other_option = tk.Frame(options, bg="#f3f4f6")
        self.other_option.pack(side="right")
        self.other_switch = Switch(self.other_option, self.show_other, command=self.render)
        self.other_switch.pack(side="right")
        other_label = tk.Label(self.other_option, text="显示无线类型待确认的 USB 外设", bg="#f3f4f6",
                               fg="#292c32", font=("Microsoft YaHei UI", 9), cursor="hand2")
        other_label.pack(side="right", padx=(0, 8))
        other_label.bind("<Button-1>", lambda _: self.other_switch.invoke())
        table_frame = tk.Frame(win, bg="#fff")
        table_frame.pack(fill="both", expand=True, padx=22)
        columns = ("model", "maker", "id", "type", "state", "level")
        self.table = ttk.Treeview(table_frame, columns=columns, show="headings",
                                  selectmode="browse", height=5, style="Receiver.Treeview")
        for column, title, width in zip(columns, ("型号 / 系统名称", "厂商", "硬件 ID / 蓝牙地址", "设备类型", "连接状态", "电量"), (250, 95, 155, 90, 105, 75)):
            self.table.heading(column, text=title)
            self.table.column(column, width=width, minwidth=65 if column in ("type", "level") else 85,
                              stretch=column in ("model", "id"))
        scroll = ttk.Scrollbar(table_frame, orient="vertical", command=self.table.yview)
        self.table.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.table.pack(fill="both", expand=True)
        self.table.bind("<<TreeviewSelect>>", self.selection_changed)
        from aula_catalog import catalog
        self.aula_models = {m["model"]: m for m in catalog()["models"]}
        model_row = tk.Frame(win, bg="#f3f4f6")
        model_row.pack(fill="x", padx=22, pady=(8, 0))
        tk.Label(model_row, text="狼蛛型号", bg="#f3f4f6", fg="#4a5059", font=("Microsoft YaHei UI", 9)).pack(side="left")
        self.aula_choice = tk.StringVar(value="自动识别")
        self.aula_combo = ttk.Combobox(model_row, textvariable=self.aula_choice, state="disabled",
                                       values=["自动识别"] + list(self.aula_models), width=19)
        self.aula_combo.pack(side="left", padx=(8, 18))
        self.aula_combo.bind("<<ComboboxSelected>>", self.choose_aula_model)
        tk.Label(model_row, text="产品图配色", bg="#f3f4f6", fg="#4a5059", font=("Microsoft YaHei UI", 9)).pack(side="left")
        self.variant_choice = tk.StringVar(value="官网配色 1")
        self.variant_combo = ttk.Combobox(model_row, textvariable=self.variant_choice, state="disabled", width=12)
        self.variant_combo.pack(side="left", padx=(8, 0))
        self.variant_combo.bind("<<ComboboxSelected>>", self.choose_aula_variant)
        tk.Label(model_row, text="名称不明确时按实物选择", bg="#f3f4f6", fg="#7e8590", font=("Microsoft YaHei UI", 8)).pack(side="left", padx=12)
        details_frame = tk.Frame(win, bg="#fff")
        details_frame.pack(fill="both", padx=22, pady=(12, 8))
        self.detail = tk.Text(details_frame, height=6, wrap="word", font=("Microsoft YaHei UI", 9),
                              bg="#fff", fg="#454c55", relief="flat", padx=12, pady=9)
        detail_scroll = ttk.Scrollbar(details_frame, orient="vertical", command=self.detail.yview)
        self.detail.configure(yscrollcommand=detail_scroll.set)
        detail_scroll.pack(side="right", fill="y")
        self.detail.pack(side="left", fill="both", expand=True)
        self.set_detail("搜索后，选中一行查看识别依据和设备名称。")
        tk.Label(win, text="蓝牙列表显示 Windows 已配对设备；只有已连接且上报电量的设备显示百分比。",
                 font=("Microsoft YaHei UI", 8), bg="#f3f4f6", fg="#7e8590").pack(anchor="w", padx=22, pady=(0, 8))
        footer = tk.Frame(win, bg="#f3f4f6")
        footer.pack(fill="x", padx=22, pady=(0, 16))
        tk.Label(footer, text="选中外设后，点击“加入设备”保存到主面板。",
                 font=("Microsoft YaHei UI", 9), bg="#f3f4f6", fg="#6e747e").pack(side="left")
        self.add_button = ttk.Button(footer, text="加入设备", command=self.add_selected, state="disabled")
        self.add_button.pack(side="right", padx=(10, 0))
        self.copy_button = ttk.Button(footer, text="复制设备信息", command=self.copy, state="disabled")
        self.copy_button.pack(side="right")
        self.timer = win.after(100, self.pump)
        self.search()

    def change_mode(self):
        if self.mode.get() == "bluetooth":
            self.other_option.pack_forget()
            self.subtitle.configure(text="显示 Windows 已配对的蓝牙外设；未配对设备请先在系统设置中完成配对。")
        else:
            self.other_option.pack(side="right")
            self.subtitle.configure(text="查找已插入电脑的无线接收器，显示型号与硬件 ID。")
        self.table.delete(*self.table.get_children())
        self.copy_button.configure(state="disabled")
        self.add_button.configure(state="disabled")
        self.search()

    def search(self):
        if self.busy or self.closed:
            return
        self.busy = True
        mode = self.mode.get()
        self.devices = []
        self.table.delete(*self.table.get_children())
        self.copy_button.configure(state="disabled")
        self.add_button.configure(state="disabled", text="加入设备")
        self.search_button.configure(state="disabled", text="搜索中…")
        self.status.set("正在读取已配对蓝牙设备…" if mode == "bluetooth" else "正在扫描 USB 接收器…")
        self.set_detail("正在读取 Windows 设备名称，请稍候…")

        def worker():
            try:
                if mode == "bluetooth":
                    devices = scan_bluetooth(force=True)
                else:
                    from aula_protocols import identify_receivers
                    devices = identify_receivers(scan_receivers())
                self.results.put((mode, devices, None))
            except Exception as exc:
                self.results.put((mode, [], str(exc)))
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
            if mode != self.mode.get():
                self.search()
                self.timer = self.window.after(100, self.pump)
                return
            self.search_button.configure(state="normal", text="重新搜索")
            self.devices = devices
            if error:
                self.status.set("搜索失败，可点击重新搜索")
                self.set_detail("无法读取设备列表：" + error)
            else:
                self.render()
        self.timer = self.window.after(100, self.pump)

    def render(self):
        if self.busy:
            return
        self.table.delete(*self.table.get_children())
        bluetooth = self.mode.get() == "bluetooth"
        visible = [i for i, d in enumerate(self.devices) if bluetooth or d.wireless or self.show_other.get()]
        count = sum(d.wireless for d in self.devices)
        self.status.set(f"找到 {len(visible)} 台已配对蓝牙设备 · {sum(d.connected is True for d in self.devices)} 台已连接" if bluetooth
                        else f"找到 {count} 个无线接收器 / 外设 · 当前显示 {len(visible)} 个")
        for index in visible:
            d = self.devices[index]
            connected = getattr(d, "connected", None)
            state = ("已连接" if connected is True else "未连接" if connected is False else "状态未知") if bluetooth else "接收器在"
            battery = getattr(d, "battery", None)
            level = f"{battery}%" if connected is True and battery is not None else "--"
            self.table.insert("", "end", iid=str(index), values=(d.model, d.manufacturer, d.hardware_id, d.category, state, level))
        if visible:
            first = str(visible[0])
            self.table.selection_set(first)
            self.table.focus(first)
            self.selection_changed()
        else:
            self.copy_button.configure(state="disabled")
            self.add_button.configure(state="disabled", text="加入设备")
            self.set_detail("未找到已配对蓝牙设备，请先在 Windows 蓝牙设置中完成配对，再点击“重新搜索”。" if bluetooth else
                            "未找到名称明确的无线接收器。\n请插入接收器后点击“重新搜索”；"
                            "也可打开上方开关，查看无法确认无线类型的 USB 外设。\n"
                            "蓝牙设备、内置触控板和虚拟输入设备不在此列表内。")

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
