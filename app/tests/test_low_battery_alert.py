import json
import ctypes
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
import uuid

from device_readers import KEYBOARD_TITLE, Reading
from low_battery_alert import AlertState, AlertStore, LowBatteryNotifier, show_notification, _request_notification
import tray_common as tc


def reading(level=9, status="放电中", error=""):
    return Reading(KEYBOARD_TITLE, "", level=level, status=status, error=error)


class PolicyTests(unittest.TestCase):
    def test_strict_threshold_and_invalid_values(self):
        for value in (0, 1, 9):
            self.assertTrue(AlertState().observe(value))
        for value in (10, 11, 100, None, -1, 101, True, "9"):
            self.assertFalse(AlertState().observe(value))

    def test_sent_alert_survives_restart_and_small_fluctuations(self):
        state = AlertState()
        state.observe(9)
        token, _ = state.begin()
        state.finish(token, True)
        state = AlertState(state.to_dict())
        for value in (8, 7, 10, 11, 14, 9):
            self.assertFalse(state.observe(value))
        state.observe(15)
        self.assertTrue(state.observe(9))

    def test_charging_rearms_without_a_percentage(self):
        state = AlertState({"version": 1, "notified": True})
        self.assertFalse(state.observe(None, charging=True))
        self.assertTrue(state.observe(9))

    def test_failed_read_does_not_alert_or_rearm(self):
        state = AlertState({"version": 1, "notified": True})
        self.assertFalse(state.observe(9, valid=False))
        self.assertFalse(state.observe(None, charging=True, valid=False))
        self.assertFalse(state.observe(9))

    def test_charging_while_a_notification_is_pending_invalidates_the_old_result(self):
        state = AlertState()
        state.observe(9)
        old, _ = state.begin()
        state.observe(None, charging=True)
        state.finish(old, True)
        self.assertTrue(state.observe(8))

    def test_aborted_pending_notification_can_be_retried(self):
        state = AlertState()
        state.observe(9)
        old, _ = state.begin()
        restored = AlertState(state.to_dict())
        new, level = restored.begin()
        self.assertNotEqual(old, new)
        self.assertEqual(level, 9)


class NotifierTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name) / "alert.json"
        self.name = "Local\\NexusAlertTest_" + uuid.uuid4().hex

    def notifier(self, notification=None, **kwargs):
        return LowBatteryNotifier(path=self.path, notification=notification or Mock(return_value=True),
                                  mutex_name=self.name, **kwargs)

    def join(self, notifier):
        if notifier._thread is not None:
            notifier._thread.join(3)
            self.assertFalse(notifier._thread.is_alive())

    def test_exactly_ten_percent_charging_and_failed_reads_do_not_show(self):
        notification = Mock(return_value=True)
        notifier = self.notifier(notification)
        for value in (reading(10), reading(None, "充电中"), reading(9, error="offline")):
            self.assertFalse(notifier.check(value))
        notification.assert_not_called()

    def test_low_battery_shows_once_and_remains_suppressed_after_restart(self):
        notification = Mock(return_value=True)
        notifier = self.notifier(notification)
        self.assertTrue(notifier.check(reading()))
        self.join(notifier)
        notification.assert_called_once_with(KEYBOARD_TITLE, 9, preview=False)
        restarted = self.notifier(notification)
        self.assertFalse(restarted.check(reading(8)))
        self.assertFalse(restarted.check(reading(10)))
        self.assertFalse(restarted.check(reading(9)))
        restarted.check(reading(None, "充电中"))
        self.assertTrue(restarted.check(reading(9)))
        self.join(restarted)
        self.assertEqual(notification.call_count, 2)

    def test_panel_and_tray_do_not_show_two_simultaneous_notifications(self):
        opened, release = threading.Event(), threading.Event()
        def notification(*args, **kwargs):
            opened.set()
            release.wait(2)
            return True
        notification = Mock(side_effect=notification)
        panel, tray = self.notifier(notification), self.notifier(notification)
        try:
            panel.check(reading())
            self.assertTrue(opened.wait(1))
            tray.check(reading())
            self.join(tray)
            self.assertEqual(notification.call_count, 1)
        finally:
            release.set()
            self.join(panel)
        self.assertFalse(tray.check(reading()))

    def test_disabled_alert_can_be_enabled_later(self):
        enabled = [False]
        notification = Mock(return_value=True)
        notifier = self.notifier(notification, enabled=lambda: enabled[0])
        self.assertFalse(notifier.check(reading()))
        notification.assert_not_called()
        enabled[0] = True
        self.assertTrue(notifier.check(reading()))
        self.join(notifier)
        notification.assert_called_once()

    def test_preview_does_not_consume_real_alert(self):
        notification = Mock(return_value=True)
        notifier = self.notifier(notification)
        self.assertTrue(notifier.preview())
        self.join(notifier)
        self.assertFalse(self.path.exists())
        notification.assert_called_once_with(KEYBOARD_TITLE, 9, preview=True)
        self.assertTrue(notifier.check(reading()))
        self.join(notifier)
        self.assertEqual(notification.call_count, 2)

    def test_notification_failure_is_retried(self):
        notification = Mock(side_effect=[OSError("unavailable"), True])
        notifier = self.notifier(notification)
        notifier.check(reading())
        self.join(notifier)
        self.assertFalse(json.loads(self.path.read_text())["notified"])
        notifier.check(reading())
        self.join(notifier)
        self.assertTrue(json.loads(self.path.read_text())["notified"])

    def test_corrupt_or_readonly_storage_does_not_break_the_alert(self):
        self.path.write_text("broken", encoding="utf-8")
        notifier = self.notifier()
        with patch("low_battery_alert.os.replace", side_effect=OSError("readonly")):
            self.assertTrue(notifier.check(reading()))
            self.join(notifier)
            self.assertFalse(notifier.check(reading(8)))
        self.assertTrue(notifier.store.memory_only)


class IntegrationTests(unittest.TestCase):
    def test_alert_setting_defaults_on_and_can_be_saved_off(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(tc, "CONFIG_PATH", str(Path(folder) / "config.json")):
                self.assertTrue(tc.load_config()["low_battery_alert"])
                config = tc.load_config()
                config["low_battery_alert"] = False
                tc.save_config(config)
                self.assertFalse(tc.load_config()["low_battery_alert"])

    def test_tray_completion_checks_alerts(self):
        from tray_app import TrayApp
        app = TrayApp.__new__(TrayApp)
        app.levels = {"keyboard": reading()}
        app.log, app.update_icons, app.low_battery_notifier = Mock(), Mock(), Mock()
        app.on_refresh_done()
        app.low_battery_notifier.check.assert_called_once_with(app.levels["keyboard"])

    def test_panel_completion_checks_alerts(self):
        from my_devices import NexusPanel
        panel = NexusPanel.__new__(NexusPanel)
        panel.root, panel.refresh_button, panel.low_battery_notifier = Mock(), Mock(), Mock()
        panel.draw = Mock()
        panel.readings = {}
        panel.refresh_generation = 1
        panel.selected_device_id = "aula-f87-default"
        sample = reading()
        panel.finish_refresh(sample, 1)
        panel.low_battery_notifier.check.assert_called_once_with(sample)


class SystemNotificationTests(unittest.TestCase):
    def tray(self):
        from tray_app import TrayApp
        app = TrayApp.__new__(TrayApp)
        app.hwnd, app.icons_added, app.log = 1234, True, Mock()
        return app

    def test_uses_the_existing_tray_icon_and_windows_notification_fields(self):
        import tray_app
        app = self.tray()
        sent = []
        def send(action, pointer):
            data = ctypes.cast(pointer, ctypes.POINTER(tray_app.NOTIFYICONDATAW)).contents
            sent.append((action, data.hWnd, data.uID, data.uFlags,
                         data.szInfoTitle, data.szInfo, data.dwInfoFlags))
            return True
        with patch.object(tray_app.SHELL32, "Shell_NotifyIconW", side_effect=send), \
             patch.object(tc, "load_config", return_value={"low_battery_alert": True}):
            self.assertTrue(app.show_system_notification(9))
        action, hwnd, icon, flags, title, body, info_flags = sent[0]
        self.assertEqual((action, hwnd, icon, flags), (tray_app.NIM_MODIFY, 1234, 1, tray_app.NIF_INFO))
        self.assertIn("低电量提醒", title)
        self.assertIn("9%", body)
        self.assertTrue(info_flags & tray_app.NIIF_RESPECT_QUIET_TIME)

    def test_disabled_real_notification_is_skipped_but_preview_is_allowed(self):
        import tray_app
        app = self.tray()
        with patch.object(tc, "load_config", return_value={"low_battery_alert": False}), \
             patch.object(tray_app.SHELL32, "Shell_NotifyIconW", return_value=True) as send:
            self.assertFalse(app.show_system_notification(9))
            send.assert_not_called()
            self.assertTrue(app.show_system_notification(9, preview=True))
            self.assertFalse(app.show_system_notification(10, preview=True))
            self.assertEqual(send.call_count, 1)

    def test_ipc_message_routes_to_the_notification_sender(self):
        app = self.tray()
        app.msg_quit, app.msg_reload, app.msg_notify = 50001, 50002, 50003
        app.show_system_notification = Mock(return_value=True)
        self.assertEqual(app.wnd_proc(1234, app.msg_notify, 9, 1), 1)
        app.show_system_notification.assert_called_once_with(9, preview=True)

    def test_clicking_a_notification_opens_the_panel(self):
        import tray_app
        app = self.tray()
        app.open_ui = Mock()
        app.wnd_proc(1234, tray_app.MSG_TRAY_CALLBACK, 1, tray_app.NIN_BALLOONUSERCLICK)
        app.open_ui.assert_called_once()

    def test_panel_starts_the_tray_if_needed(self):
        with patch.object(tc, "tray_hwnd", side_effect=[0, 1234]), \
             patch.object(tc, "start_tray_app", return_value=True) as start, \
             patch("low_battery_alert._request_notification", return_value=True) as request:
            self.assertTrue(show_notification(KEYBOARD_TITLE, 9, preview=True))
            start.assert_called_once()
            request.assert_called_once_with(1234, 9, True)

    def test_failed_tray_start_does_not_fall_back_to_a_dialog(self):
        with patch.object(tc, "tray_hwnd", return_value=0), \
             patch.object(tc, "start_tray_app", return_value=False), \
             patch("low_battery_alert._request_notification") as request:
            self.assertFalse(show_notification(KEYBOARD_TITLE, 9))
            request.assert_not_called()

    def test_ipc_success_requires_shell_acceptance(self):
        user = Mock()
        user.RegisterWindowMessageW.return_value = 50003
        def send(*args):
            ctypes.cast(args[-1], ctypes.POINTER(ctypes.c_size_t)).contents.value = 1
            return 1
        with patch.object(tc, "USER32", user):
            user.SendMessageTimeoutW.side_effect = send
            self.assertTrue(_request_notification(1234, 9, True))
            user.SendMessageTimeoutW.side_effect = lambda *args: 1
            self.assertFalse(_request_notification(1234, 9, True))


if __name__ == "__main__":
    unittest.main()
