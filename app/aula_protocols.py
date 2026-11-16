"""AULA read-only model identity and battery protocols, bound to HID paths.

Compx 0x13/0x05 identity and 0x4a battery: AulaSofwareLinux commit
2b7d1c8e13c9725a5a398a5702ac2dafcb454e72. SONiX F87S device info:
aula-f87s-hid facba64ae5a80b58f42f11837d01f0e4446c7d21.
No pairing, lighting, remapping, flash or configuration commands are exposed.
"""
from __future__ import annotations

from dataclasses import replace
import threading
import time

import aula_catalog as catalog
import hid_win as hw
from query_coordinator import shared_reading

_identity = {}
_lock = threading.RLock()


def compx_request(command):
    if command not in (0x05, 0x4A):
        raise ValueError("只允许型号和电量查询")
    data = bytearray(20)
    data[:3] = bytes((0x13, command, 1))
    data[-1] = sum(data[:-1]) & 255
    return bytes(data)


def compx_payload(report, command, size):
    if (len(report) != 20 or report[:2] != bytes((0x13, command))
            or report[2:4] != b"\x01\x00" or not size <= report[4] <= 14
            or report[-1] != sum(report[:-1]) & 255):
        return None
    return bytes(report[5:5+size])


def query_compx(dev, command, size):
    for _ in range(2):
        dev.drain()
        dev.write_output(compx_request(command))
        end = time.monotonic() + .55
        while time.monotonic() < end:
            report = dev.read_input(timeout_ms=max(1, min(120, int((end-time.monotonic())*1000))))
            if report:
                payload = compx_payload(report, command, size)
                if payload is not None:
                    return payload
    raise TimeoutError("键盘未响应，请切换 2.4G 模式并按键唤醒后重试")


def _compx_interface(d):
    return (d.vid, d.pid, d.usage_page, d.usage, d.input_len, d.output_len) == (0x3554, 0xFA09, 0xFF02, 2, 20, 20)


def _wired_interface(d):
    return ((d.vid, d.pid, d.usage_page, d.usage) == (0x258A, 0x010C, 0xFF00, 1)
            and d.feature_len == 520)


def cached_model_for_path(path):
    with _lock:
        record = _identity.get(path.casefold())
    return catalog.model_by_key(record[1]) if record and record[1] else None


def identify_interface(interface):
    """Read a six-byte OEM model identifier, never treat a shared PID as a model."""
    key = interface.path.casefold()
    with _lock:
        cached = _identity.get(key)
    if cached and time.monotonic() - cached[0] < (60 if cached[1] else 15):
        return catalog.model_by_key(cached[1]) if cached[1] else None
    if not (_compx_interface(interface) or _wired_interface(interface)):
        return None
    model = None
    try:
        with hw.query_lock(interface.vid, interface.pid, 30000, interface.path), hw.HidDevice(interface.path, write=True) as dev:
            if _compx_interface(interface):
                signature = query_compx(dev, 0x05, 6)
            else:
                request = bytes.fromhex("06 82 01 00 01 00 06 00") + bytes(512)
                dev.set_feature_report(request)
                time.sleep(.04)
                report = dev.get_feature_report(6, 520)
                if len(report) != 520 or report[:8] != request[:8]:
                    raise ValueError("有线型号回复未通过校验")
                signature = report[8:14]
        model = next((m for m in catalog.catalog()["models"] if m.get("identity_signature") == signature.hex()), None)
    except (OSError, ValueError, TimeoutError):
        pass
    with _lock:
        _identity[key] = (time.monotonic(), model["key"] if model else None)
    return model


def identify_receivers(receivers, interfaces=None):
    """Enrich descriptor-only enumeration with optional, documented identity reads."""
    interfaces = hw.enumerate_hid() if interfaces is None else interfaces
    by_path = {d.path.casefold(): d for d in interfaces}
    results = []
    for receiver in receivers:
        maker = catalog.compact(receiver.manufacturer)
        model = catalog.match_name(*receiver.names, trusted=maker in ("aula", "狼蛛"))
        # The scanner's historical F87 label is not evidence on a shared receiver.
        if receiver.hardware_id.upper() in ("3554:FA09", "258A:010C"):
            for path in receiver.hid_paths:
                interface = by_path.get(path.casefold())
                if interface is not None:
                    verified = identify_interface(interface)
                    if verified is not None:
                        model = verified
                        break
        if model is None:
            if receiver.hardware_id.upper() == "3554:FA09":
                receiver = replace(receiver, model="2.4G 接收器（型号待确认）",
                                   identification="共用硬件 ID，尚未确认配对型号；请按键唤醒后重试，或按实物选择型号")
            results.append(receiver)
            continue
        wireless = receiver.wireless or receiver.hardware_id.upper() in ("3554:FA09", "0C45:FEF9", "0C45:FEFE")
        results.append(replace(receiver, model=model["model"], manufacturer="AULA", category="键盘", wireless=wireless,
                               identification="狼蛛产品库匹配；电量支持按实际硬件版本判定"))
    return results


def legacy_default(receiver):
    if receiver.hardware_id.upper() != "3554:FA09":
        return False
    model = catalog.match_name(receiver.model, *getattr(receiver, "names", []), trusted=True)
    return model is None or model["key"] == "f87pro"


def _read_sonix(receiver, model, interfaces):
    import aula_f87_v2d as sonix
    if (model["battery_support"], receiver.hardware_id.upper()) not in (
            ("sonix-f87s", "0C45:FEF9"), ("sonix-v2", "0C45:FEFE")):
        raise ValueError("该硬件版本尚无可核实的电量协议")
    paths = {p.casefold() for p in receiver.hid_paths}
    matches = [d for d in interfaces if d.path.casefold() in paths and d.vid == 0x0C45
               and d.pid == int(receiver.hardware_id[-4:], 16)
               and d.usage_page == 0xFF60 and d.usage == 0x61
               and 10 <= d.output_len <= 1025 and 10 <= d.input_len <= 1025]
    for interface in matches:
        with hw.query_lock(interface.vid, interface.pid, 30000, interface.path), hw.HidDevice(interface.path, write=True) as dev:
            payload = bytearray()
            step = min(interface.output_len - 9, interface.input_len - 9, 56)
            for address in range(0, 56, step):
                size = min(step, 56-address)
                dev.drain()
                dev.write_output(sonix.make_request(interface.output_len, 0x10, address, size, address+size == 56), use_hidd=False)
                chunk = sonix._receive(dev, 0x10, address, size, interface.input_len, time.monotonic()+.6, None)
                if chunk is None or len(chunk) != size:
                    break
                payload.extend(chunk)
            if len(payload) != 56 or not any(payload[4:8]) or payload[17] > 100:
                continue
            if model["battery_support"] == "sonix-f87s" and payload[4:8] != bytes.fromhex("a6 38 08 29"):
                raise ValueError("接收器返回的键盘本体不是已适配的 F87S 8K")
            return payload[17]
    raise TimeoutError("没有收到有效电量回复，请按键唤醒键盘后刷新")


@shared_reading("aula-keyboard-battery-v1")
def read_keyboard(receiver, device):
    from device_readers import Reading
    model = catalog.model_for_device(device)
    reading = Reading(device.label, "接收器已连接", connected=True, status="待适配电量")
    if model is None:
        reading.error = "型号未在狼蛛机械键盘产品库中"
        return reading
    wireless = receiver.wireless or receiver.hardware_id.upper() in ("3554:FA09", "0C45:FEF9", "0C45:FEFE")
    if not wireless:
        reading.status, reading.detail = "未上报电量", "有线连接 · 该接口未提供可验证的电量"
        return reading
    interfaces = hw.enumerate_hid()
    paths = {p.casefold() for p in receiver.hid_paths}
    try:
        if model["battery_support"] == "compx" and receiver.hardware_id.upper() == "3554:FA09":
            matches = [d for d in interfaces if d.path.casefold() in paths and _compx_interface(d)]
            if not matches:
                raise ValueError("未找到所选接收器的电量接口")
            interface = matches[0]
            verified = identify_interface(interface)
            if verified is None or verified["key"] != model["key"]:
                raise ValueError("尚未确认接收器配对型号，请按键唤醒后重新搜索设备")
            if model["key"] == "f87pro":
                from device_readers import _read_f87_interface
                return replace(_read_f87_interface(interface), title=device.label)
            with hw.query_lock(interface.vid, interface.pid, 30000, interface.path), hw.HidDevice(interface.path, write=True) as dev:
                data = query_compx(dev, 0x4A, 2)
            if data[0] > 100 or data[1] not in (1, 0x10):
                raise ValueError("键盘电量或供电状态异常")
            reading.level = None if data[1] == 0x10 else data[0]
            reading.status = "充电中" if data[1] == 0x10 else "放电中"
            reading.source = "AULA 2.4G 设备上报"
            reading.detail = "键盘上报电量（2.4G）"
        elif model["battery_support"].startswith("sonix-"):
            reading.level = _read_sonix(receiver, model, interfaces)
            reading.status = "供电状态未上报"
            reading.source = "AULA SONiX 设备上报"
            reading.detail = "键盘上报电量 · 充电状态尚未验证"
            if model["battery_support"] == "sonix-v2":
                reading.detail = "协议族读取 · 需实机核对，供电状态未验证"
        else:
            reading.error = f"已识别 {model['model']}；该硬件版本的电量协议待适配"
    except (OSError, ValueError, TimeoutError) as exc:
        reading.level = None
        reading.status = "读取失败"
        reading.error = str(exc)
    return reading
