"""Desktop appearance controls and a live sample of the glass material."""

from pathlib import Path
import math
import tkinter as tk

from interval_picker import IntervalPicker
from toggle_switch import glass_capsule_png
import theme
import tray_common as tc


ASSETS = Path(__file__).resolve().parent / "assets"

class MaterialSlider(tk.Canvas):
    def __init__(self, master, scale, value, command):
        self.scale, self.value, self.command = scale, value, command
        self.focused = False
        super().__init__(master, bg=master.cget("bg"), width=round(158*scale), height=round(36*scale),
                         highlightthickness=0, takefocus=True, cursor="hand2")
        self.bind("<Configure>", lambda _event: self.draw())
        self.bind("<ButtonPress-1>", self.slide)
        self.bind("<B1-Motion>", self.slide)
        for key, amount in (("Left", -.05), ("Right", .05), ("Home", -1), ("End", 1)):
            self.bind(f"<{key}>", lambda _event, amount=amount: self.change(self.value+amount))
        for event, focused in (("<FocusIn>", True), ("<FocusOut>", False)):
            self.bind(event, lambda _event, focused=focused: self.set_focused(focused))
        theme.watch(self, self.apply_theme)

    def apply_theme(self):
        self.configure(bg=self.master.cget("bg"))
        self.draw()

    def extent(self):
        inset = round(31*self.scale) / 2 + 2
        return inset, self.winfo_width()-inset

    def slide(self, event):
        self.focus_set()
        left, right = self.extent()
        return self.change((event.x-left) / max(1, right-left))

    def set_focused(self, focused):
        self.focused = focused
        self.draw()

    def change(self, value):
        self.value = theme.glass_value(value)
        self.draw()
        self.command()
        return "break"

    def draw(self):
        left, right = self.extent()
        if right <= left:
            return
        self.delete("all")
        mid = self.winfo_height()/2
        center = left + (right-left)*self.value
        stroke = max(2, round(4*self.scale))
        self.create_line(left, mid, right, mid, fill=theme.color(self, theme.LINE),
                         width=stroke, capstyle="round")
        if center > left:
            self.create_line(left, mid, center, mid, fill=theme.color(self, theme.FOCUS),
                             width=stroke, capstyle="round")
        width, height = round(31*self.scale), round(21*self.scale)
        face = theme.color(self, theme.HIGHLIGHT)
        edge = theme.color(self, theme.FOCUS if self.focused else theme.EDGE)
        self.thumb = tk.PhotoImage(master=self, data=glass_capsule_png(width, height, face, edge,
                                                                       "#ffffff", reflection=.65))
        self.create_image(center, mid, image=self.thumb)


class MaterialPreview(tk.Canvas):
    def __init__(self, master, ui, mode, level):
        self.ui, self.mode, self.level = ui, mode, level
        super().__init__(master, bg=master.cget("bg"), width=1, height=1, highlightthickness=0, bd=0)
        self.bind("<Configure>", lambda _event: self.draw())
        self.product = ui.load_asset(ASSETS / "optimized" / "mouse-crop.png", (ui.px(57), ui.px(98)))
        theme.watch(self, self.draw)

    def draw(self):
        from app_dialogs import rounded

        width, height = self.winfo_width(), self.winfo_height()
        if min(width, height) < 50:
            return
        dark = self.mode() == "dark" or (self.mode() == "system" and theme.is_dark(self))
        color = lambda role: role.dark if dark else role.light
        self.configure(bg=color(theme.BACKGROUND))
        self.delete("all")
        base = "#334a6c" if dark else "#becff2"
        end = "#1e2234" if dark else "#e3dff2"
        radius = round(20*self.ui.factor)
        for y in range(1, height-1):
            corner = max(0, radius-min(y, height-y))
            inset = 1 + radius - math.sqrt(radius*radius-corner*corner)
            self.create_line(inset, y, width-inset, y, fill=theme.blend(base, end, y/height))
        margin = max(12, round(width*.085))
        self.create_text(margin, height*.15, anchor="w", text="设备总览",
                         fill=color(theme.TEXT), font=self.ui.font((theme.FONT, -16, "bold")))
        rounded(self, margin, height*.27, width-margin, height*.79, radius*.8,
                fill=color(theme.SURFACE), outline=color(theme.HIGHLIGHT))
        self.create_image(width*.76, height*.53, image=self.product)
        self.create_text(margin*2, height*.43, anchor="w", text="Logitech",
                         fill=color(theme.MUTED), font=self.ui.font((theme.FONT, -12)))
        self.create_text(margin*2, height*.55, anchor="w", text="PRO WIRELESS",
                         fill=color(theme.TEXT), font=self.ui.font((theme.FONT, -17, "bold")))
        w, h = min(round(width*.76), 208), round(36*self.ui.factor)
        opacity = .35 + .62*self.level()
        face = color(theme.SURFACE)
        self.sample = tk.PhotoImage(master=self, data=glass_capsule_png(w, h, face, color(theme.EDGE),
                                                                        color(theme.HIGHLIGHT),
                                                                        reflection=.63, opacity=round(opacity, 2)))
        x, y = width/2, height*.79
        self.create_image(x, y, image=self.sample)
        left = x-w/2+15
        self.create_oval(left, y-4, left+7, y+3, outline=color(theme.TEXT))
        self.create_line(left+6, y+2, left+10, y+6, fill=color(theme.TEXT))
        self.create_text(left+17, y, anchor="w", text="搜索设备", fill=color(theme.TEXT),
                         font=self.ui.font((theme.FONT, -13)))


class AppearancePanel(tk.Frame):
    def __init__(self, master, dialog):
        from app_dialogs import Button, GlassPanel

        super().__init__(master, bd=0)
        theme.style(self, bg=theme.BACKGROUND)
        self.dialog, self.panel = dialog, dialog.panel
        p, ui = dialog.p, dialog._nexus_ui
        panel_theme = self.panel.appearance
        self.mode = tk.StringVar(self, value=theme.MODE_LABELS[panel_theme.mode])
        self.feedback = tk.StringVar(self)

        footer = theme.widget(tk.Frame, self, bg=theme.BACKGROUND)
        footer.pack(side="bottom", fill="x", pady=(p(7), 0))
        theme.widget(tk.Label, footer, textvariable=self.feedback, bg=theme.BACKGROUND, fg=theme.MUTED,
                     font=dialog.font(12)).pack(side="left")
        self.save_button = Button(footer, "保存外观", self.save, scale=dialog.scale, width=102, primary=True)
        self.save_button.pack(side="right")

        content = theme.widget(tk.Frame, self, bg=theme.BACKGROUND, height=p(258))
        content.pack(fill="x")
        content.grid_propagate(False)
        content.columnconfigure(0, minsize=p(203))
        content.columnconfigure(1, weight=1)
        content.rowconfigure(0, weight=1)
        settings = GlassPanel(content, scale=dialog.scale)
        settings.grid(row=0, column=0, sticky="nsew", padx=(0, p(10)))
        fields = settings.body
        theme.widget(tk.Label, fields, text="主题", bg=theme.SURFACE, fg=theme.TEXT,
                     font=dialog.font(14)).pack(anchor="w", padx=p(9), pady=(p(6), 0))
        self.theme_picker = IntervalPicker(fields, dialog, self.mode, theme.MODE_LABELS.values(),
                                           scale=dialog.scale, show_clock=False,
                                           command=lambda _choice: self.preview.draw())
        self.theme_picker.pack(fill="x", padx=p(8), pady=(p(3), p(8)))
        theme.widget(tk.Frame, fields, bg=theme.LINE, height=1).pack(fill="x", padx=p(9))
        theme.widget(tk.Label, fields, text="Liquid Glass", bg=theme.SURFACE, fg=theme.TEXT,
                     font=dialog.font(14)).pack(anchor="w", padx=p(9), pady=(p(8), 0))
        self.slider = MaterialSlider(fields, dialog.scale, panel_theme.glass_level, lambda: self.preview.draw())
        self.slider.pack(fill="x", padx=p(8))
        endpoints = theme.widget(tk.Frame, fields, bg=theme.SURFACE)
        endpoints.pack(fill="x", padx=p(10))
        for text, side in (("通透", "left"), ("着色", "right")):
            theme.widget(tk.Label, endpoints, text=text, bg=theme.SURFACE, fg=theme.MUTED,
                         font=dialog.font(11)).pack(side=side)
        self.preview = MaterialPreview(content, ui, self.mode_key, lambda: self.slider.value)
        self.preview.grid(row=0, column=1, sticky="nsew")

    def mode_key(self):
        return next(mode for mode, label in theme.MODES if label == self.mode.get())

    def reload(self):
        panel_theme = self.panel.appearance
        self.mode.set(theme.MODE_LABELS[panel_theme.mode])
        self.slider.change(panel_theme.glass_level)
        self.feedback.set("")

    def save(self):
        cfg = tc.load_config()
        mode = self.mode_key()
        cfg.update(theme=mode, glass_level=self.slider.value)
        try:
            tc.save_config(cfg, strict=True)
        except OSError as error:
            self.feedback.set(str(error) or "外观设置保存失败")
            return
        self.panel.appearance.glass_level = self.slider.value
        self.panel.theme_choice.set(theme.MODE_LABELS[mode])
        changed = self.panel.appearance.set_mode(mode)
        if not changed:
            self.panel.draw()
        self.feedback.set("外观已保存")
