from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from device_layout import CARD_GAP, CARD_PADDING, card_rectangles, content_height, fit_ppm, read_device_batch
from device_readers import Reading
from saved_devices import DEFAULT_DEVICE_ID, DeviceStore


class BatchTests(unittest.TestCase):
    def test_two_devices_are_read_concurrently_and_keep_separate_values(self):
        barrier = threading.Barrier(2, timeout=2)
        mouse = SimpleNamespace(id="mouse", label="Mouse")
        def keyboard():
            barrier.wait()
            return [Reading("Keyboard", "", level=45)]
        def saved(device):
            barrier.wait()
            return Reading(device.label, "", level=80, estimated=True)
        results = read_device_batch([mouse], keyboard, saved)
        self.assertEqual(results[DEFAULT_DEVICE_ID].level, 45)
        self.assertEqual(results["mouse"].level, 80)

    def test_failure_of_one_reader_does_not_hide_another_device(self):
        def failed():
            raise OSError("keyboard unplugged")
        mouse = SimpleNamespace(id="mouse", label="Mouse")
        result = read_device_batch([mouse], failed, lambda _: Reading("Mouse", "", level=80))
        self.assertIn("keyboard unplugged", result[DEFAULT_DEVICE_ID].error)
        self.assertEqual(result["mouse"].display_level, "80%")

    def test_default_only_still_works(self):
        result = read_device_batch([], lambda: [Reading("Keyboard", "", level=0)], None)
        self.assertTrue(result[DEFAULT_DEVICE_ID].ok)
        self.assertEqual(len(result), 1)

    def panel(self, selected="mouse"):
        from my_devices import NexusPanel
        panel = NexusPanel.__new__(NexusPanel)
        panel.refresh_generation = 1
        panel.selected_device_id = selected
        panel.root, panel.refresh_button, panel.low_battery_notifier = Mock(), Mock(), Mock()
        panel.draw = Mock()
        return panel

    def test_overview_still_checks_keyboard_alert_when_mouse_is_selected(self):
        panel = self.panel()
        keyboard, mouse = Reading("Keyboard", "", level=9), Reading("Mouse", "", level=80)
        panel.finish_refresh({DEFAULT_DEVICE_ID: keyboard, "mouse": mouse}, 1)
        self.assertIs(panel.reading, mouse)
        panel.low_battery_notifier.check.assert_called_once_with(keyboard)

    def test_keyboard_failure_is_retained_when_another_device_succeeds(self):
        panel = self.panel(selected=DEFAULT_DEVICE_ID)
        keyboard, mouse = Reading("Keyboard", "", error="unplugged"), Reading("Mouse", "", level=80)
        panel.finish_refresh({DEFAULT_DEVICE_ID: keyboard, "mouse": mouse}, 1)
        self.assertIs(panel.reading, keyboard)
        panel.refresh_button.set_state.assert_called_once_with("success")


class GeometryTests(unittest.TestCase):
    def test_cards_stack_with_equal_margins_at_each_width(self):
        for width in (540, 640, 880):
            with self.subTest(width=width):
                cards = card_rectangles(width, 5)
                self.assertEqual(len(cards), 5)
                for left, _, right, _ in cards:
                    self.assertEqual(left, width - right)
                self.assertEqual([right[1] - left[3] for left, right in zip(cards, cards[1:])], [CARD_GAP] * 4)
                self.assertEqual(cards[-1][3] + CARD_PADDING, content_height(5))

    def test_overview_uses_less_space_when_devices_are_removed(self):
        self.assertLess(content_height(0), content_height(1))
        self.assertLess(content_height(1), content_height(2))
        self.assertLess(content_height(2), content_height(5))


class DisplayTransformTests(unittest.TestCase):
    def test_keyboard_rotation_preserves_pixels_and_changes_orientation(self):
        # 3x2 image, each pixel is a distinguishable RGB triplet.
        pixels = b"".join(bytes((i, i, i)) for i in range(1, 7))
        output = fit_ppm(b"P6\n3 2\n255\n" + pixels, 20, 20, clockwise=True)
        self.assertEqual(output, b"P6\n2 3\n255\n" + b"".join(bytes((i, i, i)) for i in (4, 1, 5, 2, 6, 3)))

    def test_resized_product_fits_the_requested_area(self):
        output = fit_ppm(b"P6\n8 4\n255\n" + bytes([120] * 8 * 4 * 3), 3, 5, clockwise=True)
        magic, dimensions, maximum, pixels = output.split(b"\n", 3)
        w, h = map(int, dimensions.split())
        self.assertLessEqual(w, 3)
        self.assertLessEqual(h, 5)
        self.assertGreater(h, w)
        self.assertEqual(len(pixels), w * h * 3)
        self.assertTrue(all(p == 120 for p in pixels))

    def test_invalid_display_pixels_are_rejected(self):
        with self.assertRaises(ValueError):
            fit_ppm(b"P6\n2 2\n255\n\0", 3, 3)


class ViewPreferenceTests(unittest.TestCase):
    def test_existing_install_defaults_to_all_devices(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "devices.json"
            path.write_text('{"version":1,"selected":"aula-f87-default","devices":[]}', encoding="utf-8")
            self.assertEqual(DeviceStore(path).view_mode(), "all")

    def test_view_preference_survives_device_changes(self):
        with tempfile.TemporaryDirectory() as folder:
            store = DeviceStore(Path(folder) / "devices.json")
            store.set_view_mode("single")
            receiver = SimpleNamespace(instance_id="USB\\VID_046D&PID_C539\\A", model="PRO WIRELESS",
                                       manufacturer="Logitech", hardware_id="046D:C539", category="鼠标", wireless=True)
            device_id, _ = store.add(receiver)
            self.assertEqual(store.view_mode(), "single")
            store.set_view_mode("all")
            store.select(DEFAULT_DEVICE_ID)
            self.assertEqual(store.view_mode(), "all")
            self.assertEqual(len(store.load()[0]), 1)
            store.remove(device_id)
            self.assertEqual(store.view_mode(), "all")


if __name__ == "__main__":
    unittest.main()
