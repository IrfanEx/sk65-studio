"""Tests for macros: the bytes sent to the keyboard, the recorder and the save flow.

Packets are compared with the layout in Womier's own MacroToData and
SetMacroKey. Nothing touches a real keyboard. Run with:
    .venv\\Scripts\\python.exe test_macros.py
"""

import ctypes
import json
import tempfile
import time
import unittest
from pathlib import Path

import sk65_studio as s

FAKE_DEVICE = {"path": b"fake", "vendor_id": 0x0603, "product_id": 0x0351, "usage_page": 0xFF02,
               "usage": 1, "interface_number": 1, "release_number": 256, "product_string": "lucky65"}
A, B, SHIFT = 0x41, 0x42, 0xA0


def macro(steps, repeat=1, held=False, fixed=False):
    return {"steps": steps, "repeat": repeat, "held": held, "fixed": fixed}


def macros(**slots):
    result = s.empty_macros()
    for name, value in slots.items():
        result[int(name[1:])] = value
    return result


def layers(base=None, fn=None, fn2=None):
    return [dict(base or {}), dict(fn or {}), dict(fn2 or {})]


class Packets(unittest.TestCase):
    STEPS = [["a", True, 0], ["a", False, 300]]

    def test_header_and_size(self):
        packet = s.build_macro_packet(3, macro(self.STEPS))
        self.assertEqual(len(packet), 620)
        self.assertEqual(packet[:8], bytes((7, 5, 1, 3, 0, 0, 0, 0)))

    def test_steps_match_the_vendor_layout(self):
        packet = s.build_macro_packet(0, macro(self.STEPS, repeat=3))
        self.assertEqual(packet[8:10], bytes((0, 3)))  # repeat, high byte first
        # Below the 20 ms minimum is raised to it; the top bit means key down.
        self.assertEqual(packet[10:13], bytes((0x80, 20, 0x04)))
        self.assertEqual(packet[13:16], bytes((300 >> 8, 300 & 0xFF, 0x04)))
        self.assertEqual(packet[16:], bytes(620 - 16))

    def test_long_delay_is_capped_at_15_bits(self):
        packet = s.build_macro_packet(0, macro([["a", True, 90000], ["a", False, 90000]]))
        self.assertEqual(packet[10:12], bytes((0xFF, 0xFF)))
        self.assertEqual(packet[13:15], bytes((0x7F, 0xFF)))

    def test_once_is_sent_as_two_so_a_tap_does_not_cut_it_off(self):
        packet = s.build_macro_packet(0, macro(self.STEPS))
        self.assertEqual(packet[8:10], bytes((0, 2)))
        self.assertEqual(packet[16:], bytes(620 - 16))  # the steps are not doubled

    def test_twice_is_sent_as_doubled_steps(self):
        packet = s.build_macro_packet(0, macro(self.STEPS, repeat=2))
        self.assertEqual(packet[8:10], bytes((0, 2)))
        self.assertEqual(packet[10:16], packet[16:22])
        self.assertEqual(packet[22:], bytes(620 - 22))

    def test_three_or_more_are_sent_as_they_are(self):
        for count in (3, 4, 500):
            self.assertEqual(s.macro_playback(macro(self.STEPS, repeat=count)), (count, self.STEPS))

    def test_macro_too_long_to_double_is_refused_before_saving(self):
        steps = [["a", i % 2 == 0, 50] for i in range(s.MACRO_MAX_STEPS // 2 + 2)]
        with self.assertRaisesRegex(ValueError, "exactly twice"):
            s.build_save_packets(layers({"b": "macro0"}), macros(m0=macro(steps, repeat=2)))
        self.assertEqual(s.macro_playback(macro(steps[:s.MACRO_MAX_STEPS // 2], repeat=2))[0], 2)

    def test_doubled_macro_still_fits_the_vendor_buffer(self):
        steps = [["a", i % 2 == 0, 50] for i in range(s.MACRO_MAX_STEPS // 2)]
        self.assertEqual(len(s.macro_playback(macro(steps, repeat=2))[1]), s.MACRO_MAX_STEPS)

    def test_repeat_while_held_uses_the_vendor_value(self):
        packet = s.build_macro_packet(0, macro(self.STEPS, repeat=5, held=True))
        self.assertEqual(packet[8:10], b"\xff\xff")

    def test_fixed_timing_ignores_recorded_delays(self):
        packet = s.build_macro_packet(0, macro(self.STEPS, fixed=True))
        self.assertEqual(packet[13:15], bytes((0, 20)))

    def test_longest_macro_fits_the_vendor_buffer(self):
        steps = [["a", i % 2 == 0, 50] for i in range(s.MACRO_MAX_STEPS)]
        packet = s.build_macro_packet(0, macro(steps))
        self.assertLessEqual(2 + 3 * s.MACRO_MAX_STEPS, 256)
        self.assertEqual(packet[8 + 2 + 3 * s.MACRO_MAX_STEPS:], bytes(620 - 8 - 2 - 3 * s.MACRO_MAX_STEPS))

    def test_key_points_at_its_slot_on_each_layer(self):
        packet = s.build_keymap_packet(layers({"a": "macro4"}, {"j": "macro1"}))
        a = 8 + s.MATRIX.index("a") * 2
        j = 8 + 204 + s.MATRIX.index("j") * 2
        self.assertEqual(packet[a:a + 2], bytes((0x80, 0xF0)))
        self.assertEqual(packet[j:j + 2], bytes((0x80, 0xF1)))

    def test_two_keys_can_share_a_macro(self):
        self.assertEqual(s.macro_slots(layers({"a": "macro2", "b": "macro2"})), {"macro2": 0})

    def test_save_sends_keymap_then_each_used_macro(self):
        packets = s.build_save_packets(layers({"a": "macro7", "b": "macro2"}),
                                       macros(m7=macro(self.STEPS), m2=macro(self.STEPS, repeat=9), m5=macro(self.STEPS)))
        self.assertEqual([p[:2] for p in packets], [bytes((7, 3)), bytes((7, 5)), bytes((7, 5))])
        self.assertEqual([p[3] for p in packets[1:]], [0, 1])
        self.assertEqual(packets[2][9], 9)  # slot 1 holds macro 2, set to run 9 times

    def test_empty_macro_on_a_key_is_refused(self):
        with self.assertRaisesRegex(ValueError, "Macro 3"):
            s.build_save_packets(layers({"a": "macro3"}), s.empty_macros())

    def test_save_without_macros_is_only_the_keymap(self):
        self.assertEqual(len(s.build_save_packets(layers({"a": "b"}), s.empty_macros())), 1)

    def test_media_keys_cannot_be_steps(self):
        self.assertTrue(s.macro_key("left shift"))
        self.assertTrue(s.macro_key("a"))
        self.assertFalse(s.macro_key("volume up"))
        self.assertFalse(s.macro_key("fn2"))
        self.assertFalse(s.macro_key("(disabled)"))

    def test_macros_can_go_on_every_layer(self):
        for layer in range(3):
            self.assertTrue(s.storable(layer, "macro0"))


class Profiles(unittest.TestCase):
    def test_round_trip_and_cleaning(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "p.json"
            profile = s.default_profile()
            profile["macros"][1] = macro([["a", True, 10], ["a", False, 20]], repeat=4, held=True, fixed=True)
            s.save_profile(profile, path)
            self.assertEqual(s.read_profile(path)["macros"][1], profile["macros"][1])
            profile["macros"] = [{"steps": [["volume up", True, 1], "bad", ["a", True, 5]], "repeat": 0}, "nope"]
            s.save_profile(profile, path)
            loaded = s.read_profile(path)["macros"]
            self.assertEqual(len(loaded), s.MACRO_COUNT)
            self.assertEqual(loaded[0]["steps"], [["a", True, 5]])
            self.assertEqual(loaded[0]["repeat"], 1)

    def test_profile_from_before_macros_still_loads(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "p.json"
            path.write_text(json.dumps({"remaps": [{}, {}, {}]}), encoding="utf-8")
            self.assertEqual(s.read_profile(path)["macros"], s.empty_macros())

    def test_knob_cannot_hold_a_macro(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "p.json"
            path.write_text(json.dumps({"knob": {"cw": "macro1"}}), encoding="utf-8")
            self.assertEqual(s.read_profile(path)["knob"]["cw"], "volume up")


class Hook(unittest.TestCase):
    def setUp(self):
        self.events = []
        self.engine = s.Engine(self.events.append)
        self.saved = s.user32.GetForegroundWindow
        s.user32.GetForegroundWindow = lambda: 111

    def tearDown(self):
        s.user32.GetForegroundWindow = self.saved

    def key(self, vk, flags=0):
        event = s.KBDLLHOOKSTRUCT(vk, 0, flags, 77, 0)
        return self.engine._on_event(0, s.WM_KEYDOWN, ctypes.addressof(event))

    def test_keys_are_recorded_and_swallowed_in_our_window(self):
        self.engine.record_window = 111
        self.assertEqual(self.key(A), 1)
        self.assertEqual(self.events, [("record", A, True, 77)])

    def test_other_windows_keep_their_keys(self):
        self.engine.record_window = 222
        self.assertNotEqual(self.key(A), 1)
        self.assertEqual(self.events, [])

    def test_injected_keys_are_not_recorded(self):
        self.engine.record_window = 111
        self.assertNotEqual(self.key(A, flags=s.LLKHF_INJECTED), 1)
        self.assertEqual(self.events, [])

    def test_no_recording_means_no_swallowing(self):
        self.assertNotEqual(self.key(A), 1)


class App(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        def no_device(*_args):
            raise AssertionError("a test tried to open the keyboard")

        cls.saved = {name: getattr(s, name) for name in ("write_report", "save_profile", "load_profile", "probe")}
        cls.saved_hid = s.hid.device, s.hid.enumerate
        cls.saved_gap = s.WRITE_GAP_SECONDS
        cls.sent = []
        s.WRITE_GAP_SECONDS = 0.01
        s.hid.device = no_device
        s.hid.enumerate = lambda *_args: [FAKE_DEVICE]
        s.write_report = lambda _devices, page, packet: cls.sent.append((page, packet))
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
        s.WRITE_GAP_SECONDS = cls.saved_gap

    @classmethod
    def spin(cls, seconds):
        end = time.time() + seconds
        while time.time() < end:
            cls.app.update()
            time.sleep(0.005)

    def setUp(self):
        app = self.app
        if app.recording:
            app.stop_record()
        app.profile.update(s.default_profile())
        app.macro_index = 0
        app.layer = 0
        app.kb.selected = None
        app.set_page("configure")
        app.set_menu("KEYMAP")
        self.sent.clear()
        self.spin(0.05)

    def record(self, *events):
        self.app.toggle_record()
        for vk, down, ms in events:
            self.app.on_record(vk, down, ms)

    def test_recorder_stores_gaps_between_keys(self):
        self.record((A, True, 1000), (A, False, 1090), (B, True, 1300), (B, False, 1350))
        self.app.toggle_record()
        self.assertEqual(self.app.macro()["steps"], [["a", True, 0], ["a", False, 90], ["b", True, 210], ["b", False, 50]])

    def test_held_key_repeats_are_ignored(self):
        self.record((A, True, 0), (A, True, 30), (A, True, 60), (A, False, 90))
        self.app.toggle_record()
        self.assertEqual(len(self.app.macro()["steps"]), 2)

    def test_key_up_from_before_recording_is_ignored(self):
        self.record((A, False, 0), (B, True, 10), (B, False, 20))
        self.app.toggle_record()
        self.assertEqual(self.app.macro()["steps"][0], ["b", True, 0])

    def test_stop_releases_keys_still_down(self):
        self.record((SHIFT, True, 0), (A, True, 10))
        self.app.toggle_record()
        steps = self.app.macro()["steps"]
        self.assertEqual({(key, down) for key, down, _ in steps[2:]}, {("left shift", False), ("a", False)})

    def test_recording_stops_itself_at_the_limit(self):
        self.app.toggle_record()
        for i in range(s.MACRO_MAX_STEPS * 2):
            self.app.on_record(A, i % 2 == 0, i * 30)
        self.assertFalse(self.app.recording)
        self.assertLessEqual(len(self.app.macro()["steps"]), s.MACRO_MAX_STEPS)
        self.assertIsNone(self.app.engine.record_window)

    def test_media_key_is_skipped_with_a_message(self):
        self.record((0xAF, True, 0))
        self.assertEqual(self.app.macro()["steps"], [])
        self.assertIn("can't go in a macro", self.app.status_text)
        self.app.toggle_record()

    def test_leaving_the_page_stops_recording(self):
        self.app.set_menu("MACROS")
        self.record((A, True, 0))
        self.app.set_menu("KEYMAP")
        self.assertFalse(self.app.recording)
        self.assertIsNone(self.app.engine.record_window)
        self.assertEqual(self.app.macro()["steps"][-1], ["a", False, s.MACRO_MIN_DELAY])

    def test_repeat_controls(self):
        self.app.change_repeat(-1)
        self.assertEqual(self.app.macro()["repeat"], 1)
        self.app.change_repeat(1)
        self.assertEqual(self.app.macro()["repeat"], 2)
        self.app.toggle_held()
        self.assertTrue(self.app.macro()["held"])
        self.app.change_repeat(1)
        self.assertFalse(self.app.macro()["held"])

    def test_software_remap_leaves_macro_keys_alone(self):
        self.app.profile["remaps"][0] = {"a": "macro0", "b": "c"}
        self.assertEqual(self.app.software_remaps(), {"b": "c"})

    def test_knob_refuses_macros(self):
        self.app.kb.selected = "knob"
        self.assertIn("keyboard's memory", self.app.code_refusal("macro0"))
        self.app.kb.selected = "a"
        self.assertEqual(self.app.code_refusal("macro0"), "")

    def test_save_writes_keymap_and_macro_in_order(self):
        app = self.app
        app.profile["macros"][2] = macro([["a", True, 0], ["a", False, 40]])
        app.profile["remaps"][0] = {"b": "macro2"}
        self.assertIn("unsaved", app.memory_status())
        app.ask_write("keymap")
        self.assertEqual(self.sent, [])  # nothing is sent until Confirm
        app.confirm_write("keymap")
        self.spin(0.3)
        self.assertEqual([(page, packet[:2]) for page, packet in self.sent],
                         [(0xFF02, bytes((7, 3))), (0xFF02, bytes((7, 5)))])
        self.assertEqual(app.memory_status(), "1 key saved")

    def test_editing_a_saved_macro_marks_it_unsaved(self):
        app = self.app
        app.profile["macros"][0] = macro([["a", True, 0], ["a", False, 40]])
        app.profile["remaps"][0] = {"b": "macro0"}
        app.confirm_write("keymap")
        self.spin(0.3)
        app.change_repeat(1)
        self.assertIn("unsaved", app.memory_status())

    def test_saving_with_an_empty_macro_stops_before_confirm(self):
        app = self.app
        app.profile["remaps"][0] = {"b": "macro6"}
        app.ask_write("keymap")
        self.assertIsNone(app.confirm)
        self.assertIn("Macro 6", app.status_text)
        self.assertEqual(self.sent, [])

    def test_restore_sends_only_the_keymap(self):
        app = self.app
        app.profile["remaps"][0] = {"b": "macro6"}
        app.confirm_write("clear")
        self.spin(0.2)
        self.assertEqual([packet[:2] for _page, packet in self.sent], [bytes((7, 3))])

    def test_macro_page_draws_with_steps(self):
        app = self.app
        app.profile["macros"][0] = macro([["left shift", True, 0], ["1", True, 50], ["1", False, 50], ["left shift", False, 50]])
        app.set_menu("MACROS")
        self.spin(0.2)
        self.assertTrue(any(hit[2] == "record" for hit in app.content.hits))
        self.assertTrue(any(hit[2] == "macro9" for hit in app.content.hits))


if __name__ == "__main__":
    unittest.main()
