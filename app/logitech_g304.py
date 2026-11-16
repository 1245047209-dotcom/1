"""Read-only G304/G305 C53F adapter, using HID++ BATTERY_STATUS (0x1000).

The replaceable AA battery reports discrete levels, not a LiPo voltage curve.
References and validation limits are recorded in G304协议说明.md.
"""
from __future__ import annotations

import re

import hid_win as hw
import logitech_battery as lb
from device_readers import Reading

HARDWARE_ID = "046D:C53F"
FAMILY_NAME = "G304 / G305"
SOURCE = "HID++ 分档电量"


def model_name(name):
    """Accept the device's own name, never infer its variant from USB PID."""
    text = " ".join(name.casefold().split())
    match = re.fullmatch(r"(?:logitech )?(g304|g305)(?: lightspeed)?(?: wireless)?(?: gaming)?(?: mouse)?", text)
    return match[1].upper() if match else ""


def decode_battery(payload):
    if len(payload) < 3:
        raise lb.HidppError("G304 电量回复不完整")
    level, next_level, status = payload[:3]
    if level > 100 or next_level > 100:
        raise lb.HidppError("G304 电量回复超出有效范围")
    # G304/G305 use an AA cell; a charging state is not a valid sample here.
    if status != 0:
        raise lb.HidppError(f"鼠标报告异常电池状态 0x{status:02X}，请检查 AA 电池")
    # HID++ 0x1000 defines zero as an unknown level, not an empty battery.
    return level or None, next_level or None


from query_coordinator import shared_reading


@shared_reading("g304-battery-v1")
def read_g304(receiver, title):
    reading = Reading(title, "接收器已连接", connected=True, status="读取失败", source=SOURCE)
    try:
        if receiver.hardware_id.upper() != HARDWARE_ID:
            raise lb.HidppError("该接收器不是 G304 / G305 C53F 接收器")
        paths = {p.casefold() for p in getattr(receiver, "hid_paths", [])}
        interfaces = [d for d in hw.enumerate_hid()
                      if d.path.casefold() in paths
                      and (d.vid, d.pid, d.usage_page, d.input_len, d.output_len)
                      == (0x046D, 0xC53F, 0xFF00, 20, 20)
                      and d.usage in (1, 2)]
        if not interfaces:
            raise lb.HidppError("未找到所选 G304 接收器的 HID++ 接口，请重新插入接收器后刷新")
        with hw.query_lock(0x046D, 0xC53F, 30000, sorted(paths)[0]):
            last_error = None
            for interface in interfaces:
                try:
                    with hw.HidDevice(interface.path, write=True) as dev:
                        if not dev.write_access:
                            raise lb.HidppError("无法访问 G304 电量接口，请关闭占用接口的软件后重试")
                        model = model_name(lb.device_name(dev))
                        if not model:
                            raise lb.HidppError("配对鼠标尚未确认为 G304 / G305，请移动鼠标唤醒后重试")
                        reading.reported_model = model
                        index = lb.feature_index(dev, 0x1000)
                        if index is None:
                            raise lb.HidppError("该鼠标未提供 G304 分档电量接口")
                        level, next_level = decode_battery(lb.exchange(dev, index))
                        reading.title = f"Logitech {model}"
                        reading.level = level
                        reading.status = "电池供电" if level is not None else "未上报电量"
                        reading.coarse = True
                        reading.detail = "设备分档电量 · AA 电池"
                        if next_level is not None and level is not None:
                            reading.detail += f" · 下一档 {next_level}%"
                        reading.extra = "鼠标通过 HID++ 直接上报分档电量，长时间保持同一档属于正常现象；不能据此判断剩余电量精确到 1%。"
                        reading.error = ""
                        return reading
                except (OSError, ValueError, TimeoutError) as exc:
                    last_error = exc
            if last_error is not None:
                raise last_error
    except (OSError, ValueError, TimeoutError) as exc:
        reading.error = str(exc)
        return reading
