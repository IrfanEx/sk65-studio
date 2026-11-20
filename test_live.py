"""Tests for the live Fade Out and Ripple effects.

No test touches the keyboard: write_report and open_stream are replaced by
recorders, and hook events are replayed by hand. Run with:
    .venv\\Scripts\\python.exe test_live.py
"""

import ctypes
import time
import unittest

import sk65_studio as s

FAKE_DEVICES = [
    {"path": b"config", "vendor_id": 0x0603, "product_id": 0x0351, "usage_page": 0xFF02,
     "usage": 1, "interface_number": 1, "release_number": 256, "product_string": "lucky65"},
    {"path": b"stream", "vendor_id": 0x0603, "product_id": 0x0351, "usage_page": 0xFF55,
     "usage": 0x0202, "interface_number": 1, "release_number": 256, "product_string": "lucky65"},
]


def light(**patch):
    return {**s.default_profile()["lighting"], **patch}


def position(key):
    return s.MATRIX.index(s.KEY_TO_MATRIX.get(key, key))


class Packets(unittest.TestCase):
    def test_five_reports_of_64_bytes(self):
        packets = s.build_stream_packets({})
        self.assertEqual([p[:2] for p in packets], [bytes((6, n)) for n in range(1, 6)])
        self.assertEqual({len(p) for p in packets}, {64})

    def test_colours_land_at_three_times_the_matrix_position(self):
        data = b"".join(p[2:] for p in s.build_stream_packets({"a": (1, 2, 3), "space": (4, 5, 6), "fn": (7, 8, 9)}))
        for key, color in (("a", (1, 2, 3)), ("space", (4, 5, 6)), ("fn", (7, 8, 9))):
            self.assertEqual(tuple(data[3 * position(key):3 * position(key) + 3]), color)

    def test_live_effect_keeps_reactive_in_the_keyboard_memory(self):
        packet = s.build_lighting_packets(light(effect="ripple"), s.default_custom())[0]
        self.assertEqual(packet[9], s.EFFECT_IDS["reactive"])


class Fade(unittest.TestCase):
    def setUp(self):
        self.fx = s.LiveEffect()
        self.fx.configure(light(effect="fade out", color="#FF0000", brightness=100, speed=50))

    def test_every_pressed_key_lights_on_its_own(self):
        self.fx.press("a", 10.0)
        self.fx.press("l", 10.0)
        frame = self.fx.frame(10.0)
        self.assertEqual(frame["a"], (255, 0, 0))
        self.assertEqual(frame["l"], (255, 0, 0))
        self.assertEqual(frame["s"], (0, 0, 0))

    def test_key_fades_then_goes_dark(self):
        self.fx.press("a", 10.0)
        half = self.fx.frame(10.0 + self.fx.fade_seconds() / 2)["a"][0]
        self.assertTrue(100 < half < 160, half)
        self.assertEqual(self.fx.frame(10.0 + self.fx.fade_seconds() + 0.01)["a"], (0, 0, 0))
        self.assertEqual(self.fx.presses, [])

    def test_speed_changes_the_fade_time(self):
        slow = s.LiveEffect()
        slow.configure(light(effect="fade out", speed=0))
        fast = s.LiveEffect()
        fast.configure(light(effect="fade out", speed=100))
        self.assertAlmostEqual(slow.fade_seconds(), 3.0)
        self.assertLess(fast.fade_seconds(), 0.1)

    def test_brightness_scales_the_colour(self):
        self.fx.configure(light(effect="fade out", color="#FF0000", brightness=50, speed=50))
        self.fx.press("a", 10.0)
        self.assertEqual(self.fx.frame(10.0)["a"], (128, 0, 0))

    def test_multicolour_changes_with_time(self):
        self.fx.configure(light(effect="fade out", color=s.MULTICOLOR, brightness=100, speed=50))
        self.fx.press("a", 10.0)
        self.fx.press("s", 12.0)
        self.assertNotEqual(self.fx.frame(12.0)["a"], self.fx.frame(12.0)["s"])

    def test_knob_and_unknown_keys_are_ignored(self):
        self.fx.press_vk(0x7B)  # F12: not on the SK65
        self.fx.press_vk(s.KNOB_SOURCES["cw"])
        self.assertEqual(self.fx.presses, [])
        self.fx.press_vk(0x41)  # A
        self.assertEqual(self.fx.presses[0][0], "a")


class Ripple(unittest.TestCase):
    def setUp(self):
        self.fx = s.LiveEffect()
        self.fx.configure(light(effect="ripple", color="#0000FF", brightness=100, speed=50))

    def test_ring_moves_out_from_the_key(self):
        self.fx.press("g", 10.0)
        start = self.fx.frame(10.0)
        self.assertGreater(start["g"][2], 200)
        self.assertEqual(start["k"], (0, 0, 0))
        later = self.fx.frame(10.0 + 3 / self.fx.ripple_speed())  # k is 3 keys right of g
        self.assertGreater(later["k"][2], 100)
        self.assertEqual(later["g"], (0, 0, 0))

    def test_ring_is_gone_after_its_reach(self):
        self.fx.press("g", 10.0)
        end = self.fx.frame(10.0 + s.RIPPLE_REACH / self.fx.ripple_speed() + 0.01)
        self.assertEqual(set(end.values()), {(0, 0, 0)})

    def test_rings_from_two_keys_both_show(self):
        self.fx.press("esc", 10.0)
        self.fx.press("right", 10.0)
        frame = self.fx.frame(10.0)
        self.assertGreater(frame["esc"][2], 200)
        self.assertGreater(frame["right"][2], 200)


class Hook(unittest.TestCase):
    def setUp(self):
        self.pressed = []
        self.engine = s.Engine(lambda _event: None)
        self.engine.live = True
        self.engine.on_press = self.pressed.append

    def key(self, vk, down=True, flags=0):
        event = s.KBDLLHOOKSTRUCT(vk, 0, flags | (0 if down else 0x80), 0, 0)
        self.engine._on_event(0, s.WM_KEYDOWN if down else 0x0101, ctypes.addressof(event))

    def test_a_held_key_counts_once(self):
        for _ in range(5):
            self.key(0x41)
        self.key(0x41, down=False)
        self.key(0x41)
        self.assertEqual(self.pressed, [0x41, 0x41])

    def test_injected_keys_do_not_light(self):
        self.key(0x41, flags=0x10)
        self.assertEqual(self.pressed, [])

    def test_off_when_no_live_effect(self):
        self.engine.live = False
        self.key(0x41)
        self.assertEqual(self.pressed, [])


class FakeStream:
    def __init__(self):
        self.packets = []
        self.closed = False

    def write(self, packet):
        self.packets.append(bytes(packet))
        return len(packet)

    def error(self):
        return ""

    def close(self):
        self.closed = True


class App(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        def no_device(*_args):
            raise AssertionError("a test tried to open the keyboard")

        cls.saved = {name: getattr(s, name) for name in
                     ("write_report", "save_profile", "load_profile", "probe", "open_stream")}
        cls.saved_hid = s.hid.device, s.hid.enumerate
        cls.sent, cls.streams = [], []

        def open_fake(devices):
            assert any(d["usage_page"] == s.STREAM_PAGE for d in devices)
            cls.streams.append(FakeStream())
            return cls.streams[-1]

        s.hid.device = no_device
        s.hid.enumerate = lambda *_args: FAKE_DEVICES
        s.write_report = lambda _devices, page, packet: cls.sent.append((page, bytes(packet)))
        s.open_stream = open_fake
        s.save_profile = lambda *_args, **_kwargs: None
        s.load_profile = s.default_profile
        s.probe = lambda devices: {"config": (True, "stub"), "firmware": (True, "stub")}
        cls.app = s.App()
        cls.app.withdraw()
        cls.app.engine.sync = lambda: None  # never install a real hook in tests
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

    def settle(self):
        self.spin(s.LIGHT_DELAY_MS / 1000 + 0.3)

    def setUp(self):
        self.app.set_light(effect="wave")
        self.settle()
        self.sent.clear()

    def test_fade_out_streams_and_writes_no_flash(self):
        self.app.set_light(effect="fade out")
        self.settle()
        self.assertEqual(self.sent, [(s.PROTOCOL["aux_page"], s.STREAM_START)])
        self.assertTrue(self.app.engine.live)
        stream = self.streams[-1]
        self.assertTrue(stream.packets)
        self.assertEqual({len(p) for p in stream.packets}, {64})

    def test_key_press_reaches_the_stream(self):
        self.app.set_light(effect="ripple", color="#00FF00", brightness=100)
        self.settle()
        self.app.live_effect.press_vk(0x41)
        self.spin(0.1)
        frames = self.streams[-1].packets
        data = b"".join(p[2:] for p in frames[-5:])
        self.assertTrue(any(data[3 * position(k) + 1] for k in ("a", "s", "q", "z")))

    def test_switching_back_stops_the_stream_and_writes_the_effect(self):
        self.app.set_light(effect="fade out")
        self.settle()
        stream = self.streams[-1]
        self.app.set_light(effect="wave")
        self.settle()
        self.assertTrue(stream.closed)
        self.assertIsNone(self.app.live_stream)
        self.assertFalse(self.app.engine.live)
        self.assertEqual(self.sent[-1][0], s.PROTOCOL["config_page"])
        self.assertEqual(self.sent[-1][1][9], s.EFFECT_IDS["wave"])


if __name__ == "__main__":
    unittest.main()
