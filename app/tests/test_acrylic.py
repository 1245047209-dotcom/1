from struct import pack, unpack
from unittest import TestCase

from acrylic import _Coverage


class AcrylicCoverageTests(TestCase):
    def test_light_background_card_and_gdi_black_remain_distinct(self):
        coverage = _Coverage(4)
        raw = pack("<4I", 0xEDF1F7, 0xF8F9FC, 0, 0xFF4218)
        palette = (0xEDF1F7, 0xF8F9FC, 72, 0x01000000)
        coated = coverage.convert(raw, *palette)
        self.assertEqual(unpack("<4I", coated), (0x01000000, 0x48464647, 0xFF000000, 0xFFFF4218))
        self.assertEqual(coverage.convert(coated, *palette), coated)

    def test_dark_tint_and_card_survive_partial_repaint(self):
        coverage = _Coverage(4)
        palette = (0x151923, 0x303744, 98, 0x46101823)
        mix = pack("<4I", 0x151923, 0x6212151A, 0x4604070A, 0xFFFFFF)
        expected = (0x4604070A, 0x6212151A, 0x4604070A, 0xFFFFFFFF)
        self.assertEqual(unpack("<4I", coverage.convert(mix, *palette)), expected)
