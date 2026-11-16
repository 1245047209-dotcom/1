"""Read-only HID++ 2.0 battery adapter for the PRO WIRELESS C539 receiver.

Protocol: Logitech cpg-docs/hidpp20/features/0x0000-IRoot.rst.
Voltage/charge flag semantics and calibration data cross-checked against:
https://github.com/pwr-Solaar/Solaar/blob/master/lib/logitech_receiver/hidpp20.py
The numeric calibration points below are voltage/percentage data, not an
exact device-reported percentage. No pairing or configuration commands are sent.
"""
from __future__ import annotations

from itertools import cycle
import time

import hid_win as hw
from device_readers import Reading

_SOFTWARE_IDS = cycle(range(8, 16))
_PRO_WIRELESS_NAMES = {"pro wireless", "g pro wireless", "g pro wireless gaming mouse"}
# Numeric voltage thresholds used by Linux's HID++ Battery Voltage backend.
# Index 0 = 100%, index 99 = 1%. Resolution is 1%; accuracy remains estimated.
_VOLTAGE_THRESHOLDS = (
    4186, 4156, 4143, 4133, 4122, 4113, 4103, 4094, 4086, 4075,
    4067, 4059, 4051, 4043, 4035, 4027, 4019, 4011, 4003, 3997,
    3989, 3983, 3976, 3969, 3961, 3955, 3949, 3942, 3935, 3929,
    3922, 3916, 3909, 3902, 3896, 3890, 3883, 3877, 3870, 3865,
    3859, 3853, 3848, 3842, 3837, 3833, 3828, 3824, 3819, 3815,
    3811, 3808, 3804, 3800, 3797, 3793, 3790, 3787, 3784, 3781,
    3778, 3775, 3772, 3770, 3767, 3764, 3762, 3759, 3757, 3754,
    3751, 3748, 3744, 3741, 3737, 3734, 3730, 3726, 3724, 3720,
    3717, 3714, 3710, 3706, 3702, 3697, 3693, 3688, 3683, 3677,
    3671, 3666, 3662, 3658, 3654, 3646, 3633, 3612, 3579, 3537,
)


class HidppError(ValueError):
    pass


def exchange(dev, feature, function=0, params=b"", *, device_index=1, timeout_ms=500):
    """Match the full transaction; retry a timed-out read once with a new ID."""
    if len(params) > 16:
        raise ValueError("HID++ 请求参数过长")
    for _attempt in range(2):
        transaction = (function << 4) | next(_SOFTWARE_IDS)
        request = bytes((0x11, device_index, feature, transaction)) + bytes(params)
        # Open the reader before sending; each retry uses a fresh software ID.
        dev.drain()
        dev.write_output(request.ljust(20, b"\0"))
        deadline = time.monotonic() + timeout_ms / 1000
        while time.monotonic() < deadline:
            remaining = max(1, int((deadline - time.monotonic()) * 1000))
            reply = dev.read_input(timeout_ms=min(100, remaining))
            if not reply or len(reply) < 4 or reply[1] != device_index:
                continue
            # HID++ errors echo the original feature/function after 0xFF / 0x8F.
            if reply[0] in (0x10, 0x11) and reply[2] in (0xFF, 0x8F):
                if len(reply) >= 6 and reply[3:5] == bytes((feature, transaction)):
                    raise HidppError(f"鼠标返回协议错误 0x{reply[5]:02X}")
                continue
            if reply[0] != 0x11 or reply[2:4] != bytes((feature, transaction)):
                continue
            if len(reply) != 20:
                raise HidppError("鼠标返回的电量数据长度异常")
            return reply[4:]
    raise TimeoutError("鼠标未响应，请移动鼠标或按键唤醒后重试")


def feature_index(dev, feature_id):
    reply = exchange(dev, 0, params=feature_id.to_bytes(2, "big"))
    # Return byte 0 is the index; byte 2 is the feature VERSION, not its index.
    return reply[0] or None


def device_name(dev):
    index = feature_index(dev, 0x0005)
    if index is None:
        return ""
    length = exchange(dev, index)[0]
    if not 1 <= length <= 128:
        raise HidppError("鼠标返回的型号名称长度异常")
    name = bytearray()
    while len(name) < length:
        chunk = exchange(dev, index, 1, bytes((len(name),)))
        name.extend(chunk[:length - len(name)])
    return name.decode("utf-8", errors="replace").strip()


def estimate_pro_wireless_level(millivolts):
    """1% display resolution, always labeled approximate in the UI."""
    if not 2500 <= millivolts <= 4500:
        raise HidppError("电池电压超出有效范围，已忽略异常数据")
    for index, threshold in enumerate(_VOLTAGE_THRESHOLDS):
        if millivolts >= threshold:
            return 100 - index
    return 0


def voltage_reading(title, millivolts, status, *, live=False):
    mode = "实时监测" if live else "直读"
    return Reading(title, "接收器已连接", level=estimate_pro_wireless_level(millivolts),
                   estimated=True, voltage_mv=millivolts, status=status, connected=True,
                   source="HID++ 实时" if live else "HID++ 2.0",
                   detail=f"HID++ {mode} · {millivolts / 1000:.3f} V · 电量按电压估算",
                   extra="直接读取接收器，无需 G HUB；百分比为电压估算，1% 分辨率不代表 1% 测量精度。")


def decode_voltage(payload):
    if len(payload) < 3:
        raise HidppError("电池电压回复不完整")
    millivolts = int.from_bytes(payload[:2], "big")
    if not 2500 <= millivolts <= 4500:
        raise HidppError("电池电压超出有效范围，已忽略异常数据")
    flags = payload[2]
    if flags & 0x80:
        states = {0: "充电中", 1: "已充满", 2: "已接电，未充电"}
        status = states.get(flags & 7)
        if status is None:
            raise HidppError("鼠标报告充电异常或未知供电状态")
    else:
        status = "电池供电"
    return millivolts, status


from query_coordinator import shared_reading


@shared_reading("pro-wireless-battery-v1")
def read_pro_wireless(receiver, title):
    reading = Reading(title, "接收器已连接", status="读取失败", connected=True, source="HID++ 2.0")
    try:
        # Paths come from the selected physical USB instance, not a VID/PID scan.
        paths = {p.casefold() for p in getattr(receiver, "hid_paths", [])}
        interfaces = [d for d in hw.enumerate_hid() if d.path.casefold() in paths
                      and (d.vid, d.pid, d.usage_page, d.usage, d.input_len, d.output_len)
                      == (0x046D, 0xC539, 0xFF00, 2, 20, 20)]
        if not interfaces:
            raise HidppError("未找到所选接收器的罗技电量接口，请重新插入接收器后刷新")
        with hw.query_lock(0x046D, 0xC539, 30000, interfaces[0].path):
            with hw.HidDevice(interfaces[0].path, write=True) as dev:
                if not dev.write_access:
                    raise HidppError("无法访问罗技电量查询接口，请关闭占用它的软件后重试")
                index = feature_index(dev, 0x1001)
                if index is None:
                    raise HidppError("该鼠标未提供已适配的电池电压接口")
                millivolts, status = decode_voltage(exchange(dev, index))
                name = " ".join(device_name(dev).casefold().split())
                if name not in _PRO_WIRELESS_NAMES:
                    raise HidppError(f"已读取 {millivolts / 1000:.3f} V；当前型号未校准，暂不换算百分比")
                return voltage_reading(title, millivolts, status)
    except (OSError, ValueError, TimeoutError) as exc:
        reading.error = str(exc)
        return reading
