from dataclasses import replace
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from device_readers import Reading, stamp_observation
from reading_ui import LiveItems, ReadingTimes, keep_newer
from ui_motion import DeviceTransition


def headphones(**parts):
    return Reading("Beats", "", connected=True, components={
        key: {"level": parts.get(key), "charging": False if parts.get(key) is not None else None}
        for key in ("left", "right", "case")})


class ObservationTests(unittest.TestCase):
    def test_success_and_zero_percent_are_timestamped_at_the_source(self):
        sample = stamp_observation(headphones(right=0, case=44), now=100)
        self.assertEqual(sample.observed_at, 100)
        self.assertEqual(sample.component_observed_at, {"right": 100, "case": 100})
        self.assertEqual(sample.components["right"]["level"], 0)

    def test_only_case_available_is_still_a_successful_component_read(self):
        sample = stamp_observation(headphones(case=99), now=100)
        self.assertFalse(sample.ok)
        self.assertEqual(sample.observed_at, 100)
        self.assertEqual(sample.component_observed_at, {"case": 100})

    def test_errors_and_missing_components_never_get_a_success_time(self):
        for sample in (headphones(), Reading("Mouse", "", error="timeout")):
            result = stamp_observation(sample, now=200)
            self.assertIsNone(result.observed_at)
            self.assertEqual(result.component_observed_at, {})
            self.assertEqual(result.checked_at, 200)

    def test_cached_mouse_reads_keep_the_original_source_timestamp(self):
        from logitech_live import BatteryWorker
        worker = BatteryWorker(SimpleNamespace(label="Mouse"), clock=lambda: 0)
        with patch("device_readers.time.time", return_value=100):
            worker.publish(Reading("Mouse", "", level=70))
        for _ in range(3):
            self.assertEqual(worker.cached().observed_at, 100)
        with patch("device_readers.time.time", return_value=200):
            worker.publish(Reading("Mouse", "", level=70))
        self.assertEqual(worker.cached().observed_at, 200)

    def test_beats_expiration_clears_current_times_and_percentages(self):
        from beats_direct import BatteryWorker
        now = [0]
        worker = BatteryWorker(SimpleNamespace(label="Beats"), clock=lambda: now[0])
        worker.publish(headphones(right=70, case=99))
        stamp = worker.cached().component_observed_at.copy()
        self.assertEqual(worker.cached().component_observed_at, stamp)
        now[0] = 13
        expired = worker.cached()
        self.assertIsNone(expired.observed_at)
        self.assertEqual(expired.component_observed_at, {})
        self.assertTrue(all(part["level"] is None for part in expired.components.values()))


class TimestampPresentationTests(unittest.TestCase):
    def test_unknown_part_retains_only_its_last_time_and_not_an_old_percentage(self):
        from my_devices import NexusPanel
        panel = NexusPanel.__new__(NexusPanel)
        panel.reading_times = ReadingTimes()
        first = stamp_observation(headphones(left=100, right=70, case=99), now=100)
        panel.reading_times.observe("beats", first)
        latest = stamp_observation(headphones(right=69), now=200)
        panel.reading_times.observe("beats", latest)
        styles = panel.reading_styles("beats", latest)
        self.assertEqual(styles["left_level"]["text"], "--")
        self.assertEqual(styles["case_level"]["text"], "--")
        self.assertTrue(styles["left_time"]["text"].startswith("上次 "))
        self.assertTrue(styles["right_time"]["text"].startswith("更新 "))
        self.assertEqual(panel.reading_times.last["beats"], {"device": 200, "left": 100, "right": 200, "case": 100})

    def test_new_device_shows_waiting_instead_of_fabricating_an_update_time(self):
        times = ReadingTimes()
        unknown = headphones()
        times.observe("beats", unknown)
        self.assertEqual(times.label("beats", unknown, "left"), "未上报")
        self.assertEqual(times.label("beats", unknown), "等待电量上报")

    def test_failure_keeps_last_success_time_and_cannot_replace_it_with_a_future_error(self):
        times = ReadingTimes()
        times.observe("mouse", stamp_observation(Reading("Mouse", "", level=70), now=100))
        failure = stamp_observation(Reading("Mouse", "", error="timeout"), now=300)
        times.observe("mouse", failure)
        self.assertTrue(times.label("mouse", failure).startswith("上次成功 "))
        self.assertEqual(times.last["mouse"]["device"], 100)

    def test_late_batches_cannot_replace_newer_live_mouse_replies(self):
        current = stamp_observation(Reading("Mouse", "", level=69, source="HID++ 实时"), now=200)
        late = stamp_observation(Reading("Mouse", "", level=70, source="HID++ 2.0"), now=100)
        self.assertTrue(keep_newer(current, late))
        failure = stamp_observation(Reading("Mouse", "", error="timeout", source="HID++ 实时"), now=300)
        self.assertFalse(keep_newer(current, failure))


class IncrementalUpdateTests(unittest.TestCase):
    def test_unchanged_values_make_no_canvas_calls(self):
        canvas, after = Mock(), Mock()
        live = LiveItems(after)
        live.link("mouse", "level", canvas, (7,), text="70%", fill="black")
        self.assertEqual(live.apply("mouse", {"level": {"text": "70%", "fill": "black"}}), 0)
        canvas.itemconfigure.assert_not_called()
        after.assert_not_called()

    def test_only_changed_device_item_is_updated_without_deleting_or_moving_items(self):
        canvas, after = Mock(), Mock()
        live = LiveItems(after)
        live.link("mouse", "level", canvas, (7,), text="70%", fill="black")
        live.link("beats", "level", canvas, (8,), text="60%", fill="black")
        self.assertEqual(live.apply("mouse", {"level": {"text": "69%", "fill": "black"}}), 1)
        canvas.itemconfigure.assert_called_once_with(7, text="69%")
        canvas.delete.assert_not_called()
        canvas.move.assert_not_called()
        after.assert_called_once_with(canvas, 7, text="69%")

    def test_next_transition_uses_the_updated_battery_image_and_color(self):
        canvas = Mock()
        canvas.type.side_effect = lambda item: "image" if item == 1 else "text"
        canvas.itemcget.side_effect = lambda item, _key: "old-image" if item == 1 else "#000000"
        motion = DeviceTransition(Mock(), Mock())
        motion.register(canvas, (1, 2))
        motion.update_style(canvas, 1, image="new-image")
        motion.update_style(canvas, 2, fill="#0f8e55")
        motion.paint()
        self.assertIn(unittest.mock.call(1, image="new-image"), canvas.itemconfigure.call_args_list)
        self.assertIn(unittest.mock.call(2, fill="#0f8e55"), canvas.itemconfigure.call_args_list)

    def test_active_transition_defers_patching_but_keeps_source_timestamps(self):
        from my_devices import NexusPanel
        panel = NexusPanel.__new__(NexusPanel)
        panel.live = Mock(bindings={"exists": 1})
        panel.reading_times = ReadingTimes()
        panel.readings = {"mouse": stamp_observation(Reading("Mouse", "", level=70), now=100)}
        panel.transition = SimpleNamespace(active=True)
        panel.draw, panel.update_runtime_display = Mock(), Mock()
        panel.refresh_readings()
        panel.live.apply.assert_not_called()
        panel.draw.assert_not_called()
        self.assertEqual(panel.reading_times.last["mouse"]["device"], 100)
