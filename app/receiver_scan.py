"""Identify connected USB receiver models from Windows device metadata.

Only reads present PnP nodes and HID descriptors. It sends no pairing,
configuration, or vendor protocol commands to a receiver.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
import re


@dataclass
class DeviceNode:
    instance_id: str
    parent_id: str = ""
    name: str = ""
    description: str = ""
    manufacturer: str = ""
    class_name: str = ""
    bus_name: str = ""
    battery_level: int | None = None


@dataclass
class Receiver:
    instance_id: str
    model: str
    manufacturer: str
    hardware_id: str
    category: str
    identification: str
    wireless: bool
    names: list[str] = field(default_factory=list)
    interface_count: int = 0
    hid_paths: list[str] = field(default_factory=list, repr=False)
    usb_paths: list[str] = field(default_factory=list, repr=False)
    product_variant: int = 0

    def details(self) -> str:
        return (f"型号 / 名称：{self.model}\n厂商：{self.manufacturer}\n"
                f"硬件 ID：{self.hardware_id}\n识别结果：{self.identification}\n"
                f"接口类型：{self.category}\nHID 接口：{self.interface_count} 个\n"
                f"系统名称：{' / '.join(self.names)}\n设备实例：{self.instance_id}\n\n"
                "型号来自 Windows 和设备描述符，可能是驱动登记的名称。"
                "接收器存在不代表外设已开机；通用接收器可能不会报告所配对外设的具体型号。"
                "搜索结果不代表已支持读取该设备的电量。")


USB_ROOT = re.compile(r"^USB\\VID_([0-9A-F]{4})&PID_([0-9A-F]{4})\\[^\\]+$", re.I)
WIRELESS = re.compile(r"receiver|dongle|wireless|lightspeed|unifying|\bbolt\b|2[.,]4\s*g|接收器|无线", re.I)
GENERIC = re.compile(r"^(USB (Composite Device|Input Device)|USB (复合设备|输入设备)|HID[ -].*|符合 HID.*|HIDI2C.*|.*HID 设备.*|USB Receiver|Wireless Receiver|2[.,]4G( Wireless)?( (Receiver|Device))?)$", re.I)
NON_PERIPHERAL = {"bluetooth", "net", "camera", "image", "diskdrive", "volume"}


def _unique(values):
    return list(dict.fromkeys(v.strip() for v in values if v and v.strip()))


def _name_score(name: str) -> tuple:
    generic = bool(GENERIC.fullmatch(name))
    receiver_only = bool(re.search(r"receiver|dongle|接收器", name, re.I))
    return (not generic, receiver_only if generic else not receiver_only,
            bool(re.search(r"\d", name)), len(name))


def identify_receivers(nodes: list[DeviceNode], interfaces) -> list[Receiver]:
    """Group by a real USB instance, never by VID/PID alone (two may match)."""
    by_id = {n.instance_id.upper(): n for n in nodes}
    roots = {key: n for key, n in by_id.items() if USB_ROOT.fullmatch(key)}
    groups = {key: [] for key in roots}
    hid_groups = {key: [] for key in roots}

    def root_for(instance_id):
        seen = set()
        key = instance_id.upper()
        while key and key not in seen:
            if key in roots:
                return key
            seen.add(key)
            node = by_id.get(key)
            key = node.parent_id.upper() if node else ""
        return None

    for node in nodes:
        root = root_for(node.instance_id)
        if root:
            groups[root].append(node)
    for iface in interfaces:
        parts = iface.path.split("#")
        if len(parts) >= 3:
            root = root_for("HID\\" + parts[1] + "\\" + parts[2])
            if root:
                hid_groups[root].append(iface)

    found = []
    for key, root in roots.items():
        members, hids = groups[key], hid_groups[key]
        classes = {n.class_name.lower() for n in members}
        has_peripheral = bool(hids or classes & {"hidclass", "keyboard", "mouse", "media", "audioendpoint", "xna composite"})
        # Network radios, storage, cameras and Bluetooth adapters are not receivers.
        if root.class_name.lower() in NON_PERIPHERAL or not has_peripheral:
            continue
        names = _unique([root.name, root.bus_name] +
                        [s for n in members for s in (n.name, n.bus_name, n.description)] +
                        [h.product for h in hids])
        names = [s for s in names if not re.search(r"virtual|虚拟|vhf driver|multitouch", s, re.I)]
        match = USB_ROOT.fullmatch(key)
        vid_pid = f"{match[1]}:{match[2]}".upper()
        wireless = any(WIRELESS.search(n) for n in names)
        model = max(names, key=_name_score) if names else "未知 USB 外设"
        identification = "系统报告名称；无线待确认"
        if vid_pid == "3554:FA09":
            model, wireless = "AULA F87 Pro", True
            identification = "2.4G 接收器（已适配硬件 ID）"
        elif wireless:
            identification = "无线接收器 / 外设（系统名称）"
        makers = _unique([h.manufacturer for h in hids] + [root.manufacturer])
        makers = [m for m in makers if m.lower() not in {"microsoft", "(标准系统设备)", "(standard system devices)"}]
        manufacturer = makers[0] if makers else "未提供"
        from aula_f87_v2d import is_f87_v2d
        if is_f87_v2d(vid_pid, *names):
            model, manufacturer, wireless = "F87 Pro V2D", "AULA", True
            identification = "AULA F87 Pro V2D 2.4G 接收器（已适配电量协议）"
        from aula_catalog import match_name
        aula_model = match_name(*names, trusted=manufacturer.casefold() == "aula")
        if aula_model is not None:
            model, manufacturer = aula_model["model"], "AULA"
            identification = "狼蛛官网产品库名称匹配；电量支持按硬件版本判定"
        if vid_pid == "046D:C53F":
            from logitech_g304 import FAMILY_NAME
            model, manufacturer, wireless = FAMILY_NAME, "Logitech", True
            identification = "G304 / G305 LIGHTSPEED 接收器（配对型号由电量查询确认）"
        kinds = []
        usages = {(h.usage_page, h.usage) for h in hids}
        if (1, 2) in usages or "mouse" in classes:
            kinds.append("鼠标")
        if (1, 6) in usages or "keyboard" in classes:
            kinds.append("键盘")
        if "media" in classes or "audioendpoint" in classes:
            kinds.append("音频")
        if usages & {(1, 4), (1, 5)}:
            kinds.append("手柄")
        found.append(Receiver(root.instance_id, model, manufacturer, vid_pid,
                              " / ".join(kinds) or "USB 外设", identification,
                              wireless, names, len(hids), [h.path for h in hids],
                              [n.instance_id for n in members if n.instance_id.upper().startswith("USB\\")]))
    return sorted(found, key=lambda d: (not d.wireless, d.model.casefold(), d.instance_id))


def enumerate_present_nodes() -> list[DeviceNode]:
    """Read the PnP parent graph with Windows SetupAPI / Configuration Manager."""
    import ctypes as c
    from ctypes import wintypes as w
    from hid_win import GUID

    class Info(c.Structure):
        _fields_ = [("cbSize", w.DWORD), ("ClassGuid", GUID),
                    ("DevInst", w.DWORD), ("Reserved", c.c_void_p)]

    class Key(c.Structure):
        _fields_ = [("fmtid", GUID), ("pid", w.DWORD)]

    api = c.WinDLL("setupapi", use_last_error=True)
    cm = c.WinDLL("cfgmgr32", use_last_error=True)
    api.SetupDiGetClassDevsW.argtypes = [c.c_void_p, w.LPCWSTR, c.c_void_p, w.DWORD]
    api.SetupDiGetClassDevsW.restype = c.c_void_p
    api.SetupDiDestroyDeviceInfoList.argtypes = [c.c_void_p]
    api.SetupDiDestroyDeviceInfoList.restype = w.BOOL
    api.SetupDiEnumDeviceInfo.argtypes = [c.c_void_p, w.DWORD, c.POINTER(Info)]
    api.SetupDiEnumDeviceInfo.restype = w.BOOL
    api.SetupDiGetDeviceInstanceIdW.argtypes = [c.c_void_p, c.POINTER(Info), w.LPWSTR, w.DWORD, c.POINTER(w.DWORD)]
    api.SetupDiGetDeviceInstanceIdW.restype = w.BOOL
    api.SetupDiGetDeviceRegistryPropertyW.argtypes = [c.c_void_p, c.POINTER(Info), w.DWORD, c.POINTER(w.DWORD), c.c_void_p, w.DWORD, c.POINTER(w.DWORD)]
    api.SetupDiGetDeviceRegistryPropertyW.restype = w.BOOL
    api.SetupDiGetDevicePropertyW.argtypes = [c.c_void_p, c.POINTER(Info), c.POINTER(Key), c.POINTER(w.DWORD), c.c_void_p, w.DWORD, c.POINTER(w.DWORD), w.DWORD]
    api.SetupDiGetDevicePropertyW.restype = w.BOOL
    cm.CM_Get_Parent.argtypes = [c.POINTER(w.DWORD), w.DWORD, w.ULONG]
    cm.CM_Get_Parent.restype = w.ULONG
    cm.CM_Get_Device_IDW.argtypes = [w.DWORD, w.LPWSTR, w.ULONG, w.ULONG]
    cm.CM_Get_Device_IDW.restype = w.ULONG
    # DEVPKEY_Device_BusReportedDeviceDesc, from Windows SDK devpkey.h.
    bus_key = Key(GUID(0x540B947E, 0x8B40, 0x45BC,
                      (c.c_ubyte * 8)(0xA8, 0xA2, 0x6A, 0x0B, 0x89, 0x4C, 0xBD, 0xA2)), 4)
    battery_key = Key(GUID(0x104EA319, 0x6EE2, 0x4701,
                          (c.c_ubyte * 8)(0xBD, 0x47, 0x8D, 0xDB, 0xF4, 0x25, 0xBB, 0xE5)), 2)
    handle = api.SetupDiGetClassDevsW(None, None, None, 0x02 | 0x04)
    if not handle or handle == c.c_void_p(-1).value:
        raise c.WinError(c.get_last_error())

    def registry_string(info, prop):
        buf = c.create_unicode_buffer(4096)
        if api.SetupDiGetDeviceRegistryPropertyW(handle, c.byref(info), prop, None, buf, c.sizeof(buf), None):
            return buf.value.strip()
        return ""

    nodes = []
    try:
        index = 0
        while True:
            info = Info()
            info.cbSize = c.sizeof(info)
            if not api.SetupDiEnumDeviceInfo(handle, index, c.byref(info)):
                error = c.get_last_error()
                if error != 259:  # ERROR_NO_MORE_ITEMS
                    raise c.WinError(error)
                break
            index += 1
            iid = c.create_unicode_buffer(4096)
            if not api.SetupDiGetDeviceInstanceIdW(handle, c.byref(info), iid, len(iid), None):
                continue  # The device may have been unplugged during enumeration.
            parent = w.DWORD()
            parent_id = c.create_unicode_buffer(4096)
            if cm.CM_Get_Parent(c.byref(parent), info.DevInst, 0) == 0:
                cm.CM_Get_Device_IDW(parent, parent_id, len(parent_id), 0)
            bus = c.create_unicode_buffer(4096)
            ptype = w.DWORD()
            api.SetupDiGetDevicePropertyW(handle, c.byref(info), c.byref(bus_key),
                                         c.byref(ptype), bus, c.sizeof(bus), None, 0)
            battery_level = None
            if iid.value.upper().startswith("BTH"):
                battery = c.c_ubyte(255)
                battery_type = w.DWORD()
                if api.SetupDiGetDevicePropertyW(handle, c.byref(info), c.byref(battery_key),
                                                 c.byref(battery_type), c.byref(battery), 1, None, 0):
                    if battery_type.value == 3 and battery.value <= 100:
                        battery_level = int(battery.value)
            nodes.append(DeviceNode(iid.value, parent_id.value,
                                    registry_string(info, 0x0C), registry_string(info, 0),
                                    registry_string(info, 0x0B), registry_string(info, 7), bus.value, battery_level))
    finally:
        api.SetupDiDestroyDeviceInfoList(handle)
    return nodes


def scan_receivers() -> list[Receiver]:
    import hid_win
    return identify_receivers(enumerate_present_nodes(), hid_win.enumerate_hid())


if __name__ == "__main__":
    import json
    print(json.dumps([asdict(r) for r in scan_receivers()], ensure_ascii=False, indent=2))
