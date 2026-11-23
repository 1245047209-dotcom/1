from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import update_client
from update_ui import UpdateManager, request_existing_window


class UpdateManagerTests(unittest.TestCase):
    def setUp(self):
        self.directory = Path(self.enterContext(TemporaryDirectory()))
        self.enterContext(patch.object(update_client, "BASE", self.directory))
        self.manager = UpdateManager(Mock())
        self.manager.window = Mock()
        self.manager.window.winfo_exists.return_value = True
        self.manager.status = Mock()
        self.manager.primary = Mock()
        self.manager.ignore = Mock()
        self.manager.notes = Mock()
        self.manager.progress = {"value": 100}
        self.manager.release = SimpleNamespace(size=128)
        self.ready = (self.directory / "installer.exe", "0" * 64)

    def test_background_no_result_preserves_offer_and_download(self):
        release = self.manager.release
        for downloaded in (None, self.ready):
            with self.subTest(downloaded=downloaded is not None):
                self.manager.downloaded = downloaded
                self.manager.busy = True
                self.manager.events.put(("checked", None, False))

                self.manager._poll()

                self.assertIs(self.manager.release, release)
                self.assertEqual(self.manager.downloaded, downloaded)
                self.assertFalse(self.manager.busy)
                self.manager.primary.configure.assert_not_called()
                self.manager.status.set.assert_not_called()

    def test_explicit_check_can_clear_offer(self):
        self.manager.downloaded = self.ready
        self.manager.events.put(("checked", None, True))

        self.manager._poll()

        self.assertIsNone(self.manager.release)
        self.assertIsNone(self.manager.downloaded)
        self.assertEqual(self.manager.primary.configure.call_args.kwargs["text"], "重新检查")

    def test_missing_or_invalid_download_restores_retry(self):
        release = self.manager.release
        path, _ = self.ready
        for exists in (False, True):
            with self.subTest(file_exists=exists):
                if exists:
                    path.write_bytes(b"not an installer")
                self.manager.downloaded = self.ready
                self.manager.progress["value"] = 100
                with patch.object(update_client.subprocess, "Popen") as launch:
                    self.manager._launch_install()
                    launch.assert_not_called()

                self.assertIs(self.manager.release, release)
                self.assertIsNone(self.manager.downloaded)
                self.assertEqual(self.manager.progress["value"], 0)
                self.manager.status.set.assert_called()
                self.manager.primary.configure.assert_called_with(
                    text="重新下载", state="normal", command=self.manager._download,
                )

    def test_launch_failure_keeps_valid_download_available(self):
        self.manager.downloaded = self.ready
        with patch.object(update_client, "launch_installer", side_effect=update_client.UpdateError("启动失败")):
            self.manager._launch_install()

        self.assertEqual(self.manager.downloaded, self.ready)
        self.manager.status.set.assert_called_once_with("启动失败")
        self.manager.primary.configure.assert_not_called()

    def test_existing_window_request_is_queued_before_focusing(self):
        request = self.directory / "open-update.request"
        tray_common = ModuleType("tray_common")

        def focus_ui(**kwargs):
            self.assertTrue(request.is_file())
            return True

        tray_common.focus_ui = Mock(side_effect=focus_ui)
        with patch.dict(sys.modules, {"tray_common": tray_common}):
            self.assertTrue(request_existing_window())

        tray_common.focus_ui.assert_called_once_with(wait_timeout=2.0)
