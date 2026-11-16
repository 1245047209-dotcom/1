"""Identification regressions: physical grouping and conservative model claims."""
from types import SimpleNamespace
import unittest

from receiver_scan import DeviceNode as Node, identify_receivers


def root(serial="A", name="USB Receiver", vidpid="VID_046D&PID_C539", cls="USB"):
    return Node(f"USB\\{vidpid}\\{serial}", name=name, class_name=cls)


def hid_node(device, suffix="0", product="USB Receiver", maker="Logitech", usage=2):
    middle = device.instance_id.split("\\")[1] + "&MI_01&COL0" + suffix
    iid = "HID\\" + middle + "\\" + device.instance_id.split("\\")[2] + suffix
    node = Node(iid, device.instance_id, name=product, class_name="HIDClass")
    iface = SimpleNamespace(path="\\\\?\\hid#" + middle + "#" + iid.split("\\")[2] + "#{guid}",
                            product=product, manufacturer=maker, usage_page=1, usage=usage)
    return node, iface


class IdentificationTests(unittest.TestCase):
    def test_named_driver_child_supplies_model_and_merges_interfaces(self):
        device = root()
        mouse, hi = hid_node(device)
        keyboard, hk = hid_node(device, "1", usage=6)
        lamp = Node("USB\\LAMPARRAY\\A", device.instance_id, name="PRO WIRELESS")
        rows = identify_receivers([device, mouse, keyboard, lamp], [hi, hk])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].model, "PRO WIRELESS")
        self.assertEqual(rows[0].interface_count, 2)
        self.assertTrue(rows[0].wireless)

    def test_identical_receivers_on_different_ports_are_not_merged(self):
        a, b = root("A"), root("B")
        na, ha = hid_node(a)
        nb, hb = hid_node(b)
        rows = identify_receivers([a, b, na, nb], [ha, hb])
        self.assertEqual(len(rows), 2)
        self.assertNotEqual(rows[0].instance_id, rows[1].instance_id)

    def test_virtual_hid_is_not_a_physical_receiver(self):
        virtual = Node("HID\\GVINPUT\\A", "ROOT\\VIRTUAL\\A", name="Wireless Virtual Mouse", class_name="HIDClass")
        self.assertEqual(identify_receivers([virtual], []), [])

    def test_bluetooth_adapter_is_excluded(self):
        radio = root(name="Wireless Bluetooth Adapter", cls="Bluetooth")
        child, iface = hid_node(radio)
        self.assertEqual(identify_receivers([radio, child], [iface]), [])

    def test_network_radio_is_excluded(self):
        radio = root(name="2.4G Wireless Adapter", cls="Net")
        child, iface = hid_node(radio)
        self.assertEqual(identify_receivers([radio, child], [iface]), [])

    def test_usb_hubs_are_not_shown(self):
        self.assertEqual(identify_receivers([Node("USB\\ROOT_HUB30\\A", name="USB Hub", class_name="USB")], []), [])

    def test_generic_wired_peripheral_does_not_become_wireless(self):
        device = root(name="USB Optical Mouse")
        child, iface = hid_node(device, product="USB Optical Mouse")
        row = identify_receivers([device, child], [iface])[0]
        self.assertFalse(row.wireless)
        self.assertIn("待确认", row.identification)

    def test_generic_receiver_does_not_invent_paired_model(self):
        device = root()
        child, iface = hid_node(device)
        row = identify_receivers([device, child], [iface])[0]
        self.assertEqual(row.model, "USB Receiver")
        self.assertTrue(row.wireless)
        self.assertNotIn("2.4G", row.identification)

    def test_generic_input_names_do_not_hide_receiver_name(self):
        device = root()
        child, iface = hid_node(device, product="USB Input Device")
        row = identify_receivers([device, child], [iface])[0]
        self.assertEqual(row.model, "USB Receiver")

    def test_known_aula_receiver_can_be_recognized_without_named_driver(self):
        device = root(name="USB Input Device", vidpid="VID_3554&PID_FA09")
        child, iface = hid_node(device, product="USB Input Device", maker="")
        row = identify_receivers([device, child], [iface])[0]
        self.assertEqual(row.model, "AULA F87 Pro")
        self.assertTrue(row.wireless)

    def test_audio_receiver_without_hid_is_included(self):
        device = root(name="Headset Wireless Dongle", cls="USBDevice")
        child = Node("USB\\AUDIO\\A", device.instance_id, name="Wireless Headset", class_name="MEDIA")
        row = identify_receivers([device, child], [])[0]
        self.assertTrue(row.wireless)
        self.assertEqual(row.category, "音频")

    def test_broken_or_cyclic_parent_graph_terminates(self):
        a = Node("HID\\A\\1", "HID\\B\\1", name="Wireless HID", class_name="HIDClass")
        b = Node("HID\\B\\1", a.instance_id, name="Wireless HID", class_name="HIDClass")
        self.assertEqual(identify_receivers([a, b], []), [])

    def test_device_ids_and_parent_matching_ignore_case(self):
        device = root()
        child, iface = hid_node(device)
        child.parent_id = child.parent_id.lower()
        row = identify_receivers([device, child], [iface])[0]
        self.assertEqual(row.interface_count, 1)


if __name__ == "__main__":
    unittest.main()
