from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from device_navigation import DeviceNavigation
from view_switcher import ViewSwitcher


def control():
    widget = DeviceNavigation.__new__(DeviceNavigation)
    widget.position = widget.origin = widget.target = 0.0
    widget.started, widget.after_id, widget.closed = 0.0, None, False
    widget.after, widget.after_cancel, widget.place_marker = Mock(return_value="timer"), Mock(), Mock()
    return widget


class DeviceNavigationTests(unittest.TestCase):
    def test_delayed_mouse_focus_is_not_mistaken_for_keyboard_focus(self):
        for kind in (DeviceNavigation, ViewSwitcher):
            with self.subTest(kind=kind.__name__):
                widget = kind.__new__(kind)
                widget._pointer_focus, widget.pressed = True, None
                widget.paint, widget.update_content = Mock(), Mock()
                widget.focus_in(None)
                self.assertFalse(widget.keyboard_focus)
                widget.focus_out(None)
                widget.focus_in(None)
                self.assertTrue(widget.keyboard_focus)

    def test_reversal_starts_at_the_current_animated_position(self):
        widget = control()
        with patch("device_navigation.time.perf_counter", return_value=0) as clock:
            widget.animate_to(100)
            clock.return_value = .1
            widget.animate_to(0)
            self.assertAlmostEqual(widget.position,87.5)
            self.assertAlmostEqual(widget.origin,87.5)
            widget.after_cancel.assert_called_once_with("timer")
            clock.return_value = .4
            widget.tick()
        self.assertEqual(widget.position,0)
        self.assertIsNone(widget.after_id)

    def test_close_cancels_pending_animation_and_late_frames(self):
        widget = control()
        with patch("device_navigation.time.perf_counter", return_value=0):
            widget.animate_to(100)
        widget.close()
        widget.after.reset_mock()
        widget.tick()
        widget.after_cancel.assert_called_once_with("timer")
        widget.after.assert_not_called()

    def test_unchanged_items_do_not_rebuild_or_restart_animation(self):
        widget = control()
        widget.items = (("mouse","Mouse","mouse"),)
        widget.selected = "mouse"
        widget.build,widget.animate_to = Mock(),Mock()
        widget.set_items(list(widget.items),"mouse")
        widget.build.assert_not_called()
        widget.animate_to.assert_not_called()

    def test_keyboard_crosses_page_boundaries_and_handles_an_empty_list(self):
        widget = control()
        widget.items = tuple((str(i),str(i),"usb") for i in range(4))
        widget.selected,widget.paint,widget.command = "2",Mock(),Mock()
        self.assertEqual(widget.key(SimpleNamespace(keysym="Right")),"break")
        widget.command.assert_called_once_with("3")
        widget.items = ()
        widget.command.reset_mock()
        widget.key(SimpleNamespace(keysym="Home"))
        widget.command.assert_not_called()
