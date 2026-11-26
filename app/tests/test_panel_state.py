import json
from contextlib import contextmanager
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from unittest import TestCase, skipUnless
from unittest.mock import Mock, call, patch
from uuid import uuid4


@skipUnless(sys.platform == "win32", "Nexus panel state uses Windows APIs")
class PanelStateTests(TestCase):
    def test_starts_native_process_before_initializing_tcl_or_loading_panel(self):
        import panel_startup as startup

        @contextmanager
        def primary_instance():
            yield True

        config = {"theme": "dark", "glass_backdrop": False, "ui_font": "alibaba-regular"}
        calls = Mock()
        process = calls.launch.return_value
        process.poll.return_value = 0
        with (patch.object(startup.tc, "ui_instance", primary_instance),
              patch.object(startup.tc, "load_config", return_value=config),
              patch.object(startup, "launch_native_process", calls.launch),
              patch.object(startup, "_open_panel", calls.open_panel)):
            startup.main(["--keep-warm", "--check-updates"], prepare_tcl=calls.prepare)

        self.assertEqual(calls.mock_calls[:3], [
            call.launch(dark=True, glass=False, font="alibaba-regular", keep_warm=True),
            call.prepare(),
            call.open_panel(config, process, True),
        ])
        process.terminate.assert_not_called()

    def test_early_native_process_is_closed_if_tcl_fails(self):
        import panel_startup as startup

        @contextmanager
        def primary_instance():
            yield True

        process = Mock()
        process.poll.return_value = None
        prepare = Mock(side_effect=OSError("Tcl unavailable"))
        with (patch.object(startup.tc, "ui_instance", primary_instance),
              patch.object(startup.tc, "load_config", return_value={}),
              patch.object(startup, "launch_native_process", return_value=process) as launch,
              patch.object(startup, "_open_panel") as open_panel,
              self.assertRaisesRegex(OSError, "Tcl unavailable")):
            startup.main([], prepare_tcl=prepare)

        launch.assert_called_once_with(dark=None, glass=True, font=None, keep_warm=False)
        open_panel.assert_not_called()
        process.terminate.assert_called_once_with()

    def test_legacy_settings_default_to_auto_refresh_and_partial_saves_preserve_it(self):
        import tray_common as tc

        with TemporaryDirectory() as directory:
            path = Path(directory) / "tray_config.json"
            path.write_text(json.dumps({"interval": 120}), encoding="utf-8")
            with patch.object(tc, "CONFIG_PATH", path):
                self.assertTrue(tc.load_config()["panel_auto_refresh"])
                tc.save_config({"panel_auto_refresh": False}, strict=True)
                tc.save_config({"autostart": False}, strict=True)
                settings = tc.load_config()
                self.assertFalse(settings["panel_auto_refresh"])
                self.assertFalse(settings["autostart"])
                self.assertEqual(settings["interval"], 120)

    def test_main_window_lock_is_shared_with_another_process_and_released(self):
        import tray_common as tc

        script = ("import sys, tray_common as tc\n"
                  "tc.UI_INSTANCE_KEY = sys.argv[1]\n"
                  "with tc.ui_instance() as primary:\n"
                  "    print(primary)\n")
        instance_key = "NexusBatteryCenter.Test." + uuid4().hex

        def child():
            result = subprocess.run(
                [sys.executable, "-B", "-c", script, instance_key],
                cwd=Path(tc.__file__).parent, check=True, timeout=10,
                capture_output=True, text=True,
            )
            return result.stdout.strip()

        with patch.object(tc, "UI_INSTANCE_KEY", instance_key):
            with tc.ui_instance() as primary:
                self.assertTrue(primary)
                self.assertEqual(child(), "False")
            self.assertEqual(child(), "True")

    def test_second_launch_reuses_the_current_panel_for_both_shortcuts(self):
        import my_devices as panel
        import tray_common as tc

        @contextmanager
        def second_instance():
            yield False

        with (patch.object(tc, "ui_instance", second_instance),
              patch.object(tc, "focus_ui") as focus,
              patch("update_ui.request_existing_window") as check,
              patch("tkinter.Tk") as make_window):
            panel.main([])
            panel.main(["--check-updates"])

        focus.assert_called_once_with(wait_timeout=2.0)
        check.assert_called_once_with()
        make_window.assert_not_called()
