"""Tests for Fn and Fn2 layer editing. Nothing here touches the keyboard.

Run from this folder:  .venv\\Scripts\\python.exe -m unittest test_layers -v

write_report is replaced by a recorder and hid.device by a class that fails on
use, so a real write cannot happen. Expected offsets are written out as numbers
instead of being computed the way build_keymap_packet computes them.
"""

import json
import tempfile
import time
import unittest
from pathlib import Path

import sk65_studio as s

LAYER_START = (8, 212, 416)
HEADER = bytes((0x07, 0x03, 0x01))
FN_OFFSETS = (198, 402, 606)  # position 95 on each layer
FAKE_DEVICE = {"usage_page": 0xFF02, "path": b"fake", "vendor_id": 0x0603, "product_id": 0x0351}


def at(layer, position):
    return LAYER_START[layer] + position * 2


def layers(base=None, fn=None, fn2=None):
    return [dict(base or {}), dict(fn or {}), dict(fn2 or {})]


FACTORY = s.build_keymap_packet(layers())


def changed(packet):
    return [i for i, (a, b) in enumerate(zip(packet, FACTORY)) if a != b]


class Packet(unittest.TestCase):
    def test_layout_fills_the_report_exactly(self):
        self.assertEqual(len(FACTORY), 620)
        self.assertEqual(LAYER_START[2] + 102 * 2, 620)
        self.assertEqual(FACTORY[:3], HEADER)
        self.assertEqual(FACTORY[3:8], bytes(5))

    def test_factory_packet_matches_the_vendor_on_all_layers(self):
        for layer in range(3):
            for position in range(102):
                listed = position in {s.MATRIX.index(b) for b in s.MATRIX_BUTTONS}
                code = s.MATRIX_DEFAULTS[position] if listed else 0
                self.assertEqual(FACTORY[at(layer, position):at(layer, position) + 2], bytes((0, code)),
                                 (layer, position))

    def test_each_layer_lands_at_its_own_offset(self):
        for layer, offset in enumerate((172, 376, 580)):  # page down is position 82
            remaps = layers()
            remaps[layer]["page down"] = "end"
            packet = s.build_keymap_packet(remaps)
            self.assertEqual(packet[offset:offset + 2], bytes((0x80, 0x4D)))
            self.assertEqual(changed(packet), [offset, offset + 1])

    def test_first_and_last_keys_of_each_layer(self):
        for layer, first, last in ((0, 42, 206), (1, 246, 410), (2, 450, 614)):  # esc 17, right 99
            remaps = layers()
            remaps[layer].update({"esc": "a", "right": "b"})
            packet = s.build_keymap_packet(remaps)
            self.assertEqual(packet[first:first + 2], bytes((0x80, 0x04)))
            self.assertEqual(packet[last:last + 2], bytes((0x80, 0x05)))
            self.assertEqual(changed(packet), [first, first + 1, last, last + 1])

    def test_every_key_on_every_layer(self):
        for layer in range(3):
            for key in s.REMAPPABLE:
                remaps = layers()
                remaps[layer][key] = "(disabled)"
                offset = at(layer, s.MATRIX.index(s.KEY_TO_MATRIX[key]))
                packet = s.build_keymap_packet(remaps)
                self.assertEqual(packet[offset:offset + 2], bytes((0x80, 0x00)), key)
                self.assertTrue(set(changed(packet)) <= {offset, offset + 1}, key)

    def test_layers_do_not_disturb_each_other(self):
        packet = s.build_keymap_packet(layers({"a": "b"}, {"a": "c"}, {"a": "d"}))  # a is position 52
        self.assertEqual(packet[112:114], bytes((0x80, 0x05)))
        self.assertEqual(packet[316:318], bytes((0x80, 0x06)))
        self.assertEqual(packet[520:522], bytes((0x80, 0x07)))
        self.assertEqual(changed(packet), [112, 113, 316, 317, 520, 521])

    def test_fn_key_survives_every_key_remapped_on_every_layer(self):
        everything = {key: "a" for key in s.REMAPPABLE}
        packet = s.build_keymap_packet([everything, everything, everything])
        for offset in FN_OFFSETS:
            self.assertEqual(packet[offset:offset + 2], bytes((0x00, 0xFE)))

    def test_fn_key_is_not_a_valid_source(self):
        for layer in range(3):
            remaps = layers()
            remaps[layer]["fn"] = "a"
            with self.assertRaises(ValueError):
                s.build_keymap_packet(remaps)

    def test_fn2_key_goes_on_base_and_fn2_like_the_vendor(self):
        packet = s.build_keymap_packet(layers({"right ctrl": "fn2"}))  # position 96
        self.assertEqual(packet[200:202], bytes((0x80, 0xFF)))
        self.assertEqual(packet[404:406], FACTORY[404:406])
        self.assertEqual(packet[608:610], bytes((0x80, 0xFF)))
        self.assertEqual(changed(packet), [200, 201, 608, 609])

    def test_fn2_mirror_beats_a_stale_layer_2_entry(self):
        packet = s.build_keymap_packet(layers({"a": "fn2"}, fn2={"a": "z"}))
        self.assertEqual(packet[520:522], bytes((0x80, 0xFF)))

    def test_refuses_what_a_layer_cannot_hold(self):
        for bad in (layers({"a": "wheel up"}), layers(fn={"a": "volume mute"}), layers(fn2={"a": "wheel up"}),
                    layers(fn={"a": "fn2"}), layers(fn2={"a": "fn2"}), layers(fn={"knob": "a"})):
            with self.assertRaises(ValueError, msg=bad):
                s.build_keymap_packet(bad)

    def test_refusal_names_the_layer(self):
        with self.assertRaisesRegex(ValueError, "Fn layer A to Mouse wheel up"):
            s.build_keymap_packet(layers(fn={"a": "wheel up"}))


class Profiles(unittest.TestCase):
    def load(self, data):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "profile.json"
            path.write_text(json.dumps(data), encoding="utf-8")
            return s.read_profile(path)

    def test_old_profile_moves_to_the_base_layer(self):
        old = {"remaps": {"page down": "end"}, "flashed": {"page down": "end"}}
        profile = self.load(old)
        self.assertEqual(profile["remaps"], layers({"page down": "end"}))
        self.assertEqual(profile["flashed"], layers({"page down": "end"}))

    def test_old_key_ids_are_not_mistaken_for_layer_numbers(self):
        old = {"remaps": {"1": "f1", "2": "f2", "0": "f10"}}
        self.assertEqual(self.load(old)["remaps"], layers({"1": "f1", "2": "f2", "0": "f10"}))

    def test_profile_from_before_flashed_existed(self):
        profile = self.load({"vid": "0x0603", "remaps": {}, "lighting": {
            "effect": "wave", "color": "#E8C4B8", "brightness": 80, "speed": 45}})
        self.assertEqual(profile["remaps"], layers())
        self.assertEqual(profile["flashed"], layers())

    def test_profiles_on_this_machine_still_load(self):
        here = Path(__file__).resolve().parent
        for path in (here / "sk65_profile.json", here / "dist" / "sk65_profile.json"):
            if path.exists():
                profile = s.read_profile(path)
                self.assertEqual(len(profile["remaps"]), 3, path)
                self.assertEqual(len(profile["flashed"]), 3, path)

    def test_layers_round_trip(self):
        original = s.default_profile()
        original["remaps"] = layers({"a": "fn2"}, {"j": "home"}, {"k": "end"})
        original["flashed"] = layers(fn={"j": "home"})
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "profile.json"
            s.save_profile(original, path)
            loaded = s.read_profile(path)
        self.assertEqual(loaded["remaps"], original["remaps"])
        self.assertEqual(loaded["flashed"], original["flashed"])

    def test_bad_shapes_become_empty_layers(self):
        for bad in ("x", 3, None, []):
            self.assertEqual(self.load({"remaps": bad})["remaps"], layers(), bad)
        self.assertEqual(self.load({"remaps": [{"a": "b"}]})["remaps"], layers({"a": "b"}))
        self.assertEqual(self.load({"remaps": [{}, {}, {}, {"a": "b"}]})["remaps"], layers())
        self.assertEqual(self.load({"remaps": ["x", {"a": "b"}, 5]})["remaps"], layers(fn={"a": "b"}))

    def test_unstorable_codes_are_dropped_from_fn_layers_only(self):
        profile = self.load({"remaps": [{"a": "wheel up"}, {"a": "wheel up", "b": "c"}, {"a": "fn2"}]})
        self.assertEqual(profile["remaps"], layers({"a": "wheel up"}, {"b": "c"}))

    def test_knob_cannot_hold_fn2(self):
        self.assertEqual(self.load({"knob": {"cw": "fn2"}})["knob"]["cw"], s.KNOB_DEFAULTS["cw"])


class Stubbed(unittest.TestCase):
    """Drives the real App with writes recorded instead of sent."""

    @classmethod
    def setUpClass(cls):
        def no_device(*_args):
            raise AssertionError("a test tried to open the keyboard")

        cls.saved = {name: getattr(s, name) for name in ("write_report", "save_profile", "load_profile", "probe")}
        cls.saved_hid = s.hid.device, s.hid.enumerate
        s.hid.device = no_device
        s.hid.enumerate = lambda *_args: [FAKE_DEVICE]
        s.write_report = lambda devices, page, packet: cls.sent.append((page, packet))
        s.save_profile = lambda *_args, **_kwargs: None
        s.load_profile = s.default_profile
        s.probe = lambda devices: {"config": (True, "stub"), "firmware": (True, "stub")}
        cls.sent = []
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

    def setUp(self):
        self.sent.clear()
        app = self.app
        app.profile = s.default_profile()
        app.layer = 0
        app.kb.selected = app.enc_slot = None
        app.sync_engine()

    def put(self, layer, key, code):
        self.app.set_layer(layer)
        self.app.key_clicked(key)
        self.app.assign(code)

    def save(self, kind="keymap"):
        self.app.ask_write(kind)
        self.assertEqual(self.sent, [], "asking must not write")
        self.app.confirm_write(kind)
        self.spin(0.1)

    def test_save_sends_one_report_holding_all_layers(self):
        self.put(0, "page down", "end")
        self.put(1, "j", "home")
        self.put(2, "k", "end")
        self.save()
        self.assertEqual(len(self.sent), 1)
        page, packet = self.sent[0]
        self.assertEqual(page, 0xFF02)
        self.assertEqual(packet[172:174], bytes((0x80, 0x4D)))  # j is 58, k is 59
        self.assertEqual(packet[328:330], bytes((0x80, 0x4A)))
        self.assertEqual(packet[534:536], bytes((0x80, 0x4D)))
        self.assertEqual(len(changed(packet)), 6)

    def test_flashed_record_is_a_copy_of_what_was_sent(self):
        self.put(1, "j", "home")
        self.save()
        flashed = self.app.profile["flashed"]
        self.assertEqual(flashed, layers(fn={"j": "home"}))
        self.assertEqual(self.app.memory_status(), "1 key saved")
        self.put(1, "k", "end")
        self.assertEqual(flashed, layers(fn={"j": "home"}))
        self.assertEqual(self.app.memory_status(), "1 key saved, unsaved changes")

    def test_edit_during_a_write_is_not_recorded_as_saved(self):
        self.put(1, "j", "home")
        self.app.ask_write("keymap")
        self.app.confirm_write("keymap")
        self.put(1, "k", "end")  # before the worker's result is handled
        self.spin(0.1)
        self.assertEqual(self.app.profile["flashed"], layers(fn={"j": "home"}))

    def test_restore_clears_the_record_and_keeps_the_edits(self):
        self.put(1, "j", "home")
        self.save()
        self.sent.clear()
        self.save("clear")
        self.assertEqual(self.sent[0][1], FACTORY)
        self.assertEqual(self.app.profile["flashed"], layers())
        self.assertEqual(self.app.memory_status(), "Factory keymap, unsaved changes")

    def test_memory_status_with_nothing_saved(self):
        self.assertEqual(self.app.memory_status(), "Factory keymap")

    def test_reset_clears_every_layer(self):
        self.put(0, "a", "b")
        self.put(1, "a", "b")
        self.put(2, "a", "b")
        self.app.reset_remaps()
        self.assertEqual(self.app.profile["remaps"], layers())

    def test_own_code_is_a_change_on_fn_only(self):
        self.put(0, "a", "a")
        self.put(1, "a", "a")
        self.put(2, "a", "a")
        self.assertEqual(self.app.profile["remaps"], layers(fn={"a": "a"}))

    def test_default_removes_the_entry_on_each_layer(self):
        for layer in range(3):
            self.put(layer, "a", "b")
            self.put(layer, "a", "(default)")
        self.assertEqual(self.app.profile["remaps"], layers())

    def test_software_uses_the_base_layer_only(self):
        self.put(0, "a", "b")
        self.put(1, "c", "d")
        self.put(2, "e", "f")
        self.assertEqual(self.app.software_remaps(), {"a": "b"})
        self.assertEqual(self.app.engine.map, {0x41: "b"})

    def test_software_skips_fn2_so_the_key_does_not_go_dead(self):
        self.put(0, "a", "fn2")
        self.put(0, "c", "d")
        self.assertEqual(self.app.software_remaps(), {"c": "d"})
        self.assertNotIn(0x41, self.app.engine.map)

    def test_saved_fn_layer_leaves_software_base_remaps_running(self):
        self.put(1, "j", "home")
        self.save()
        self.put(0, "a", "b")
        self.assertEqual(self.app.engine.map, {0x41: "b"})

    def test_saved_base_layer_stops_software_remaps(self):
        self.put(0, "a", "b")
        self.save()
        self.assertEqual(self.app.engine.map, {})

    def test_fn2_warning_follows_layer_0(self):
        app = self.app
        self.assertFalse(app.fn2_warning())
        app.set_layer(2)
        self.assertTrue(app.fn2_warning())
        app.set_layer(0)
        self.put(1, "a", "b")
        self.assertFalse(app.fn2_warning())
        self.put(2, "a", "b")
        app.set_layer(0)
        self.assertTrue(app.fn2_warning())
        self.put(0, "right ctrl", "fn2")
        self.assertFalse(app.fn2_warning())
        app.set_layer(2)
        self.assertFalse(app.fn2_warning())

    def test_fn2_key_is_locked_on_layer_2_only(self):
        app = self.app
        self.put(0, "right ctrl", "fn2")
        self.assertTrue(app.selectable("right ctrl"))
        app.set_layer(1)
        self.assertTrue(app.selectable("right ctrl"))
        app.key_clicked("right ctrl")
        app.set_layer(2)
        self.assertFalse(app.selectable("right ctrl"))
        self.assertIsNone(app.kb.selected)

    def test_assigning_fn2_drops_an_old_layer_2_entry(self):
        self.put(2, "a", "b")
        self.put(0, "a", "fn2")
        self.assertEqual(self.app.profile["remaps"], layers({"a": "fn2"}))

    def test_auto_advance_steps_over_a_locked_key(self):
        app = self.app
        keys = s.REMAPPABLE
        after = keys[keys.index("right ctrl") - 1]
        self.put(0, "right ctrl", "fn2")
        self.put(2, after, "a")
        self.assertEqual(app.kb.selected, keys[keys.index("right ctrl") + 1])

    def test_fn_key_cannot_be_selected_on_any_layer(self):
        for layer in range(3):
            self.app.set_layer(layer)
            self.assertFalse(self.app.selectable("fn"))

    def test_knob_is_base_layer_only(self):
        app = self.app
        app.key_clicked("knob")
        self.assertEqual(app.enc_slot, "ccw")
        app.set_layer(1)
        self.assertIsNone(app.kb.selected)
        self.assertIsNone(app.enc_slot)
        self.assertFalse(app.selectable("knob"))

    def test_keycode_refusals(self):
        app = self.app
        app.key_clicked("a")
        self.assertEqual(app.code_refusal("wheel up"), "")
        self.assertEqual(app.code_refusal("fn2"), "")
        for layer in (1, 2):
            app.set_layer(layer)
            self.assertTrue(app.code_refusal("wheel up"), layer)
            self.assertTrue(app.code_refusal("volume mute"), layer)
            self.assertTrue(app.code_refusal("fn2"), layer)
            self.assertEqual(app.code_refusal("home"), "")
            self.assertEqual(app.code_refusal("(disabled)"), "")
            self.assertEqual(app.code_refusal("(default)"), "")
        app.set_layer(0)
        app.key_clicked("knob")
        self.assertTrue(app.code_refusal("fn2"))
        self.assertEqual(app.code_refusal("wheel up"), "")


if __name__ == "__main__":
    unittest.main()
