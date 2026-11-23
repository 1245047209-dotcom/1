"""Close only this installation's processes and remove its own startup link."""
from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
from pathlib import Path
import os
import time

import tray_common as tc
import win_shell

BASE = Path(__file__).resolve().parent
RUNTIME = os.path.normcase(str(BASE / "runtime" / "pythonw.exe"))
FRONTEND = os.path.normcase(str(BASE / "native" / "NexusNative.exe"))
user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, ctypes.c_size_t, ctypes.c_ssize_t]


def window_executable(hwnd):
    if not hwnd:
        return None
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    handle = kernel32.OpenProcess(0x1000, False, pid.value)
    if not handle:
        return None
    try:
        text = ctypes.create_unicode_buffer(32768)
        length = wintypes.DWORD(len(text))
        if kernel32.QueryFullProcessImageNameW(handle, 0, text, ctypes.byref(length)):
            return os.path.normcase(text.value)
        return None
    finally:
        kernel32.CloseHandle(handle)


def owned_window(hwnd):
    return window_executable(hwnd) in (RUNTIME, FRONTEND)


def shutdown():
    # EnumWindows also finds multiple UI windows with the same title.
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, ctypes.c_ssize_t)
    user32.EnumWindows.argtypes = [callback_type, ctypes.c_ssize_t]
    windows = []

    @callback_type
    def collect(hwnd, _):
        if owned_window(hwnd):
            windows.append(hwnd)
        return True

    user32.EnumWindows(collect, 0)
    tray = tc.tray_hwnd()
    if owned_window(tray):
        user32.PostMessageW(tray, user32.RegisterWindowMessageW(tc.MSG_QUIT), 0, 0)
    for hwnd in windows:
        if hwnd != tray:
            user32.PostMessageW(hwnd, 0x0010, 0, 0)  # WM_CLOSE
    deadline = time.monotonic() + 10
    while any(owned_window(hwnd) for hwnd in windows) or owned_window(tray):
        if time.monotonic() >= deadline:
            raise RuntimeError("请先退出 Nexus 主界面与电量托盘，再重新尝试。")
        time.sleep(0.1)


def remove_startup():
    path = tc.startup_lnk()
    link = win_shell.read_shortcut(path)
    if os.path.normcase(link.get("target", "")) == RUNTIME and str(BASE / "tray_app.py") in link.get("arguments", ""):
        tc.set_autostart(False)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--uninstall", action="store_true")
    args = parser.parse_args()
    shutdown()
    if args.uninstall:
        remove_startup()
