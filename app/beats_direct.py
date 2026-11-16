"""Beats Studio Buds battery GETs over the paired device's serial service.

Protocol facts: B2P framing documented by PairFree (docs/apple/B2P.md), then
verified against the official Beats 2.12 x86_64 library and this device's replies.
Only product-ID (013A) and untethered battery-state (0430) GETs can be sent.
No settings, pairing, firmware, reset or authentication/key commands are exposed.
"""
import copy
import threading
import time

from beats_battery import BatteryWorker as PassiveWorker, open_stream, unknown_components
from device_readers import Reading

SERIAL_SERVICE = "00001101-0000-1000-8000-00805f9b34fb"
POLL_SECONDS, MAX_AGE, RESPONSE_TIMEOUT = 5.0, 12.0, 2.0
PRODUCT_GET, BATTERY_GET = 0x013a, 0x0430
SUPPORTED_PRODUCT = 0x2011
SOURCE = "Beats 原生查询"


class ProtocolError(ValueError):
    pass


def crc32(data):
    value = 0
    for byte in data:
        value ^= byte << 24
        for _ in range(8):
            value = ((value << 1) ^ (0x04c11db7 if value & 0x80000000 else 0)) & 0xffffffff
    return value ^ 0xffffffff


def query_frame(sequence, opcode):
    if opcode not in (PRODUCT_GET, BATTERY_GET) or not 1 <= sequence <= 255:
        raise ProtocolError("仅允许型号与电量查询")
    header = b"\xff\xb2\x0b\x00" + bytes((sequence,)) + opcode.to_bytes(2, "little")
    return header + crc32(header).to_bytes(4, "little")


class FrameBuffer:
    def __init__(self):
        self.buffer = bytearray()

    def feed(self, data):
        self.buffer.extend(data)
        if len(self.buffer) > 8192:
            raise ProtocolError("电量数据缓冲区异常")
        frames = []
        while len(self.buffer) >= 4:
            if self.buffer[:2] != b"\xff\xb2":
                del self.buffer[0]
                continue
            size = int.from_bytes(self.buffer[2:4], "little")
            if not 11 <= size <= 4096:
                raise ProtocolError("电量数据长度异常")
            if len(self.buffer) < size:
                break
            packet = bytes(self.buffer[:size])
            del self.buffer[:size]
            if crc32(packet[:-4]) != int.from_bytes(packet[-4:], "little"):
                raise ProtocolError("电量数据校验失败")
            frames.append((packet[4], int.from_bytes(packet[5:7], "little"), packet[7:-4]))
        return frames


def battery_reading(title, payload):
    # Response byte 0 is the command status; 0 means success.
    if not payload or payload[0] != 0:
        raise ProtocolError("耳机拒绝电量查询")
    values = payload[1:]
    if len(values) != 20:
        raise ProtocolError("此型号的部件电量格式不匹配")
    components = unknown_components()
    names = {2: "right", 4: "left", 8: "case"}
    seen = set()
    for offset in (0, 5, 10):
        kind, marker, level, status, connected = values[offset:offset+5]
        if kind not in names or kind in seen or marker != 1 or connected != 1:
            raise ProtocolError("部件电量标识异常")
        seen.add(kind)
        if status not in (0, 1, 2, 4):
            raise ProtocolError("部件电量状态异常")
        if status == 4:
            # Disconnected components contain a zero placeholder, not a 0% reading.
            continue
        if level > 100:
            raise ProtocolError("部件电量超出有效范围")
        # The official getter also sets its charge bit for status 0 at 100%
        # (charge complete while docked). Other status-0 levels are not charging.
        components[names[kind]] = {"level": level, "charging": status == 1 or (status == 0 and level == 100)}
    ears = [components[key]["level"] for key in ("left", "right") if components[key]["level"] is not None]
    case_known = components["case"]["level"] is not None
    any_known = any(part["level"] is not None for part in components.values())
    return Reading(title, "蓝牙已连接", level=min(ears) if ears else None, connected=True,
                   status="已连接" if any_known else "未上报电量", source=SOURCE, components=components,
                   detail="Beats 原生查询 · " + ("每 5 秒核对电量" if case_known else "充电仓暂不可读取"),
                   extra="各部件取自设备查询回复。不可读取的部件显示 --，不会沿用旧读数。")


class Connection:
    def __init__(self, channel, stop, *, clock=time.monotonic):
        self.channel, self.stop, self.clock = channel, stop, clock
        self.sequence = 0
        self.parser = FrameBuffer()

    def query(self, opcode):
        self.sequence = self.sequence % 255 + 1
        self.channel.sendall(query_frame(self.sequence, opcode))
        deadline = self.clock() + RESPONSE_TIMEOUT
        while not self.stop.is_set() and self.clock() < deadline:
            try:
                data = self.channel.recv(4096)
            except TimeoutError:
                continue
            if not data:
                raise ConnectionError("耳机电量通道断开")
            for sequence, response, payload in self.parser.feed(data):
                if sequence == self.sequence and response == opcode + 1:
                    return payload
        if self.stop.is_set():
            raise InterruptedError("电量查询已停止")
        raise TimeoutError("耳机未回应电量查询")


def open_serial(address, stop):
    return open_stream(address, stop, service_uuid=SERIAL_SERVICE)


class BatteryWorker(PassiveWorker):
    def __init__(self, device, *, channel_factory=open_serial, clock=time.monotonic):
        super().__init__(device, channel_factory=channel_factory)
        self.clock, self.updated_at = clock, None
        self.refresh_event = threading.Event()

    def unavailable(self, error):
        return Reading(self.device.label, "电量暂不可读取", error=str(error), connected=None,
                       source=SOURCE, components=unknown_components())

    def publish(self, reading):
        if not self.stop_event.is_set():
            from device_readers import stamp_observation
            with self.lock:
                self.snapshot, self.updated_at = stamp_observation(reading), self.clock()

    def cached(self):
        with self.lock:
            if self.stop_event.is_set() or self.snapshot is None:
                return None
            if self.clock() - self.updated_at > MAX_AGE:
                return self.unavailable("电量查询已过期，正在重新连接")
            return copy.deepcopy(self.snapshot)

    def request_refresh(self):
        self.refresh_event.set()

    def run(self):
        while not self.stop_event.is_set():
            try:
                self.session()
            except Exception as error:
                self.publish(self.unavailable(error))
            self.stop_event.wait(2)

    def session(self):
        with self.channel_factory(self.device.hardware_id, self.stop_event) as channel:
            connection = Connection(channel, self.stop_event, clock=self.clock)
            identity = connection.query(PRODUCT_GET)
            if len(identity) != 3 or identity[0] != 0 or int.from_bytes(identity[1:], "little") != SUPPORTED_PRODUCT:
                raise ProtocolError("当前配对型号不是已验证的 Beats Studio Buds")
            while not self.stop_event.is_set():
                self.refresh_event.clear()
                self.publish(battery_reading(self.device.label, connection.query(BATTERY_GET)))
                deadline = self.clock() + POLL_SECONDS
                while not self.stop_event.is_set() and not self.refresh_event.is_set():
                    remaining = deadline - self.clock()
                    if remaining <= 0:
                        break
                    self.refresh_event.wait(min(.3, remaining))
