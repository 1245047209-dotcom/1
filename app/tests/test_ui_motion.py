import unittest
from collections import OrderedDict
from types import SimpleNamespace
from unittest.mock import Mock, patch

from ui_motion import DeviceTransition, ease_out_cubic, smootherstep


class Loop:
    def __init__(self):
        self.now, self.next_id, self.pending = 0.0, 0, {}

    def after(self, delay, callback):
        self.next_id += 1
        self.pending[self.next_id] = callback
        return self.next_id

    def after_cancel(self, key):
        self.pending.pop(key, None)

    def tick(self, now):
        self.now = now
        key = min(self.pending)
        self.pending.pop(key)()


class CurveTests(unittest.TestCase):
    def test_curves_are_bounded_monotonic_and_reach_exact_endpoints(self):
        for curve in (smootherstep, ease_out_cubic):
            values = [curve(t / 100) for t in range(-1, 102)]
            self.assertEqual(values, sorted(values))
            self.assertEqual((values[0], values[-1]), (0, 1))
        self.assertLess(smootherstep(.1), .02)
        self.assertGreater(ease_out_cubic(.5), .8)


class SchedulingTests(unittest.TestCase):
    def setup_motion(self):
        loop, reveal = Loop(), Mock()
        motion = DeviceTransition(loop, reveal, clock=lambda: loop.now, finished=Mock())
        return loop, reveal, motion

    def test_switch_fades_out_reveals_then_finishes_without_a_position_offset(self):
        loop, reveal, motion = self.setup_motion()
        motion.start()
        loop.tick(.09)
        reveal.assert_called_once()
        self.assertEqual((motion.phase, motion.alpha, motion.offset), ("in", 0, 24))
        loop.tick(.25)
        self.assertAlmostEqual(motion.alpha, .5)
        self.assertAlmostEqual(motion.offset, 3)
        loop.tick(.42)
        self.assertEqual((motion.phase, motion.alpha, motion.offset), ("idle", 1, 0))
        self.assertEqual(reveal.call_count, 1)
        motion.finished.assert_called_once()
        self.assertEqual(loop.pending, {})

    def test_a_rapid_second_switch_cancels_old_completion_and_keeps_current_opacity(self):
        loop, reveal, motion = self.setup_motion()
        motion.start()
        loop.tick(.09)
        loop.tick(.25)
        old_callback = next(iter(loop.pending.values()))
        alpha, offset = motion.alpha, motion.offset
        motion.start()
        self.assertEqual((motion.alpha, motion.offset), (alpha, offset))
        old_callback()
        self.assertEqual(reveal.call_count, 1)
        loop.tick(.34)
        loop.tick(.67)
        self.assertEqual(reveal.call_count, 2)
        motion.finished.assert_called_once()
        self.assertEqual((motion.alpha, motion.offset), (1, 0))
        self.assertEqual(loop.pending, {})

    def test_close_invalidates_late_callbacks(self):
        loop, reveal, motion = self.setup_motion()
        motion.start()
        old_callback = next(iter(loop.pending.values()))
        motion.close()
        old_callback()
        reveal.assert_not_called()
        motion.finished.assert_not_called()
        self.assertFalse(motion.active)
        self.assertEqual(loop.pending, {})

    def test_slow_frames_follow_elapsed_time_without_queuing_animation_frames(self):
        loop, reveal, motion = self.setup_motion()
        motion.start()
        loop.tick(1)
        self.assertEqual(motion.phase, "in")
        self.assertEqual(len(loop.pending), 1)
        loop.tick(2)
        self.assertFalse(motion.active)
        self.assertEqual(loop.pending, {})

    def test_cold_layout_yields_to_input_and_cancellation_discards_partial_work(self):
        loop, reveal, motion = self.setup_motion()
        completed, discarded = Mock(), Mock()
        def prepare():
            try:
                for _ in range(20):
                    loop.now += .003
                    yield
                completed()
            finally:
                discarded()
        reveal.side_effect = prepare
        motion.start()
        loop.tick(.09)
        self.assertEqual(motion.phase, "prepare")
        self.assertLess(loop.now, .10)
        self.assertEqual(len(loop.pending), 1)
        late_callback = next(iter(loop.pending.values()))
        motion.close()
        late_callback()
        completed.assert_not_called()
        discarded.assert_called_once()
        self.assertFalse(loop.pending)
        self.assertFalse(motion.pacer.active)

    def test_fade_in_waits_for_all_layout_chunks_to_complete(self):
        loop, reveal, motion = self.setup_motion()
        completed = Mock()
        def prepare():
            for _ in range(12):
                yield
            completed()
        reveal.side_effect = prepare
        motion.start()
        loop.tick(.09)
        self.assertEqual(motion.phase, "prepare")
        completed.assert_not_called()
        loop.tick(.10)
        completed.assert_called_once()
        self.assertEqual(motion.phase, "in")
        loop.tick(.43)
        motion.finished.assert_called_once()
        self.assertFalse(motion.pacer.active)


class PanelSwitchTests(unittest.TestCase):
    @patch("my_devices.window_work_area", return_value=((0, 0, 1000, 750), 12, 32))
    def test_dragging_window_redraws_only_if_dimensions_or_monitor_change(self, work_area):
        from my_devices import NexusPanel
        panel = NexusPanel.__new__(NexusPanel)
        panel.root = Mock()
        panel._layout_size = (544, 316)
        panel._geometry_after_id = None
        panel._window_work_area = work_area.return_value[0]
        for x, y in ((0, 0), (40, 20), (600, 300)):
            panel.on_configure(SimpleNamespace(widget=panel.root, width=544, height=316, x=x, y=y))
        panel.root.after.assert_not_called()
        panel.on_configure(SimpleNamespace(widget=panel.root, width=500, height=316))
        panel.root.after.assert_called_once_with(32, panel.redraw_after_resize)
        work_area.return_value = ((1000, 0, 1700, 500), 12, 32)
        panel.on_configure(SimpleNamespace(widget=panel.root, width=500, height=316))
        panel.root.after_cancel.assert_called_once()
        self.assertEqual(panel.root.after.call_count, 2)

    def test_child_widget_configure_does_not_schedule_a_redraw(self):
        from my_devices import NexusPanel
        panel = NexusPanel.__new__(NexusPanel)
        panel.root = Mock()
        event = Mock()
        event.widget = Mock()
        panel.on_configure(event)
        panel.root.after.assert_not_called()


class FrameCacheTests(unittest.TestCase):
    def test_budget_evicts_individual_frames_while_painted_images_stay_referenced(self):
        motion = DeviceTransition(Loop(), Mock())
        motion.image_frames = OrderedDict(old=[object()], current=[object()], recent=[object()])
        motion.frame_sizes = {name: 12 * 1024 * 1024 for name in motion.image_frames}
        painted = motion.image_frames["old"]
        motion.layers = [{"applied": {1: {"image": painted}}}]
        motion.trim_frames()
        self.assertEqual(list(motion.image_frames), ["current", "recent"])
        self.assertEqual(set(motion.frame_sizes), set(motion.image_frames))
        self.assertIs(motion.layers[0]["applied"][1]["image"], painted)

    def test_slide_moves_one_group_and_unchanged_frames_do_not_reconfigure_items(self):
        root, canvas = Loop(), Mock()
        root.winfo_rgb = Mock(return_value=(0, 0, 0))
        canvas.type.return_value = "text"
        canvas.itemcget.return_value = "#000000"
        motion = DeviceTransition(root, Mock())
        motion.register(canvas, [1, 2, 3])
        motion.alpha, motion.offset = .5, 12
        motion.paint()
        canvas.move.assert_called_once_with("device_motion_0_0", 12, 0)
        self.assertEqual(canvas.itemconfigure.call_count, 3)
        canvas.move.reset_mock()
        canvas.itemconfigure.reset_mock()
        motion.paint()
        canvas.move.assert_not_called()
        canvas.itemconfigure.assert_not_called()

    def test_only_requested_opacity_frames_are_allocated_and_cached(self):
        from unittest.mock import patch
        root = Mock()
        root.tk.call.return_value = b"P6\n1 1\n255\n\x10\x20\x30"
        motion = DeviceTransition(root, Mock())
        with patch("tkinter.PhotoImage") as photo:
            motion.alpha = .25
            first = motion.fade_image("product")
            self.assertIs(motion.fade_image("product"), first)
            self.assertEqual(photo.call_count, 1)
            motion.alpha = .5
            motion.fade_image("product")
            self.assertEqual(photo.call_count, 2)
        root.tk.call.assert_called_once()

    def test_close_releases_retained_frame_memory(self):
        motion = DeviceTransition(Loop(), Mock())
        motion.image_frames["photo"] = [object()]
        motion.frame_sizes["photo"] = 1234
        motion.close()
        self.assertFalse(motion.image_frames)
        self.assertFalse(motion.frame_sizes)
        self.assertFalse(motion.sources)


if __name__ == "__main__":
    unittest.main()
