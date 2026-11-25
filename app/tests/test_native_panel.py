"""The panel must not replace direct hardware readings with driver IPC data."""
import unittest
import queue
from types import SimpleNamespace
from unittest.mock import Mock, patch

from battery import _parse_aula_battery
from device_readers import Reading, _read_f87_interface, stamp_observation
from my_devices import NexusPanel
from native_panel import NativePanel, make_snapshot
from reading_ui import ReadingTimes
from saved_devices import DEFAULT_DEVICE_ID, SavedDevice


class IndependentPanelTests(unittest.TestCase):
    def test_ready_reading_is_displayed_without_an_artificial_feedback_delay(self):
        panel = NexusPanel.__new__(NexusPanel)
        panel.results, panel.root, panel.finish_refresh = queue.Queue(), Mock(), Mock()
        panel.refresh_generation = 2
        reading = Reading("Keyboard", "", level=0)
        panel.results.put((2, reading))
        panel.pump()
        panel.finish_refresh.assert_called_once_with(reading, 2)
        panel.root.after.assert_called_once_with(100, panel.pump)

    def test_late_result_from_an_old_refresh_is_ignored(self):
        panel = NexusPanel.__new__(NexusPanel)
        panel.results, panel.root, panel.finish_refresh = queue.Queue(), Mock(), Mock()
        panel.refresh_generation = 2
        panel.results.put((1, Reading("Keyboard", "", level=99)))
        panel.pump()
        panel.finish_refresh.assert_not_called()

    def test_live_mouse_updates_leave_other_device_values_intact(self):
        panel = NexusPanel.__new__(NexusPanel)
        mouse, headphones = SimpleNamespace(id="mouse"), SimpleNamespace(id="audio")
        panel.saved_devices = [mouse, headphones]
        old = Reading("Mouse", "", level=80)
        latest = Reading("Mouse", "", level=78, estimated=True, source="HID++ 实时")
        audio = Reading("Headphones", "", level=20)
        panel.readings = {"mouse": old, "audio": audio}
        panel.selected_device_id, panel.reading = "mouse", old
        panel.draw, panel.mouse_monitor = Mock(), Mock()
        panel.mouse_monitor.cached_reading.side_effect = [latest, None]
        panel.sync_mouse_updates()
        self.assertIs(panel.reading, latest)
        self.assertIs(panel.readings["audio"], audio)
        panel.draw.assert_called_once()

    def test_timer_does_not_query_or_start_the_old_driver_monitor(self):
        panel = NexusPanel.__new__(NexusPanel)
        panel.root = Mock()
        panel.refresh_readings = Mock()
        panel.sync_mouse_updates = Mock()
        with patch("ghub_battery.cached_reading") as cached, \
                patch("ghub_battery.read_from_ghub") as start:
            panel.runtime_tick()
        cached.assert_not_called()
        start.assert_not_called()
        panel.refresh_readings.assert_called_once()

    def test_closing_the_panel_does_not_load_the_old_driver_monitor(self):
        panel = NexusPanel.__new__(NexusPanel)
        panel.root, panel.receiver_search = Mock(), None
        panel.mouse_monitor = Mock()
        for name in ("after_id", "refresh_result_after_id", "refresh_feedback_after_id",
                     "startup_after_id", "pump_after_id", "runtime_after_id"):
            setattr(panel, name, None)
        with patch("ghub_battery.stop_monitor") as monitor:
            panel.close()
        monitor.assert_not_called()
        panel.root.destroy.assert_called_once()

    def test_native_voltage_does_not_inherit_a_driver_remaining_time(self):
        reading = Reading("Mouse", "", level=80, remaining_hours=51.18, source="HID++ 2.0")
        self.assertEqual(NexusPanel.device_runtime_text(reading), "暂不支持估算")

    def test_native_charging_is_shown_without_a_fabricated_remaining_time(self):
        reading = Reading("Mouse", "", level=80, status="充电中", source="HID++ 2.0")
        self.assertEqual(NexusPanel.device_runtime_text(reading), "充电中")


class NativeBridgeTests(unittest.TestCase):
    @staticmethod
    def f87_report(level, state):
        packet = bytearray(20)
        packet[:7] = bytes((0x13, 0x4A, 1, 0, 2, level, state))
        packet[19] = sum(packet[:19]) & 0xFF
        return bytes(packet)

    def test_f87_reports_both_the_percentage_and_charging(self):
        report = self.f87_report(8, 0x10)
        interface = SimpleNamespace(vid_pid="3554:FA09", product="AULA F87 Pro")
        with patch("aula_protocols.identify_interface", return_value={"key": "f87pro"}), \
                patch("device_readers.bt.strategy_aula_f87", return_value=_parse_aula_battery(report)):
            reading = stamp_observation(_read_f87_interface.__wrapped__(interface))
        panel = NexusPanel.__new__(NexusPanel)
        panel.saved_devices = []
        panel.readings = {DEFAULT_DEVICE_ID: reading}
        panel.reading_times = ReadingTimes()
        panel.appearance, panel.glass_enabled, panel.busy = SimpleNamespace(dark=False), True, False

        snapshot = make_snapshot(panel)
        ring, = snapshot["rings"]
        device, = snapshot["devices"]
        self.assertEqual(ring["level"], 8)
        self.assertTrue(ring["charging"])
        self.assertEqual(device["details"]["level"], "8%")
        self.assertEqual(device["power"], "正在充电")
        self.assertIn({"label": "续航", "value": "充电中"}, device["details"]["rows"])
        self.assertFalse(snapshot["summaryLow"])

    def test_f87_charging_placeholder_does_not_hide_a_valid_discharging_level(self):
        charging = _parse_aula_battery(self.f87_report(100, 0x10))
        self.assertTrue(charging.ok)
        self.assertEqual(charging.status, "charging")
        self.assertEqual(charging.raw_level, 100)
        self.assertIsNone(charging.level)
        for level in (0, 100):
            with self.subTest(level=level):
                result = _parse_aula_battery(self.f87_report(level, 0x01))
                self.assertEqual(result.level, level)

    def test_action_failure_does_not_stop_other_actions_or_polling(self):
        error = RuntimeError("dialog unavailable")
        panel = SimpleNamespace(root=Mock(), open_settings=Mock(side_effect=error), refresh=Mock())
        bridge = NativePanel.__new__(NativePanel)
        bridge.panel = panel
        bridge._incoming = queue.Queue()
        bridge._incoming.put({"action": "settings"})
        bridge._incoming.put({"action": "refresh"})
        bridge._send_snapshot = Mock()

        with patch("native_panel.make_snapshot", return_value={"type": "snapshot"}):
            bridge._tick()

        panel.root.report_callback_exception.assert_called_once_with(RuntimeError, error, error.__traceback__)
        panel.refresh.assert_called_once_with()
        bridge._send_snapshot.assert_called_once_with({"type": "snapshot"})
        panel.root.after.assert_called_once_with(50, bridge._tick)

    def test_snapshot_reuses_overview_data_in_device_details(self):
        panel = NexusPanel.__new__(NexusPanel)
        panel.saved_devices = [SavedDevice("mouse", "mouse", "PRO WIRELESS", "Logitech", "046D:C539", "鼠标")]
        panel.readings = {DEFAULT_DEVICE_ID: Reading("F87 Pro", "", level=58, connected=True),
                          "mouse": Reading("PRO WIRELESS", "", level=18, connected=True)}
        panel.reading_times = ReadingTimes()
        panel.appearance, panel.glass_enabled, panel.busy = SimpleNamespace(dark=False), True, False
        panel.device_entries = Mock(wraps=panel.device_entries)
        panel.reading_styles = Mock(wraps=panel.reading_styles)

        with patch("device_sheet._runtime_text", return_value="约 5 天"):
            snapshot = make_snapshot(panel)

        panel.device_entries.assert_called_once_with()
        self.assertEqual(panel.reading_styles.call_count, 2)
        keyboard, mouse = snapshot["devices"]
        self.assertEqual(keyboard["details"]["level"], "58%")
        self.assertEqual(keyboard["connection"], "2.4G 无线")
        self.assertIn({"label": "续航", "value": "约 5 天"}, keyboard["details"]["rows"])
        self.assertTrue(mouse["low"])
        self.assertEqual(snapshot["summary"], "1 台需要充电")


if __name__ == "__main__":
    unittest.main()
