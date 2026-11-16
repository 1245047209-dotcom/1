from contextlib import nullcontext
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from device_readers import Reading
import logitech_live as live


def device(key="mouse", instance="USB\\RECEIVER_A", transport="usb", hardware="046D:C539"):
    return SimpleNamespace(id=key, instance_id=instance, label="PRO WIRELESS",
                           transport=transport, hardware_id=hardware)


def event(payload=bytes.fromhex("0f 8a 00")):
    return bytes((0x11, 1, 6, 0)) + payload.ljust(16, b"\0")


class EventTests(unittest.TestCase):
    def test_battery_broadcast_carries_voltage_and_charge_state(self):
        self.assertEqual(live.decode_battery_event(event(), 6), (3978, "电池供电"))
        self.assertEqual(live.decode_battery_event(event(bytes.fromhex("0f 8a 80")), 6), (3978, "充电中"))

    def test_other_clients_devices_features_and_short_reports_are_ignored(self):
        valid = event()
        for wrong in (bytes((0x11, 1, 6, 8)) + valid[4:],
                      bytes((0x11, 2, 6, 0)) + valid[4:],
                      bytes((0x11, 1, 7, 0)) + valid[4:], valid[:7], b""):
            with self.subTest(wrong=wrong):
                self.assertIsNone(live.decode_battery_event(wrong, 6))

    def test_invalid_battery_payload_is_not_reported_as_zero(self):
        with self.assertRaises(live.lb.HidppError):
            live.decode_battery_event(event(bytes(3)), 6)


class WorkerTests(unittest.TestCase):
    def test_old_data_becomes_unknown_and_stopping_hides_the_snapshot(self):
        now = [10.0]
        worker = live.BatteryWorker(device(), clock=lambda: now[0])
        worker.publish(Reading("Mouse", "", level=78))
        copy = worker.cached()
        self.assertEqual(copy.level, 78)
        copy.level = 1
        self.assertEqual(worker.cached().level, 78)
        now[0] += live.MAX_AGE + 1
        self.assertFalse(worker.cached().ok)
        self.assertIsNone(worker.cached().level)
        worker.stop_event.set()
        self.assertIsNone(worker.cached())

    def test_missing_selected_receiver_never_uses_a_same_model_receiver(self):
        worker = live.BatteryWorker(device())
        other = SimpleNamespace(instance_id="USB\\RECEIVER_B", hardware_id="046D:C539")
        with patch.object(live, "scan_receivers", return_value=[other]), \
                patch.object(live.hw, "HidDevice") as opened:
            worker.connected_session()
        self.assertFalse(worker.cached().connected)
        self.assertIsNone(worker.cached().level)
        opened.assert_not_called()

    def test_event_updates_the_selected_mouse_between_polls(self):
        worker = live.BatteryWorker(device(), clock=lambda: 0)
        receiver = SimpleNamespace(instance_id=worker.device.instance_id, hardware_id="046D:C539", hid_paths=["selected"])
        interface = SimpleNamespace(path="selected", vid=0x046D, pid=0xC539,
                                    usage_page=0xFF00, usage=2, input_len=20, output_len=20)
        unrelated = SimpleNamespace(**(vars(interface) | {"path": "unrelated"}))
        dev = Mock(write_access=True)
        dev.__enter__, dev.__exit__ = Mock(return_value=dev), Mock(return_value=False)
        def receive(**kwargs):
            if not getattr(receive, "sent", False):
                receive.sent = True
                return event()
            worker.stop_event.set()
            return None
        dev.read_input.side_effect = receive
        with patch.object(live, "scan_receivers", return_value=[receiver]), \
                patch.object(live.hw, "enumerate_hid", return_value=[unrelated, interface]), \
                patch.object(live.hw, "HidDevice", return_value=dev) as opened, \
                patch.object(live.hw, "query_lock", side_effect=lambda *_: nullcontext()), \
                patch.object(live.lb, "device_name", return_value="G Pro Wireless Gaming Mouse"), \
                patch.object(live.lb, "feature_index", return_value=6), \
                patch.object(live.lb, "exchange", return_value=bytes.fromhex("0f 95 00")) as queried:
            worker.connected_session()
        self.assertEqual(worker.snapshot[1].level, 78)
        self.assertTrue(worker.snapshot[1].estimated)
        self.assertEqual(worker.snapshot[1].source, "HID++ 实时")
        queried.assert_called_once_with(dev, 6)
        opened.assert_called_once_with("selected", write=True)

    def test_an_unvalidated_model_cannot_use_the_curve(self):
        with patch.object(live.lb, "device_name", return_value="G Pro X Superlight 2"), \
                self.assertRaises(live.lb.HidppError):
            live.BatteryWorker.check_model(Mock())


class LifecycleTests(unittest.TestCase):
    def test_only_saved_usb_receivers_are_monitored_and_removal_stops_worker(self):
        factory = Mock(side_effect=lambda target: Mock(device=target))
        monitor = live.MouseMonitor(worker_factory=factory)
        mouse = device()
        monitor.set_devices([mouse, device("bt", transport="bluetooth"), device("other", hardware="1234:5678")])
        worker = monitor.workers["mouse"]
        monitor.set_devices([mouse])
        factory.assert_called_once_with(mouse)
        worker.start.assert_called_once()
        monitor.set_devices([])
        worker.stop_event.set.assert_called_once()
        self.assertIsNone(monitor.cached_reading(mouse))
        monitor.close()
        monitor.set_devices([mouse])
        self.assertEqual(monitor.workers, {})

    def test_same_saved_id_with_a_different_physical_instance_cannot_reuse_cache(self):
        monitor = live.MouseMonitor(worker_factory=lambda target: Mock(device=target))
        monitor.set_devices([device()])
        old = monitor.workers["mouse"]
        replacement = device(instance="USB\\RECEIVER_B")
        self.assertIsNone(monitor.cached_reading(replacement))
        monitor.set_devices([replacement])
        old.stop_event.set.assert_called_once()
        monitor.close()


if __name__ == "__main__":
    unittest.main()
