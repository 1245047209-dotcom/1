import ctypes
from ctypes import wintypes
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import tray_app
import tray_common as tc


class PanelActivationTests(unittest.TestCase):
    def test_warm_activation_targets_only_the_window_for_this_installation(self):
        user = Mock()
        user.FindWindowExW.side_effect = [111, 222]
        user.GetPropW.side_effect = lambda hwnd, key: hwnd == 222 and key in {
            tc.UI_INSTANCE_KEY, tc.WARM_UI_PROPERTY,
        }
        user.RegisterWindowMessageW.return_value = 0xC123

        def owner(hwnd, pid):
            self.assertEqual(hwnd, 222)
            ctypes.cast(pid, ctypes.POINTER(wintypes.DWORD)).contents.value = 3737
            return 1

        user.GetWindowThreadProcessId.side_effect = owner
        user.PostMessageW.return_value = True
        with patch.object(tc, "USER32", user):
            self.assertTrue(tc.focus_ui())

        user.AllowSetForegroundWindow.assert_called_once_with(3737)
        user.RegisterWindowMessageW.assert_called_once_with(tc.MSG_ACTIVATE_WARM_UI)
        user.PostMessageW.assert_called_once_with(222, 0xC123, 0, 0)
        user.ShowWindowAsync.assert_not_called()

    def test_existing_restore_for_tk_and_failed_warm_message(self):
        for warm in (False, True):
            with self.subTest(warm=warm):
                user = Mock()
                user.GetPropW.return_value = warm
                user.RegisterWindowMessageW.return_value = 0xC123
                user.PostMessageW.return_value = False
                with patch.object(tc, "USER32", user), patch.object(tc, "ui_hwnd", return_value=222):
                    self.assertTrue(tc.focus_ui())

                if not warm:
                    user.PostMessageW.assert_not_called()
                user.ShowWindowAsync.assert_called_once_with(222, 9)
                user.SetForegroundWindow.assert_called_once_with(222)

    def test_tray_uses_activation_and_only_starts_ui_when_missing(self):
        app = SimpleNamespace(log=Mock())
        with patch.object(tc, "focus_ui", side_effect=[True, False]) as activate, \
                patch("subprocess.Popen") as spawn:
            tray_app.TrayApp.open_ui(app)
            spawn.assert_not_called()
            tray_app.TrayApp.open_ui(app)
            spawn.assert_called_once()
        self.assertEqual(activate.call_count, 2)


if __name__ == "__main__":
    unittest.main()
