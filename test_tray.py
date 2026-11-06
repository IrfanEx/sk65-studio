"""Tests for the tray option, the single running copy and the profile location.

The tray icon is replaced by a fake and nothing touches the keyboard. Run with:
    .venv\\Scripts\\python.exe test_tray.py
"""

import json
import tempfile
import time
import unittest
from pathlib import Path

import sk65_studio as s

FAKE_DEVICE = {"path": b"fake", "vendor_id": 0x0603, "product_id": 0x0351, "usage_page": 0xFF02,
               "usage": 1, "interface_number": 1, "release_number": 256, "product_string": "lucky65"}


class FakeIcon:
    made = []

    def __init__(self, name, image, title, menu):
        self.title, self.menu = title, menu
        self.running, self.size = False, image.size
        FakeIcon.made.append(self)

    def run_detached(self):
        self.running = True

    def stop(self):
        self.running = False


class Profiles(unittest.TestCase):
    def setUp(self):
        self.saved = s.PROFILE_PATH, s.LEGACY_PROFILE_PATH, s.save_profile.__defaults__
        self.dir = tempfile.TemporaryDirectory()
        root = Path(self.dir.name)
        s.PROFILE_PATH = root / "SK65 Studio" / "sk65_profile.json"
        s.LEGACY_PROFILE_PATH = root / "old" / "sk65_profile.json"
        s.save_profile.__defaults__ = (s.PROFILE_PATH,)

    def tearDown(self):
        s.PROFILE_PATH, s.LEGACY_PROFILE_PATH, s.save_profile.__defaults__ = self.saved
        self.dir.cleanup()

    def test_tray_is_off_by_default(self):
        self.assertIs(s.default_profile()["tray"], False)

    def test_save_creates_the_user_folder_and_loads_back(self):
        profile = s.default_profile()
        profile["tray"] = True
        s.save_profile(profile)
        self.assertIs(s.load_profile()["tray"], True)

    def test_old_profile_next_to_the_exe_is_picked_up(self):
        s.LEGACY_PROFILE_PATH.parent.mkdir()
        s.LEGACY_PROFILE_PATH.write_text(json.dumps({"lighting": {
            "effect": "static", "color": "#FF0000", "brightness": 80, "speed": 45}}), encoding="utf-8")
        self.assertEqual(s.load_profile()["lighting"]["effect"], "static")

    def test_user_folder_wins_over_old_profile(self):
        s.LEGACY_PROFILE_PATH.parent.mkdir()
        s.LEGACY_PROFILE_PATH.write_text(json.dumps({"tray": False}), encoding="utf-8")
        profile = s.default_profile()
        profile["tray"] = True
        s.save_profile(profile)
        self.assertIs(s.load_profile()["tray"], True)

    def test_broken_file_falls_back_to_defaults(self):
        s.PROFILE_PATH.parent.mkdir()
        s.PROFILE_PATH.write_text("not json", encoding="utf-8")
        self.assertEqual(s.load_profile()["vid"], s.DEFAULT_VID)

    def test_real_location_is_under_appdata(self):
        self.assertIn("SK65 Studio", str(self.saved[0]))


class Version(unittest.TestCase):
    def test_installer_default_matches_the_app(self):
        script = (Path(__file__).resolve().parent / "installer.iss").read_text(encoding="utf-8")
        self.assertIn(f'#define AppVersion "{s.APP_VERSION}"', script)


class SingleInstance(unittest.TestCase):
    def setUp(self):
        self.saved = s.INSTANCE_NAME
        s.INSTANCE_NAME = f"SK65StudioTest{time.time_ns()}"

    def tearDown(self):
        s.INSTANCE_NAME = self.saved

    def test_second_launch_signals_the_first(self):
        self.assertTrue(s.first_instance())
        event = s.kernel32.CreateEventW(None, False, False, s.INSTANCE_NAME + "Show")
        self.assertFalse(s.first_instance())
        self.assertEqual(s.kernel32.WaitForSingleObject(event, 1000), 0)

    def test_second_launch_works_before_the_event_exists(self):
        self.assertTrue(s.first_instance())
        self.assertFalse(s.first_instance())


class Tray(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        def no_device(*_args):
            raise AssertionError("a test tried to open the keyboard")

        cls.saved = {name: getattr(s, name) for name in ("write_report", "save_profile", "load_profile", "probe")}
        cls.saved_hid = s.hid.device, s.hid.enumerate
        cls.saved_icon = s.pystray.Icon
        s.pystray.Icon = FakeIcon
        s.hid.device = no_device
        s.hid.enumerate = lambda *_args: [FAKE_DEVICE]
        s.write_report = lambda *_args: None
        s.save_profile = lambda *_args, **_kwargs: None
        s.load_profile = s.default_profile
        s.probe = lambda devices: {"config": (True, "stub"), "firmware": (True, "stub")}
        cls.app = s.App()
        cls.spin(0.4)

    @classmethod
    def tearDownClass(cls):
        cls.app._on_close()
        for name, value in cls.saved.items():
            setattr(s, name, value)
        s.hid.device, s.hid.enumerate = cls.saved_hid
        s.pystray.Icon = cls.saved_icon

    @classmethod
    def spin(cls, seconds):
        end = time.time() + seconds
        while time.time() < end:
            cls.app.update()
            time.sleep(0.005)

    def setUp(self):
        self.app.profile["tray"] = False
        self.app.apply_tray()
        self.app.show_window()
        self.spin(0.1)

    def minimize(self):
        self.app.iconify()
        self.spin(0.3)

    def test_tray_stays_off_until_checked(self):
        self.assertIsNone(self.app.tray.icon)
        self.minimize()
        self.assertEqual(self.app.state(), "iconic")
        self.assertFalse(self.app.hidden)

    def test_checked_minimize_hides_in_tray(self):
        self.app.toggle_tray()
        self.assertTrue(self.app.tray.icon.running)
        self.assertEqual(self.app.tray.icon.title, s.APP_NAME)
        self.minimize()
        self.assertEqual(self.app.state(), "withdrawn")
        self.assertTrue(self.app.hidden)

    def test_tray_click_brings_the_window_back(self):
        self.app.toggle_tray()
        self.minimize()
        default = next(item for item in self.app.tray.icon.menu.items if item.default)
        default(self.app.tray.icon)
        self.spin(0.2)
        self.assertEqual(self.app.state(), "normal")
        self.assertFalse(self.app.hidden)

    def test_second_launch_brings_the_window_back(self):
        self.app.toggle_tray()
        self.minimize()
        self.app.events.put(("show",))
        self.spin(0.2)
        self.assertEqual(self.app.state(), "normal")

    def test_unchecking_stops_the_icon_and_restores_the_window(self):
        self.app.toggle_tray()
        icon = self.app.tray.icon
        self.minimize()
        self.app.toggle_tray()
        self.spin(0.2)
        self.assertFalse(icon.running)
        self.assertIsNone(self.app.tray.icon)
        self.assertEqual(self.app.state(), "normal")

    def test_tray_exit_closes_the_app(self):
        closed = []
        self.app._on_close, original = (lambda: closed.append(True)), self.app._on_close
        self.app.toggle_tray()
        exit_item = next(item for item in self.app.tray.icon.menu.items if str(item) == "Exit")
        exit_item(self.app.tray.icon)
        self.spin(0.2)
        self.app._on_close = original
        self.assertEqual(closed, [True])

    def test_settings_page_has_a_clickable_option(self):
        self.app.set_page("settings")
        self.spin(0.2)
        self.assertEqual(self.app.menu_item["settings"], "SETTINGS")
        entry = next(hit for hit in self.app.content.hits if hit[2] == "tray")
        entry[1](0, 0)
        self.assertTrue(self.app.profile["tray"])
        self.app.set_page("configure")

    def test_settings_page_shows_the_version(self):
        rows = []
        original = self.app.control_row

        def spy(surf, img, w, label, detail=None, *args):
            rows.append((label, detail))
            return original(surf, img, w, label, detail, *args)

        self.app.control_row = spy
        self.app.set_page("settings")
        self.spin(0.2)
        del self.app.control_row
        self.app.set_page("configure")
        self.assertIn(("Version", s.APP_VERSION), rows)

    def test_checkbox_saves_before_a_crash_could_lose_it(self):
        saved = []
        original = s.save_profile
        s.save_profile = lambda profile, *_args: saved.append(profile["tray"])
        self.app.toggle_tray()
        s.save_profile = original
        self.assertEqual(saved, [True])


if __name__ == "__main__":
    unittest.main()
