"""Start the native frontend before importing the device and Tk backend."""

import argparse
import os
from pathlib import Path
import subprocess
import sys

import tray_common as tc


APP_ROOT = Path(__file__).resolve().parent
PANEL_EXE = APP_ROOT / "native" / "NexusNative.exe"


def launch_native_process(*, dark, glass, font, keep_warm=False):
    if sys.platform != "win32" or sys.getwindowsversion().build < 22621 or not PANEL_EXE.is_file():
        return None
    if not isinstance(font, str) or len(font) > 32 or "\x00" in font:
        font = tc.DEFAULT_CONFIG["ui_font"]
    command = [str(PANEL_EXE), "--backend-pid", str(os.getpid()), "--font", font]
    if dark is not None:
        command.append("--dark" if dark else "--light")
    if not glass:
        command.append("--glass-off")
    if keep_warm:
        command.append("--keep-warm")
    try:
        return subprocess.Popen(command, cwd=APP_ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                encoding="utf-8", errors="replace",
                                creationflags=subprocess.CREATE_NO_WINDOW)
    except OSError as error:
        if sys.stderr is not None:
            print("Nexus native startup:", error, file=sys.stderr)
        return None


def _open_panel(config, process, check_updates):
    import tkinter as tk

    from my_devices import NexusPanel
    from native_panel import start_native_panel

    root = tk.Tk()
    root.withdraw()
    panel = NexusPanel(root, config=config, defer_start=True)
    if process is None or start_native_panel(panel, process) is None:
        panel.place_initial_window()
        root.deiconify()
        tc.register_ui_window(root)
    from update_ui import UpdateManager

    panel.updates = UpdateManager(root, on_install=panel.close)
    panel.updates.start()
    panel.start_background()
    if check_updates:
        root.after(50, panel.updates.open)
    root.mainloop()


def main(argv=None, *, prepare_tcl=None):
    parser = argparse.ArgumentParser(description="狼蛛 F87 Pro 电量面板")
    parser.add_argument("--once", action="store_true", help="读取一次后退出")
    parser.add_argument("--install-links", action="store_true", help="重建桌面入口")
    parser.add_argument("--autostart", choices=["on", "off", "status"],
                        help="开机自启：on / off / status")
    parser.add_argument("--check-updates", action="store_true", help="在主窗口内检查软件更新")
    parser.add_argument("--keep-warm", action="store_true", help="关闭原生主窗口后短时保留，便于再次打开")
    args = parser.parse_args(argv)

    if args.install_links:
        result = tc.install_desktop_links()
        print("桌面入口已处理")
        print(result)
        return
    if args.autostart:
        if args.autostart == "status":
            print(tc.autostart_status())
        else:
            print("开机自启", "已启用" if tc.set_autostart(args.autostart == "on") else "设置失败")
        return
    if args.once:
        from my_devices import cli_once

        cli_once()
        return

    with tc.ui_instance() as primary:
        if not primary:
            if args.check_updates:
                from update_ui import request_existing_window

                request_existing_window()
            else:
                tc.focus_ui(wait_timeout=2.0)
            return

        config = tc.load_config()
        mode = config.get("theme")
        # When following the system, the native process reads the Windows theme itself.
        dark = (mode == "dark") if mode in ("light", "dark") else None
        process = launch_native_process(dark=dark, glass=bool(config.get("glass_backdrop", True)),
                                        font=config.get("ui_font"), keep_warm=args.keep_warm)
        try:
            if prepare_tcl is not None:
                prepare_tcl()
            _open_panel(config, process, args.check_updates)
        finally:
            if process is not None and process.poll() is None:
                process.terminate()
