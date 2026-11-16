"""Read-only HID++ battery event listener with a five-second polling fallback.

Each worker is bound to a saved physical USB instance, validates the paired
model, and owns its HID handles. It never contacts a driver IPC service.
"""
from dataclasses import replace
import threading
import time

import hid_win as hw
import logitech_battery as lb
from device_readers import Reading, stamp_observation
from receiver_scan import scan_receivers
from query_coordinator import query_reading, resource_key

POLL_SECONDS = 5.0
MAX_AGE = 12.0


def decode_battery_event(report, feature_index):
    # Software ID zero is a broadcast. Other clients' query replies are ignored.
    if len(report) != 20 or report[:4] != bytes((0x11, 1, feature_index, 0)):
        return None
    return lb.decode_voltage(report[4:])


class BatteryWorker:
    def __init__(self, device, *, clock=time.monotonic):
        self.device, self.clock = device, clock
        self.stop_event = threading.Event()
        self.lock = threading.Lock()
        self.snapshot = None
        self.thread = threading.Thread(target=self.run, name="HIDppBattery", daemon=True)

    def start(self):
        self.thread.start()

    def publish(self, reading):
        if not self.stop_event.is_set():
            with self.lock:
                self.snapshot = (self.clock(), stamp_observation(reading))

    def cached(self):
        with self.lock:
            snapshot = self.snapshot
        if snapshot is None or self.stop_event.is_set():
            return None
        updated, reading = snapshot
        if self.clock() - updated > MAX_AGE:
            return Reading(self.device.label, "监测未更新", error="电池监测数据已过期，正在重新连接",
                           source="HID++ 实时", connected=None)
        return replace(reading)

    def run(self):
        while not self.stop_event.is_set():
            try:
                self.connected_session()
            except Exception as exc:
                self.publish(Reading(self.device.label, "监测暂不可用", error=str(exc),
                                     source="HID++ 实时", connected=None))
            self.stop_event.wait(2)

    def connected_session(self):
        receiver = next((r for r in scan_receivers()
                         if r.instance_id.casefold() == self.device.instance_id.casefold()
                         and r.hardware_id.upper() == "046D:C539"), None)
        if receiver is None:
            self.publish(Reading(self.device.label, "接收器未连接", error="未找到已加入的接收器",
                                 connected=False, source="HID++ 实时"))
            return
        paths = {p.casefold() for p in receiver.hid_paths}
        interfaces = [d for d in hw.enumerate_hid() if d.path.casefold() in paths
                      and (d.vid, d.pid, d.usage_page, d.usage, d.input_len, d.output_len)
                      == (0x046D, 0xC539, 0xFF00, 2, 20, 20)]
        if not interfaces:
            raise lb.HidppError("未找到所选接收器的罗技电量接口")
        with hw.HidDevice(interfaces[0].path, write=True) as dev:
            if not dev.write_access:
                raise lb.HidppError("无法访问罗技电量查询接口")
            with hw.query_lock(0x046D, 0xC539, 30000, interfaces[0].path):
                self.check_model(dev)
                index = lb.feature_index(dev, 0x1001)
                if index is None:
                    raise lb.HidppError("该鼠标没有已适配的电池电压接口")
            next_poll, next_identity_check = 0, self.clock() + 30
            while not self.stop_event.is_set():
                if self.clock() >= next_poll:
                    def poll():
                        nonlocal next_identity_check
                        with hw.query_lock(0x046D, 0xC539, 30000, interfaces[0].path):
                            if self.clock() >= next_identity_check:
                                self.check_model(dev)
                                next_identity_check = self.clock() + 30
                            millivolts, status = lb.decode_voltage(lb.exchange(dev, index))
                        return lb.voltage_reading(self.device.label, millivolts, status, live=True)
                    sample = query_reading(resource_key(receiver), "pro-wireless-battery-v1", poll)
                    self.publish(replace(sample, title=self.device.label, source="HID++ 实时"))
                    next_poll = self.clock() + POLL_SECONDS
                # Listening is also serialized. An idle background listener must
                # never consume a foreground client's solicited response.
                with hw.query_lock(0x046D, 0xC539, 30000, interfaces[0].path):
                    report = dev.read_input(timeout_ms=max(1, min(250, int((next_poll - self.clock()) * 1000))),
                                            stop_flag=self.stop_event.is_set)
                if report:
                    voltage = decode_battery_event(report, index)
                    if voltage is not None:
                        self.publish(lb.voltage_reading(self.device.label, *voltage, live=True))

    @staticmethod
    def check_model(dev):
        name = " ".join(lb.device_name(dev).casefold().split())
        if name not in lb._PRO_WIRELESS_NAMES:
            raise lb.HidppError("当前配对型号未校准，已停止电压换算")


class MouseMonitor:
    def __init__(self, *, worker_factory=BatteryWorker):
        self.workers = {}
        self.worker_factory = worker_factory
        self.closed = False

    def set_devices(self, devices):
        if self.closed:
            return
        wanted = {d.id: d for d in devices if d.transport == "usb" and d.hardware_id.upper() == "046D:C539"}
        for key, worker in list(self.workers.items()):
            if key not in wanted or worker.device.instance_id.casefold() != wanted[key].instance_id.casefold():
                worker.stop_event.set()
                del self.workers[key]
        for key, device in wanted.items():
            if key not in self.workers:
                worker = self.worker_factory(device)
                self.workers[key] = worker
                worker.start()

    def cached_reading(self, device):
        worker = self.workers.get(device.id)
        if worker is None or worker.device.instance_id.casefold() != device.instance_id.casefold():
            return None
        return worker.cached()

    def close(self):
        self.closed = True
        workers = list(self.workers.values())
        self.workers.clear()
        for worker in workers:
            worker.stop_event.set()
        deadline = time.monotonic() + 1.2
        for worker in workers:
            worker.thread.join(max(0, deadline - time.monotonic()))
