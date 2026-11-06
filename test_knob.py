"""Tests for remapping the knob, plus the real mouse path through the UI.

Windows marks the knob's media keys as injected, so they are replayed here with
the same flags. No real hook is installed and nothing touches the keyboard. Run:
    .venv\\Scripts\\python.exe test_knob.py
"""

import ctypes
import threading
import time
import unittest

import sk65_studio as s

FAKE_DEVICE = {"path": b"fake", "vendor_id": 0x0603, "product_id": 0x0351, "usage_page": 0xFF02,
               "usage": 1, "interface_number": 1, "release_number": 256, "product_string": "lucky65"}
VOL_DOWN, VOL_UP, MUTE = 0xAE, 0xAF, 0xAD
INJECTED, EXTENDED = 0x10, 0x01


class Engine(unittest.TestCase):
    def setUp(self):
        self.done = []
        self.engine = s.Engine(lambda _event: None)
        self.engine._perform = lambda dst, down: self.done.append((dst, down))
        self.engine.remap_on = True
        self.engine.set_map({}, {"ccw": "wheel down", "cw": "wheel up", "press": "volume mute"})

    def key(self, vk, down=True, flags=INJECTED | EXTENDED, tag=0):
        event = s.KBDLLHOOKSTRUCT(vk, 0, flags | (0 if down else 0x80), 0, tag)
        message = s.WM_KEYDOWN if down else 0x0101
        return self.engine._on_event(0, message, ctypes.addressof(event))

    def test_knob_turn_arrives_injected_and_is_remapped(self):
        self.engine.knob_open = True
        self.engine.note_knob()
        self.assertEqual(self.key(VOL_UP), 1)
        self.assertEqual(self.key(VOL_UP, down=False), 1)
        self.assertEqual(self.done, [("wheel up", True), ("wheel up", False)])

    def test_report_arriving_just_after_the_key_still_matches(self):
        self.engine.knob_open = True
        threading.Timer(0.005, self.engine.note_knob).start()
        self.assertEqual(self.key(VOL_DOWN), 1)
        self.key(VOL_DOWN, down=False)
        self.assertEqual(self.done[0], ("wheel down", True))

    def test_other_devices_keep_their_volume_keys(self):
        self.engine.knob_open = True
        self.assertNotEqual(self.key(VOL_UP), 1)
        self.assertNotEqual(self.key(VOL_UP, down=False), 1)
        self.assertEqual(self.done, [])

    def test_old_report_does_not_catch_a_later_headset_press(self):
        self.engine.knob_open = True
        self.engine.note_knob()
        time.sleep(s.KNOB_MATCH_SECONDS * 4)
        self.assertNotEqual(self.key(VOL_UP), 1)

    def test_without_the_knob_channel_all_volume_keys_are_remapped(self):
        self.engine.knob_open = False
        self.assertEqual(self.key(VOL_UP), 1)

    def test_slot_left_on_default_passes_through(self):
        self.engine.knob_open = True
        self.engine.note_knob()
        self.assertNotEqual(self.key(MUTE), 1)
        self.assertEqual(self.done, [])

    def test_remap_off_leaves_the_knob_alone(self):
        self.engine.remap_on = False
        self.assertNotEqual(self.key(VOL_UP), 1)

    def test_swapped_directions_do_not_loop(self):
        self.engine.set_map({}, {"ccw": "volume up", "cw": "volume down", "press": "volume mute"})
        self.engine.knob_open = False
        self.assertEqual(self.key(VOL_DOWN), 1)
        self.assertEqual(self.done, [("volume up", True)])
        self.assertNotEqual(self.key(VOL_UP, tag=s.SELF_INPUT_TAG), 1)  # the key we just sent
        self.assertEqual(len(self.done), 1)

    def test_other_injected_keys_are_still_ignored(self):
        self.engine.set_map({"a": "b"}, s.KNOB_DEFAULTS)
        self.assertNotEqual(self.key(0x41), 1)
        self.assertEqual(self.key(0x41, flags=0), 1)

    def test_sent_keys_carry_the_tag(self):
        sent = []
        original = s.user32.SendInput
        s.user32.SendInput = lambda _n, pointer, _size: sent.append(pointer._obj.u.ki.dwExtraInfo)
        try:
            s.send_key(VOL_UP, True)
        finally:
            s.user32.SendInput = original
        self.assertEqual(sent, [s.SELF_INPUT_TAG])


class Clicks(unittest.TestCase):
    """Drives the real window with mouse events, because a direct assign() call
    cannot show a click that never lands."""

    @classmethod
    def setUpClass(cls):
        def no_device(*_args):
            raise AssertionError("a test tried to open the keyboard")

        cls.saved = {name: getattr(s, name) for name in ("write_report", "save_profile", "load_profile", "probe")}
        cls.saved_hid = s.hid.device, s.hid.enumerate
        cls.saves = []
        s.hid.device = no_device
        s.hid.enumerate = lambda *_args: [FAKE_DEVICE]
        s.write_report = lambda *_args: None
        s.save_profile = lambda profile, *_args: cls.saves.append(
            (profile["remap_on"], dict(profile["knob"])))
        s.load_profile = s.default_profile
        s.probe = lambda devices: {"config": (True, "stub"), "firmware": (True, "stub")}
        cls.app = s.App()
        cls.spin(0.5)

    @classmethod
    def tearDownClass(cls):
        cls.app.engine.remap_on = False
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

    def click(self, surface, key):
        self.spin(0.1)
        (x0, y0, x1, y1), *_ = next(hit for hit in surface.hits if hit[2] == key)
        x, y = int((x0 + x1) / 2), int((y0 + y1) / 2)
        surface.event_generate("<Motion>", x=x, y=y)
        surface.event_generate("<Button-1>", x=x, y=y)
        surface.event_generate("<ButtonRelease-1>", x=x, y=y)
        self.spin(0.1)

    def setUp(self):
        self.app.profile["knob"] = dict(s.KNOB_DEFAULTS)
        self.app.profile["remap_on"] = False
        self.app.sync_engine()
        self.app.set_page("configure")
        self.app.set_menu("KEYMAP")
        self.saves.clear()

    def test_full_knob_setup_with_the_mouse(self):
        app = self.app
        self.click(app.nav, "remap")
        self.assertTrue(app.profile["remap_on"])
        self.click(app.kb, "knob")
        self.assertEqual(app.kb.selected, "knob")
        self.click(app.sub, "special")
        self.click(app.content, "wheel up")
        self.click(app.content, "wheel down")
        self.assertEqual(app.profile["knob"]["ccw"], "wheel up")
        self.assertEqual(app.profile["knob"]["cw"], "wheel down")
        self.assertEqual(app.engine.map, {VOL_DOWN: "wheel up", VOL_UP: "wheel down"})
        self.assertTrue(app.engine.remap_on)
        self.assertEqual(self.saves[-1], (True, app.profile["knob"]))

    def test_setup_works_after_coming_back_from_the_tray(self):
        app = self.app
        app.iconify()
        self.spin(0.2)
        app.show_window()
        self.spin(0.3)
        self.click(app.nav, "remap")
        self.assertTrue(app.profile["remap_on"])
        self.assertTrue(self.saves)


if __name__ == "__main__":
    unittest.main()
