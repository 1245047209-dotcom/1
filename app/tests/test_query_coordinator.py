from contextlib import nullcontext
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from device_readers import Reading
import query_coordinator as qc


class QueryCoordinatorTests(unittest.TestCase):
    def test_followers_share_a_real_source_time_and_physical_devices_stay_separate(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(qc,"cache_directory",return_value=Path(directory)), \
                patch("hid_win.resource_lock",side_effect=lambda *_args:nullcontext()), patch.object(qc.time,"time",return_value=100) as now:
            first = Mock(return_value=Reading("Mouse","",level=70))
            a = qc.query_reading("physical-A","battery",first)
            now.return_value = 101
            follower = Mock()
            b = qc.query_reading("physical-A","battery",follower)
            follower.assert_not_called()
            self.assertEqual((a.level,b.level,a.observed_at,b.observed_at),(70,70,100,100))
            other = qc.query_reading("physical-B","battery",lambda:Reading("Other","",level=20))
            self.assertEqual(other.level,20)

    def test_a_failed_refresh_replaces_old_success_and_can_be_retried_quickly(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(qc,"cache_directory",return_value=Path(directory)), \
                patch("hid_win.resource_lock",side_effect=lambda *_args:nullcontext()), patch.object(qc.time,"time",return_value=100) as now:
            qc.query_reading("physical-A","battery",lambda:Reading("Mouse","",level=70))
            now.return_value = 103
            failed = qc.query_reading("physical-A","battery",lambda:Reading("Mouse","",error="timeout"))
            self.assertIsNone(failed.level)
            self.assertIsNone(failed.observed_at)
            now.return_value = 103.1
            self.assertFalse(qc.query_reading("physical-A","battery",Mock()).ok)
            now.return_value = 103.4
            recovered = qc.query_reading("physical-A","battery",lambda:Reading("Mouse","",level=69))
            self.assertEqual(recovered.level,69)
