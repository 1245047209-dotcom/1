"""Read-only G HUB IPC client: loopback WebSocket, no third-party dependencies.

GET /devices/list, GET /battery/<id>/state and state subscriptions were
cross-checked with the original LGSTrayBattery GHubManager implementation:
https://github.com/andyvorld/LGSTrayBattery/blob/master/LGSTrayCore/Managers/GHubManager.cs
Only the local driver is contacted. No profile, pairing or hardware settings
are changed, and no G HUB database files are opened.
"""
from __future__ import annotations

import base64
import hashlib
import json
import math
import os
import re
import socket
import struct
import threading
import time

from device_readers import Reading

MAX_MESSAGE = 2 * 1024 * 1024
MAX_AGE = 15.0
_ID = re.compile(r"dev[A-Za-z0-9_-]+\Z")
_GPW_NAMES = {"prowireless", "prowirelessmouse", "gprowireless", "gprowirelessgamingmouse"}


class GHubError(ValueError):
    pass


class LocalWebSocket:
    """RFC 6455 text transport, fixed to G HUB's loopback endpoint."""
    def __init__(self):
        self.socket = socket.create_connection(("127.0.0.1", 9010), timeout=.8)
        self.buffer = bytearray()
        self.fragments = bytearray()
        self.fragmenting = False
        try:
            key = base64.b64encode(os.urandom(16)).decode("ascii")
            request = ("GET / HTTP/1.1\r\nHost: 127.0.0.1:9010\r\n"
                       "Upgrade: websocket\r\nConnection: Upgrade\r\nOrigin: file://\r\n"
                       f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n"
                       "Sec-WebSocket-Protocol: json\r\n\r\n")
            self.socket.sendall(request.encode("ascii"))
            deadline = time.monotonic() + 1.2
            while b"\r\n\r\n" not in self.buffer:
                if time.monotonic() >= deadline or len(self.buffer) > 32768:
                    raise GHubError("G HUB 握手超时或返回异常")
                self._receive_bytes()
            header, remainder = bytes(self.buffer).split(b"\r\n\r\n", 1)
            self.buffer = bytearray(remainder)
            lines = header.decode("iso-8859-1").split("\r\n")
            headers = {k.strip().lower(): v.strip() for k, v in
                       (line.split(":", 1) for line in lines[1:] if ":" in line)}
            expected = base64.b64encode(hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()).decode()
            if (lines[0].split()[1:2] != ["101"] or
                    headers.get("sec-websocket-accept") != expected or
                    headers.get("upgrade", "").lower() != "websocket" or
                    headers.get("sec-websocket-extensions")):
                raise GHubError("G HUB 本机接口握手失败")
            self.socket.settimeout(.4)
        except Exception:
            self.socket.close()
            raise

    def _receive_bytes(self):
        chunk = self.socket.recv(65536)
        if not chunk:
            raise ConnectionError("G HUB 已断开")
        self.buffer.extend(chunk)
        if len(self.buffer) > MAX_MESSAGE + 32:
            raise GHubError("G HUB 消息过大")

    def send_frame(self, opcode, payload):
        payload = bytes(payload)
        if len(payload) > MAX_MESSAGE:
            raise GHubError("消息过大")
        length = len(payload)
        header = bytes((0x80 | opcode, 0x80 | (length if length < 126 else 126 if length <= 65535 else 127)))
        if length >= 126:
            header += struct.pack("!H" if length <= 65535 else "!Q", length)
        mask = os.urandom(4)
        self.socket.sendall(header + mask + bytes(value ^ mask[i % 4] for i, value in enumerate(payload)))

    def send_json(self, message):
        if message.get("verb") not in ("GET", "SUBSCRIBE"):
            raise GHubError("此适配器只允许读取和订阅设备状态")
        self.send_frame(1, json.dumps(message, separators=(",", ":")).encode("utf-8"))

    def receive_json(self):
        while True:
            while len(self.buffer) < 2:
                self._receive_bytes()
            first, second = self.buffer[:2]
            final, opcode = bool(first & 0x80), first & 0x0F
            if first & 0x70 or second & 0x80:
                raise GHubError("不支持的 G HUB WebSocket 帧")
            length, offset = second & 0x7F, 2
            if length in (126, 127):
                size = 2 if length == 126 else 8
                while len(self.buffer) < 2 + size:
                    self._receive_bytes()
                length = int.from_bytes(self.buffer[2:2 + size], "big")
                offset += size
            if length > MAX_MESSAGE or (opcode >= 8 and (not final or length > 125)):
                raise GHubError("G HUB 帧长度异常")
            while len(self.buffer) < offset + length:
                self._receive_bytes()
            payload = bytes(self.buffer[offset:offset + length])
            del self.buffer[:offset + length]
            if opcode == 8:
                raise ConnectionError("G HUB 已关闭连接")
            if opcode == 9:
                self.send_frame(10, payload)
                continue
            if opcode == 10:
                continue
            if opcode == 1:
                if self.fragmenting:
                    raise GHubError("消息分片异常")
                self.fragments = bytearray(payload)
                self.fragmenting = not final
            elif opcode == 0 and self.fragmenting:
                self.fragments.extend(payload)
                self.fragmenting = not final
            else:
                raise GHubError("不支持的 G HUB 消息类型")
            if len(self.fragments) > MAX_MESSAGE:
                raise GHubError("G HUB 消息过大")
            if final:
                message = json.loads(self.fragments.decode("utf-8"))
                self.fragments.clear()
                if not isinstance(message, dict):
                    raise GHubError("G HUB 返回了无效消息")
                return message

    def close(self):
        self.socket.close()


def is_pro_wireless(info):
    if str(info.get("deviceType", "")).upper() != "MOUSE":
        return False
    if info.get("isConnected") is False or info.get("connected") is False:
        return False
    if info.get("state") not in (None, "ACTIVE", "CONNECTED"):
        return False
    capabilities = info.get("capabilities")
    if not isinstance(capabilities, dict) or capabilities.get("hasBatteryStatus") is not True:
        return False
    names = [info.get("displayName", ""), info.get("extendedDisplayName", "")]
    return any(re.sub(r"[^a-z0-9]", "", str(n).casefold()) in _GPW_NAMES for n in names)


def choose_device(devices, receiver, unique_receiver):
    paths = {p.upper() for p in getattr(receiver, "usb_paths", [])}
    exact = []
    metadata_present = False
    for device_id, info in devices.items():
        interfaces = info.get("activeInterfaces", [])
        if not isinstance(interfaces, list):
            interfaces = []
        reported = [info.get("path", "")] + [i.get("path", "") for i in interfaces if isinstance(i, dict)]
        reported = {re.sub(r":\d+$", "", p).upper() for p in reported if isinstance(p, str) and p}
        metadata_present |= bool(reported)
        if paths & reported:
            exact.append(device_id)
    if len(exact) == 1:
        return exact[0]
    # Never override an explicit physical-path mismatch with a model-name match.
    if not exact and unique_receiver and len(devices) == 1 and not (paths and metadata_present):
        return next(iter(devices))
    return None


def reading_from_payload(payload, device_id, title):
    if payload.get("deviceId") != device_id:
        raise GHubError("G HUB 设备标识不匹配")
    level = payload.get("percentage")
    if type(level) not in (int, float) or not math.isfinite(level) or not 0 <= level <= 100:
        raise GHubError("G HUB 电量无效")
    charging = payload.get("charging")
    if type(charging) is not bool or payload.get("isBatteryRemoved") is True or payload.get("chargingError") is True:
        raise GHubError("G HUB 报告电池状态异常")
    mileage = payload.get("mileage")
    if (payload.get("batteryMileageSupport") != "MILEAGE_SUPPORTED" or
            type(mileage) not in (int, float) or not math.isfinite(mileage) or not 0 <= mileage <= 10000):
        mileage = None
    if mileage == 0 and level > 0:
        mileage = None
    status = "充电中" if charging else "已充满" if payload.get("fullyCharged") is True else "电池供电"
    return Reading(title, "接收器已连接", level=round(level), status=status,
                   detail="来自本机 Logitech G HUB · 自动同步",
                   extra="电量使用 G HUB 当前报告值；剩余时长为驱动估计。",
                   remaining_hours=mileage, connected=True, estimated=False, source="G HUB")


class GHubMonitor:
    def __init__(self):
        self.condition = threading.Condition()
        self.stop_event = threading.Event()
        self.connected = False
        self.devices = {}
        self.batteries = {}
        self.bindings = {}
        self.interests = {}
        self.revision = 0
        self.thread = threading.Thread(target=self._run, name="Nexus-GHub-Battery", daemon=True)
        self.thread.start()

    def handle(self, message, ws):
        path, payload = message.get("path", ""), message.get("payload", {})
        if not isinstance(path, str) or not isinstance(payload, dict):
            return
        result = message.get("result") or {}
        if not isinstance(result, dict) or result.get("code", "SUCCESS") != "SUCCESS":
            match = re.fullmatch(r"/battery/(dev[A-Za-z0-9_-]+)/state", path)
            if match:
                with self.condition:
                    self.batteries.pop(match[1], None)
                    self.revision += 1
                    self.condition.notify_all()
            return
        if path == "/devices/list":
            infos = payload.get("deviceInfos")
            if not isinstance(infos, list):
                raise GHubError("G HUB 设备列表无效")
            devices = {d["id"]: d for d in infos if isinstance(d, dict) and
                       isinstance(d.get("id"), str) and _ID.fullmatch(d["id"]) and is_pro_wireless(d)}
            with self.condition:
                self.devices = devices
                self.batteries = {key: value for key, value in self.batteries.items() if key in devices}
                self.bindings = {instance: device_id for instance, (receiver, unique) in self.interests.items()
                                 if (device_id := choose_device(devices, receiver, unique)) is not None}
                self.condition.notify_all()
            for device_id in devices:
                ws.send_json({"msgId": "nexus-battery", "verb": "GET", "path": f"/battery/{device_id}/state"})
        elif path == "/devices/state/changed":
            with self.condition:
                self.batteries.clear()
                self.bindings.clear()
                self.revision += 1
                self.condition.notify_all()
            ws.send_json({"msgId": "nexus-devices", "verb": "GET", "path": "/devices/list"})
        elif path == "/battery/state/changed" or re.fullmatch(r"/battery/dev[A-Za-z0-9_-]+/state", path):
            device_id = payload.get("deviceId")
            with self.condition:
                if device_id not in self.devices:
                    return
                if path != "/battery/state/changed" and path != f"/battery/{device_id}/state":
                    return
                try:
                    reading_from_payload(payload, device_id, "PRO WIRELESS")
                except GHubError:
                    self.batteries.pop(device_id, None)
                else:
                    self.batteries[device_id] = (dict(payload), time.monotonic())
                self.revision += 1
                self.condition.notify_all()

    def _run(self):
        while not self.stop_event.is_set():
            ws = None
            try:
                ws = LocalWebSocket()
                with self.condition:
                    self.connected = True
                    self.condition.notify_all()
                for path in ("/devices/state/changed", "/battery/state/changed"):
                    ws.send_json({"msgId": "nexus-subscribe", "verb": "SUBSCRIBE", "path": path})
                ws.send_json({"msgId": "nexus-devices", "verb": "GET", "path": "/devices/list"})
                last_poll = time.monotonic()
                while not self.stop_event.is_set():
                    if time.monotonic() - last_poll >= 5:
                        # Poll also validates the connection when state is unchanged.
                        ws.send_json({"msgId": "nexus-devices", "verb": "GET", "path": "/devices/list"})
                        last_poll = time.monotonic()
                    try:
                        message = ws.receive_json()
                    except socket.timeout:
                        continue
                    self.handle(message, ws)
            except (OSError, ValueError, ConnectionError):
                pass
            finally:
                if ws:
                    ws.close()
                with self.condition:
                    self.connected = False
                    self.devices.clear()
                    self.batteries.clear()
                    self.bindings.clear()
                    self.revision += 1
                    self.condition.notify_all()
            self.stop_event.wait(2)

    def read(self, receiver, title, *, bind=False, unique_receiver=False, wait=0):
        instance = receiver.instance_id.upper()
        deadline = time.monotonic() + wait
        with self.condition:
            while True:
                device_id = self.bindings.get(instance)
                if bind:
                    self.interests[instance] = (receiver, unique_receiver)
                    device_id = choose_device(self.devices, receiver, unique_receiver)
                    if device_id is not None:
                        self.bindings[instance] = device_id
                snapshot = self.batteries.get(device_id)
                if (self.connected and device_id in self.devices and snapshot is not None and
                        time.monotonic() - snapshot[1] <= MAX_AGE):
                    return reading_from_payload(snapshot[0], device_id, title)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self.condition.wait(remaining)


_monitor = None
_monitor_lock = threading.Lock()


def get_monitor():
    global _monitor
    with _monitor_lock:
        if _monitor is None:
            _monitor = GHubMonitor()
        return _monitor


def read_from_ghub(receiver, title, *, unique_receiver):
    if receiver.hardware_id.upper() != "046D:C539":
        return None
    return get_monitor().read(receiver, title, bind=True, unique_receiver=unique_receiver, wait=1.4)


def cached_reading(device):
    if _monitor is None:
        return None
    return _monitor.read(device, device.label)


def stop_monitor():
    if _monitor is not None:
        _monitor.stop_event.set()
