from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from battery_runtime import HISTORY_AGE, MAX_GAP, UsageEstimator, format_remaining, record_sample, get_runtime_view
from keyboard_activity import ActivityClock, get_snapshot, is_target_device
import device_readers
from device_readers import KEYBOARD_TITLE, Reading

START = 1_700_000_000
SAMPLES = [(0, 100), (600, 99), (2400, 98), (4200, 97)]


def activity(at, *, active=True, session="test"):
    return {"session": session, "active_seconds": max(0, at - START), "active": active}


class AllActiveEstimator(UsageEstimator):
    def observe(self, level, charging=False, now=None, **kwargs):
        kwargs.setdefault("activity", activity(now))
        return super().observe(level, charging, now, **kwargs)


def tracked_sample(level, **kwargs):
    kwargs.setdefault("activity", activity(kwargs["now"]))
    return record_sample(level, **kwargs)


class EstimationTests(unittest.TestCase):
    def calibrated(self):
        estimator = AllActiveEstimator()
        for offset, level in SAMPLES:
            hours = estimator.observe(level, now=START + offset)
        self.assertAlmostEqual(hours, 48.5)
        return estimator

    def test_excludes_initial_partial_percentage(self):
        estimator = AllActiveEstimator()
        self.assertIsNone(estimator.observe(100, now=START))
        self.assertIsNone(estimator.observe(99, now=START + 600))
        self.assertIsNone(estimator.observe(98, now=START + 2400))
        self.assertAlmostEqual(estimator.observe(97, now=START + 4200), 48.5)

    def test_fast_percentage_changes_do_not_create_an_early_estimate(self):
        estimator = AllActiveEstimator()
        for offset, level in [(0, 100), (60, 99), (120, 98), (180, 97)]:
            self.assertIsNone(estimator.observe(level, now=START + offset))

    def test_repeated_polling_and_duplicate_timestamps_do_not_change_rate(self):
        estimator = self.calibrated()
        for offset in [4200, 4230, 4260, 4290]:
            self.assertAlmostEqual(estimator.observe(97, now=START + offset), 48.5)
        self.assertEqual(len(estimator.intervals), 2)

    def test_charging_resets_the_anchor_but_retains_valid_learned_rate(self):
        estimator = self.calibrated()
        self.assertIsNone(estimator.observe(None, charging=True, now=START + 4300))
        self.assertIsNone(estimator.boundary)
        self.assertAlmostEqual(estimator.observe(100, now=START + 4400), 50)
        self.assertEqual(len(estimator.intervals), 2)

    def test_long_gap_is_not_counted_as_discharge_time(self):
        estimator = self.calibrated()
        resumed = START + 4200 + MAX_GAP + 1
        estimator.observe(90, now=resumed, activity=activity(resumed, session="resumed"))
        estimator.observe(89, now=resumed + 1800, activity=activity(resumed + 1800, session="resumed"))
        self.assertEqual(len(estimator.intervals), 2)
        estimator.observe(88, now=resumed + 3600, activity=activity(resumed + 3600, session="resumed"))
        self.assertEqual(estimator.intervals[-1]["seconds"], 1800)
        self.assertEqual(estimator.intervals[-1]["drop"], 1)

    def test_increases_and_large_jumps_are_not_used_to_learn_a_rate(self):
        estimator = AllActiveEstimator()
        for offset, level in [(0, 100), (600, 99), (1200, 100),
                              (1800, 99), (2400, 60)]:
            self.assertIsNone(estimator.observe(level, now=START + offset))
        self.assertEqual(estimator.intervals, [])
        self.assertIsNone(estimator.observe(59, now=START + 6000))
        self.assertAlmostEqual(estimator.observe(58, now=START + 9600), 58)

    def test_stale_history_and_clock_rollback_do_not_produce_bad_estimates(self):
        estimator = self.calibrated()
        self.assertIsNone(estimator.observe(96, now=START + 4200 + HISTORY_AGE + 1))
        estimator = self.calibrated()
        self.assertIsNone(estimator.observe(96, now=START - 1))

    def test_malformed_history_is_safely_ignored(self):
        for state in [None, [], {"version": 1, "previous": 42, "intervals": "bad"},
                      {"version": 1, "intervals": [None, {}, {"at": START, "seconds": 1e308, "drop": 2}]}]:
            self.assertIsNone(UsageEstimator(state).observe(97, now=START))

    def test_formats_as_approximate_coarse_durations(self):
        self.assertEqual(format_remaining(None), "正在估算…")
        self.assertEqual(format_remaining(0), "电量即将耗尽")
        self.assertEqual(format_remaining(0.2), "约 10 分钟")
        self.assertEqual(format_remaining(3.7), "约 3.5 小时")
        self.assertEqual(format_remaining(72), "约 3 天")


class PersistenceTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name) / "battery_usage.json"

    def learn(self):
        for offset, level in SAMPLES:
            hours = tracked_sample(level, path=self.path, now=START + offset)
        self.assertAlmostEqual(hours, 48.5)

    def test_restarts_reload_the_learned_rate(self):
        self.learn()
        self.assertAlmostEqual(tracked_sample(96, path=self.path, now=START + 6000), 48)
        self.assertEqual(len(json.loads(self.path.read_text())["intervals"]), 3)

    def test_concurrent_writers_keep_history_valid(self):
        self.learn()
        def sample(_):
            return tracked_sample(97, path=self.path, now=START + 4500)
        with ThreadPoolExecutor(max_workers=8) as pool:
            self.assertEqual(list(pool.map(sample, range(24))), [48.5] * 24)
        self.assertEqual(len(json.loads(self.path.read_text())["intervals"]), 2)
        self.assertFalse(self.path.with_suffix(".json.tmp").exists())

    def test_missing_replies_do_not_write_false_measurements(self):
        self.learn()
        original = self.path.read_bytes()
        self.assertIsNone(tracked_sample(None, valid=False, path=self.path, now=START + 4300))
        self.assertEqual(original, self.path.read_bytes())

    def test_corrupt_files_recover_and_failed_writes_preserve_existing_data(self):
        self.path.write_text("{broken", encoding="utf-8")
        self.assertIsNone(tracked_sample(97, path=self.path, now=START))
        original = self.path.read_bytes()
        with patch("battery_runtime.os.replace", side_effect=OSError("disk unavailable")):
            self.assertIsNone(tracked_sample(96, path=self.path, now=START + 1800))
        self.assertEqual(original, self.path.read_bytes())
        self.assertFalse(self.path.with_suffix(".json.tmp").exists())


class ReaderIntegrationTests(unittest.TestCase):
    def test_panel_and_tray_share_the_recording_path(self):
        for reading, charging in [(Reading(KEYBOARD_TITLE, "", level=97), False),
                                  (Reading(KEYBOARD_TITLE, "", status="充电中"), True),
                                  (Reading(KEYBOARD_TITLE, "", error="offline"), False)]:
            with self.subTest(charging=charging, error=reading.error):
                with patch.object(device_readers.hw, "enumerate_hid", return_value=[]), \
                     patch.object(device_readers, "_read_keyboard", return_value=reading), \
                     patch.object(device_readers, "record_sample", return_value=48.5) as record:
                    self.assertIs(device_readers.read_all()[0], reading)
                    record.assert_called_once_with(reading.level, charging=charging, valid=reading.ok)
                    self.assertEqual(reading.remaining_hours, 48.5)


class ActivityTests(unittest.TestCase):
    def test_idle_time_does_not_increase_active_seconds(self):
        clock = ActivityClock(now=0)
        clock.note_event(0)
        for now in range(1, 121):
            snapshot = clock.snapshot(now)
        self.assertEqual(snapshot["active_seconds"], 60)
        self.assertFalse(snapshot["active"])
        clock.note_event(120)
        for now in range(121, 151):
            snapshot = clock.snapshot(now)
        self.assertEqual(snapshot["active_seconds"], 90)
        self.assertTrue(snapshot["active"])

    def test_suspend_gap_breaks_the_activity_session(self):
        clock = ActivityClock(now=0)
        clock.note_event(0)
        first = clock.snapshot(1)
        resumed = clock.snapshot(100)
        self.assertNotEqual(first["session"], resumed["session"])
        self.assertEqual(resumed["active_seconds"], 0)
        self.assertFalse(resumed["active"])

    def test_only_the_aula_receiver_matches(self):
        self.assertTrue(is_target_device(r"HID#vid_3554&pid_fa09&mi_00"))
        self.assertFalse(is_target_device("HID#HID_DEVICE_SYSTEM_VHF"))
        self.assertFalse(is_target_device("HID#VID_3554&PID_1234"))

    def test_stale_or_unavailable_heartbeat_is_not_reported_as_idle(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "activity.json"
            data = activity(START)
            data.update(at=START, available=True)
            path.write_text(json.dumps(data))
            self.assertIsNotNone(get_snapshot(path=path, now=START + 1))
            self.assertIsNone(get_snapshot(path=path, now=START + 10))
            data["available"] = False
            path.write_text(json.dumps(data))
            self.assertIsNone(get_snapshot(path=path, now=START + 1))


class LiveProjectionTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name) / "usage.json"
        self.estimator = AllActiveEstimator()
        for offset, level in SAMPLES:
            self.estimator.observe(level, now=START + offset)
        self.save()

    def save(self):
        self.path.write_text(json.dumps(self.estimator.to_dict()))

    def view(self, at, counter, active=False):
        snapshot = {"session": "test", "active_seconds": counter, "active": active}
        return get_runtime_view(97, path=self.path, now=at, activity=snapshot)

    def test_idle_pauses_active_usage_and_typing_resumes_projection(self):
        first = self.view(START + 4260, 4200)
        later_idle = self.view(START + 4320, 4200)
        typing = self.view(START + 4380, 4260, True)
        self.assertEqual(first["hours"], later_idle["hours"])
        self.assertAlmostEqual(typing["hours"], first["hours"] - 1/60)
        self.assertIn("闲置", later_idle["hint"])
        self.assertIn("使用中", typing["hint"])

    def test_repeated_integer_percentage_reports_do_not_rewind_projection(self):
        self.estimator.observe(97, now=START + 4500)
        self.save()
        self.assertEqual(self.estimator.projection_anchor["at"], START + 4200)
        self.assertAlmostEqual(self.view(START + 4500, 4500, True)["hours"], 48.5 - 300/3600)

    def test_projection_stays_within_one_unreported_percentage(self):
        self.assertAlmostEqual(self.view(START + 7800, 7800, True)["hours"], (97 - 0.99)/2)

    def test_session_changes_do_not_bridge_unknown_activity(self):
        self.estimator.observe(96, now=START + 4800,
                               activity={"session": "new", "active_seconds": 10})
        self.assertEqual(len(self.estimator.intervals), 2)

    def test_continuously_observed_long_idle_is_not_mistaken_for_sleep(self):
        self.estimator.observe(96, now=START + 11400,
                               activity={"session": "test", "active_seconds": 4200})
        self.assertEqual(len(self.estimator.intervals), 3)
        self.assertEqual(self.estimator.intervals[-1]["seconds"], 7200)
        self.assertEqual(self.estimator.intervals[-1]["active_seconds"], 0)

    def test_mixed_usage_identifies_standby_and_active_power(self):
        state = {"version": 2, "intervals": [
            {"at": START + 3600, "seconds": 3600, "drop": 2, "active_seconds": 1200},
            {"at": START + 7200, "seconds": 3600, "drop": 3, "active_seconds": 2400},
            {"at": START + 10800, "seconds": 3600, "drop": 1, "active_seconds": 0}]}
        rates = UsageEstimator(state).usage_rates(START + 10800)
        self.assertAlmostEqual(rates["active_rate"], 4)
        self.assertAlmostEqual(rates["idle_rate"], 1)

    def test_learned_standby_consumption_is_deducted_separately(self):
        anchor = {"at": START + 7200, "level": 97, "session": "test", "active_seconds": 3600}
        state = {"version": 2, "projection_anchor": anchor, "intervals": [
            {"at": START + 3600, "seconds": 3600, "drop": 2, "active_seconds": 1200},
            {"at": START + 7200, "seconds": 3600, "drop": 3, "active_seconds": 2400}]}
        self.path.write_text(json.dumps(state))
        view = self.view(START + 9000, 3600, False)
        # Half an hour at 1%/hour idle consumes 0.5%, not 2% active power.
        self.assertAlmostEqual(view["hours"], 96.5 / 4)

    def test_identical_mixed_duty_cycles_or_idle_only_do_not_invent_active_power(self):
        for active in (0, 1800):
            state = {"version": 2, "intervals": [
                {"at": START + 3600, "seconds": 3600, "drop": 1, "active_seconds": active},
                {"at": START + 7200, "seconds": 3600, "drop": 1, "active_seconds": active}]}
            self.assertIsNone(UsageEstimator(state).usage_rates(START + 7200))

    def test_legacy_records_remain_but_are_not_misclassified_as_active(self):
        state = {"version": 1, "intervals": [
            {"at": START + 3600, "seconds": 3600, "drop": 2}]}
        estimator = UsageEstimator(state)
        self.assertEqual(len(estimator.intervals), 1)
        self.assertIsNone(estimator.usage_rates(START + 3600))

    def test_live_format_has_no_false_second_precision(self):
        self.assertEqual(format_remaining(3.7, live=True), "约 3 小时 40 分钟")


if __name__ == "__main__":
    unittest.main()
