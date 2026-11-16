from contextlib import nullcontext
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import logitech_battery as lb
from device_readers import Reading


class ExchangeDevice:
    def __init__(self, response):
        self.response = response
        self.sent = None
        self.drains = 0
        self.reads = 0

    def drain(self):
        self.drains += 1

    def write_output(self, report):
        self.sent = report

    def read_input(self, **kwargs):
        self.reads += 1
        return self.response(self.sent, self.reads)


def frame(request, payload=b"\x0f\x9b\x00"):
    return request[:4] + payload.ljust(16, b"\0")


class ProtocolTests(unittest.TestCase):
    def test_feature_version_is_not_mistaken_for_its_index(self):
        with patch.object(lb, "exchange", return_value=bytes.fromhex("06 00 02") + bytes(13)):
            self.assertEqual(lb.feature_index(None, 0x1001), 6)

    def test_absent_feature_has_no_index(self):
        with patch.object(lb, "exchange", return_value=bytes(16)):
            self.assertIsNone(lb.feature_index(None, 0x1001))

    def test_queries_do_not_accept_other_clients_replies_or_events(self):
        def response(request, count):
            if count == 1:
                return None
            if count == 2:
                return bytes((0x11, 1, request[2], 0)) + bytes(16)  # unsolicited event
            if count == 3:
                return bytes((0x11, 1, request[2], request[3] ^ 1)) + bytes(16)
            if count == 4:
                return bytes((0x11, 2, request[2], request[3])) + bytes(16)
            return frame(request)
        dev = ExchangeDevice(response)
        self.assertEqual(lb.exchange(dev, 6)[:3], bytes.fromhex("0f 9b 00"))
        self.assertEqual(dev.reads, 5)
        self.assertEqual(dev.drains, 1)
        self.assertEqual(len(dev.sent), 20)

    def test_matching_protocol_error_is_rejected(self):
        dev = ExchangeDevice(lambda req, _: bytes((0x11, 1, 0xFF, req[2], req[3], 7)) + bytes(14))
        with self.assertRaisesRegex(lb.HidppError, "0x07"):
            lb.exchange(dev, 6)

    def test_short_reply_is_rejected(self):
        dev = ExchangeDevice(lambda req, _: req[:4] + b"\0\0")
        with self.assertRaisesRegex(lb.HidppError, "长度"):
            lb.exchange(dev, 6)

    def test_missing_reply_times_out_instead_of_producing_zero(self):
        dev = ExchangeDevice(lambda *_: None)
        with self.assertRaises(TimeoutError):
            lb.exchange(dev, 6, timeout_ms=2)

    def test_lost_reply_is_retried_once_and_late_old_transaction_is_ignored(self):
        old_requests = []
        def response(request, count):
            if dev.drains == 1:
                if not old_requests:
                    old_requests.append(request)
                return None
            if not getattr(dev, "late_seen", False):
                dev.late_seen = True
                return frame(old_requests[0], bytes.fromhex("0e 00 00"))
            return frame(request, bytes.fromhex("0f 95 00"))
        dev = ExchangeDevice(response)
        self.assertEqual(lb.exchange(dev, 6, timeout_ms=5)[:3], bytes.fromhex("0f 95 00"))
        self.assertEqual(dev.drains, 2)
        self.assertNotEqual(dev.sent[3], old_requests[0][3])

    def test_name_is_read_across_multiple_report_chunks(self):
        name = b"G Pro Wireless Gaming Mouse"
        with patch.object(lb, "feature_index", return_value=3), \
                patch.object(lb, "exchange", side_effect=[bytes((len(name),)) + bytes(15), name[:16], name[16:]]) as exchange:
            self.assertEqual(lb.device_name(None), name.decode())
        self.assertEqual(exchange.call_args.args[3], b"\x10")


class DecodingTests(unittest.TestCase):
    def test_voltage_is_big_endian_and_current_sample_is_about_80_percent(self):
        mv, status = lb.decode_voltage(bytes.fromhex("0f 9b 00"))
        self.assertEqual((mv, status), (3995, "电池供电"))
        self.assertEqual(lb.estimate_pro_wireless_level(mv), 80)

    def test_charging_full_and_not_charging_flags_are_distinct(self):
        for flags, status in ((0x80, "充电中"), (0x81, "已充满"), (0x82, "已接电，未充电")):
            with self.subTest(flags=flags):
                self.assertEqual(lb.decode_voltage(bytes((0x0F, 0x9B, flags)))[1], status)

    def test_invalid_charge_flags_do_not_fabricate_charge_state(self):
        with self.assertRaises(lb.HidppError):
            lb.decode_voltage(bytes.fromhex("0f 9b 87"))

    def test_empty_or_swapped_voltage_data_is_rejected(self):
        for payload in (b"", b"\x0f", b"\0\0\0", b"\xff\xff\0", bytes.fromhex("9b 0f 00")):
            with self.subTest(payload=payload), self.assertRaises(lb.HidppError):
                lb.decode_voltage(payload)

    def test_voltage_curve_is_bounded_and_monotonic(self):
        values = [lb.estimate_pro_wireless_level(mv) for mv in range(3000, 4401)]
        self.assertEqual(values, sorted(values))
        self.assertEqual((values[0], values[-1]), (0, 100))
        self.assertEqual(lb.estimate_pro_wireless_level(3978), 78)
        self.assertEqual(lb.estimate_pro_wireless_level(3983), 79)
        self.assertEqual(lb.estimate_pro_wireless_level(3989), 80)

    def test_numeric_label_omits_prefix_without_changing_estimation_metadata(self):
        reading = Reading("GPW", "", level=80, estimated=True)
        self.assertEqual(reading.display_level, "80%")
        self.assertTrue(reading.estimated)
        self.assertEqual(Reading("F87", "", level=80).display_level, "80%")


class AdapterTests(unittest.TestCase):
    def read(self, *, payload=bytes.fromhex("0f 9b 00"), name="G Pro Wireless Gaming Mouse", paths=None, write=True):
        selected_path = "selected physical receiver"
        receiver = SimpleNamespace(hid_paths=[selected_path] if paths is None else paths)
        unrelated = SimpleNamespace(path="other identical receiver", vid=0x046D, pid=0xC539,
                                    usage_page=0xFF00, usage=2, input_len=20, output_len=20)
        selected = SimpleNamespace(**(vars(unrelated) | {"path": selected_path}))
        dev = Mock(write_access=write)
        dev.__enter__ = Mock(return_value=dev)
        dev.__exit__ = Mock(return_value=False)
        with patch.object(lb.hw, "enumerate_hid", return_value=[unrelated, selected]), \
                patch.object(lb.hw, "query_lock", return_value=nullcontext()), \
                patch.object(lb.hw, "HidDevice", return_value=dev) as open_device, \
                patch.object(lb, "feature_index", return_value=6), \
                patch.object(lb, "device_name", return_value=name), \
                patch.object(lb, "exchange", side_effect=payload if isinstance(payload, Exception) else None,
                             return_value=payload):
            # Adapter behavior is tested independently of the v66 cross-process
            # observation cache, which must not retain another mock's result.
            result = lb.read_pro_wireless.__wrapped__(receiver, "Logitech PRO WIRELESS")
        return result, open_device

    def test_selected_physical_receiver_is_read_and_labeled_approximate(self):
        result, opened = self.read()
        self.assertTrue(result.ok)
        self.assertEqual((result.level, result.voltage_mv, result.estimated), (80, 3995, True))
        self.assertEqual(result.status, "电池供电")
        self.assertEqual(result.source, "HID++ 2.0")
        self.assertIsNone(result.remaining_hours)
        opened.assert_called_once_with("selected physical receiver", write=True)

    def test_unrelated_same_model_is_never_used_as_fallback(self):
        result, opened = self.read(paths=["unplugged selected receiver"])
        self.assertFalse(result.ok)
        self.assertIsNone(result.level)
        opened.assert_not_called()

    def test_unknown_model_does_not_use_pro_wireless_calibration(self):
        result, _ = self.read(name="G Pro X Superlight 2")
        self.assertFalse(result.ok)
        self.assertIsNone(result.level)
        self.assertIn("未校准", result.error)

    def test_timeout_clears_battery_instead_of_showing_a_stale_value(self):
        result, _ = self.read(payload=TimeoutError("鼠标未响应"))
        self.assertFalse(result.ok)
        self.assertIsNone(result.level)
        self.assertEqual(result.status, "读取失败")

    def test_read_only_handle_is_reported_as_access_failure(self):
        result, _ = self.read(write=False)
        self.assertFalse(result.ok)
        self.assertIn("无法访问", result.error)


if __name__ == "__main__":
    unittest.main()
