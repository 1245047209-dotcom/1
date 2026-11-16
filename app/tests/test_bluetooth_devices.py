from pathlib import Path
import queue
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

import bluetooth_devices as bd
from receiver_scan import DeviceNode
from saved_devices import DEFAULT_DEVICE_ID, DeviceStore, read_saved_device

ADDRESS = "AABBCCDDEEFF"
ROOT = r"BTHENUM\DEV_AABBCCDDEEFF\ROOT"


def row(**kwargs):
    return {"id": "Bluetooth#adapter-a-aa:bb:cc:dd:ee:ff", "name": "Test Buds", "paired": True,
            "address": "aa:bb:cc:dd:ee:ff", "connected": True, "battery": 30, **kwargs}


def peripheral(**kwargs):
    return bd.normalize_devices([row(**kwargs)], {})[0]


class BluetoothDiscoveryTests(unittest.TestCase):
    def test_duplicate_pairings_from_two_radios_are_one_device(self):
        devices = bd.normalize_devices([row(connected=False, battery=90), row(id="other-radio", battery=30)], {})
        self.assertEqual(len(devices), 1)
        self.assertEqual((devices[0].connected, devices[0].battery), (True, 30))
        self.assertEqual(devices[0].instance_id, "BT\\" + ADDRESS)

    def test_identical_names_with_distinct_addresses_are_not_merged(self):
        devices = bd.normalize_devices([row(), row(address="11:22:33:44:55:66")], {})
        self.assertEqual(len(devices), 2)
        self.assertNotEqual(devices[0].instance_id, devices[1].instance_id)

    def test_disconnected_or_unknown_connection_hides_stale_battery(self):
        for connected in (False, None):
            with self.subTest(connected=connected):
                device = bd.normalize_devices([row(connected=connected, battery=90)], {ADDRESS: 90})[0]
                self.assertIsNone(device.battery)

    def test_unpaired_devices_and_invalid_addresses_are_not_imported(self):
        self.assertEqual(bd.normalize_devices([row(paired=False), row(address="invalid")], {}), [])

    def test_unknown_or_out_of_range_battery_is_not_fabricated(self):
        for value in (None, -1, 101, True, "30", float("nan")):
            self.assertIsNone(peripheral(battery=value).battery)

    def test_zero_percent_is_a_valid_current_reading(self):
        self.assertEqual(peripheral(battery=0).battery, 0)

    def test_native_windows_battery_fills_missing_aep_value(self):
        self.assertEqual(bd.normalize_devices([row(battery=None)], {ADDRESS: 30})[0].battery, 30)

    def test_pnp_battery_is_grouped_by_bluetooth_parent_not_name(self):
        root = DeviceNode(ROOT)
        profile = DeviceNode(r"BTHHFENUM\HFP\INSTANCE", ROOT, battery_level=30)
        unrelated = DeviceNode(r"USB\VID_1234&PID_5678\DEVICE", battery_level=90)
        self.assertEqual(bd.pnp_batteries([root, profile, unrelated]), {ADDRESS: 30})

    def test_conflicting_component_batteries_are_not_averaged(self):
        root = DeviceNode(ROOT, battery_level=30)
        profile = DeviceNode(r"BTHHFENUM\HFP\INSTANCE", ROOT, battery_level=80)
        self.assertEqual(bd.pnp_batteries([root, profile]), {})

    def test_sibling_audio_profile_address_is_recognized(self):
        node = DeviceNode(r"BTHENUM\{0000111E-SERVICE}\7&ABC&0&AABBCCDDEEFF_C00000000", battery_level=30)
        self.assertEqual(bd.pnp_batteries([node]), {ADDRESS: 30})

    def test_cache_prevents_a_powershell_process_per_added_device(self):
        with patch.object(bd, "_CACHE_AT", 0), patch.object(bd, "_CACHE", []), \
                patch.object(bd, "query_windows_bluetooth", return_value=[row()]) as query, \
                patch.object(bd, "enumerate_present_nodes", return_value=[]):
            bd.scan_bluetooth()
            bd.scan_bluetooth()
            bd.scan_bluetooth(force=True)
        self.assertEqual(query.call_count, 2)

    def test_windows_query_stays_hidden_and_times_out(self):
        completed = subprocess.CompletedProcess([], 0, stdout=b"[]", stderr=b"")
        with patch.object(bd.subprocess, "run", return_value=completed) as run:
            self.assertEqual(bd.query_windows_bluetooth(), [])
        self.assertNotIn("-ExecutionPolicy", run.call_args.args[0])
        self.assertEqual(run.call_args.kwargs["timeout"], 11)
        self.assertNotIn("shell", run.call_args.kwargs)


class BluetoothMembershipTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = DeviceStore(Path(self.temp.name) / "devices.json")

    def saved(self, device=None):
        self.store.add(device or peripheral())
        return self.store.load()[0][0]

    def test_bluetooth_device_survives_reopening_and_duplicate_add(self):
        device = peripheral()
        device_id, created = self.store.add(device)
        self.assertTrue(created)
        self.assertEqual(self.store.add(device), (device_id, False))
        saved, selected = self.store.load()
        self.assertEqual(selected, device_id)
        self.assertEqual(saved[0].transport, "bluetooth")
        self.assertEqual(saved[0].hardware_id, "AA:BB:CC:DD:EE:FF")

    def test_bluetooth_aula_does_not_replace_default_24g_device(self):
        device_id, _ = self.store.add(peripheral(name="AULA-F87Pro 5.0"))
        self.assertNotEqual(device_id, DEFAULT_DEVICE_ID)
        self.assertEqual(self.store.load()[0][0].transport, "bluetooth")

    def test_old_usb_registry_remains_compatible(self):
        self.store.path.write_text('{"version":1,"devices":[{"id":"old","instance_id":"USB\\\\A",'
                                   '"model":"Mouse","manufacturer":"Logitech","hardware_id":"046D:C539",'
                                   '"category":"鼠标"}],"selected":"old"}', encoding="utf-8")
        self.assertEqual(self.store.load()[0][0].transport, "usb")

    def test_bluetooth_routes_to_windows_reader_instead_of_usb_scan(self):
        saved = self.saved()
        with patch.object(bd, "scan_bluetooth", return_value=[peripheral()]), \
                patch("receiver_scan.scan_receivers") as usb:
            reading = read_saved_device(saved)
        usb.assert_not_called()
        self.assertTrue(reading.ok)
        self.assertEqual(reading.display_level, "30%")
        self.assertEqual(reading.source, "Windows 蓝牙")
        self.assertIsNone(reading.remaining_hours)

    def test_disconnection_clears_previous_percentage_without_removing_device(self):
        saved = self.saved()
        with patch.object(bd, "scan_bluetooth", return_value=[peripheral(connected=False, battery=90)]):
            reading = read_saved_device(saved)
        self.assertFalse(reading.connected)
        self.assertIsNone(reading.level)
        self.assertEqual(len(self.store.load()[0]), 1)

    def test_connected_device_without_battery_reports_unsupported_status(self):
        saved = self.saved()
        with patch.object(bd, "scan_bluetooth", return_value=[peripheral(battery=None)]):
            reading = read_saved_device(saved)
        self.assertTrue(reading.connected)
        self.assertFalse(reading.ok)
        self.assertEqual(reading.status, "未上报电量")

    def test_query_failure_is_not_reported_as_definite_disconnection(self):
        saved = self.saved()
        with patch.object(bd, "scan_bluetooth", side_effect=bd.BluetoothError("query unavailable")):
            self.assertIsNone(read_saved_device(saved).connected)


class BluetoothPanelTests(unittest.TestCase):
    def test_transport_labels_and_product_images_are_distinct(self):
        from my_devices import connection_text, product_kind
        self.assertEqual(connection_text(peripheral()), "蓝牙")
        self.assertIsNone(product_kind(peripheral()))
        self.assertEqual(product_kind(peripheral(name="AULA-F87Pro 5.0")), "keyboard")
        self.assertEqual(connection_text(None), "2.4G 无线")

    def test_switching_search_tabs_discards_the_previous_tabs_result(self):
        from receiver_search import ReceiverSearch
        search = ReceiverSearch.__new__(ReceiverSearch)
        search.closed, search.busy = False, True
        search.results = queue.Queue()
        search.results.put(("usb", [], None))
        search.mode = Mock()
        search.mode.get.return_value = "bluetooth"
        search.window, search.search, search.render = Mock(), Mock(), Mock()
        search.pump()
        search.search.assert_called_once()
        search.render.assert_not_called()


if __name__ == "__main__":
    unittest.main()
