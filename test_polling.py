"""Tests for the Polling Rate check. Nothing here touches the keyboard.

Run from this folder:  .venv\\Scripts\\python.exe test_polling.py
"""

import unittest

import sk65_studio as s

# The SK65's configuration descriptor as read from its hub: interface 0 is the
# boot keyboard with endpoint 81, then the two vendor interfaces.
SK65 = bytes.fromhex(
    "09026200030100a0f8"
    "090400000103010100" "092111010001223f00" "07058103400001"
    "090401000203000000" "09211101000122e900" "07058203400001" "07050203400001"
    "090402000203000000" "092111010001222100" "07058303400001" "07050303400001")


def config(*interfaces):
    """A configuration descriptor from (class, protocol, [(address, attributes, interval)])."""
    body = b""
    for number, (cls, protocol, endpoints) in enumerate(interfaces):
        body += bytes((9, 4, number, 0, len(endpoints), cls, 1 if protocol else 0, protocol, 0))
        for address, attributes, interval in endpoints:
            body += bytes((7, 5, address, attributes, 64, 0, interval))
    return bytes((9, 2, 9 + len(body), 0, len(interfaces), 1, 0, 0xA0, 0xFA)) + body


class Interval(unittest.TestCase):
    def test_sk65_is_1000_hz(self):
        self.assertEqual(s.key_poll_interval(1, SK65), 1.0)

    def test_keyboard_interface_wins_over_a_faster_one(self):
        desc = config((3, 0, [(0x81, 3, 1)]), (3, 1, [(0x82, 3, 8)]))
        self.assertEqual(s.key_poll_interval(1, desc), 8.0)

    def test_falls_back_to_the_fastest_hid_endpoint(self):
        desc = config((3, 0, [(0x01, 3, 1), (0x81, 3, 4)]), (3, 0, [(0x82, 3, 2)]))
        self.assertEqual(s.key_poll_interval(1, desc), 2.0)

    def test_ignores_out_bulk_and_non_hid_endpoints(self):
        desc = config((8, 0, [(0x81, 3, 1)]), (3, 1, [(0x02, 3, 1), (0x83, 2, 1), (0x84, 3, 4)]))
        self.assertEqual(s.key_poll_interval(1, desc), 4.0)

    def test_full_speed_rounds_down_to_a_power_of_two(self):
        for interval, ms in ((1, 1), (2, 2), (3, 2), (7, 4), (10, 8), (31, 16), (32, 32), (255, 32)):
            self.assertEqual(s.key_poll_interval(1, config((3, 1, [(0x81, 3, interval)]))), ms, interval)

    def test_low_speed_follows_the_same_rule(self):
        self.assertEqual(s.key_poll_interval(0, config((3, 1, [(0x81, 3, 10)]))), 8.0)

    def test_high_speed_counts_microframes(self):
        for interval, ms in ((1, 0.125), (2, 0.25), (4, 1.0), (7, 8.0)):
            self.assertEqual(s.key_poll_interval(2, config((3, 1, [(0x81, 3, interval)]))), ms, interval)

    def test_broken_descriptors_do_not_crash(self):
        for bad in (b"", b"\x00\x00", SK65[:30], b"\x09\x02" + bytes(7) + b"\x00\x05", config((3, 1, [(0x81, 3, 0)]))):
            self.assertIsNone(s.key_poll_interval(1, bad), bad.hex())


class Text(unittest.TestCase):
    def setUp(self):
        self.real = s.read_usb_config

    def tearDown(self):
        s.read_usb_config = self.real

    def test_reports_hz_interval_and_speed(self):
        s.read_usb_config = lambda vid, pid: (1, SK65)
        self.assertEqual(s.polling_rate(0x0603, 0x0351), (True, "1000 Hz, every 1 ms (full speed USB)"))

    def test_high_speed_text(self):
        s.read_usb_config = lambda vid, pid: (2, config((3, 1, [(0x81, 3, 1)])))
        self.assertEqual(s.polling_rate(1, 2), (True, "8000 Hz, every 0.125 ms (high speed USB)"))

    def test_read_failure_is_a_failed_check(self):
        def fail(vid, pid):
            raise OSError("Keyboard not on USB")
        s.read_usb_config = fail
        self.assertEqual(s.polling_rate(1, 2), (False, "Could not read: Keyboard not on USB"))

    def test_no_key_endpoint(self):
        s.read_usb_config = lambda vid, pid: (1, config((8, 0, [(0x81, 2, 0)])))
        self.assertEqual(s.polling_rate(1, 2), (False, "No key endpoint found"))

    def test_probe_without_a_keyboard(self):
        self.assertEqual(s.probe([])["polling"], (False, "Keyboard not found"))


if __name__ == "__main__":
    unittest.main()
