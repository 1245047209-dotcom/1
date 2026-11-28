import unittest
import queue
from types import SimpleNamespace
from unittest.mock import Mock, patch

from native_panel import NativePanel


class NativeSettingsBridgeTests(unittest.TestCase):
    def make_bridge(self):
        panel = SimpleNamespace(
            root=Mock(),
            appearance=SimpleNamespace(mode="system", font_choice="system", set_mode=Mock(), set_font=Mock()),
            interval=60,
            auto=True,
            glass_enabled=True,
            updates=None,
            low_battery_notifier=Mock(preview=Mock(return_value=True)),
            schedule=Mock(),
            set_gpu_enabled=Mock(),
            draw=Mock(),
            close=Mock(),
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
                patch("update_client.current_version", return_value="v73.9"), \
                patch("update_client.load_state", return_value={"auto_check": True}):
            bridge._dispatch({"action": "querySettings"})

        payload = bridge._commands.popleft()
        self.assertEqual(payload["type"], "settings")
        self.assertEqual(payload["themeMode"], "system")
        self.assertEqual(payload["interval"], 60)
        self.assertFalse(payload["gpuAnimation"])
        self.assertEqual(payload["version"], "v73.9")
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
                "uiFont": "alibaba-medium",
            })

        save_cfg.assert_called_once()
        saved = save_cfg.call_args.args[0]
        self.assertEqual(saved["interval"], 120)
        self.assertEqual(saved["theme"], "dark")
        self.assertFalse(saved["glass_backdrop"])
        panel.appearance.set_mode.assert_called_once_with("dark")
        panel.appearance.set_font.assert_called_once_with("alibaba-medium")
        post_tray.assert_called_once()
        reply = bridge._commands.popleft()
        self.assertEqual(reply, {"type": "settingsSaved", "ok": True})

    def test_tool_actions_return_inline_feedback(self):
        bridge, panel = self.make_bridge()
        bridge._dispatch({"action": "testAlert"})
        panel.low_battery_notifier.preview.assert_called_once_with()
        self.assertEqual(bridge._commands.popleft()["type"], "toolFeedback")


if __name__ == "__main__":
    unittest.main()
