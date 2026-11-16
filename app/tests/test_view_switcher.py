"""Regressions for interruption, scheduling, and input handling."""
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from view_switcher import ViewSwitcher


ITEMS = (("all", "全部设备", "all"), ("single", "单个设备", "single"))


def control():
    widget = ViewSwitcher.__new__(ViewSwitcher)
    widget.items, widget.selected = ITEMS, "all"
    widget.position = widget.origin = widget.target = 0.0
    widget.started, widget.after_id, widget.closed = 0.0, None, False
    widget.pressed, widget.hover, widget.keyboard_focus = None, None, False
    widget.after, widget.after_cancel = Mock(return_value="timer"), Mock()
    widget.place_thumb, widget.update_content, widget.build = Mock(), Mock(), Mock()
    widget.command = Mock()
    return widget


class ViewSwitcherTests(unittest.TestCase):
    def test_fast_reversal_continues_from_current_position(self):
        widget = control()
        with patch("view_switcher.time.perf_counter", return_value=0) as clock:
            widget.animate_to(1)
            clock.return_value = .09
            widget.animate_to(0)
            self.assertAlmostEqual(widget.position, .875)
            self.assertAlmostEqual(widget.origin, .875)
            widget.after_cancel.assert_called_once_with("timer")
            clock.return_value = .30
            widget.tick()
        self.assertEqual(widget.position, 0)
        self.assertIsNone(widget.after_id)

    def test_battery_redraw_does_not_restart_selection_animation(self):
        widget = control()
        with patch("view_switcher.time.perf_counter", return_value=0):
            widget.set_items(ITEMS, "single")
            calls = widget.after.call_count
            widget.set_items(list(ITEMS), "single")
        self.assertEqual(widget.after.call_count, calls)
        widget.after_cancel.assert_not_called()
        widget.build.assert_not_called()

    def test_close_cancels_animation_and_prevents_another_frame(self):
        widget = control()
        with patch("view_switcher.time.perf_counter", return_value=0):
            widget.animate_to(1)
        widget.close()
        widget.after.reset_mock()
        widget.tick()
        widget.after_cancel.assert_called_once_with("timer")
        widget.after.assert_not_called()
        self.assertIsNone(widget.after_id)

    def test_keyboard_only_changes_selection_when_needed(self):
        widget = control()
        widget.command.side_effect = lambda selected: setattr(widget, "selected", selected)
        for key in ("End", "Right", "Return"):
            self.assertEqual(widget.key(SimpleNamespace(keysym=key)), "break")
        widget.command.assert_called_once_with("single")
        self.assertTrue(widget.keyboard_focus)

    def test_dragging_off_the_pressed_segment_does_not_activate_it(self):
        widget = control()
        widget.hit = Mock(return_value=1)
        widget.pressed = 0
        widget.release(SimpleNamespace(x=150, y=15))
        widget.command.assert_not_called()
        self.assertIsNone(widget.pressed)
        widget.pressed = 1
        widget.release(SimpleNamespace(x=150, y=15))
        widget.command.assert_called_once_with("single")
