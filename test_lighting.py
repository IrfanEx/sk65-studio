"""Tests for instant lighting, the app icon, the knob and the bottom row.

No test touches the keyboard: write_report is replaced by a recorder and
hid.device by a class that fails on use. Run with:
    .venv\\Scripts\\python.exe test_lighting.py
"""

import time
import unittest

import sk65_studio as s

FAKE_DEVICE = {"path": b"fake", "vendor_id": 0x0603, "product_id": 0x0351, "usage_page": 0xFF02,
               "usage": 1, "interface_number": 1, "release_number": 256, "product_string": "lucky65"}


class Static(unittest.TestCase):
    def test_bottom_row_has_ctrl_right_of_fn(self):
        row = [k["id"] for k in s.KEYS if k["y"] == 4]
        self.assertLess(row.index("fn"), row.index("right ctrl"))
        self.assertEqual(s.KEYS[-1]["x"] + s.KEYS[-1]["w"], s.UNITS_W)

    def test_knob_is_a_filled_disc(self):
        sprite = s.knob_sprite(60)
        self.assertEqual(sprite.getpixel((30, 30))[:3], (197, 167, 150))
        self.assertEqual(sprite.getpixel((30, 30))[3], 255)
        self.assertEqual(sprite.getpixel((1, 1))[3], 0)
        # Round all the way around: no teeth along the edge.
        import math
        edge = {sprite.getpixel((round(30 + 28 * math.cos(a / 9)), round(30 + 28 * math.sin(a / 9))))[3] > 200
                for a in range(57)}
        self.assertEqual(edge, {True})

    def test_icon_file_exists(self):
        self.assertTrue(s.ICON_PATH.is_file())


class Lighting(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        def no_device(*_args):
            raise AssertionError("a test tried to open the keyboard")

        cls.saved = {name: getattr(s, name) for name in ("write_report", "save_profile", "load_profile", "probe")}
        cls.saved_hid = s.hid.device, s.hid.enumerate
        cls.sent, cls.fail = [], None

        def record(_devices, page, packet):
            if cls.fail:
                raise OSError(cls.fail)
            cls.sent.append((page, packet))

        s.hid.device = no_device
        s.hid.enumerate = lambda *_args: [FAKE_DEVICE]
        s.write_report = record
        s.save_profile = lambda *_args, **_kwargs: None
        s.load_profile = s.default_profile
        s.probe = lambda devices: {"config": (True, "stub"), "firmware": (True, "stub")}
        cls.app = s.App()
        cls.app.withdraw()
        cls.spin(0.3)

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

    def settle(self):
        self.spin(s.LIGHT_DELAY_MS / 1000 + 0.25)

    def setUp(self):
        self.sent.clear()
        type(self).fail = None
        self.app.connected = True
        self.app.light_sent = None
        self.app.profile["lighting"] = dict(s.default_profile()["lighting"])

    def test_window_uses_the_icon(self):
        self.assertTrue(self.app._iconbitmap_method_called)  # stops CTk applying its own icon

    def test_change_reaches_keyboard_without_a_button(self):
        self.app.set_light(effect="breathing")
        self.settle()
        self.assertEqual(len(self.sent), 1)
        page, packet = self.sent[0]
        self.assertEqual(page, 0xFF02)
        self.assertEqual(packet[:3], bytes((7, 2, 1)))
        self.assertEqual(packet[9], 2)
        self.assertEqual(self.app.light_status[0], "Sent")

    def test_burst_of_changes_sends_once(self):
        for value in range(70, 100, 3):
            self.app.set_light(brightness=value)
            self.spin(0.01)
        self.settle()
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.sent[0][1][10], 4)

    def test_same_packet_is_not_rewritten(self):
        self.app.set_light(effect="reactive")
        self.settle()
        self.app.set_light(brightness=81)  # same 0-4 step as 80
        self.settle()
        self.assertEqual(len(self.sent), 1)

    def test_send_again_forces_a_write(self):
        self.app.set_light(effect="wave")
        self.settle()
        self.app.send_lighting()
        self.settle()
        self.assertEqual(len(self.sent), 2)

    def test_static_cannot_stay_multicolour(self):
        self.app.set_light(effect="static")
        self.settle()
        self.assertEqual(self.app.profile["lighting"]["color"], s.SWATCHES[0])
        self.assertEqual(self.sent[0][1][12], 0)

    def test_change_during_a_write_is_sent_after_it(self):
        self.app.writing = True
        self.app.set_light(effect="spectrum")
        self.settle()
        self.assertEqual(self.sent, [])
        self.app.on_written("keymap_stub", "stub")
        self.settle()
        self.assertEqual(self.sent[0][1][9], 4)

    def test_failure_is_shown_and_retried_next_change(self):
        type(self).fail = "device busy"
        self.app.set_light(effect="breathing")
        self.settle()
        self.assertEqual(self.app.light_status[0], "Failed")
        self.assertIn("device busy", self.app.status_text)
        type(self).fail = None
        self.app.set_light(brightness=81)
        self.settle()
        self.assertEqual(len(self.sent), 1)

    def test_disconnected_keyboard_is_reported(self):
        self.app.connected = False
        self.app.set_light(effect="breathing")
        self.settle()
        self.assertEqual(self.sent, [])
        self.assertEqual(self.app.light_status[0], "Keyboard not connected")


if __name__ == "__main__":
    unittest.main()
