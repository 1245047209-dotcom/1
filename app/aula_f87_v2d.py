"""Read-only GET_DEVICE_INFO for the SONiX AULA F87 Pro V2D dongle.

Protocol reference: JKWTCN/BatteryMonitor, MIT, commit
50f628c1ae1ced004e9fa03737cee9d6a561d80e, AulaHidProvider.cpp/.h.
Only battery/device-info queries are sent; no configuration commands are used.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
import time

HARDWARE_ID = "0C45:FEFE"
TITLE = "AULA F87 Pro V2D"
SOURCE = "AULA F87 Pro V2D 2.4G 设备上报"
PROFILES = ((0x10, 56, True), (0x10, 48, True), (0xA0, 64, False))
_last_good = {}


class ProtocolError(ValueError):
    pass


@dataclass(frozen=True)
class DeviceInfo:
    level: int
    charging: bool
    work_mode: int
    command: int


def is_f87_v2d(hardware_id, *names):
    # FEFE is shared by several SONiX keyboards; VID/PID alone is insufficient.
    return str(hardware_id).upper() == HARDWARE_ID and any(
        "f87prov2d" in re.sub(r"[^a-z0-9]", "", str(name).casefold())
        for name in names)


def make_request(report_length, command, address, size, last):
    """Windows caps lengths include the leading zero Report ID."""
    if command not in (0x10, 0xA0) or not 10 <= report_length <= 1025:
        raise ProtocolError("不支持的电量查询格式")
    if not 1 <= size <= min(255, report_length - 9) or not 0 <= address <= 0xFFFF:
        raise ProtocolError("无效的电量查询分包")
    report = bytearray(report_length)
    report[1:6] = bytes((0xAA, command, size, address & 255, address >> 8))
    report[7] = int(bool(last)) if command == 0x10 else 0
    return bytes(report)


def response_chunk(report, command, address, size):
    """Ignore notifications and stale packets instead of joining their data."""
    if not report:
        return None
    if report[0] == 0 and len(report) > 1:
        report = report[1:]
    if len(report) < 9 or report[:2] != bytes((0x55, command)):
        return None
    if int.from_bytes(report[3:5], "little") != address:
        return None
    # byte 2 can be a response type rather than a literal payload length.
    return bytes(report[8:8 + size])


def decode_info(payload, command):
    if len(payload) < 19:
        raise ProtocolError("电量回复不完整")
    mode, level, charging = payload[16:19]
    if not (any(payload[4:8]) or mode or level or charging):
        raise ProtocolError("接收器返回空数据，无法确认键盘电量")
    if level > 100:
        raise ProtocolError("接收器尚未提供有效百分比")
    if mode == 0:
        raise ProtocolError("键盘未处于 2.4G 模式，请切换模式并按键唤醒")
    return DeviceInfo(level, bool(charging), mode, command)


def _receive(dev, command, address, size, input_len, deadline, trace):
    end = min(deadline, time.monotonic() + .5)
    while time.monotonic() < end:
        timeout = max(1, int((end - time.monotonic()) * 1000))
        report = dev.read_input(timeout_ms=timeout, length=input_len)
        if report is None:
            return None
        chunk = response_chunk(report, command, address, size)
        if trace is not None and chunk is not None:
            trace.append({"response": report.hex(" ")})
        if chunk is not None:
            return chunk
    return None


def query_info(dev, output_len, input_len, deadline=None, trace=None, preferred=None):
    if not 10 <= output_len <= 1025 or not 10 <= input_len <= 1025:
        raise ProtocolError("电量接口没有有效的输入/输出报告")
    deadline = time.monotonic() + 6 if deadline is None else deadline
    chunk_size = min(output_len - 9, input_len - 9, 255)
    profiles = sorted(PROFILES, key=lambda p: p != preferred)
    reason = "没有收到有效电量回复，请确认使用 2.4G 模式并按键唤醒"
    for profile in profiles:
        command, total, last_flag = profile
        payload = bytearray()
        for address in range(0, total, chunk_size):
            size = min(chunk_size, total - address)
            request = make_request(output_len, command, address, size,
                                   last_flag and address + size == total)
            chunk = None
            for _ in range(2):
                if time.monotonic() >= deadline:
                    break
                # Open the independent read handle before sending a fast query.
                dev.drain()
                if trace is not None:
                    trace.append({"request": request.hex(" ")})
                dev.write_output(request, use_hidd=False)
                chunk = _receive(dev, command, address, size, input_len, deadline, trace)
                if chunk is not None:
                    break
            if chunk is None:
                break
            payload.extend(chunk)
            if len(chunk) < size:
                break
        try:
            return decode_info(payload, command), profile
        except ProtocolError as exc:
            if payload:
                reason = str(exc)
    raise ProtocolError(reason)


from query_coordinator import shared_reading


@shared_reading("f87-v2d-battery-v1")
def read_f87_v2d(receiver, title=None, *, trace=None):
    import hid_win as hw
    from device_readers import Reading

    reading = Reading(title or TITLE, "2.4G 无线接收器", connected=True, source=SOURCE)
    if not is_f87_v2d(receiver.hardware_id, receiver.model, *receiver.names):
        reading.error = "所选接收器不是已适配的 AULA F87 Pro V2D"
        return reading
    paths = {path.casefold() for path in receiver.hid_paths}
    try:
        interfaces = [d for d in hw.enumerate_hid()
                      if d.path.casefold() in paths and (d.vid, d.pid) == (0x0C45, 0xFEFE)
                      and d.usage_page >= 0xFF00 and 10 <= d.output_len <= 1025
                      and 10 <= d.input_len <= 1025]
        if not interfaces:
            raise ProtocolError("未找到所选接收器的电量接口，请重新插入接收器后搜索设备")
        cached_path, preferred = _last_good.get(receiver.instance_id, (None, None))
        interfaces.sort(key=lambda d: d.path != cached_path)
        reason = "电量查询超时，请按键唤醒键盘后刷新"
        from contextlib import ExitStack
        with ExitStack() as locks:
            for path in sorted({d.path.casefold() for d in interfaces}):
                locks.enter_context(hw.query_lock(0x0C45, 0xFEFE, 30000, path))
            deadline = time.monotonic() + 6
            for iface in interfaces:
                if time.monotonic() >= deadline:
                    break
                try:
                    if trace is not None:
                        trace.append({"usage_page": iface.usage_page, "usage": iface.usage,
                                      "input_length": iface.input_len, "output_length": iface.output_len})
                    with hw.HidDevice(iface.path, write=True) as dev:
                        info, profile = query_info(dev, iface.output_len, iface.input_len,
                                                   deadline, trace, preferred if iface.path == cached_path else None)
                    _last_good[receiver.instance_id] = (iface.path, profile)
                    reading.level = info.level
                    reading.status = "充电中" if info.charging else "放电中"
                    reading.detail = "键盘上报电量（2.4G）"
                    return reading
                except (OSError, ProtocolError) as exc:
                    reason = str(exc)
        raise ProtocolError(reason)
    except (OSError, ProtocolError) as exc:
        reading.error = str(exc)
        return reading


def tray_readings():
    """Use the added V2D keyboard when the legacy default keyboard is absent."""
    from device_readers import read_all
    from saved_devices import DeviceStore, read_saved_device
    store = DeviceStore()
    readings = read_all() if store.default_visible() else []
    if readings and readings[0].ok:
        return readings
    try:
        devices, _ = store.load()
    except ValueError:
        return readings
    candidates = [device for device in devices
                  if device.transport == "usb" and is_f87_v2d(device.hardware_id, device.model)]
    fallback = None
    for device in candidates:
        result = read_saved_device(device)
        fallback = result if fallback is None else fallback
        if result.ok:
            return [result]
    return [fallback] if fallback is not None else readings


if __name__ == "__main__":
    import json
    from pathlib import Path
    from receiver_scan import scan_receivers
    entries = []
    for receiver in scan_receivers():
        if not is_f87_v2d(receiver.hardware_id, receiver.model, *receiver.names):
            continue
        trace = []
        reading = read_f87_v2d(receiver, trace=trace)
        entries.append({"model": TITLE, "hardware_id": HARDWARE_ID, "level": reading.level,
                        "status": reading.status, "error": reading.error, "trace": trace})
    report = Path(__file__).with_name("F87ProV2D-诊断.json")
    report.write_text(json.dumps({"devices": entries}, ensure_ascii=False, indent=2), encoding="utf-8")
    print("诊断报告已保存：", report)
