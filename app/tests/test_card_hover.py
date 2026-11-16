import unittest
from unittest.mock import Mock

from card_hover import CardHover


class Loop:
    def __init__(self):
        self.now, self.counter, self.pending = 0, 0, {}

    def after(self, delay, callback):
        self.counter += 1
        self.pending[self.counter] = callback
        return self.counter

    def after_cancel(self, timer):
        self.pending.pop(timer, None)

    def tick(self, now):
        self.now = now
        self.pending.pop(min(self.pending))()


class HoverTests(unittest.TestCase):
    def setup_hover(self):
        root, canvas = Loop(), Mock()
        canvas.itemcget.return_value = "normal"
        canvas.tk.call.return_value = 100
        hover = CardHover(root, canvas, scale=.85, clock=lambda: root.now)
        hover.shadow_frames = Mock(return_value=[f"shadow{i}" for i in range(5)])
        hover.refresh_pointer = Mock()
        hover.register([("keyboard", (0, 0, 100, 100), {1, 2}, 1),
                        ("mouse", (120, 0, 220, 100), {3, 4}, 3)])
        return root, canvas, hover

    def test_only_hovered_card_lifts_with_easing_and_timer_stops_at_rest(self):
        root, canvas, hover = self.setup_hover()
        hover.set_target("keyboard")
        root.tick(.1)
        self.assertGreater(hover.states["keyboard"]["value"], .5)
        self.assertEqual(hover.states["mouse"]["value"], 0)
        root.tick(.21)
        self.assertEqual(hover.states["keyboard"]["value"], 1)
        self.assertFalse(root.pending)
        lift = sum(call.args[2] for call in canvas.move.call_args_list if call.args[0] == "hover_card_0")
        self.assertAlmostEqual(lift, -6.8)
        hover.set_target(None)
        root.tick(.5)
        self.assertEqual(hover.states["keyboard"]["value"], 0)
        self.assertFalse(root.pending)

    def test_fast_crossing_reverses_from_current_height_instead_of_jumping(self):
        root, _, hover = self.setup_hover()
        hover.set_target("keyboard")
        root.tick(.08)
        current = hover.states["keyboard"]["value"]
        hover.set_target("mouse")
        self.assertAlmostEqual(hover.states["keyboard"]["from"], current)
        root.tick(.18)
        self.assertLess(hover.states["keyboard"]["value"], current)
        self.assertGreater(hover.states["mouse"]["value"], 0)
        root.tick(.5)
        self.assertEqual((hover.states["keyboard"]["value"], hover.states["mouse"]["value"]), (0, 1))

    def test_raised_top_and_original_bottom_remain_clickable_without_edge_flicker(self):
        root, _, hover = self.setup_hover()
        hover.set_target("keyboard")
        root.tick(.21)
        self.assertEqual(hover.hit(50, -6), "keyboard")
        self.assertEqual(hover.hit(50, 99), "keyboard")
        self.assertIsNone(hover.hit(110, 50))

    def test_data_redraw_preserves_lift_on_replacement_canvas_items(self):
        root, canvas, hover = self.setup_hover()
        hover.set_target("keyboard")
        root.tick(.21)
        canvas.move.reset_mock()
        hover.register([("keyboard", (0, 0, 100, 100), {10, 11}, 10)])
        self.assertEqual(hover.states["keyboard"]["value"], 1)
        canvas.move.assert_called_once_with("hover_card_0", 0, -6.8)

    def test_closing_cancels_pending_frame_and_ignores_late_callbacks(self):
        root, canvas, hover = self.setup_hover()
        hover.set_target("keyboard")
        callback = next(iter(root.pending.values()))
        hover.close()
        canvas.move.reset_mock()
        callback()
        canvas.move.assert_not_called()
        self.assertFalse(root.pending)
        self.assertFalse(hover.layers)


if __name__ == "__main__":
    unittest.main()
