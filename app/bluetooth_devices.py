"""Paired Bluetooth discovery and Windows-reported battery, read-only.

Windows AEP IsConnected is used instead of treating installed PnP nodes as
connected hardware. Device profiles are grouped by remote Bluetooth address.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import subprocess
import threading
import time

from device_readers import Reading
from receiver_scan import enumerate_present_nodes

_CACHE_LOCK = threading.Lock()
_CACHE_AT = 0.0
_CACHE = []
_BT_ROOT = re.compile(r"^BTH(?:LE|ENUM)\\DEV_([0-9A-F]{12})\\", re.I)


class BluetoothError(ValueError):
    pass


def normalized_address(value):
    if not isinstance(value, str):
        return None
    value = re.sub(r"[:-]", "", value).upper()
    return value if re.fullmatch(r"[0-9A-F]{12}", value) else None


@dataclass(frozen=True)
class BluetoothPeripheral:
    instance_id: str
    model: str
    manufacturer: str
    hardware_id: str
    category: str
    connected: bool | None
    battery: int | None
    aep_ids: tuple[str, ...] = ()
    transport: str = "bluetooth"
    wireless: bool = True

    def details(self):
        state = "已连接" if self.connected is True else "未连接" if self.connected is False else "状态未知"
        level = f"{self.battery}%" if self.connected is True and self.battery is not None else "未提供"
        return (f"设备名称：{self.model}\n连接方式：蓝牙\n连接状态：{state}\n电量：{level}\n"
                f"蓝牙地址：{self.hardware_id}\n设备类型：{self.category}\n\n"
                "列表来自 Windows 已配对设备。仅在当前已连接且 Windows 提供有效电量时显示百分比。"
                "未连接时不显示历史电量；部分设备不向 Windows 上报电量。")


def query_windows_bluetooth():
    script = Path(__file__).with_name("bluetooth_query.ps1").read_text(encoding="utf-8-sig")
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    executable = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    try:
        result = subprocess.run([str(executable), "-NoLogo", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=11,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), check=False)
        if result.returncode:
            raise BluetoothError("Windows 蓝牙查询失败，请稍后重新搜索")
        data = json.loads(result.stdout.decode("utf-8-sig"))
        if not isinstance(data, list):
            raise BluetoothError("Windows 返回的蓝牙列表格式无效")
        return data
    except (OSError, subprocess.TimeoutExpired, UnicodeError, json.JSONDecodeError) as exc:
        raise BluetoothError("暂时无法读取 Windows 蓝牙设备，请稍后重试") from exc


def pnp_batteries(nodes):
    by_id = {n.instance_id.upper(): n for n in nodes}
    found = {}
    for node in nodes:
        level = getattr(node, "battery_level", None)
        if type(level) is not int or not 0 <= level <= 100:
            continue
        current, seen = node, set()
        address = None
        while current is not None and current.instance_id.upper() not in seen:
            key = current.instance_id.upper()
            seen.add(key)
            match = _BT_ROOT.match(key)
            if match:
                address = match[1]
                break
            # Some Bluetooth audio services are siblings of the DEV_ node.
            match = re.search(r"[&_ ]([0-9A-F]{12})(?:_C[0-9A-F]+|\\)", key) if key.startswith("BTH") else None
            if match:
                address = match[1]
                break
            current = by_id.get(current.parent_id.upper())
        if address:
            found.setdefault(address, set()).add(level)
    # Different component batteries are not silently averaged into one number.
    return {address: next(iter(levels)) for address, levels in found.items() if len(levels) == 1}


def category_for(name):
    if re.search(r"keyboard|键盘|F87", name, re.I):
        return "键盘"
    if re.search(r"headphone|headset|earbud|airpods|buds|耳机|WH-", name, re.I):
        return "音频"
    if re.search(r"mouse|鼠标", name, re.I):
        return "鼠标"
    return "蓝牙外设"


def normalize_devices(rows, batteries):
    groups = {}
    for row in rows:
        if not isinstance(row, dict) or row.get("paired") is not True:
            continue
        address = normalized_address(row.get("address"))
        if address is None:
            continue
        groups.setdefault(address, []).append(row)
    devices = []
    for address, matches in groups.items():
        connected = True if any(r.get("connected") is True for r in matches) else (
            False if all(r.get("connected") is False for r in matches) else None)
        active = [r for r in matches if r.get("connected") is True]
        preferred = (active or matches)[0]
        name = str(preferred.get("name") or preferred.get("model") or "未命名蓝牙设备").strip()
        maker = str(preferred.get("manufacturer") or "未提供").strip()
        levels = {r["battery"] for r in active if type(r.get("battery")) in (int, float)
                  and 0 <= r["battery"] <= 100 and r["battery"] == int(r["battery"])}
        level = int(next(iter(levels))) if len(levels) == 1 else None
        if not levels and connected is True:
            level = batteries.get(address)
        if connected is not True:
            level = None
        pretty_address = ":".join(address[i:i + 2] for i in range(0, 12, 2))
        devices.append(BluetoothPeripheral("BT\\" + address, name, maker, pretty_address,
                                            category_for(name), connected, level,
                                            tuple(r["id"] for r in matches if isinstance(r.get("id"), str))))
    return sorted(devices, key=lambda d: (d.connected is not True, d.model.casefold(), d.instance_id))


def scan_bluetooth(*, force=False):
    global _CACHE, _CACHE_AT
    with _CACHE_LOCK:
        if not force and time.monotonic() - _CACHE_AT < 8:
            return list(_CACHE)
        rows = query_windows_bluetooth()
        batteries = pnp_batteries(enumerate_present_nodes())
        _CACHE = normalize_devices(rows, batteries)
        _CACHE_AT = time.monotonic()
        return list(_CACHE)


def invalidate_bluetooth_cache():
    global _CACHE_AT
    # The next batch shares one fresh Windows query. An in-flight query is
    # already fresh and may safely finish after this atomic invalidation.
    _CACHE_AT = 0.0


def read_bluetooth_device(device):
    try:
        current = next((d for d in scan_bluetooth() if d.instance_id.upper() == device.instance_id.upper()), None)
    except (OSError, ValueError) as exc:
        return Reading(device.label, "蓝牙状态未知", status="设备检查失败", error=str(exc), connected=None)
    if current is None or current.connected is False:
        return Reading(device.label, "蓝牙未连接", status="未连接", connected=False,
                       error="蓝牙设备未连接，请开机并在 Windows 中连接后刷新")
    if current.connected is None:
        return Reading(device.label, "蓝牙状态未知", status="设备检查失败", connected=None,
                       error="Windows 暂未提供该蓝牙设备的连接状态")
    if current.battery is None:
        return Reading(device.label, "蓝牙已连接", status="未上报电量", connected=True,
                       error="Windows 暂未提供该蓝牙设备的电量", source="Windows 蓝牙")
    return Reading(device.label, "蓝牙已连接", level=current.battery, status="已连接", connected=True,
                   detail="来自 Windows 蓝牙电量上报", source="Windows 蓝牙")
