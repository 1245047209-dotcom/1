"""Non-blocking update window shared by the main panel and the tray."""
from __future__ import annotations

import queue
import threading
import time
import tkinter as tk

import update_client as client


def request_existing_window(timeout=.8):
    """Ask this installation's running UI to open its embedded update card."""
    import tray_common as tc
    hwnd = tc.ui_hwnd()
    if not hwnd:
        return False
    request = client.BASE / "open-update.request"
    try:
        request.touch()
        tc.USER32.ShowWindow(hwnd, 9)
        tc.USER32.SetForegroundWindow(hwnd)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not request.exists():
                return True
            time.sleep(.05)
    except OSError:
        pass
    return False


class UpdateManager:
    def __init__(self, root, *, on_install=None, standalone=False):
        self.root, self.on_install, self.standalone = root, on_install, standalone
        self.events = queue.Queue()
        self.window = None
        self.release = None
        self.downloaded = None
        self.busy = False
        self.cancel = threading.Event()
        self.closed = False
        self.request_path = client.BASE / "open-update.request"
        self.poll_id = root.after(100, self._poll)
        self.timer_id = None

    def start(self):
        self.timer_id = self.root.after(1500, self._automatic)

    def _automatic(self):
        self.timer_id = None
        self.check(manual=False)
        self.timer_id = self.root.after(10 * 60 * 1000, self._automatic)

    def open(self):
        self._window()
        show_updates = getattr(self.window, "show_updates", None)
        if show_updates is not None:
            show_updates()
        if not self.busy:
            self.check(manual=True)

    def _window(self):
        if self.window is not None and self.window.winfo_exists():
            self.window.show()
            return
        from app_dialogs import SettingsDialog, UpdateDialog
        panel = getattr(self.root, "_nexus_panel", None)
        if panel is not None:
            panel.updates = self
            dialog = SettingsDialog(panel, self)
            panel.settings_window = dialog
        else:
            dialog = UpdateDialog(self)
            self._attach(dialog)

    def _attach(self, dialog):
        self.window = dialog
        section = dialog.update_section
        for name in ("status", "notes", "progress", "auto", "ignore", "primary"):
            setattr(self, name, getattr(section, name))
        self._notes("新的功能改进和设备适配会在这里显示。点击“检查更新”获取最新正式版本。")

    def _notes(self, text):
        self.notes.configure(state="normal")
        self.notes.delete("1.0", "end")
        self.notes.insert("1.0", text)
        self.notes.configure(state="disabled")

    def check(self, manual):
        if self.closed or self.busy:
            return
        if manual:
            self._window()
            self.status.set("正在连接 GitHub 检查更新…")
            self.primary.configure(state="disabled")
        self.busy = True

        def worker():
            try:
                release = client.check_for_updates(force=manual)
                self.events.put(("checked", release, manual))
            except Exception as exc:
                self.events.put(("error", str(exc), manual))
        threading.Thread(target=worker, daemon=True).start()

    def _download(self):
        if self.busy or self.release is None:
            return
        self.busy = True
        self.cancel.clear()
        self.status.set("正在下载更新…")
        self.primary.configure(state="disabled")
        self.ignore.configure(state="disabled")
        release = self.release

        def worker():
            try:
                result = client.download_installer(release, cancel=self.cancel,
                    progress=lambda done, total: self.events.put(("progress", done, total)))
                self.events.put(("downloaded", result, True))
            except Exception as exc:
                self.events.put(("error", str(exc), True))
        threading.Thread(target=worker, daemon=True).start()

    def _install(self):
        if self.downloaded and self.release is not None:
            self.window.confirm_install(self._launch_install)

    def _launch_install(self):
        if not self.downloaded or self.release is None:
            return
        try:
            path, checksum = self.downloaded
            client.launch_installer(path, self.release, checksum)
        except client.UpdateError as exc:
            self.status.set(str(exc))
            return
        if self.on_install is not None:
            self.on_install()
        else:
            self.close()
            self.root.destroy()

    def _ignore(self):
        if self.release:
            client.save_state(ignored_tag=self.release.tag)
            self.status.set(f"已忽略 {self.release.tag}，其他设置仍可继续修改。")
            self.ignore.configure(state="disabled")

    def _dismiss(self):
        self.cancel.set()
        if self.window is not None:
            self.window.destroy()
            self.window = None
        if self.standalone:
            self.close()
            self.root.destroy()

    def _poll(self):
        self.poll_id = None
        if self.closed:
            return
        request_path = getattr(self, "request_path", None)
        if request_path is not None and request_path.exists():
            try:
                request_path.unlink(missing_ok=True)
                self.open()
            except OSError:
                pass
        while True:
            try:
                kind, value, extra = self.events.get_nowait()
            except queue.Empty:
                break
            if kind == "progress":
                if self.window is not None:
                    self.progress["value"] = value * 100 / extra
                    self.status.set(f"正在下载：{value / 1048576:.1f} / {extra / 1048576:.1f} MB")
                continue
            self.busy = False
            if kind == "checked":
                if extra and self.window is None:
                    continue
                self.release, self.downloaded = value, None
                if value is None and not extra:
                    continue
                self._window()
                self.progress["value"] = 0
                self.primary.configure(state="normal")
                if value is None:
                    self.status.set("没有发现比当前版本更新的正式版本。")
                    self._notes("当前已是最新正式版本。\n\n新的功能改进和设备适配会在这里显示，你也可以前往发布记录查看历史版本。")
                    self.primary.configure(text="重新检查", command=lambda: self.check(manual=True))
                    self.ignore.configure(state="disabled")
                else:
                    if not extra:
                        show_updates = getattr(self.window, "show_updates", None)
                        if show_updates is not None:
                            show_updates()
                    client.save_state(notified_tag=value.tag)
                    self.status.set(f"发现新版本 {value.tag} · {value.size / 1048576:.1f} MB")
                    self._notes(value.notes)
                    self.primary.configure(text="下载更新", command=self._download)
                    self.ignore.configure(state="normal")
            elif kind == "downloaded":
                if self.window is None:
                    continue
                self.downloaded = value
                self._window()
                self.status.set("下载完成，SHA-256 校验通过。点击“立即安装”继续。")
                self.progress["value"] = 100
                self.primary.configure(text="立即安装", state="normal", command=self._install)
            elif kind == "error" and extra and self.window is not None:
                self._window()
                self.status.set(value)
                self.primary.configure(text="重新检查", state="normal", command=lambda: self.check(manual=True))
        if not self.closed:
            self.poll_id = self.root.after(100, self._poll)

    def close(self):
        self.closed = True
        self.cancel.set()
        for timer in (self.poll_id, self.timer_id):
            if timer is not None:
                self.root.after_cancel(timer)
        self.poll_id = self.timer_id = None


if __name__ == "__main__":
    from my_devices import main
    main(["--check-updates"])
