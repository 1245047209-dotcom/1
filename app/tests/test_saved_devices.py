import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from receiver_scan import Receiver
from saved_devices import DEFAULT_DEVICE_ID, DeviceStore, DeviceStoreError, read_saved_device


def receiver(serial="A", hardware="046D:C539"):
    return Receiver(f"USB\\VID_{hardware[:4]}&PID_{hardware[-4:]}\\{serial}",
                    "PRO WIRELESS", "Logitech", hardware, "鼠标 / 键盘",
                    "系统名称", True)


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "devices.json"
        self.store = DeviceStore(self.path)

    def test_membership_and_selection_survive_reopening(self):
        device_id, created = self.store.add(receiver())
        self.assertTrue(created)
        devices, selected = DeviceStore(self.path).load()
        self.assertEqual(selected, device_id)
        self.assertEqual(devices[0].model, "PRO WIRELESS")

    def test_duplicate_add_selects_existing_without_duplicating(self):
        device_id, _ = self.store.add(receiver())
        same = receiver()
        same.instance_id = same.instance_id.lower()
        self.assertEqual(self.store.add(same), (device_id, False))
        self.assertEqual(len(self.store.load()[0]), 1)

    def test_identical_models_with_distinct_instances_remain_distinct(self):
        a, _ = self.store.add(receiver("A"))
        b, _ = self.store.add(receiver("B"))
        self.assertNotEqual(a, b)
        self.assertEqual(len(self.store.load()[0]), 2)

    def test_stale_store_object_does_not_erase_new_devices(self):
        second = DeviceStore(self.path)
        second.load()
        self.store.add(receiver("A"))
        second.add(receiver("B"))
        self.assertEqual(len(self.store.load()[0]), 2)

    def test_default_aula_is_already_a_member(self):
        aula = receiver(hardware="3554:FA09")
        self.assertEqual(self.store.id_for(aula), DEFAULT_DEVICE_ID)
        self.assertEqual(self.store.add(aula), (DEFAULT_DEVICE_ID, False))
        self.assertEqual(self.store.load(), ([], DEFAULT_DEVICE_ID))

    def test_removing_selected_device_returns_to_default(self):
        device_id, _ = self.store.add(receiver())
        self.store.remove(device_id)
        self.assertEqual(self.store.load(), ([], DEFAULT_DEVICE_ID))

    def test_removing_another_device_keeps_selection(self):
        a, _ = self.store.add(receiver("A"))
        b, _ = self.store.add(receiver("B"))
        self.store.remove(a)
        self.assertEqual(self.store.load()[1], b)

    def test_default_can_be_removed_persistently_and_explicitly_added_again(self):
        self.store.remove(DEFAULT_DEVICE_ID)
        reopened = DeviceStore(self.path)
        self.assertFalse(reopened.default_visible())
        self.assertEqual(reopened.load(), ([], None))
        aula = receiver(hardware="3554:FA09")
        self.assertIsNone(reopened.id_for(aula))
        self.assertEqual(reopened.add(aula), (DEFAULT_DEVICE_ID, True))
        self.assertTrue(reopened.default_visible())
        self.assertEqual(reopened.load(), ([], DEFAULT_DEVICE_ID))

    def test_selection_persists_and_unknown_selection_does_not_write(self):
        self.store.add(receiver())
        self.store.select(DEFAULT_DEVICE_ID)
        old = self.path.read_bytes()
        with self.assertRaises(DeviceStoreError):
            self.store.select("missing")
        self.assertEqual(self.path.read_bytes(), old)
        self.assertEqual(self.store.load()[1], DEFAULT_DEVICE_ID)

    def test_corrupt_file_is_reported_and_not_overwritten(self):
        self.path.write_text("broken json", encoding="utf-8")
        with self.assertRaises(DeviceStoreError):
            self.store.add(receiver())
        self.assertEqual(self.path.read_text(), "broken json")

    def test_failed_atomic_write_preserves_existing_file(self):
        self.store.add(receiver())
        old = self.path.read_bytes()
        with patch("saved_devices.os.replace", side_effect=OSError("disk failure")):
            with self.assertRaises(OSError):
                self.store.add(receiver("B"))
        self.assertEqual(self.path.read_bytes(), old)
        self.assertEqual(list(self.path.parent.glob("*.tmp")), [])

    def test_unknown_schema_is_not_silently_erased(self):
        self.path.write_text(json.dumps({"version": 2, "devices": []}), encoding="utf-8")
        with self.assertRaises(DeviceStoreError):
            self.store.load()

    def saved(self):
        self.store.add(receiver())
        return self.store.load()[0][0]

    def test_connected_unadapted_device_has_no_fabricated_battery(self):
        unsupported = receiver(hardware="1234:5678")
        self.store.add(unsupported)
        saved = self.store.load()[0][0]
        with patch("receiver_scan.scan_receivers", return_value=[unsupported]), \
                patch("battery_runtime.record_sample") as history:
            reading = read_saved_device(saved)
        self.assertTrue(reading.connected)
        self.assertFalse(reading.ok)
        self.assertIsNone(reading.level)
        self.assertEqual(reading.status, "待适配电量")
        history.assert_not_called()

    def test_logitech_receiver_uses_the_adapter_without_keyboard_history(self):
        from device_readers import Reading
        saved = self.saved()
        current = receiver()
        expected = Reading(saved.label, "接收器已连接", level=80, estimated=True, connected=True)
        with patch("receiver_scan.scan_receivers", return_value=[current]), \
                patch("ghub_battery.read_from_ghub", side_effect=AssertionError("G HUB must not be queried")) as ghub, \
                patch("logitech_battery.read_pro_wireless", return_value=expected) as adapter, \
                patch("battery_runtime.record_sample") as history:
            self.assertIs(read_saved_device(saved), expected)
        adapter.assert_called_once_with(current, saved.label)
        ghub.assert_not_called()
        history.assert_not_called()

    def test_direct_protocol_is_used_even_when_ghub_has_a_different_percentage(self):
        from device_readers import Reading
        saved = self.saved()
        current = receiver()
        expected = Reading(saved.label, "接收器已连接", level=75, connected=True, estimated=True, source="HID++ 2.0")
        with patch("receiver_scan.scan_receivers", return_value=[current]), \
                patch("ghub_battery.read_from_ghub", return_value=Reading(saved.label, "", level=80)) as ghub, \
                patch("logitech_battery.read_pro_wireless", return_value=expected) as direct:
            self.assertIs(read_saved_device(saved), expected)
        direct.assert_called_once_with(current, saved.label)
        ghub.assert_not_called()

    def test_protocol_failure_does_not_fall_back_to_ghub_or_old_battery(self):
        from device_readers import Reading
        saved = self.saved()
        current = receiver()
        failed = Reading(saved.label, "接收器已连接", connected=True, error="鼠标未响应", source="HID++ 2.0")
        with patch("receiver_scan.scan_receivers", return_value=[current]), \
                patch("ghub_battery.read_from_ghub") as ghub, \
                patch("logitech_battery.read_pro_wireless", return_value=failed):
            result = read_saved_device(saved)
        self.assertIs(result, failed)
        self.assertIsNone(result.level)
        ghub.assert_not_called()

    def test_unplugged_device_stays_saved_and_displays_disconnected(self):
        saved = self.saved()
        with patch("receiver_scan.scan_receivers", return_value=[]):
            reading = read_saved_device(saved)
        self.assertFalse(reading.connected)
        self.assertEqual(len(self.store.load()[0]), 1)

    def test_scan_failure_is_not_reported_as_a_known_disconnection(self):
        saved = self.saved()
        with patch("receiver_scan.scan_receivers", side_effect=OSError("enumeration failed")):
            reading = read_saved_device(saved)
        self.assertIsNone(reading.connected)
        self.assertIn("设备检查失败", reading.error)


class SwitchingTests(unittest.TestCase):
    def test_late_previous_device_result_does_not_replace_selection(self):
        from my_devices import NexusPanel
        from device_readers import Reading
        panel = NexusPanel.__new__(NexusPanel)
        panel.refresh_generation = 2
        panel.reading = Reading("new device", "正在检查")
        panel.finish_refresh(Reading("old device", "", level=99), 1)
        self.assertEqual(panel.reading.title, "new device")


if __name__ == "__main__":
    unittest.main()
