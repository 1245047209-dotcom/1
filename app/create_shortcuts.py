"""Create normal Windows shortcuts for this portable folder."""
import argparse
import sys
from pathlib import Path
import win_shell

def create_links(desktop=None):
    base = Path(__file__).resolve().parent
    desktop = Path(desktop) if desktop is not None else Path(win_shell.desktop_dir())
    executable = base / "runtime" / "pythonw.exe"
    if not executable.is_file():
        executable = Path(sys.executable).with_name("pythonw.exe")
    if not executable.is_file():
        raise FileNotFoundError(f"找不到 GUI 运行时：{executable}")
    icon = base / "assets" / "nexus-app.ico"
    desktop.mkdir(parents=True, exist_ok=True)
    success = True
    for name, script in (("Nexus 外设电量中心", "my_devices.pyw"), ("Nexus 电量托盘", "tray_app.py")):
        link = desktop / (name + ".lnk")
        created = win_shell.create_shortcut(str(link), str(executable),
            arguments='"' + str(base / script) + '"', workdir=str(base),
            description=name, icon=str(icon))
        print(("已创建：" if created else "创建失败：") + str(link))
        success = success and created
    return success

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--desktop-dir")
    args = parser.parse_args()
    raise SystemExit(0 if create_links(args.desktop_dir) else 1)
