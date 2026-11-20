"""Tests for the volume bar and Start With Windows.

The stream, keyboard, volume reader and registry are all replaced with fakes. Run:
    .venv\\Scripts\\python.exe test_volume.py
"""

import json
import tempfile
import time
import unittest
from pathlib import Path

import sk65_studio as s

FAKE_DEVICE = {"path": b"fake", "vendor_id": 0x0603, "product_id": 0x0351, "usage_page": 0xFF02,
               "usage": 1, "interface_number": 1, "release_number": 256, "product_string": "lucky65"}
GREEN = (0, 255, 80)


class Bar(unittest.TestCase):
    def test_row_is_esc_to_backspace(self):
        self.assertEqual(len(s.VOLUME_ROW), 14)
        self.assertEqual((s.VOLUME_ROW[0], s.VOLUME_ROW[-1]), ("esc", "backspace"))

    def test_keys_follow_the_volume(self):
        cases = {0.0: 0, 0.004: 0, 0.01: 1, 0.07: 1, 0.08: 2, 0.5: 7, 0.93: 14, 1.0: 14}
        for level, keys in cases.items():
            self.assertEqual(s.volume_keys(level, False), keys, level)

    def test_muted_is_dark_at_any_volume(self):
        self.assertEqual(s.volume_keys(1.0, True), 0)

    def test_bar_draws_over_the_frame_then_goes(self):
        bar = s.VolumeBar()
        base = {"esc": (9, 9, 9), "q": (1, 2, 3)}
        bar.show(0.5, False, GREEN)
        now = time.monotonic()
        frame = bar.apply(base, now)
        self.assertEqual([frame[k] for k in s.VOLUME_ROW[:7]], [GREEN] * 7)
        self.assertEqual([frame[k] for k in s.VOLUME_ROW[7:]], [(0, 0, 0)] * 7)
        self.assertEqual(frame["q"], (1, 2, 3))  # other rows keep the base
        self.assertEqual(base["esc"], (9, 9, 9))  # the base isn't changed
        self.assertEqual(bar.apply(base, now + s.VOLUME_BAR_SECONDS + 0.1), base)

    def test_stream_frames_layer_the_bar(self):
        bar = s.VolumeBar()
        frames = s.StreamFrames(lambda now: {"q": (5, 5, 5)}, bar)
        bar.show(1.0, False, GREEN)
        frame = frames.frame(time.monotonic())
        self.assertEqual(frame["backspace"], GREEN)
        self.assertEqual(frame["q"], (5, 5, 5))


class Profiles(unittest.TestCase):
    def test_defaults_and_cleaning(self):
        self.assertFalse(s.default_profile()["volume_bar"])
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "p.json"
            path.write_text(json.dumps({"volume_bar": 1, "volume_color": "green"}), encoding="utf-8")
            profile = s.read_profile(path)
            self.assertIs(profile["volume_bar"], True)
            self.assertEqual(profile["volume_color"], s.VOLUME_DEFAULT_COLOR)


class FakeKey:
    values = {}

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class FakeWinreg:
    """Stands in for winreg so tests never touch the real Run key."""
    HKEY_CURRENT_USER, REG_SZ = "HKCU", 1

    def OpenKey(self, _root, _path):
        return FakeKey()

    def CreateKey(self, _root, _path):
        return FakeKey()

    def QueryValueEx(self, _key, name):
        if name not in FakeKey.values:
            raise FileNotFoundError(name)
        return FakeKey.values[name], self.REG_SZ

    def SetValueEx(self, _key, name, _reserved, _kind, value):
        FakeKey.values[name] = value

    def DeleteValue(self, _key, name):
        if name not in FakeKey.values:
            raise FileNotFoundError(name)
        del FakeKey.values[name]


class Startup(unittest.TestCase):
    def setUp(self):
        self.saved = s.winreg
        s.winreg = FakeWinreg()
        FakeKey.values = {}

    def tearDown(self):
        s.winreg = self.saved

    def test_on_and_off(self):
        self.assertFalse(s.startup_enabled())
        s.set_startup(True)
        self.assertTrue(s.startup_enabled())
        self.assertTrue(FakeKey.values[s.APP_NAME].endswith(s.STARTUP_ARG))
        s.set_startup(False)
        self.assertFalse(s.startup_enabled())
        s.set_startup(False)  # turning it off twice is fine

    def test_entry_for_another_copy_counts_as_off(self):
        FakeKey.values[s.APP_NAME] = '"C:\\old\\Womier_SK65_Studio.exe" --startup'
        self.assertFalse(s.startup_enabled())


class FakeStream:
    made = []

    def __init__(self, frames, devices, post):
        self.frames, self.stopping, self.started = frames, False, False
        FakeStream.made.append(self)

    def start(self):
        self.started = True

    def join(self, _timeout=None):
        pass


class App(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        def no_device(*_args):
            raise AssertionError("a test tried to open the keyboard")

        cls.saved = {name: getattr(s, name) for name in
                     ("write_report", "save_profile", "load_profile", "probe", "LiveStream", "VolumeWatcher", "winreg")}
        cls.saved_hid = s.hid.device, s.hid.enumerate
        cls.sent = []
        s.hid.device = no_device
        s.hid.enumerate = lambda *_args: [FAKE_DEVICE]
        s.write_report = lambda _devices, page, packet: cls.sent.append(packet)
        s.save_profile = lambda *_args, **_kwargs: None
        s.load_profile = s.default_profile
        s.probe = lambda devices: {"config": (True, "stub"), "firmware": (True, "stub")}
        s.LiveStream = FakeStream
        s.VolumeWatcher = lambda post: type("W", (), {"start": lambda self: None, "stopping": False})()
        s.winreg = FakeWinreg()
        cls.app = s.App()
        cls.spin(0.4)

    @classmethod
    def tearDownClass(cls):
        cls.app._on_close()
        for name, value in cls.saved.items():
            setattr(s, name, value)
        s.hid.device, s.hid.enumerate = cls.saved_hid

    @classmethod
    def spin(cls, seconds):
        end = time.time() + seconds
        while time.time() < end:
            cls.app.update()
            time.sleep(0.005)

    def setUp(self):
        app = self.app
        app.stop_bar()
        app.stop_live()
        app.profile.update(s.default_profile())
        app.profile["volume_bar"] = True
        app.connected = True
        app.bar.until = 0
        FakeStream.made.clear()
        FakeKey.values = {}
        self.sent.clear()

    def test_volume_change_starts_a_bar_stream(self):
        self.app.on_volume(0.5, False)
        self.assertEqual(len(FakeStream.made), 1)
        frame = FakeStream.made[0].frames.frame(time.monotonic())
        self.assertEqual(sum(1 for k in s.VOLUME_ROW if frame[k] != (0, 0, 0)), 7)

    def test_more_changes_reuse_the_stream(self):
        self.app.on_volume(0.5, False)
        self.app.on_volume(0.6, False)
        self.assertEqual(len(FakeStream.made), 1)

    def test_bar_ends_and_normal_lighting_is_resent(self):
        self.app.on_volume(0.3, False)
        self.app.bar.until = time.monotonic() - 1
        self.app.light_sent = None
        self.spin(0.6)
        self.assertIsNone(self.app.bar_stream)
        self.assertTrue(FakeStream.made[0].stopping)
        self.assertTrue(any(p[:2] == bytes((7, 2)) for p in self.sent))

    def test_custom_design_stays_under_the_bar(self):
        self.app.profile["lighting"].update(effect="custom", brightness=100)
        self.app.profile["custom"]["presets"][0] = {"q": "#102030"}
        self.app.on_volume(0.5, False)
        frame = FakeStream.made[0].frames.frame(time.monotonic())
        self.assertEqual(frame["q"], (0x10, 0x20, 0x30))

    def test_live_effect_draws_the_bar_without_a_second_stream(self):
        self.app.profile["lighting"]["effect"] = "ripple"
        self.app.start_live()
        made = len(FakeStream.made)
        self.app.on_volume(1.0, False)
        self.assertEqual(len(FakeStream.made), made)
        frame = self.app.live_stream.frames.frame(time.monotonic())
        self.assertEqual(frame["backspace"], s.rgb(s.VOLUME_DEFAULT_COLOR))

    def test_nothing_when_off_or_disconnected(self):
        self.app.profile["volume_bar"] = False
        self.app.on_volume(0.5, False)
        self.app.profile["volume_bar"] = True
        self.app.connected = False
        self.app.on_volume(0.5, False)
        self.assertEqual(FakeStream.made, [])

    def test_settings_rows(self):
        self.app.set_page("settings")
        self.spin(0.2)
        keys = {hit[2] for hit in self.app.content.hits}
        self.assertTrue({"tray", "startup", "volume bar", f"volume {s.VOLUME_DEFAULT_COLOR}"} <= keys)
        self.app.set_page("configure")

    def test_startup_toggle_needs_the_installed_app(self):
        self.app.toggle_startup()  # running from source
        self.assertEqual(FakeKey.values, {})
        self.assertIn("installed app", self.app.status_text)

    def test_startup_toggle_in_the_installed_app(self):
        s.sys.frozen = True
        try:
            self.app.toggle_startup()
            self.assertTrue(s.startup_enabled())
            self.app.toggle_startup()
            self.assertFalse(s.startup_enabled())
        finally:
            del s.sys.frozen


if __name__ == "__main__":
    unittest.main()
