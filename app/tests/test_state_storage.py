from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import textwrap
import unittest

from saved_devices import DeviceStore
from update_client import load_state


APP_ROOT = Path(__file__).resolve().parent.parent


class SharedStateTests(unittest.TestCase):
    def _worker(self, source, path):
        process = subprocess.Popen(
            [sys.executable, "-B", "-X", "utf8", "-c", textwrap.dedent(source), str(path)],
            cwd=APP_ROOT,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            encoding="utf-8",
        )
        self.addCleanup(self._close_worker, process)
        return process

    @staticmethod
    def _close_worker(process):
        if process.poll() is None:
            process.kill()
            process.wait(timeout=10)
        for stream in (process.stdin, process.stdout, process.stderr):
            stream.close()

    def _wait_for_writers(self, first, second):
        self.assertEqual(second.stdout.readline().strip(), "writing")
        with self.assertRaises(subprocess.TimeoutExpired):
            second.wait(timeout=.3)
        _, stderr = first.communicate("\n", timeout=15)
        self.assertEqual(first.returncode, 0, stderr)
        _, stderr = second.communicate(timeout=15)
        self.assertEqual(second.returncode, 0, stderr)

    def test_concurrent_device_additions_preserve_both_members(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        path = Path(folder.name) / "devices.json"
        first = self._worker(r"""
            import sys
            from unittest.mock import patch
            from receiver_scan import Receiver
            from saved_devices import DeviceStore

            store = DeviceStore(sys.argv[1])
            save = store._save

            def pause_before_save(*args, **kwargs):
                print("read", flush=True)
                sys.stdin.readline()
                return save(*args, **kwargs)

            device = Receiver("USB\\VID_046D&PID_C539\\first", "PRO WIRELESS",
                              "Logitech", "046D:C539", "mouse", "", True)
            with patch.object(store, "_save", side_effect=pause_before_save):
                store.add(device)
        """, path)
        self.assertEqual(first.stdout.readline().strip(), "read")
        second = self._worker(r"""
            import sys
            from receiver_scan import Receiver
            from saved_devices import DeviceStore

            device = Receiver("USB\\VID_046D&PID_C539\\second", "PRO WIRELESS",
                              "Logitech", "046D:C539", "mouse", "", True)
            print("writing", flush=True)
            DeviceStore(sys.argv[1]).add(device)
        """, path)
        self._wait_for_writers(first, second)
        devices, _ = DeviceStore(path).load()
        self.assertEqual({device.instance_id.rsplit("\\", 1)[-1] for device in devices}, {"first", "second"})

    def test_update_preference_and_notification_do_not_overwrite_each_other(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        first = self._worker("""
            import sys
            from unittest.mock import patch
            import update_client as client

            load = client.load_state

            def pause_after_load(base):
                state = load(base)
                print("read", flush=True)
                sys.stdin.readline()
                return state

            with patch.object(client, "load_state", side_effect=pause_after_load):
                client.save_state(sys.argv[1], auto_check=False)
        """, folder.name)
        self.assertEqual(first.stdout.readline().strip(), "read")
        second = self._worker("""
            import sys
            import update_client as client

            print("writing", flush=True)
            client.save_state(sys.argv[1], notified_tag="v73.2")
        """, folder.name)
        self._wait_for_writers(first, second)
        state = load_state(folder.name)
        self.assertFalse(state["auto_check"])
        self.assertEqual(state["notified_tag"], "v73.2")
