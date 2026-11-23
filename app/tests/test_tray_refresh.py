import ctypes
import unittest
from unittest.mock import Mock, patch

import tray_app
from device_readers import Reading


class TrayRefreshTests(unittest.TestCase):
    def setUp(self):
        self.app = tray_app.TrayApp.__new__(tray_app.TrayApp)
        self.app.hwnd = 1234
        self.app.busy = True
        self.app.icons_added = True
        self.app.log = Mock()
        self.app.low_battery_notifier = Mock()
        self.app.levels = {"keyboard": Reading("狼蛛 F87 Pro", "", level=81)}

    def test_refresh_with_no_saved_keyboard_clears_the_icon_and_tooltip(self):
        tips = []

        def record_icon(_action, icon_data):
            data = ctypes.cast(icon_data, ctypes.POINTER(tray_app.NOTIFYICONDATAW)).contents
            tips.append(data.szTip)
            return True

        with patch("saved_devices.DeviceStore") as store, \
             patch.object(tray_app.USER32, "PostMessageW") as post, \
             patch.object(tray_app.win_icon, "make_hicon", return_value=1) as make_icon, \
             patch.object(tray_app.SHELL32, "Shell_NotifyIconW", side_effect=record_icon):
            store.return_value.default_visible.return_value = False
            store.return_value.load.return_value = ([], None)
            self.app._worker(self.app.hwnd)
            self.app.on_refresh_done()

        post.assert_called_once_with(1234, tray_app.MSG_REFRESH_DONE, 0, 0)
        make_icon.assert_called_once_with("--", (150, 155, 160), True)
        self.assertEqual(tips, ["键盘电量：无可用键盘"])
        self.assertFalse(self.app.busy)
        self.assertIsNone(self.app.levels["keyboard"].level)

    def test_refresh_preserves_a_valid_reading(self):
        reading = Reading("狼蛛 F87 Pro V2D", "", level=8, status="放电中")
        with patch("aula_f87_v2d.tray_readings", return_value=[reading]), \
             patch.object(tray_app.USER32, "PostMessageW"), \
             patch.object(self.app, "update_icons"):
            self.app._worker(self.app.hwnd)
            self.app.on_refresh_done()

        self.assertIs(self.app.levels["keyboard"], reading)
        self.app.low_battery_notifier.check.assert_called_once_with(reading)

    def test_refresh_preserves_the_read_error_state(self):
        with patch("aula_f87_v2d.tray_readings", side_effect=OSError("读取失败")), \
             patch.object(tray_app.USER32, "PostMessageW"), \
             patch.object(self.app, "update_icons"):
            self.app._worker(self.app.hwnd)
            self.app.on_refresh_done()

        reading = self.app.levels["keyboard"]
        self.assertIsNone(reading.level)
        self.assertEqual(reading.error, "读取异常，请重试")
