import tempfile
import unittest
import queue
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from native_panel import NativePanel, make_snapshot
from reading_ui import ReadingTimes
from receiver_scan import Receiver
from saved_devices import DEFAULT_DEVICE_ID, DeviceStore


class NativeSettingsBridgeTests(unittest.TestCase):
    def make_bridge(self):
        panel = SimpleNamespace(
            root=Mock(),
            appearance=SimpleNamespace(mode="system", font_choice="system", glass_level=0.48, set_mode=Mock(), set_font=Mock()),
            interval=60,
            auto=True,
            glass_enabled=True,
            _gpu_animation=True,
            updates=None,
            low_battery_notifier=Mock(preview=Mock(return_value=True)),
            schedule=Mock(),
            set_gpu_enabled=Mock(),
            draw=Mock(),
            close=Mock(),
            reorder_devices=Mock(),
        )
        bridge = NativePanel.__new__(NativePanel)
        bridge.panel = panel
        bridge._closed = False
        bridge._hidden = False
        bridge._incoming = queue.Queue()
        bridge._condition = __import__("threading").Condition()
        bridge._commands = __import__("collections").deque()
        bridge._pending_snapshot = None
        bridge._last_snapshot = None
        bridge._next_snapshot = 0.0
        return bridge, panel

    def test_query_settings_enqueues_current_preferences(self):
        bridge, _panel = self.make_bridge()
        with patch("tray_common.load_config", return_value={"low_battery_alert": True, "gpu_animation": False}), \
                patch("tray_common.is_autostart_enabled", return_value=True), \
                patch("update_client.current_version", return_value="v74.0"), \
                patch("update_client.load_state", return_value={"auto_check": True}):
            bridge._dispatch({"action": "querySettings"})

        payload = bridge._commands.popleft()
        self.assertEqual(payload["type"], "settings")
        self.assertEqual(payload["themeMode"], "system")
        self.assertTrue(payload["glassBackdrop"])
        self.assertAlmostEqual(payload["glassLevel"], 0.48)
        self.assertEqual(payload["interval"], 60)
        self.assertFalse(payload["gpuAnimation"])
        self.assertEqual(payload["version"], "v74.0")
        self.assertTrue(payload["updateAutoCheck"])

    def test_save_settings_persists_config_and_notifies_tray(self):
        bridge, panel = self.make_bridge()
        with patch("tray_common.is_autostart_enabled", return_value=True), \
                patch("tray_common.save_config") as save_cfg, \
                patch("tray_common.post_to_tray") as post_tray:
            bridge._dispatch({
                "action": "saveSettings",
                "interval": 120,
                "autoRefresh": False,
                "autostart": True,
                "lowBatteryAlert": False,
                "gpuAnimation": True,
                "themeMode": "dark",
                "glassBackdrop": False,
                "glassLevel": 0.76,
                "uiFont": "alibaba-medium",
            })

        save_cfg.assert_called_once()
        saved = save_cfg.call_args.args[0]
        self.assertEqual(saved["interval"], 120)
        self.assertEqual(saved["theme"], "dark")
        self.assertFalse(saved["glass_backdrop"])
        self.assertAlmostEqual(saved["glass_level"], 0.76)
        self.assertAlmostEqual(panel.appearance.glass_level, 0.76)
        panel.appearance.set_mode.assert_called_once_with("dark")
        panel.appearance.set_font.assert_called_once_with("alibaba-medium")
        post_tray.assert_called_once()
        reply = bridge._commands.popleft()
        self.assertEqual(reply, {"type": "settingsSaved", "ok": True})

    def test_save_settings_clamps_out_of_range_glass_level(self):
        bridge, panel = self.make_bridge()
        with patch("tray_common.is_autostart_enabled", return_value=True), \
                patch("tray_common.save_config") as save_cfg, \
                patch("tray_common.post_to_tray"):
            bridge._dispatch({"action": "saveSettings", "glassLevel": 1.85})

        saved = save_cfg.call_args.args[0]
        self.assertEqual(saved["glass_level"], 1.0)
        self.assertEqual(panel.appearance.glass_level, 1.0)

    def test_tool_actions_return_inline_feedback(self):
        bridge, panel = self.make_bridge()
        bridge._dispatch({"action": "testAlert"})
        panel.low_battery_notifier.preview.assert_called_once_with()
        self.assertEqual(bridge._commands.popleft()["type"], "toolFeedback")

    def test_reorder_action_forwards_device_ids_to_panel(self):
        bridge, panel = self.make_bridge()
        bridge._dispatch({"action": "reorder", "ids": ["mouse-1", DEFAULT_DEVICE_ID]})
        panel.reorder_devices.assert_called_once_with(["mouse-1", DEFAULT_DEVICE_ID])

    def test_device_store_reorder_persists_custom_order_including_default_keyboard(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = DeviceStore(Path(tmp) / "devices.json")
            a, _ = store.add(Receiver("USB\\VID_046D&PID_C539\\A", "PRO WIRELESS", "Logitech", "046D:C539", "鼠标", "sys", True))
            b, _ = store.add(Receiver("USB\\VID_046D&PID_C53F\\B", "G304", "Logitech", "046D:C53F", "鼠标", "sys", True))
            store.reorder([b, DEFAULT_DEVICE_ID, a])
            reopened = DeviceStore(Path(tmp) / "devices.json")
            devices, _ = reopened.load()
            self.assertEqual([d.id for d in devices], [b, a])
            self.assertEqual(reopened.order(), [b, DEFAULT_DEVICE_ID, a])

    def test_native_search_session_scans_and_formats_aula_variants(self):
        bridge, panel = self.make_bridge()
        panel.device_store = Mock(id_for=Mock(return_value=None))
        panel.root.after.side_effect = lambda _ms, cb: cb()
        found = [
            Receiver("USB\\VID_258A&PID_010C\\1", "F87 Pro", "AULA", "258A:010C", "键盘", "USB Receiver", True),
            Receiver("USB\\VID_046D&PID_C539\\2", "PRO WIRELESS", "Logitech", "046D:C539", "鼠标", "Receiver", True),
        ]
        with patch("receiver_search.scan_devices", return_value=found):
            bridge._dispatch({"action": "searchScan", "mode": "usb", "showOther": False})
            # Wait briefly for daemon scan thread
            import time
            for _ in range(30):
                if len(bridge._commands) >= 2:
                    break
                time.sleep(0.02)

        states = list(bridge._commands)
        self.assertTrue(states[0]["searchBusy"])
        final = states[-1]
        self.assertEqual(final["type"], "searchState")
        self.assertFalse(final["searchBusy"])
        self.assertEqual(len(final["searchItems"]), 2)
        self.assertTrue(final["searchItems"][0]["aulaEligible"])
        self.assertEqual(final["searchItems"][1]["action"], "添加")

    def test_snapshot_records_session_battery_history_points(self):
        from device_readers import Reading
        from my_devices import NexusPanel

        panel = NexusPanel.__new__(NexusPanel)
        panel.saved_devices = []
        panel.reading_times = ReadingTimes()
        panel.appearance, panel.glass_enabled, panel.busy = SimpleNamespace(dark=True), True, False
        panel.readings = {DEFAULT_DEVICE_ID: Reading("F87 Pro", "", level=82, connected=True)}
        first = make_snapshot(panel)
        panel.readings[DEFAULT_DEVICE_ID] = Reading("F87 Pro", "", level=79, connected=True)
        second = make_snapshot(panel)
        self.assertEqual(first["devices"][0]["details"]["history"], [82])
        self.assertEqual(second["devices"][0]["details"]["history"], [82, 79])


if __name__ == "__main__":
    unittest.main()
