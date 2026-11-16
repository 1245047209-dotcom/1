"""The panel must not replace direct hardware readings with driver IPC data."""
import unittest
import queue
from types import SimpleNamespace
from unittest.mock import Mock, patch

from device_readers import Reading
from my_devices import NexusPanel


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


if __name__ == "__main__":
    unittest.main()
