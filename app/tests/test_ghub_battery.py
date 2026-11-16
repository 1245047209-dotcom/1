import base64
import hashlib
import json
import socket
import struct
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import ghub_battery as gb
from device_readers import Reading

PATH_A = r"USB\VID_046D&PID_C539&MI_02\PORT_A"
PATH_B = r"USB\VID_046D&PID_C539&MI_02\PORT_B"


def info(device_id="devA", path=PATH_A):
    return {"id": device_id, "displayName": "PRO WIRELESS", "deviceType": "MOUSE", "state": "ACTIVE",
            "capabilities": {"hasBatteryStatus": True}, "path": path.lower() + ":0"}


def payload(device_id="devA", percentage=80, **kwargs):
    return {"deviceId": device_id, "percentage": percentage, "charging": False,
            "mileage": 51.18, "batteryMileageSupport": "MILEAGE_SUPPORTED", **kwargs}


def server_frame(data, opcode=1, final=True):
    length = len(data)
    header = bytes(((0x80 if final else 0) | opcode, length if length < 126 else 126 if length < 65536 else 127))
    if length >= 126:
        header += struct.pack("!H" if length < 65536 else "!Q", length)
    return header + data


class FakeSocket:
    def __init__(self, chunks=()):
        self.chunks = list(chunks)
        self.sent = []
        self.closed = False

    def recv(self, size):
        if not self.chunks:
            raise socket.timeout()
        chunk = self.chunks.pop(0)
        if isinstance(chunk, Exception):
            raise chunk
        return chunk

    def sendall(self, data):
        self.sent.append(data)

    def settimeout(self, value):
        pass

    def close(self):
        self.closed = True


def transport(chunks):
    ws = gb.LocalWebSocket.__new__(gb.LocalWebSocket)
    ws.socket = FakeSocket(chunks)
    ws.buffer, ws.fragments = bytearray(), bytearray()
    ws.fragmenting = False
    return ws


def monitor():
    m = gb.GHubMonitor.__new__(gb.GHubMonitor)
    m.condition = threading.Condition()
    m.stop_event = threading.Event()
    m.connected, m.revision = True, 0
    m.devices, m.batteries, m.bindings, m.interests = {}, {}, {}, {}
    return m


class TransportTests(unittest.TestCase):
    def test_handshake_checks_accept_and_only_contacts_loopback(self):
        fake = FakeSocket()
        def send(request):
            fake.sent.append(request)
            key = next(line.split(b": ", 1)[1] for line in request.split(b"\r\n") if line.startswith(b"Sec-WebSocket-Key:"))
            accept = base64.b64encode(hashlib.sha1(key + b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11").digest())
            fake.chunks.append(b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nSec-WebSocket-Accept: " + accept + b"\r\n\r\n" + server_frame(b'{"path":"ok"}'))
        fake.sendall = send
        with patch.object(gb.socket, "create_connection", return_value=fake) as connect:
            ws = gb.LocalWebSocket()
        self.assertEqual(connect.call_args.args[0], ("127.0.0.1", 9010))
        self.assertEqual(ws.receive_json(), {"path": "ok"})

    def test_invalid_handshake_closes_socket(self):
        fake = FakeSocket([b"HTTP/1.1 403 Forbidden\r\n\r\n"])
        with patch.object(gb.socket, "create_connection", return_value=fake), self.assertRaises(gb.GHubError):
            gb.LocalWebSocket()
        self.assertTrue(fake.closed)

    def test_partial_frame_survives_socket_timeout(self):
        data = server_frame(b'{"percentage":80}')
        ws = transport([data[:4], socket.timeout(), data[4:]])
        with self.assertRaises(socket.timeout):
            ws.receive_json()
        self.assertEqual(ws.receive_json(), {"percentage": 80})

    def test_fragmented_message_and_interleaved_ping(self):
        ws = transport([server_frame(b'{"percentage":', final=False) +
                        server_frame(b"check", opcode=9) + server_frame(b"80}", opcode=0)])
        self.assertEqual(ws.receive_json()["percentage"], 80)
        pong = ws.socket.sent[0]
        self.assertEqual(pong[0], 0x8A)
        self.assertTrue(pong[1] & 0x80)
        self.assertEqual(bytes(b ^ pong[2 + i % 4] for i, b in enumerate(pong[6:])), b"check")

    def test_extended_length_device_message_is_supported(self):
        message = {"value": "x" * 70000}
        ws = transport([server_frame(json.dumps(message).encode())])
        self.assertEqual(ws.receive_json(), message)

    def test_oversized_frame_and_compressed_frame_are_rejected(self):
        for header in (b"\x81\x7f" + struct.pack("!Q", gb.MAX_MESSAGE + 1), b"\xc1\x00"):
            with self.subTest(header=header), self.assertRaises(gb.GHubError):
                transport([header]).receive_json()

    def test_close_is_not_treated_as_current_battery_data(self):
        with self.assertRaises(ConnectionError):
            transport([server_frame(b"", opcode=8)]).receive_json()

    def test_client_masks_payload_and_blocks_configuration_writes(self):
        ws = transport([])
        message = {"verb": "GET", "path": "/devices/list"}
        ws.send_json(message)
        frame = ws.socket.sent[0]
        self.assertTrue(frame[1] & 0x80)
        raw = bytes(b ^ frame[2 + i % 4] for i, b in enumerate(frame[6:]))
        self.assertEqual(json.loads(raw), message)
        with self.assertRaises(gb.GHubError):
            ws.send_json({"verb": "SET", "path": "/devices/config"})


class DriverDataTests(unittest.TestCase):
    def test_real_driver_sample_is_not_labeled_voltage_estimation(self):
        reading = gb.reading_from_payload(payload(), "devA", "Mouse")
        self.assertEqual(reading.display_level, "80%")
        self.assertEqual(reading.source, "G HUB")
        self.assertEqual(reading.remaining_hours, 51.18)
        self.assertFalse(reading.estimated)

    def test_invalid_percentages_are_rejected_including_boolean_and_nan(self):
        for percentage in (-1, 101, True, "80", None, float("nan"), float("inf")):
            with self.subTest(percentage=percentage), self.assertRaises(gb.GHubError):
                gb.reading_from_payload(payload(percentage=percentage), "devA", "Mouse")

    def test_zero_percent_is_valid(self):
        self.assertEqual(gb.reading_from_payload(payload(percentage=0), "devA", "Mouse").display_level, "0%")

    def test_other_device_or_battery_errors_are_rejected(self):
        for data in (payload(device_id="devB"), payload(chargingError=True), payload(isBatteryRemoved=True), payload(charging="false")):
            with self.subTest(data=data), self.assertRaises(gb.GHubError):
                gb.reading_from_payload(data, "devA", "Mouse")

    def test_charge_state_and_invalid_mileage(self):
        self.assertEqual(gb.reading_from_payload(payload(charging=True), "devA", "Mouse").status, "充电中")
        self.assertEqual(gb.reading_from_payload(payload(fullyCharged=True), "devA", "Mouse").status, "已充满")
        for mileage in (-1, float("nan"), "51", 0):
            self.assertIsNone(gb.reading_from_payload(payload(mileage=mileage), "devA", "Mouse").remaining_hours)

    def test_inactive_or_wrong_model_is_not_matched(self):
        self.assertTrue(gb.is_pro_wireless(info()))
        for override in ({"state": "INACTIVE"}, {"displayName": "G PRO X 2"}, {"capabilities": None}, {"connected": False}):
            self.assertFalse(gb.is_pro_wireless(info() | override))

    def test_exact_usb_path_distinguishes_identical_receivers(self):
        receiver = SimpleNamespace(usb_paths=[PATH_B])
        self.assertEqual(gb.choose_device({"devA": info(), "devB": info("devB", PATH_B)}, receiver, False), "devB")

    def test_model_name_never_overrides_explicit_path_mismatch(self):
        self.assertIsNone(gb.choose_device({"devA": info()}, SimpleNamespace(usb_paths=[PATH_B]), True))

    def test_ambiguous_legacy_devices_are_not_guessed(self):
        receiver = SimpleNamespace(usb_paths=[])
        self.assertIsNone(gb.choose_device({"devA": info(), "devB": info("devB")}, receiver, True))


class MonitorTests(unittest.TestCase):
    def test_state_push_updates_cached_percentage_and_removes_failed_data(self):
        m, ws = monitor(), Mock()
        receiver = SimpleNamespace(instance_id="receiver", usb_paths=[PATH_A])
        m.interests["RECEIVER"] = (receiver, True)
        m.handle({"path": "/devices/list", "payload": {"deviceInfos": [info()]}}, ws)
        for level in (80, 79):
            m.handle({"path": "/battery/state/changed", "payload": payload(percentage=level)}, ws)
            self.assertEqual(m.read(receiver, "Mouse").level, level)
        m.handle({"path": "/battery/devA/state", "result": {"code": "FAILED"}}, ws)
        self.assertIsNone(m.read(receiver, "Mouse"))

    def test_stale_or_disconnected_cache_is_never_current(self):
        m = monitor()
        m.devices["devA"] = info()
        m.bindings["RECEIVER"] = "devA"
        receiver = SimpleNamespace(instance_id="receiver")
        m.batteries["devA"] = (payload(), time.monotonic() - gb.MAX_AGE - 1)
        self.assertIsNone(m.read(receiver, "Mouse"))
        m.batteries["devA"] = (payload(), time.monotonic())
        m.connected = False
        self.assertIsNone(m.read(receiver, "Mouse"))

    def test_device_removal_clears_binding_and_battery(self):
        m, ws = monitor(), Mock()
        receiver = SimpleNamespace(instance_id="receiver", usb_paths=[PATH_A])
        m.interests["RECEIVER"] = (receiver, True)
        m.handle({"path": "/devices/list", "payload": {"deviceInfos": [info()]}}, ws)
        m.handle({"path": "/battery/devA/state", "payload": payload()}, ws)
        m.handle({"path": "/devices/list", "payload": {"deviceInfos": []}}, ws)
        self.assertIsNone(m.read(receiver, "Mouse"))
        self.assertEqual(m.bindings, {})

    def test_mismatched_path_cannot_replace_another_devices_battery(self):
        m = monitor()
        m.devices = {"devA": info(), "devB": info("devB", PATH_B)}
        m.handle({"path": "/battery/devA/state", "payload": payload("devB")}, Mock())
        self.assertEqual(m.batteries, {})


if __name__ == "__main__":
    unittest.main()
