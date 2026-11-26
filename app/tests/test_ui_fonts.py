from functools import cache
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

import ui_fonts


class FontLoadingTests(unittest.TestCase):
    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.local_data = Path(folder.name) / "local"
        self.windows = Path(folder.name) / "windows"
        self.paths = {
            key: Path(folder.name) / f"AlibabaPuHuiTi-2-{number}-{name}.ttf"
            for key, number, name in (("alibaba-thin", 35, "Thin"), ("alibaba-light", 45, "Light"),
                                      ("alibaba-regular", 55, "Regular"))
        }
        for path in self.paths.values():
            path.touch()
        self.registered = []
        self.failed = set()

        @cache
        def register(path):
            self.registered.append(path)
            return path not in self.failed

        mocks = (
            patch.dict(os.environ, LOCALAPPDATA=str(self.local_data), WINDIR=str(self.windows)),
            patch.object(ui_fonts, "FONT_DIR", Path(folder.name)),
            patch.object(ui_fonts, "_register", register),
            patch.object(ui_fonts.tkfont, "nametofont", return_value=Mock(cget=Mock(return_value="normal"))),
            patch.object(ui_fonts.tkfont, "Font"),
        )
        for mock in mocks:
            mock.start()
            self.addCleanup(mock.stop)
        self.fonts = ui_fonts.UIFonts(Mock(), "System", "System Numeric")

    def test_system_and_settings_lookup_do_not_register_any_faces(self):
        self.assertEqual(self.fonts.choice, "system")
        self.assertEqual(self.fonts.choices[self.fonts.choice], "系统默认")
        self.fonts.font(-15, "bold")
        self.assertEqual(self.registered, [])

    def test_selected_face_and_emphasis_load_only_when_used(self):
        self.fonts.set_choice("alibaba-thin")
        self.assertEqual(self.registered, [self.paths["alibaba-thin"]])
        self.fonts.font(-15, "bold")
        self.assertEqual(self.registered, [self.paths["alibaba-thin"], self.paths["alibaba-light"]])

    def test_selector_filters_failed_and_missing_faces(self):
        self.failed.add(self.paths["alibaba-light"])
        choices = self.fonts.choices
        self.fonts.prepare_choices()
        self.assertIs(choices, self.fonts.choices)
        self.assertEqual(list(choices), ["system", "alibaba-thin", "alibaba-regular"])
        self.fonts.set_choice("alibaba-light")
        self.assertEqual(self.fonts.choice, "system")

    def test_public_package_reads_installed_fonts_when_app_faces_are_absent(self):
        per_user = self.local_data / "Microsoft" / "Windows" / "Fonts" / self.paths["alibaba-thin"].name
        system = self.windows / "Fonts" / self.paths["alibaba-light"].name
        for face in (per_user, system):
            face.parent.mkdir(parents=True)
            face.touch()

        with patch.object(ui_fonts, "FONT_DIR", self.local_data / "empty"):
            fonts = ui_fonts.UIFonts(Mock(), "System", "System Numeric")
        self.assertEqual(list(fonts.choices), ["system", "alibaba-thin", "alibaba-light"])
        self.assertEqual(self.registered, [])
        fonts.prepare_choices()
        self.assertEqual(self.registered, [per_user, system])

    def test_public_package_without_installed_fonts_defaults_to_system(self):
        with patch.object(ui_fonts, "FONT_DIR", self.local_data / "empty"):
            fonts = ui_fonts.UIFonts(Mock(), "System", "System Numeric")
        fonts.set_choice("alibaba-thin")
        self.assertEqual(fonts.choice, "system")
        self.assertEqual(list(fonts.choices), ["system"])
        self.assertEqual(self.registered, [])
