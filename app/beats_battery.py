"""Paired Beats monitor plumbing and the legacy Fast Pair battery decoder.

BeatsMonitor defaults to the validated B2P GET worker in beats_direct. The Fast
Pair parser remains available for diagnostics; its case value can be stale.
Only an existing paired address is used. No pairing, ANC, audio routing,
firmware, reset, key extraction or configuration commands are sent.
"""
import copy
import ctypes
from ctypes import wintypes
import select
import socket
import threading
import time
import uuid

from bluetooth_devices import normalized_address
from device_readers import Reading, stamp_observation

SERVICE_UUID = "df21fe2c-2515-4fdb-8886-f12c4d67927c"
PARTS = ("left", "right", "case")


def supported(device):
    return (device.transport == "bluetooth" and
            " ".join(device.model.casefold().split()).endswith("beats studio buds") and
            normalized_address(device.hardware_id) is not None)


def unknown_components():
    return {name: {"level": None, "charging": None} for name in PARTS}


def battery_reading(title, payload):
    if len(payload) != 3:
        raise ValueError("独立电量数据长度异常")
    components = {}
    for name, value in zip(PARTS, payload):
        level = value & 0x7f
        if 100 < level < 127:
            raise ValueError("独立电量数据超出有效范围")
        components[name] = {"level": None if level == 127 else level,
                            "charging": None if level == 127 else bool(value & 0x80)}
    ears = [components[name]["level"] for name in ("left", "right") if components[name]["level"] is not None]
    return Reading(title, "蓝牙已连接", level=min(ears) if ears else None,
                   status="已连接" if ears else "未上报电量", connected=True,
                   components=components, source="Google Fast Pair",
                   detail="Fast Pair 独立电量上报 · 充电仓开盖后更新",
                   extra="左耳、右耳及充电仓分别取自耳机上报；未知项显示 --。仓电量可能是耳机最近获知的值，合盖休眠后不保证持续更新。")


class FrameBuffer:
    def __init__(self):
        self.buffer = bytearray()

    def feed(self, data):
        self.buffer.extend(data)
        if len(self.buffer) > 8192:
            raise ValueError("Fast Pair 数据缓冲区异常")
        frames = []
        while len(self.buffer) >= 4:
            group, code = self.buffer[:2]
            length = int.from_bytes(self.buffer[2:4], "big")
            if length > 4096:
                raise ValueError("Fast Pair 消息长度异常")
            if len(self.buffer) < length + 4:
                break
            frames.append((group, code, bytes(self.buffer[4:4 + length])))
            del self.buffer[:4 + length]
        return frames


class BluetoothAddress(ctypes.Structure):
    _pack_ = 1  # Microsoft's ws2bth.h uses pshpack1.h (30-byte SOCKADDR_BTH).
    _fields_ = [("family", wintypes.USHORT), ("address", ctypes.c_uint64),
                ("service", ctypes.c_ubyte * 16), ("port", wintypes.ULONG)]


def open_stream(address, stop, *, service_uuid=None):
    normalized = normalized_address(address)
    if normalized is None:
        raise ValueError("蓝牙地址无效")
    remote = BluetoothAddress(socket.AF_BLUETOOTH, int(normalized, 16),
                              (ctypes.c_ubyte * 16).from_buffer_copy(uuid.UUID(service_uuid or SERVICE_UUID).bytes_le), 0)
    ws = ctypes.WinDLL("ws2_32", use_last_error=True)
    ws.connect.argtypes = [ctypes.c_size_t, ctypes.POINTER(BluetoothAddress), ctypes.c_int]
    ws.connect.restype = ctypes.c_int
    ws.WSAGetLastError.restype = ctypes.c_int
    channel = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_STREAM, socket.BTPROTO_RFCOMM)
    try:
        channel.setblocking(False)
        result = ws.connect(channel.fileno(), ctypes.byref(remote), ctypes.sizeof(remote))
        if result != 0:
            code = ws.WSAGetLastError()
            if code != 10035:
                raise OSError(code, "独立电量通道暂未连接，请唤醒耳机或打开充电仓")
            deadline = time.monotonic() + 6
            while not stop.is_set():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("独立电量通道连接超时")
                _, ready, failed = select.select([], [channel], [channel], min(.2, remaining))
                if ready or failed:
                    code = channel.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR)
                    if failed or code:
                        raise OSError(code, "独立电量通道暂不可用")
                    break
            else:
                raise InterruptedError("电量监听已停止")
        channel.settimeout(.5)
        return channel
    except BaseException:
        channel.close()
        raise


class BatteryWorker:
    def __init__(self, device, *, channel_factory=open_stream):
        self.device, self.channel_factory = device, channel_factory
        self.stop_event, self.lock = threading.Event(), threading.Lock()
        self.snapshot = None
        self.thread = threading.Thread(target=self.run, name="BeatsBattery", daemon=True)

    def start(self):
        self.thread.start()

    def publish(self, reading):
        if not self.stop_event.is_set():
            with self.lock:
                self.snapshot = stamp_observation(reading)

    def cached(self):
        with self.lock:
            return None if self.stop_event.is_set() else copy.deepcopy(self.snapshot)

    def run(self):
        while not self.stop_event.is_set():
            try:
                self.session()
            except Exception as exc:
                self.publish(Reading(self.device.label, "独立电量暂不可用", connected=None,
                                     error=str(exc), source="Google Fast Pair", components=unknown_components()))
            self.stop_event.wait(2)

    def session(self):
        parser = FrameBuffer()
        received_battery = False
        first_deadline = time.monotonic() + 10
        with self.channel_factory(self.device.hardware_id, self.stop_event) as channel:
            while not self.stop_event.is_set():
                try:
                    chunk = channel.recv(4096)
                except TimeoutError:
                    if not received_battery and time.monotonic() > first_deadline:
                        raise TimeoutError("耳机暂未上报独立电量，请打开充电仓重试")
                    continue
                if not chunk:
                    raise ConnectionError("耳机电量通道已断开，正在重连")
                for group, code, payload in parser.feed(chunk):
                    if (group, code) == (3, 3):
                        self.publish(battery_reading(self.device.label, payload))
                        received_battery = True


class BeatsMonitor:
    def __init__(self, *, worker_factory=None):
        self.workers, self.closed = {}, False
        if worker_factory is None:
            from beats_direct import BatteryWorker as worker_factory
        self.worker_factory = worker_factory

    def set_devices(self, devices):
        if self.closed:
            return
        wanted = {d.id: d for d in devices if supported(d)}
        for key, worker in list(self.workers.items()):
            if key not in wanted or normalized_address(worker.device.hardware_id) != normalized_address(wanted[key].hardware_id):
                worker.stop_event.set()
                del self.workers[key]
        for key, device in wanted.items():
            if key not in self.workers:
                worker = self.worker_factory(device)
                self.workers[key] = worker
                worker.start()

    def cached_reading(self, device):
        worker = self.workers.get(device.id)
        if worker is None or normalized_address(worker.device.hardware_id) != normalized_address(device.hardware_id):
            return None
        return worker.cached()

    def request_refresh(self):
        for worker in self.workers.values():
            refresh = getattr(worker, "request_refresh", None)
            if refresh is not None:
                refresh()

    def close(self):
        self.closed = True
        workers = list(self.workers.values())
        self.workers.clear()
        for worker in workers:
            worker.stop_event.set()
            refresh = getattr(worker, "request_refresh", None)
            if refresh is not None:
                refresh()
        deadline = time.monotonic() + 1.2
        for worker in workers:
            worker.thread.join(max(0, deadline - time.monotonic()))
