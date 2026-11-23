"""v73-iOS: battery widget + grouped list (pure logic, no Tk window)."""
from types import SimpleNamespace
import struct
import unittest

from device_layout import IOS_EMPTY, IOS_RING_COLUMNS, IOS_ROW, ios_layout
from device_readers import Reading
from ios_art import ring_png, rounded_surface_png
from device_readers import stamp_observation
from reading_ui import ReadingTimes, relative_time


def png_size(data):
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    return struct.unpack("!II", data[16:24])


def buds(left=None, right=None, case=None, charging=()):
    return Reading("Beats", "", connected=True, components={
        key: {"level": value, "charging": key in charging}
        for key, value in (("left", left), ("right", right), ("case", case))})


class LayoutTests(unittest.TestCase):
    def test_empty_list_has_a_placeholder_only(self):
        layout = ios_layout(640, 0, 0)
        self.assertEqual(layout["height"], IOS_EMPTY)
        self.assertEqual(layout["rows"], [])

    def test_rows_are_contiguous_and_inside_the_group(self):
        layout = ios_layout(640, 4, 3)
        rows = layout["rows"]
        self.assertEqual([r[3] - r[1] for r in rows], [IOS_ROW] * 3)
        self.assertEqual([a[3] for a in rows[:-1]], [b[1] for b in rows[1:]])
        self.assertEqual(layout["group"][1], rows[0][1])
        self.assertEqual(layout["group"][3], rows[-1][3])
        self.assertGreater(rows[0][1], layout["widget"][3])

    def test_more_than_four_rings_wrap_to_a_second_row(self):
        one, two = ios_layout(640, IOS_RING_COLUMNS, 4), ios_layout(640, IOS_RING_COLUMNS + 1, 4)
        self.assertGreater(two["widget"][3], one["widget"][3])
        self.assertGreater(two["rings"][-1][1], two["rings"][0][1])

    def test_height_grows_with_devices_and_does_not_depend_on_width(self):
        heights = [ios_layout(640, n, n)["height"] for n in range(6)]
        self.assertEqual(heights, sorted(heights))
        self.assertEqual(ios_layout(540, 3, 2)["height"], ios_layout(880, 3, 2)["height"])

    def test_ring_slots_stay_inside_the_widget_at_every_width(self):
        for width in (540, 640, 880):
            layout = ios_layout(width, 4, 4)
            left, _top, right, _bottom = layout["widget"]
            for x, _y in layout["rings"]:
                self.assertTrue(left < x < right)


class ArtworkTests(unittest.TestCase):
    def test_rings_are_rendered_at_the_requested_pixel_size(self):
        for level in (None, 0, 18, 100):
            self.assertEqual(png_size(ring_png(46, 5, level, "#34c759", "#e5e5ea")), (46, 46))
        badge = ring_png(46, 5, 40, "#34c759", "#e5e5ea", ("#ffffff", "#34c759", "#ffffff"))
        self.assertEqual(png_size(badge), (46, 46))

    def test_opaque_surface_blends_corners_onto_the_background(self):
        import zlib
        data = rounded_surface_png(30, 20, 8, "#ffffff", "#000000")
        self.assertEqual(png_size(data), (30, 20))
        raw = zlib.decompress(data[data.index(b"IDAT") + 4:data.index(b"IEND") - 8])
        row = 30 * 4 + 1
        alphas = {raw[1 + y * row + x * 4 + 3] for y in range(20) for x in range(30)}
        self.assertEqual(alphas, {255})                        # fully opaque
        self.assertEqual(tuple(raw[1:4]), (0, 0, 0))           # outside the corner: background
        self.assertEqual(tuple(raw[1 + 10 * row:4 + 10 * row]), (255, 255, 255))  # edge mid-height
        self.assertEqual(raw[1 + 0 * row + 29 * 4], 0)          # mirrored right corner
        self.assertNotEqual(rounded_surface_png(30, 20, 8, "#ffffff"), data)

    def test_rounded_surface_size(self):
        self.assertEqual(png_size(rounded_surface_png(300, 120, 17, "#ffffff")), (300, 120))
        self.assertEqual(png_size(rounded_surface_png(10, 10, 40, "#ffffff")), (10, 10))


class ScrollThumbTests(unittest.TestCase):
    def test_no_thumb_when_everything_fits(self):
        from ios_controls import thumb_geometry
        self.assertIsNone(thumb_geometry(300, 0, 1, 3, 28))

    def test_thumb_is_proportional_and_reaches_both_ends(self):
        from ios_controls import thumb_geometry
        top, bottom = thumb_geometry(306, 0, .5, 3, 28)
        self.assertEqual((top, bottom), (3, 153))
        top, bottom = thumb_geometry(306, .5, 1, 3, 28)
        self.assertEqual((top, bottom), (153, 303))

    def test_long_content_keeps_a_grabbable_minimum(self):
        from ios_controls import thumb_geometry
        top, bottom = thumb_geometry(306, .99, 1, 3, 28)
        self.assertEqual(bottom - top, 28)
        self.assertAlmostEqual(bottom, 303)


class SwitchArtTests(unittest.TestCase):
    def test_ios_switch_is_51_by_31_logical_and_thumb_moves(self):
        from toggle_switch import Switch, _image
        self.assertEqual(png_size(_image(43, 26, 0, "#e3e3e8", "#007aff", False)), (43, 26))
        self.assertNotEqual(_image(43, 26, 0, "#34c759", "#007aff", False),
                            _image(43, 26, 1000, "#34c759", "#007aff", False))
        self.assertIn("51 * scale", __import__("inspect").getsource(Switch.__init__))


class PanelStateTests(unittest.TestCase):
    def panel(self):
        from my_devices import NexusPanel
        panel = NexusPanel.__new__(NexusPanel)
        panel.reading_times = ReadingTimes()
        return panel

    def test_earbuds_ring_uses_the_lower_ear_and_case_is_separate(self):
        from my_devices import NexusPanel
        reading = buds(left=85, right=80, case=40, charging=("case",))
        self.assertEqual(NexusPanel.ring_state(reading), (80, False))
        self.assertEqual(NexusPanel.ring_state(reading, "case"), (40, True))

    def test_single_number_earbuds_have_no_case_value(self):
        from my_devices import NexusPanel
        reading = Reading("Beats", "", level=90, connected=True, source="Windows 蓝牙")
        self.assertEqual(NexusPanel.ring_state(reading, "case"), (None, False))
        self.assertEqual(NexusPanel.ring_state(reading), (90, False))

    def test_errors_show_no_level(self):
        from my_devices import NexusPanel
        self.assertEqual(NexusPanel.ring_state(Reading("Mouse", "", level=50, error="timeout")), (None, False))

    def test_low_threshold_and_charging(self):
        from my_devices import NexusPanel
        self.assertTrue(NexusPanel.needs_charge(Reading("Mouse", "", level=20)))
        self.assertFalse(NexusPanel.needs_charge(Reading("Mouse", "", level=21)))
        self.assertFalse(NexusPanel.needs_charge(Reading("Mouse", "", level=5, status="充电中")))
        self.assertFalse(NexusPanel.needs_charge(Reading("Mouse", "", error="timeout")))

    def test_low_device_row_says_it_needs_charging(self):
        panel = self.panel()
        styles = panel.reading_styles("mouse", Reading("Mouse", "", level=18, connected=True))
        self.assertEqual(styles["row_value"]["text"], "18%")
        self.assertEqual(styles["ring_value"]["text"], "18%")
        self.assertTrue(styles["row_sub"]["text"].startswith("电量低"))

    def test_unknown_level_shows_dashes(self):
        panel = self.panel()
        styles = panel.reading_styles("mouse", Reading("Mouse", "", connected=False, error="未连接"))
        self.assertEqual(styles["row_value"]["text"], "--")
        self.assertEqual(styles["ring_case_value"]["text"], "--")

    def test_header_counts_devices_that_need_charging(self):
        panel = self.panel()
        panel.default_visible = False
        panel.saved_devices = [SimpleNamespace(id="mouse"), SimpleNamespace(id="keyboard")]
        panel.readings = {"mouse": Reading("Mouse", "", level=12), "keyboard": Reading("Keyboard", "", level=80)}
        self.assertEqual(panel.header_styles()["summary"]["text"], "1 台需要充电")
        panel.readings["mouse"] = Reading("Mouse", "", level=60)
        self.assertEqual(panel.header_styles()["summary"]["text"], "2/2 在线")

    def test_only_the_warning_is_red_and_the_refresh_time_is_grey(self):
        import time
        import theme
        panel = self.panel()
        panel.default_visible = False
        panel.saved_devices = [SimpleNamespace(id="mouse")]
        panel.readings = {"mouse": Reading("Mouse", "", level=12)}
        panel.last_refresh_at = time.time()
        styles = panel.header_styles()
        self.assertEqual(styles["summary"]["text"], "1 台需要充电")
        self.assertEqual(styles["summary"]["fill"], theme.DANGER.light)
        self.assertEqual(styles["summary_time"]["text"], " · 刚刚更新")
        self.assertEqual(styles["summary_time"]["fill"], theme.MUTED.light)
        panel.readings = {"mouse": Reading("Mouse", "", level=60)}
        self.assertEqual(panel.header_styles()["summary"]["fill"], theme.MUTED.light)

    def test_no_refresh_time_without_devices(self):
        import time
        panel = self.panel()
        panel.default_visible, panel.saved_devices, panel.readings = False, [], {}
        panel.last_refresh_at = time.time()
        self.assertEqual(panel.header_styles()["summary_time"]["text"], "")

    def test_rings_are_labelled_by_kind_unless_two_share_it(self):
        from my_devices import NexusPanel
        beats = SimpleNamespace(id="b", model="敏博的Beats Studio Buds", transport="bluetooth", hardware_id="",
                                label="Beats", manufacturer="Beats", wireless=True)
        mouse = SimpleNamespace(id="m", model="PRO WIRELESS", transport="usb", hardware_id="046D:C539",
                                label="PRO WIRELESS", manufacturer="Logitech", wireless=True)
        g304 = SimpleNamespace(id="g", model="G304", transport="usb", hardware_id="046D:C53F",
                               label="G304", manufacturer="Logitech", wireless=True)
        labels = [ring[3] for ring in NexusPanel.ring_entries([("k", None), ("m", mouse), ("b", beats)])]
        self.assertEqual(labels, ["键盘", "鼠标", "耳机", "充电仓"])
        labels = [ring[3] for ring in NexusPanel.ring_entries([("m", mouse), ("g", g304)])]
        self.assertEqual(labels, ["PRO WIRELESS", "G304"])

    def test_earbud_footnote_only_with_earbuds(self):
        with_note, without = ios_layout(484, 4, 3, note=True), ios_layout(484, 3, 3)
        self.assertIsNotNone(with_note["note_y"])
        self.assertIsNone(without["note_y"])
        self.assertGreater(with_note["height"], without["height"])

    def test_three_devices_fit_a_tall_narrow_window(self):
        from device_layout import IOS_BOTTOM, IOS_HEADER
        height = IOS_HEADER + ios_layout(484, 4, 3, note=True)["height"] + IOS_BOTTOM
        self.assertTrue(600 <= height <= 650, height)  # ≈ 530 px client at the 0.85 UI scale

    def test_earbud_case_and_main_rings_are_listed(self):
        from my_devices import NexusPanel
        beats = SimpleNamespace(id="b", model="Beats Studio Buds", transport="bluetooth", hardware_id="", label="Beats")
        rings = list(NexusPanel.ring_entries([("b", beats)]))
        self.assertEqual([ring[2] for ring in rings], [None, "case"])


class RelativeTimeTests(unittest.TestCase):
    def test_ios_style_ages(self):
        self.assertEqual(relative_time(1000, now=1000), "刚刚")
        self.assertEqual(relative_time(1000, now=1059), "刚刚")
        self.assertEqual(relative_time(1000, now=1060), "1 分钟前")
        self.assertEqual(relative_time(1000, now=1000 + 59 * 60), "59 分钟前")
        self.assertEqual(relative_time(1000, now=1000 + 3600 * 5), "5 小时前")
        self.assertRegex(relative_time(1000, now=1000 + 86400 * 2), r"^\d+月\d+日$")

    def test_clock_going_backwards_reads_as_just_now(self):
        self.assertEqual(relative_time(2000, now=1000), "刚刚")

    def test_row_time_is_relative_and_failure_keeps_last_success(self):
        times = ReadingTimes()
        ok = stamp_observation(Reading("Mouse", "", level=70), now=100)
        times.observe("mouse", ok)
        self.assertEqual(times.ago("mouse", ok, now=130), "刚刚")
        self.assertEqual(times.ago("mouse", ok, now=100 + 180), "3 分钟前")
        failure = stamp_observation(Reading("Mouse", "", error="timeout"), now=400)
        times.observe("mouse", failure)
        self.assertEqual(times.ago("mouse", failure, now=100 + 600), "上次读取 10 分钟前")

    def test_unread_device_keeps_waiting_text(self):
        times = ReadingTimes()
        waiting = Reading("Beats", "", connected=True)
        self.assertEqual(times.ago("beats", waiting), "等待电量上报")

    def test_info_dialog_still_uses_clock_times(self):
        times = ReadingTimes()
        ok = stamp_observation(Reading("Mouse", "", level=70), now=100)
        times.observe("mouse", ok)
        self.assertTrue(times.label("mouse", ok).startswith("读取 "))


class SheetValuesTests(unittest.TestCase):
    def panel(self, devices, readings, default=False):
        from my_devices import NexusPanel
        panel = NexusPanel.__new__(NexusPanel)
        panel.reading_times = ReadingTimes()
        panel.default_visible, panel.saved_devices, panel.readings = default, devices, readings
        for key, reading in readings.items():
            panel.reading_times.observe(key, reading)
        return panel

    mouse = SimpleNamespace(id="mouse", model="PRO WIRELESS", label="PRO WIRELESS", hardware_id="046D:C539",
                            transport="usb", wireless=True, manufacturer="Logitech")
    beats = SimpleNamespace(id="b", model="Beats Studio Buds", label="Beats", hardware_id="00:11:22:33:44:55",
                            transport="bluetooth", wireless=True, manufacturer="Beats")

    def test_low_mouse(self):
        import theme
        from device_sheet import sheet_values
        reading = stamp_observation(Reading("Mouse", "", level=18, connected=True))
        values = sheet_values(self.panel([self.mouse], {"mouse": reading}), "mouse")
        self.assertEqual(values["title"], "PRO WIRELESS")
        self.assertEqual(values["level"], "18%")
        self.assertIs(values["level_tone"], theme.LOW)
        self.assertEqual(values["status"], "电量低，建议尽快充电")
        self.assertIs(values["status_tone"], theme.DANGER)
        self.assertEqual(values["status_time"], " · 刚刚")  # grey, separate from the red warning
        rows = {name: value for name, _title, value in values["rows"]}
        self.assertEqual(rows["connection"], "2.4G 无线")
        self.assertEqual(rows["hardware"], "046D:C539")
        self.assertNotIn("runtime", rows)
        self.assertEqual(values["parts"], [])

    def test_earbuds_list_each_part_and_case_charging(self):
        from device_sheet import sheet_values
        reading = stamp_observation(buds(left=85, right=80, case=40, charging=("case",)))
        values = sheet_values(self.panel([self.beats], {"b": reading}), "b")
        self.assertEqual(values["level"], "80%")
        self.assertEqual([label for _part, label, _text, _when in values["parts"]], ["左耳", "右耳", "充电仓"])
        self.assertEqual(values["parts"][2][2], "40% · 充电中")
        self.assertEqual(values["status"], "充电仓充电中")  # only the case charges
        self.assertFalse(values["charging"])

    def test_earbuds_without_charging_show_connected(self):
        from device_sheet import sheet_values
        reading = stamp_observation(buds(left=85, right=80, case=40))
        values = sheet_values(self.panel([self.beats], {"b": reading}), "b")
        self.assertEqual(values["status"], "已连接")
        self.assertTrue(values["parts"][0][3].startswith("更新 "))

    def test_unreachable_device_shows_dashes(self):
        import theme
        from device_sheet import sheet_values
        reading = stamp_observation(Reading("Mouse", "", connected=False, error="未找到接收器"))
        values = sheet_values(self.panel([self.mouse], {"mouse": reading}), "mouse")
        self.assertEqual(values["level"], "--")
        self.assertIs(values["level_tone"], theme.MUTED)
        self.assertTrue(values["status"].startswith("未连接"))
        self.assertEqual(values["footer"], "未找到接收器")

    def test_default_keyboard_has_a_runtime_row(self):
        from device_sheet import sheet_values
        from saved_devices import DEFAULT_DEVICE_ID
        reading = stamp_observation(Reading("F87 Pro", "", level=60, connected=True))
        values = sheet_values(self.panel([], {DEFAULT_DEVICE_ID: reading}, default=True), DEFAULT_DEVICE_ID)
        self.assertEqual(values["title"], "AULA F87 Pro")
        self.assertIn("runtime", [name for name, _title, _value in values["rows"]])
        self.assertNotIn("hardware", [name for name, _title, _value in values["rows"]])


class AddDeviceTests(unittest.TestCase):
    def receiver(self, instance="r1", model="PRO WIRELESS", maker="Logitech", hw="046D:C539",
                 category="鼠标", wireless=True):
        from receiver_scan import Receiver
        return Receiver(instance, model, maker, hw, category, "测试", wireless)

    def search(self, devices, added=()):
        from receiver_search import ReceiverSearch
        search = ReceiverSearch.__new__(ReceiverSearch)
        search.devices, search.pending = list(devices), set()
        search.is_added = lambda device: device.instance_id in added
        return search

    def test_actions(self):
        search = self.search([self.receiver("a"), self.receiver("b"), self.receiver("c", wireless=False)], added={"a"})
        self.assertEqual([search.action_label(i) for i in range(3)], ["已添加", "添加", "仍然添加"])
        search.pending.add("a")
        self.assertEqual(search.action_label(0), "更新")

    def test_store_errors_do_not_break_the_list(self):
        from receiver_search import ReceiverSearch
        search = ReceiverSearch.__new__(ReceiverSearch)
        search.devices, search.pending = [self.receiver()], set()
        def broken(_device):
            raise OSError("devices.json 无法读取")
        search.is_added = broken
        self.assertEqual(search.action_label(0), "添加")

    def test_subtitles(self):
        from bluetooth_devices import BluetoothPeripheral
        from receiver_search import row_subtitle
        self.assertEqual(row_subtitle(self.receiver(), False), "046D:C539 · 鼠标")
        self.assertEqual(row_subtitle(self.receiver(wireless=False, category="USB 外设"), False),
                         "046D:C539 · 待确认 · USB 外设")
        buds = BluetoothPeripheral("b", "Beats", "Beats", "00:11:22:33:44:55", "耳机", True, 80)
        self.assertEqual(row_subtitle(buds, True), "00:11:22:33:44:55 · 已连接 · 80%")
        gone = BluetoothPeripheral("b", "Beats", "Beats", "00:11:22:33:44:55", "耳机", False, 80)
        self.assertEqual(row_subtitle(gone, True), "00:11:22:33:44:55 · 未连接")

    def test_status_text(self):
        from receiver_search import status_text
        devices = [SimpleNamespace(connected=True), SimpleNamespace(connected=False)]
        self.assertEqual(status_text(True, devices, [0, 1], False), "2 台已配对 · 1 台已连接")
        self.assertEqual(status_text(False, devices, [0], False), "找到 1 个无线接收器 / 外设")
        self.assertEqual(status_text(False, devices, [0, 1], True), "显示 2 个 USB 设备")

    def test_aula_rows_and_swatches(self):
        from receiver_search import aula_eligible, variant_swatches
        self.assertTrue(aula_eligible(self.receiver(model="USB Receiver", maker="未提供", hw="258A:010C")))
        self.assertFalse(aula_eligible(self.receiver()))
        model = {"photos": [{"swatches": ["#ffffff", "#dcdcdc"]}, {"swatches": ["red", "#12345"]}, {}]}
        self.assertEqual(variant_swatches(model), [("配色 1", ["#ffffff", "#dcdcdc"]), ("配色 2", []), ("配色 3", [])])


class GlassTests(unittest.TestCase):
    def test_wallpaper_styles(self):
        from glass import wallpaper_rects
        # Fill: a 4:3 picture on a 16:9 monitor is cropped top and bottom.
        source, target = wallpaper_rects(1600, 1200, 1920, 1080, "10")
        self.assertEqual(target, (0, 0, 1920, 1080))
        self.assertAlmostEqual(source[2], 1600)
        self.assertAlmostEqual(source[3], 900)
        self.assertAlmostEqual(source[1], 150)
        # Fit: letterboxed left and right.
        source, target = wallpaper_rects(1600, 1200, 1920, 1080, "6")
        self.assertEqual(source, (0, 0, 1600, 1200))
        self.assertAlmostEqual(target[2], 1440)
        self.assertAlmostEqual(target[0], 240)
        # Stretch and centre.
        self.assertEqual(wallpaper_rects(800, 600, 1920, 1080, "2"), ((0, 0, 800, 600), (0, 0, 1920, 1080)))
        self.assertEqual(wallpaper_rects(800, 600, 1920, 1080, "0", "0")[1], (560, 240, 800, 600))

    def test_blur_keeps_a_flat_colour_and_softens_an_edge(self):
        from glass import box_blur
        flat = box_blur(6, 4, [(10, 20, 30)] * 24)
        self.assertTrue(all(abs(v - e) < 1e-9 for p in flat for v, e in zip(p, (10, 20, 30))))
        edge = box_blur(8, 1, [(0, 0, 0)] * 4 + [(255, 255, 255)] * 4)
        self.assertTrue(0 < edge[3][0] < 255 and 0 < edge[4][0] < 255)

    def test_tint_keeps_text_readable(self):
        from glass import luminance, tint
        light = tint((20, 30, 120), (242, 242, 247), .58, .80, False)
        self.assertGreaterEqual(luminance(light), .80 - 1e-3)
        dark = tint((250, 240, 200), (0, 0, 0), .62, .14, True)
        self.assertLessEqual(luminance(dark), .14 + 1e-3)
        calm = tint((240, 240, 240), (242, 242, 247), .58, .80, False)
        self.assertGreater(luminance(calm), .9)

    def test_card_rim_is_a_thin_top_lit_highlight(self):
        import zlib
        from glass import bgra_to_png, rim_coverage
        width, height, radius = 40, 30, 8
        dark = bytes((60, 60, 60, 255)) * width * height
        plain = bgra_to_png(width, height, dark, width * 4, radius)
        lit = bgra_to_png(width, height, dark, width * 4, radius, rim=(.8, .3))
        self.assertNotEqual(plain, lit)
        raw = zlib.decompress(lit[lit.index(b"IDAT") + 4:lit.index(b"IEND") - 8])
        row = width * 4 + 1
        pixel = lambda x, y: raw[1 + y * row + x * 4]
        self.assertGreater(pixel(20, 0), 200)                   # top edge: bright
        self.assertEqual(pixel(20, 15), 60)                     # inside: untouched
        self.assertGreater(pixel(20, 0), pixel(20, height - 1))  # lit from above
        self.assertGreater(pixel(0, 15), 60)                    # side edge also lit
        self.assertEqual(pixel(3, 15), 60)                      # stroke is thin
        self.assertEqual(rim_coverage(20, 15, width, height, radius), 0)

    def test_glass_png_has_rounded_corners(self):
        import zlib
        from glass import bgra_to_png
        width, height = 20, 20
        data = bgra_to_png(width, height, bytes((10, 20, 30, 255)) * width * height, width * 4, radius=5)
        self.assertEqual(png_size(data), (width, height))
        raw = zlib.decompress(data[data.index(b"IDAT") + 4:data.index(b"IEND") - 8])
        row = width * 4 + 1
        first, middle = raw[1:1 + width * 4], raw[1 + 10 * row:1 + 10 * row + width * 4]
        self.assertEqual(first[3], 0)                     # transparent corner
        self.assertEqual(middle[3], 255)                  # opaque edge mid-height
        self.assertEqual(tuple(middle[:3]), (30, 20, 10))  # BGRA became RGBA


if __name__ == "__main__":
    unittest.main()
