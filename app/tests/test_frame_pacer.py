import unittest
from unittest.mock import Mock, patch
from frame_pacer import FramePacer, TimerResolution


class FramePacerTests(unittest.TestCase):
    def setup_pacer(self):
        self.now = 0.0
        root, resolution = Mock(), Mock()
        return root, resolution, FramePacer(root, clock=lambda: self.now, resolution=resolution)

    def test_render_cost_is_subtracted_from_the_next_frame_wait(self):
        root, resolution, pacer = self.setup_pacer()
        pacer.start()
        self.now = .004
        pacer.after(Mock())
        self.assertEqual(root.after.call_args.args[0], 13)
        self.now = .021
        pacer.after(Mock())
        self.assertEqual(root.after.call_args.args[0], 12)
        resolution.acquire.assert_called_once()
        pacer.stop()
        pacer.stop()
        resolution.release.assert_called_once()

    def test_stalls_skip_expired_deadlines_without_catchup_callbacks(self):
        root, _resolution, pacer = self.setup_pacer()
        pacer.start()
        self.now = .102
        callback = Mock()
        pacer.after(callback)
        root.after.assert_called_once_with(15, callback)
        self.assertGreater(pacer.deadline, self.now)
        pacer.stop()

    def test_restart_reuses_precision_and_reanchors_deadline(self):
        root, resolution, pacer = self.setup_pacer()
        pacer.start()
        self.now = .50
        pacer.start()
        pacer.after(Mock())
        self.assertEqual(root.after.call_args.args[0], 17)
        resolution.acquire.assert_called_once()
        pacer.stop()

    def test_overlapping_animations_share_one_balanced_windows_request(self):
        api = Mock()
        api.timeBeginPeriod.return_value = 0
        with patch("frame_pacer.sys.platform", "win32"), patch("ctypes.WinDLL", return_value=api):
            resolution = TimerResolution()
            resolution.acquire()
            resolution.acquire()
            api.timeBeginPeriod.assert_called_once_with(1)
            resolution.release()
            api.timeEndPeriod.assert_not_called()
            resolution.release()
            resolution.release()
            api.timeEndPeriod.assert_called_once_with(1)
            self.assertEqual(resolution.users, 0)

    def test_precision_failure_falls_back_to_tk_without_a_matching_end_call(self):
        api = Mock()
        api.timeBeginPeriod.return_value = 97
        with patch("frame_pacer.sys.platform", "win32"), patch("ctypes.WinDLL", return_value=api):
            resolution = TimerResolution()
            resolution.acquire()
            resolution.release()
        api.timeEndPeriod.assert_not_called()
