import ctypes
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import beats_battery as beats
from device_readers import Reading


def device(key="beats", address="02:00:00:00:00:01", model="测试的Beats Studio Buds", transport="bluetooth"):
    return SimpleNamespace(id=key, hardware_id=address, model=model, label=model, transport=transport)


def frame(payload=bytes((100, 0xc7, 55)), group=3, code=3):
    return bytes((group, code)) + len(payload).to_bytes(2, "big") + payload


class DecodingTests(unittest.TestCase):
    def test_case_charge_flag_updates_without_changing_its_percentage_or_ear_flags(self):
        before = beats.battery_reading("Beats", bytes((0x7f, 80, 44)))
        during = beats.battery_reading("Beats", bytes((0x7f, 80, 44 | 0x80)))
        after = beats.battery_reading("Beats", bytes((0x7f, 80, 44)))
        self.assertEqual(before.components["case"]["level"], during.components["case"]["level"])
        self.assertTrue(during.components["case"]["charging"])
        self.assertFalse(after.components["case"]["charging"])
        self.assertFalse(during.components["right"]["charging"])

    def test_real_three_component_sample_and_charging_are_not_windows_combined_battery(self):
        reading = beats.battery_reading("Beats", bytes((100, 0xc7, 55)))
        self.assertEqual(reading.components, {"left": {"level": 100, "charging": False},
                                             "right": {"level": 71, "charging": True},
                                             "case": {"level": 55, "charging": False}})
        self.assertEqual(reading.level, 71)
        self.assertFalse(reading.estimated)

    def test_unknown_is_not_zero_or_charging_even_with_high_bit_set(self):
        reading = beats.battery_reading("Beats", bytes((0xff, 72, 0x7f)))
        self.assertEqual(reading.components["left"], {"level": None, "charging": None})
        self.assertIsNone(reading.components["case"]["level"])
        self.assertEqual(reading.level, 72)

    def test_empty_battery_and_unknown_ears_are_distinct(self):
        reading = beats.battery_reading("Beats", bytes((0x80, 1, 0)))
        self.assertEqual(reading.components["left"], {"level": 0, "charging": True})
        self.assertEqual(reading.level, 0)
        self.assertTrue(reading.ok)
        case_only = beats.battery_reading("Beats", bytes((0x7f, 0xff, 55)))
        self.assertIsNone(case_only.level)
        self.assertEqual(case_only.components["case"]["level"], 55)

    def test_malformed_and_invalid_percentages_are_rejected(self):
        for payload in (b"", b"\x64\x64", b"\x64\x64\x64\x64", bytes((101, 50, 55)), bytes((50, 126, 55))):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                beats.battery_reading("Beats", payload)

    def test_windows_bluetooth_address_structure_matches_sdk_packing(self):
        self.assertEqual(ctypes.sizeof(beats.BluetoothAddress), 30)


class FramingTests(unittest.TestCase):
    def test_fragmented_header_and_payload_and_multiple_frames(self):
        parser = beats.FrameBuffer()
        packet = frame()
        self.assertEqual(parser.feed(packet[:2]), [])
        self.assertEqual(parser.feed(packet[2:5]), [])
        frames = parser.feed(packet[5:] + frame(b"abc", code=1))
        self.assertEqual(frames, [(3, 3, bytes((100, 0xc7, 55))), (3, 1, b"abc")])

    def test_oversized_length_and_buffer_do_not_allocate_unbounded_data(self):
        with self.assertRaises(ValueError):
            beats.FrameBuffer().feed(bytes((3, 3, 0xff, 0xff)))
        with self.assertRaises(ValueError):
            beats.FrameBuffer().feed(bytes(8193))


class WorkerTests(unittest.TestCase):
    def test_worker_listens_without_sending_commands_and_ignores_non_battery_frames(self):
        channel = Mock()
        channel.__enter__, channel.__exit__ = Mock(return_value=channel), Mock(return_value=False)
        worker = beats.BatteryWorker(device(), channel_factory=Mock(return_value=channel))
        packets = [frame(b"abc", code=1) + frame()[:3], frame()[3:]]
        def receive(_size):
            if packets:
                return packets.pop(0)
            worker.stop_event.set()
            raise TimeoutError()
        channel.recv.side_effect = receive
        worker.session()
        self.assertEqual(worker.snapshot.components["right"]["level"], 71)
        channel.send.assert_not_called()
        channel.sendall.assert_not_called()

    def test_disconnection_replaces_previous_numbers_with_unknown(self):
        channel = Mock()
        channel.__enter__, channel.__exit__ = Mock(return_value=channel), Mock(return_value=False)
        channel.recv.side_effect = [frame(), b""]
        worker = beats.BatteryWorker(device(), channel_factory=Mock(return_value=channel))
        with patch.object(worker.stop_event, "wait", side_effect=lambda _: worker.stop_event.set()):
            worker.run()
        self.assertIsNone(worker.snapshot.level)
        self.assertTrue(worker.snapshot.error)
        self.assertTrue(all(part["level"] is None for part in worker.snapshot.components.values()))

    def test_cached_components_are_copied_and_stop_clears_them(self):
        worker = beats.BatteryWorker(device())
        worker.publish(beats.battery_reading("Beats", bytes((100, 71, 55))))
        copy = worker.cached()
        copy.components["left"]["level"] = 1
        self.assertEqual(worker.cached().components["left"]["level"], 100)
        worker.stop_event.set()
        self.assertIsNone(worker.cached())


class BindingTests(unittest.TestCase):
    def test_original_model_only_and_pairing_address_are_required(self):
        self.assertTrue(beats.supported(device()))
        self.assertFalse(beats.supported(device(model="Beats Studio Buds +")))
        self.assertFalse(beats.supported(device(model="AirPods Pro")))
        self.assertFalse(beats.supported(device(address="invalid")))
        self.assertFalse(beats.supported(device(transport="usb")))

    def test_other_same_model_cannot_supply_saved_devices_numbers(self):
        monitor = beats.BeatsMonitor(worker_factory=lambda target: Mock(device=target))
        monitor.set_devices([device()])
        old = monitor.workers["beats"]
        replacement = device(address="AA:BB:CC:DD:EE:FF")
        self.assertIsNone(monitor.cached_reading(replacement))
        monitor.set_devices([replacement])
        old.stop_event.set.assert_called_once()
        monitor.set_devices([])
        self.assertIsNone(monitor.cached_reading(replacement))
        monitor.close()
        monitor.set_devices([device()])
        self.assertEqual(monitor.workers, {})


class PanelTests(unittest.TestCase):
    def test_case_charge_icon_and_summary_do_not_mark_an_ear_as_charging(self):
        import my_devices
        reading = beats.battery_reading("Beats", bytes((0x7f, 80, 0xac)))
        canvas = Mock()
        canvas.tk.call.return_value = 30
        with patch.object(my_devices, "battery_icon") as icon:
            my_devices.draw_component_batteries(canvas, 0, 0, reading.components)
        self.assertEqual([call.args[-1] for call in icon.call_args_list], [False, False, True])
        texts = [call.kwargs["text"] for call in canvas.create_text.call_args_list]
        self.assertNotIn("充电", texts)
        self.assertEqual(my_devices.component_charging_text(reading.components), "充电仓充电中")

    def test_unknown_charge_status_does_not_create_a_charging_badge(self):
        import my_devices
        self.assertEqual(my_devices.component_charging_text(None), "")
        self.assertEqual(my_devices.component_charging_text(beats.unknown_components()), "")

    def test_missing_independent_values_never_copy_windows_combined_percentage(self):
        import my_devices
        canvas = Mock()
        with patch.object(my_devices, "battery_icon"):
            my_devices.draw_component_batteries(canvas, 0, 0, None)
        values = [call.kwargs["text"] for call in canvas.create_text.call_args_list]
        self.assertEqual(values, ["左耳", "--", "右耳", "--", "充电仓", "--"])

    def test_refresh_does_not_replace_direct_components_with_windows_single_number(self):
        from my_devices import NexusPanel
        panel = NexusPanel.__new__(NexusPanel)
        panel.refresh_generation, panel.selected_device_id, panel.view_mode = 1, "beats", "single"
        panel.root, panel.refresh_button, panel.low_battery_notifier, panel.draw = Mock(), Mock(), Mock(), Mock()
        panel.saved_devices = [device()]
        panel.beats_monitor = Mock()
        direct = beats.battery_reading("Beats", bytes((100, 71, 55)))
        panel.beats_monitor.cached_reading.return_value = direct
        panel.finish_refresh({"beats": Reading("Beats", "", level=100, connected=True, source="Windows 蓝牙")}, 1)
        self.assertIs(panel.reading, direct)
        self.assertEqual(panel.reading.components["case"]["level"], 55)


if __name__ == "__main__":
    unittest.main()
