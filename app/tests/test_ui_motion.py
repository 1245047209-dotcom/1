import unittest
from collections import OrderedDict
from types import SimpleNamespace
from unittest.mock import Mock

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
        motion = DeviceTransition(loop, reveal, clock=lambda: loop.now)
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
        self.assertEqual(reveal.call_count, 2)
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
        self.assertEqual(reveal.call_count, 3)
        self.assertEqual((motion.alpha, motion.offset), (1, 0))
        self.assertEqual(loop.pending, {})

    def test_close_invalidates_late_callbacks(self):
        loop, reveal, motion = self.setup_motion()
        motion.start()
        old_callback = next(iter(loop.pending.values()))
        motion.close()
        old_callback()
        reveal.assert_not_called()
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


class PanelSwitchTests(unittest.TestCase):
    def test_dragging_fixed_window_does_not_redraw_its_contents(self):
        from my_devices import NexusPanel
        panel = NexusPanel.__new__(NexusPanel)
        panel.root, panel.draw = Mock(), Mock()
        panel._layout_size = (952, 553)
        for x, y in ((0, 0), (40, 20), (600, 300)):
            panel.on_configure(SimpleNamespace(widget=panel.root, width=952, height=553, x=x, y=y))
        panel.draw.assert_not_called()
        panel.on_configure(SimpleNamespace(widget=panel.root, width=900, height=550))
        panel.draw.assert_called_once()

    def test_view_switch_keeps_user_window_geometry_and_starts_transition(self):
        from my_devices import NexusPanel
        panel = NexusPanel.__new__(NexusPanel)
        panel.view_mode, panel.busy = "all", False
        panel.root, panel.device_store, panel.transition = Mock(), Mock(), Mock()
        panel.refresh_button = Mock()
        panel.set_view_mode("single")
        self.assertEqual(panel.view_mode, "single")
        panel.device_store.set_view_mode.assert_called_once_with("single")
        panel.transition.start.assert_called_once()
        panel.root.geometry.assert_not_called()

    def test_child_widget_configure_does_not_trigger_a_full_redraw(self):
        from my_devices import NexusPanel
        panel = NexusPanel.__new__(NexusPanel)
        panel.root, panel.draw = Mock(), Mock()
        event = Mock()
        event.widget = Mock()
        panel.on_configure(event)
        panel.draw.assert_not_called()
        event.widget = panel.root
        panel.on_configure(event)
        panel.draw.assert_called_once()


class FrameCacheTests(unittest.TestCase):
    def test_budget_eviction_keeps_images_used_by_the_current_animation(self):
        motion = DeviceTransition(Loop(), Mock())
        motion.image_frames = OrderedDict(old=[object()], current=[object()], recent=[object()])
        motion.frame_sizes = {name: 12 * 1024 * 1024 for name in motion.image_frames}
        motion.layers = [{"entries": [(1, {"image": "current"})]}]
        motion.trim_frames()
        self.assertEqual(list(motion.image_frames), ["current", "recent"])
        self.assertEqual(set(motion.frame_sizes), set(motion.image_frames))

    def test_close_releases_retained_frame_memory(self):
        motion = DeviceTransition(Loop(), Mock())
        motion.image_frames["photo"] = [object()]
        motion.frame_sizes["photo"] = 1234
        motion.close()
        self.assertFalse(motion.image_frames)
        self.assertFalse(motion.frame_sizes)


if __name__ == "__main__":
    unittest.main()
