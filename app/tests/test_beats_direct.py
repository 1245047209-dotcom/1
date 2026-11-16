import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

import beats_direct as direct

REAL_STATE = bytes.fromhex("0002013f0201040100040108010004010200000003")


def response(sequence, opcode, payload):
    data = b"\xff\xb2" + (11+len(payload)).to_bytes(2, "little") + bytes((sequence,)) + opcode.to_bytes(2, "little") + payload
    return data + direct.crc32(data).to_bytes(4, "little")


class ProtocolTests(unittest.TestCase):
    def test_queries_match_real_hardware_frames_and_never_allow_setting_opcodes(self):
        self.assertEqual(direct.query_frame(1, direct.PRODUCT_GET).hex(), "ffb20b00013a01b29e9a3b")
        self.assertEqual(direct.query_frame(1, direct.BATTERY_GET).hex(), "ffb20b000130048428e506")
        for opcode in (0x0008, 0x0154, 0x015a, 0x0434):
            with self.assertRaises(direct.ProtocolError):
                direct.query_frame(1, opcode)

    def test_real_reply_split_across_reads_keeps_component_identity(self):
        packet = bytes.fromhex("ffb20e00013b010011206b77dd53")
        parser = direct.FrameBuffer()
        self.assertEqual(parser.feed(packet[:3]), [])
        self.assertEqual(parser.feed(packet[3:8]), [])
        self.assertEqual(parser.feed(packet[8:]), [(1, 0x013b, bytes.fromhex("001120"))])

    def test_coalesced_replies_bad_crc_and_oversized_data(self):
        packet = response(2, direct.BATTERY_GET+1, REAL_STATE)
        self.assertEqual(len(direct.FrameBuffer().feed(packet+packet)), 2)
        damaged = bytearray(packet)
        damaged[10] ^= 1
        with self.assertRaises(direct.ProtocolError):
            direct.FrameBuffer().feed(damaged)
        with self.assertRaises(direct.ProtocolError):
            direct.FrameBuffer().feed(bytes(8193))

    def test_disconnected_case_placeholder_is_unknown_instead_of_zero_or_old_44(self):
        reading = direct.battery_reading("Beats", REAL_STATE)
        self.assertEqual(reading.components["right"], {"level": 63, "charging": False})
        self.assertEqual(reading.components["left"], {"level": None, "charging": None})
        self.assertEqual(reading.components["case"], {"level": None, "charging": None})

    def test_independent_case_level_and_charge_state_follow_each_new_reply(self):
        fixture = bytearray(REAL_STATE)
        fixture[13:15] = bytes((59, 1))
        during = direct.battery_reading("Beats", fixture)
        self.assertEqual(during.components["case"], {"level": 59, "charging": True})
        fixture[13:15] = bytes((60, 2))
        after = direct.battery_reading("Beats", fixture)
        self.assertEqual(after.components["case"], {"level": 60, "charging": False})
        self.assertFalse(after.components["right"]["charging"])

    def test_real_docked_reply_matches_phone_case_and_accepts_charge_complete_left_ear(self):
        reading = direct.battery_reading("Beats", bytes.fromhex("000201580101040164000108016301010102000101"))
        self.assertEqual(reading.components["right"], {"level": 88, "charging": True})
        self.assertEqual(reading.components["left"], {"level": 100, "charging": True})
        self.assertEqual(reading.components["case"], {"level": 99, "charging": True})

    def test_error_responses_and_malformed_component_packets_are_rejected(self):
        for bad in (b"\x03", b"\x00", REAL_STATE[:-1], REAL_STATE[:6]+REAL_STATE[1:6]+REAL_STATE[11:]):
            with self.subTest(bad=bad), self.assertRaises(direct.ProtocolError):
                direct.battery_reading("Beats", bad)

    def test_query_ignores_a_previous_requests_reply(self):
        channel = Mock()
        channel.recv.side_effect = [response(99, direct.PRODUCT_GET+1, b"old"),
                                    response(1, direct.PRODUCT_GET+1, b"\0\x11\x20")]
        result = direct.Connection(channel, threading.Event()).query(direct.PRODUCT_GET)
        self.assertEqual(result, b"\0\x11\x20")
        self.assertEqual(channel.sendall.call_count, 1)


class CacheTests(unittest.TestCase):
    def test_expired_data_is_cleared_and_refresh_wakes_polling(self):
        now = [1.0]
        worker = direct.BatteryWorker(SimpleNamespace(label="Beats"), clock=lambda: now[0])
        worker.publish(direct.battery_reading("Beats", REAL_STATE))
        self.assertEqual(worker.cached().level, 63)
        copy = worker.cached()
        copy.components["right"]["level"] = 99
        self.assertEqual(worker.cached().level, 63)
        now[0] = 14
        self.assertIsNone(worker.cached().level)
        self.assertTrue(all(part["level"] is None for part in worker.cached().components.values()))
        worker.request_refresh()
        self.assertTrue(worker.refresh_event.is_set())
        worker.stop_event.set()
        self.assertIsNone(worker.cached())


if __name__ == "__main__":
    unittest.main()
