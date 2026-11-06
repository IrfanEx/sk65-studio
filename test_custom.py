"""Tests for custom lighting: the bytes sent, painting, presets and the colour picker.

The "07 07" layout was confirmed on a real board. Nothing here touches one. Run:
    .venv\\Scripts\\python.exe test_custom.py
"""

import json
import tempfile
import time
import unittest
from pathlib import Path

import sk65_studio as s

FAKE_DEVICE = {"path": b"fake", "vendor_id": 0x0603, "product_id": 0x0351, "usage_page": 0xFF02,
               "usage": 1, "interface_number": 1, "release_number": 256, "product_string": "lucky65"}


def rgb_at(packet, matrix_name):
    offset = 8 + 3 * s.MATRIX.index(matrix_name)
    return tuple(packet[offset:offset + 3])


class Packets(unittest.TestCase):
    def test_custom_packet_layout(self):
        packet = s.build_custom_packet({"esc": "#102030", "enter": "#FF0000", "fn": "#00FF00", "space": "#0000FF"})
        self.assertEqual(len(packet), 620)
        self.assertEqual(packet[:2], bytes((7, 7)))
        self.assertEqual(rgb_at(packet, "esc"), (0x10, 0x20, 0x30))
        self.assertEqual(rgb_at(packet, "enter"), (255, 0, 0))
        self.assertEqual(rgb_at(packet, "fn"), (0, 255, 0))
        self.assertEqual(rgb_at(packet, "mspace"), (0, 0, 255))

    def test_keys_not_listed_stay_off(self):
        packet = s.build_custom_packet({"esc": "#FFFFFF"})
        self.assertEqual(rgb_at(packet, "q"), (0, 0, 0))

    def test_every_light_key_has_a_position(self):
        packet = s.build_custom_packet({key: "#010203" for key in s.LIGHT_KEYS})
        self.assertEqual(len(s.LIGHT_KEYS), 66)
        self.assertEqual(sum(1 for i in range(8, len(packet), 3) if packet[i:i + 3] == b"\x01\x02\x03"), 66)

    def test_custom_effect_sends_effect_19_then_colours(self):
        custom = s.default_custom()
        custom["presets"][0] = {"esc": "#FF0000"}
        light = {"effect": "custom", "color": s.MULTICOLOR, "brightness": 100, "speed": 50}
        first, second = s.build_lighting_packets(light, custom)
        self.assertEqual(first[:3], bytes((7, 2, 1)))
        self.assertEqual(first[9], 19)
        self.assertEqual(first[10], 4)
        self.assertEqual(second[:2], bytes((7, 7)))

    def test_built_in_effects_still_send_one_packet(self):
        light = {"effect": "wave", "color": s.MULTICOLOR, "brightness": 80, "speed": 45}
        self.assertEqual(len(s.build_lighting_packets(light, s.default_custom())), 1)

    def test_picker_round_trip(self):
        for color in ("#FF6010", "#00FF00", "#123456"):
            r, g, b = s.colorsys.hsv_to_rgb(*s.picker_from(color))
            self.assertEqual(f"#{round(r * 255):02X}{round(g * 255):02X}{round(b * 255):02X}", color)


class Profiles(unittest.TestCase):
    def test_round_trip_and_cleaning(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "p.json"
            path.write_text(json.dumps({"custom": {
                "preset": 2, "color": "#abcdef",
                "presets": [{"esc": "#ff0000", "knob": "#00FF00", "nope": "#00FF00", "a": "red"}, "bad"]}}), encoding="utf-8")
            custom = s.read_profile(path)["custom"]
            self.assertEqual(custom["preset"], 2)
            self.assertEqual(custom["color"], "#ABCDEF")
            self.assertEqual(custom["presets"][0], {"esc": "#FF0000"})
            self.assertEqual(len(custom["presets"]), s.CUSTOM_PRESETS)

    def test_bad_values_fall_back(self):
        custom = s.clean_custom({"preset": 99, "color": "pink", "presets": 3})
        self.assertEqual(custom, s.default_custom())

    def test_profile_from_before_custom_lighting_loads(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "p.json"
            path.write_text(json.dumps({"lighting": {"effect": "static", "color": "#FF0000",
                                                     "brightness": 80, "speed": 45}}), encoding="utf-8")
            self.assertEqual(s.read_profile(path)["custom"], s.default_custom())


class App(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        def no_device(*_args):
            raise AssertionError("a test tried to open the keyboard")

        cls.saved = {name: getattr(s, name) for name in ("write_report", "save_profile", "load_profile", "probe")}
        cls.saved_hid = s.hid.device, s.hid.enumerate
        cls.saved_gap = s.WRITE_GAP_SECONDS, s.PAINT_DELAY_MS
        s.WRITE_GAP_SECONDS, s.PAINT_DELAY_MS = 0.01, 60
        cls.sent = []
        s.hid.device = no_device
        s.hid.enumerate = lambda *_args: [FAKE_DEVICE]
        s.write_report = lambda _devices, page, packet: cls.sent.append(packet)
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
        s.WRITE_GAP_SECONDS, s.PAINT_DELAY_MS = cls.saved_gap

    @classmethod
    def spin(cls, seconds):
        end = time.time() + seconds
        while time.time() < end:
            cls.app.update()
            time.sleep(0.005)

    def setUp(self):
        app = self.app
        app.profile.update(s.default_profile())
        app.picker = s.picker_from(s.CUSTOM_DEFAULT_COLOR)
        app.brush = "key"
        app.light_sent = None
        app.kb.selected = None
        app.set_page("configure")
        app.set_menu("LIGHTING")
        self.spin(0.1)
        self.sent.clear()

    def custom_on(self):
        self.app.set_light(effect="custom")
        self.spin(0.5)
        self.sent.clear()

    def click_key(self, kid):
        self.spin(0.1)
        (x0, y0, x1, y1), *_ = next(hit for hit in self.app.kb.hits if hit[2] == kid)
        x, y = int((x0 + x1) / 2), int((y0 + y1) / 2)
        self.app.kb.event_generate("<Button-1>", x=x, y=y)
        self.app.kb.event_generate("<ButtonRelease-1>", x=x, y=y)
        self.spin(0.05)

    def test_choosing_custom_lights_the_whole_board(self):
        self.app.set_light(effect="custom")
        self.spin(0.5)
        self.assertEqual(len(self.app.custom_colors()), 66)
        self.assertEqual([packet[:2] for packet in self.sent], [bytes((7, 2)), bytes((7, 7))])

    def test_clicking_a_key_paints_only_that_key(self):
        self.custom_on()
        self.app.set_color("#00FF00")
        self.click_key("a")
        colors = self.app.custom_colors()
        self.assertEqual(colors["a"], "#00FF00")
        self.assertEqual(colors["s"], s.CUSTOM_DEFAULT_COLOR)

    def test_painting_does_not_leave_the_lighting_page(self):
        self.custom_on()
        self.click_key("a")
        self.assertEqual(self.app.menu_item["configure"], "LIGHTING")
        self.assertIsNone(self.app.kb.selected)

    def test_row_brush(self):
        self.custom_on()
        self.app.set_color("#0000FF")
        self.app.set_brush("row")
        self.click_key("a")
        colors = self.app.custom_colors()
        self.assertEqual(colors["l"], "#0000FF")
        self.assertEqual(colors["enter"], "#0000FF")
        self.assertEqual(colors["q"], s.CUSTOM_DEFAULT_COLOR)

    def test_all_brush(self):
        self.custom_on()
        self.app.set_color("#FFFFFF")
        self.app.set_brush("all")
        self.click_key("a")
        self.assertEqual(set(self.app.custom_colors().values()), {"#FFFFFF"})

    def test_a_drag_becomes_one_write(self):
        self.custom_on()
        self.app.set_color("#00FF00")
        for key in "asdfgh":
            self.app.paint_key(key)
            self.spin(0.005)
        self.spin(0.3)
        self.assertEqual(len([p for p in self.sent if p[:2] == bytes((7, 7))]), 1)

    def test_repainting_the_same_colour_writes_nothing(self):
        self.custom_on()
        self.app.paint_key("a")
        self.spin(0.3)
        self.assertEqual(self.sent, [])

    def test_knob_cannot_be_painted(self):
        self.custom_on()
        self.assertIsNone(next(hit for hit in self.app.kb.hits if hit[2] == "knob")[1])

    def test_presets_are_separate(self):
        self.custom_on()
        self.app.set_color("#00FF00")
        self.app.paint_key("a")
        self.app.select_preset(1)
        self.spin(0.3)
        self.assertEqual(self.app.custom_colors()["a"], "#00FF00")  # new preset fills with the current colour
        self.app.set_color("#FF0000")
        self.app.paint_key("a")
        self.app.select_preset(0)
        self.spin(0.3)
        self.assertEqual(self.app.custom_colors()["a"], "#00FF00")

    def test_picker_sets_the_colour(self):
        self.custom_on()
        self.app.set_picker(hue=0.0, sat=1.0, val=1.0)
        self.assertEqual(self.app.profile["custom"]["color"], "#FF0000")
        self.app.set_picker(hue=1 / 3)
        self.assertEqual(self.app.profile["custom"]["color"], "#00FF00")

    def test_picker_hits_are_on_the_page(self):
        self.custom_on()
        self.spin(0.2)
        keys = {hit[2] for hit in self.app.content.hits}
        self.assertTrue({"sv", "hue", "preset4", "brush row", "type color", "black", "brightness", "Custom"} <= keys)

    def test_other_effects_keep_their_normal_page(self):
        self.app.set_light(effect="wave")
        self.spin(0.2)
        keys = {hit[2] for hit in self.app.content.hits}
        self.assertIn("speed", keys)
        self.assertNotIn("sv", keys)
        self.assertFalse(self.app.painting())

    def test_clicking_a_key_on_other_effects_still_opens_keymap(self):
        self.app.set_light(effect="wave")
        self.click_key("a")
        self.assertEqual(self.app.menu_item["configure"], "KEYMAP")
        self.assertEqual(self.app.kb.selected, "a")


if __name__ == "__main__":
    unittest.main()
