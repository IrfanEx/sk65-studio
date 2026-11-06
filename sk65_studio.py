"""Womier SK65 V1 Studio.

VIA-styled desktop configurator for the Womier SK65 (v1) on stock firmware.
The SK65 V1 is a Lucky65 V1 with a knob in place of Insert. It enumerates as
USB 0603:0351 with the product string "lucky65".

What works today:
  * Saving all three keymap layers (base, Fn, Fn2), macros and lighting into
    the keyboard's memory, using the vendor protocol documented in PROTOCOL.md.
  * Software remapping of base layer keys and the knob with a native low-level
    keyboard hook. It is active while the app runs and needs no firmware
    support. Windows never sees the Fn key, so software cannot do Fn layers.
  * Live connection monitor and self-test.
  * Knob monitor: reads the keyboard's consumer-control interface directly, so
    a knob event is proven to come from this board.
  * Key tester, HID console, profiles stored in %APPDATA%\\SK65 Studio.
  * Optional minimize to the system tray, with one running copy at a time.

The firmware has no known setting for knob rotation, so the knob stays software.
"""

from __future__ import annotations

import ctypes
import json
import math
import os
import queue
import re
import sys
import threading
import time
from ctypes import wintypes
from pathlib import Path
from tkinter import filedialog

import customtkinter as ctk
import hid
import pystray
import tkinter as tk
from PIL import Image, ImageColor, ImageDraw, ImageFont, ImageTk

APP_NAME = "Womier SK65 Studio"
APP_VERSION = "1.5.2"  # the release workflow refuses a tag that differs

# Patched by the build script with the detected config interface.
DEFAULT_VID = "0x0603"
DEFAULT_PID = "0x0351"
DEFAULT_USAGE_PAGE = "0xFF02"

# Vendor protocol. See PROTOCOL.md. Interfaces come from the board's HID report
# descriptors, packet layouts from the official Womier software.
PROTOCOL = {
    "config_page": 0xFF02,   # feature report 7, 619 bytes: keymap and lighting
    "config_report": 7,
    "config_size": 620,      # including the report ID
    "aux_page": 0xFF03,      # feature report 8, 7 bytes
    "aux_report": 8,
    "consumer_page": 0x000C, # input report 3: 16-bit consumer usage (knob)
    "consumer_report": 3,
}

# VIA OLIVIA_DARK theme and dark-mode tokens, taken from the VIA source.
ACCENT = "#E8C4B8"          # --color_accent
INSIDE_ACCENT = "#363434"   # --color_inside-accent, keycap colour
DARK_GREY = "#414141"       # --bg_control, --bg_outside-accent, cell borders
MEDIUM_GREY = "#707070"     # --color_label, --border_color_icon
LIGHT_GREY = "#D9D9D9"      # --color_label-highlighted
LIGHT_JET = "#222222"       # --bg_menu
JET = "#1D1B1B"             # --color_jet
ERROR = "#D15E5E"           # --color_error
CANVAS_GRADIENT = (30, [(0.1, (150, 150, 150)), (0.5, (186, 157, 147)), (0.9, (150, 150, 150))])
WELL_GRADIENT = (200, [(0.4, (35, 29, 28)), (0.6, (58, 49, 46)), (0.8, (46, 39, 37))])

FONT_DIR = Path(r"C:\Windows\Fonts")
ICON_FONT = "segmdl2.ttf"
GLYPH_KEYBOARD, GLYPH_TEST, GLYPH_USB = "\uE765", "\uE9D9", "\uE88E"
GLYPH_LIGHT, GLYPH_SAVE, GLYPH_CONSOLE = "\uEA80", "\uE74E", "\uE756"

GLYPH_CHECK, GLYPH_CROSS, GLYPH_SETTINGS = chr(0xE73E), chr(0xE711), chr(0xE713)
GLYPH_MACRO = chr(0xE768)

# Limits from the vendor software. Its recorder stops at 80 steps, and 80 fit in
# the 256 bytes it sends per macro.
MACRO_COUNT = 10
MACRO_MAX_STEPS = 80
MACRO_MIN_DELAY, MACRO_MAX_DELAY = 20, 32767
MACRO_MAX_REPEAT = 65534
MACRO_REPEAT_HELD = 0xFFFF  # the firmware repeats until the key is released
WRITE_GAP_SECONDS = 0.2  # the vendor app's pause between macro reports
SYMBOL_FONT = "seguisym.ttf"

EXTENDED_VKS = set(range(0x21, 0x29)) | {0x2C, 0x2D, 0x2E, 0x5B, 0x5C, 0x5D, 0xA3, 0xA5} | set(range(0xA6, 0xB8))

# id, legend, virtual key, category, description. Ids match profile files.
KEYCODES = [
    ("(disabled)", "", None, "basic", "No key: the key does nothing"),
    ("(default)", "▽", None, "basic", "Default: remove the remap"),
    ("esc", "Esc", 0x1B, "basic", "Escape"),
] + [
    (c, c.upper(), ord(c.upper()), "basic", c.upper()) for c in "abcdefghijklmnopqrstuvwxyz"
] + [
    (c, f"{s}\n{c}", ord(c), "basic", c) for c, s in zip("1234567890", "!@#$%^&*()")
] + [
    (f"f{i}", f"F{i}", 0x6F + i, "basic", f"F{i}") for i in range(1, 13)
] + [
    ("-", "_\n-", 0xBD, "basic", "Minus"), ("=", "+\n=", 0xBB, "basic", "Equals"),
    ("[", "{\n[", 0xDB, "basic", "Left bracket"), ("]", "}\n]", 0xDD, "basic", "Right bracket"),
    ("\\", "|\n\\", 0xDC, "basic", "Backslash"), (";", ":\n;", 0xBA, "basic", "Semicolon"),
    ("'", "\"\n'", 0xDE, "basic", "Quote"), ("`", "~\n`", 0xC0, "basic", "Grave"),
    (",", "<\n,", 0xBC, "basic", "Comma"), (".", ">\n.", 0xBE, "basic", "Period"),
    ("/", "?\n/", 0xBF, "basic", "Slash"),
    ("enter", "Enter", 0x0D, "basic", "Enter"), ("backspace", "Bksp", 0x08, "basic", "Backspace"),
    ("tab", "Tab", 0x09, "basic", "Tab"), ("space", "Space", 0x20, "basic", "Space"),
    ("caps lock", "Caps", 0x14, "basic", "Caps Lock"),
    ("print screen", "Print", 0x2C, "basic", "Print Screen"),
    ("scroll lock", "Scroll", 0x91, "basic", "Scroll Lock"), ("pause", "Pause", 0x13, "basic", "Pause"),
    ("insert", "Ins", 0x2D, "basic", "Insert"), ("delete", "Del", 0x2E, "basic", "Delete"),
    ("home", "Home", 0x24, "basic", "Home"), ("end", "End", 0x23, "basic", "End"),
    ("page up", "PgUp", 0x21, "basic", "Page Up"), ("page down", "PgDn", 0x22, "basic", "Page Down"),
    ("up", "↑", 0x26, "basic", "Up"), ("down", "↓", 0x28, "basic", "Down"),
    ("left", "←", 0x25, "basic", "Left"), ("right", "→", 0x27, "basic", "Right"),
    ("left ctrl", "LCtl", 0xA2, "basic", "Left Control"), ("left shift", "LSft", 0xA0, "basic", "Left Shift"),
    ("left alt", "LAlt", 0xA4, "basic", "Left Alt"), ("left windows", "LWin", 0x5B, "basic", "Left Windows"),
    ("right ctrl", "RCtl", 0xA3, "basic", "Right Control"), ("right shift", "RSft", 0xA1, "basic", "Right Shift"),
    ("right alt", "RAlt", 0xA5, "basic", "Right Alt"), ("right windows", "RWin", 0x5C, "basic", "Right Windows"),
    ("menu", "Menu", 0x5D, "basic", "Application menu"),
    ("volume mute", "Mute", 0xAD, "media", "Mute audio"),
    ("volume down", "Vol -", 0xAE, "media", "Volume down"),
    ("volume up", "Vol +", 0xAF, "media", "Volume up"),
    ("play/pause media", "Play", 0xB3, "media", "Play or pause"),
    ("stop media", "Stop", 0xB2, "media", "Stop media"),
    ("previous track", "Prev", 0xB1, "media", "Previous track"),
    ("next track", "Next", 0xB0, "media", "Next track"),
    ("browser back", "Back", 0xA6, "media", "Browser back"),
    ("browser forward", "Fwd", 0xA7, "media", "Browser forward"),
    ("browser refresh", "Rfsh", 0xA8, "media", "Browser refresh"),
    ("browser search", "Find", 0xAA, "media", "Browser search"),
    ("launch mail", "Mail", 0xB4, "media", "Open mail"),
    ("launch calculator", "Calc", 0xB7, "media", "Open calculator"),
    ("launch computer", "PC", 0xB6, "media", "Open This PC"),
    ("wheel up", "Whl ↑", None, "special", "Mouse wheel up"),
    ("wheel down", "Whl ↓", None, "special", "Mouse wheel down"),
    ("wheel left", "Whl ←", None, "special", "Mouse wheel left"),
    ("wheel right", "Whl →", None, "special", "Mouse wheel right"),
    ("fn2", "Fn2", None, "special", "Fn2: hold this key to reach layer 2"),
] + [
    (f"macro{i}", f"M{i}", None, "macro", f"Macro {i}") for i in range(MACRO_COUNT)
]
KC = {k[0]: k for k in KEYCODES}
KC_BY_VK = {k[2]: k for k in KEYCODES if k[2] is not None}
MACRO_IDS = [f"macro{i}" for i in range(MACRO_COUNT)]
CATEGORIES = [("basic", "BASIC"), ("media", "MEDIA"), ("special", "SPECIAL"), ("macro", "MACRO")]
WHEEL_ACTIONS = {"wheel up": (0x0800, 120), "wheel down": (0x0800, -120),
                 "wheel left": (0x1000, -120), "wheel right": (0x1000, 120)}

# SK65 ANSI: 66 keys plus the knob where a Lucky65 has Insert.
# (key id or "knob" or "fn", width in units, accent key?)
LAYOUT = [
    [("esc", 1, True)] + [(c, 1, False) for c in "1234567890-="] + [("backspace", 2, False), ("knob", 1, False)],
    [("tab", 1.5, False)] + [(c, 1, False) for c in "qwertyuiop[]"] + [("\\", 1.5, False), ("delete", 1, False)],
    [("caps lock", 1.75, False)] + [(c, 1, False) for c in "asdfghjkl;'"] + [("enter", 2.25, True), ("page up", 1, False)],
    [("left shift", 2.25, False)] + [(c, 1, False) for c in "zxcvbnm,./"]
    + [("right shift", 1.75, False), ("up", 1, False), ("page down", 1, False)],
    [("left ctrl", 1.25, False), ("left windows", 1.25, False), ("left alt", 1.25, False), ("space", 6.25, False),
     ("fn", 1.25, False), ("right ctrl", 1.25, False), (None, 0.5, False),
     ("left", 1, False), ("down", 1, False), ("right", 1, False)],
]
UNITS_W, UNITS_H = 16, 5
KEYS = []
for _y, _row in enumerate(LAYOUT):
    _x = 0.0
    for _id, _w, _accent in _row:
        if _id:
            KEYS.append({"id": _id, "x": _x, "y": _y, "w": _w, "accent": _accent})
        _x += _w
REMAPPABLE = [k["id"] for k in KEYS if k["id"] not in ("knob", "fn")]
KEY_LEGENDS = {"left shift": "Shift", "right shift": "Shift", "left ctrl": "Ctrl", "right ctrl": "Ctrl",
               "left alt": "Alt", "left windows": "Win", "backspace": "Backspace", "caps lock": "Caps Lock"}

# Firmware key matrix from the vendor software: 6 rows of 17, "." is unused.
MATRIX = """
. . . . . . . . . . . . . . . . .
esc n1 n2 n3 n4 n5 n6 n7 n8 n9 n0 minus plus . bksp . .
tab q w e r t y u i o p lqu rqu k29 delete . .
caps a s d f g h j k l sem quo k42 enter pageup . .
lshift . z x c v b n m comma dot qmark rshift up pagedown . .
lctrl win lalt lspace ralt . mspace rspace mute rspace fn rctrl left down right . .
""".split()
MATRIX_DEFAULTS = bytes.fromhex("""
00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00
29 1e 1f 20 21 22 23 24 25 26 27 2d 2e 00 2a 00 00
2b 14 1a 08 15 17 1c 18 0c 12 13 2f 30 31 4c 00 00
39 04 16 07 09 0a 0b 0d 0e 0f 33 34 31 28 4b 00 00
e1 00 1d 1b 06 19 05 11 10 36 37 38 e5 52 4e 00 00
e0 e3 e2 2c e6 00 2c 00 a2 2c fe e4 50 51 4f 00 00
""")
# Positions the vendor app writes. Anything else stays 00 00.
MATRIX_BUTTONS = """
esc n1 n2 n3 n4 n5 n6 n7 n8 n9 n0 minus plus bksp
tab q w e r t y u i o p lqu rqu k29 delete
caps a s d f g h j k l sem quo k42 enter pageup
lshift z x c v b n m comma dot qmark rshift up pagedown
lctrl win lalt lspace ralt mspace rspace fn rctrl left down right
""".split()
KEY_TO_MATRIX = {
    **{c: c for c in "abcdefghijklmnopqrstuvwxyz"}, **{c: f"n{c}" for c in "1234567890"},
    "esc": "esc", "-": "minus", "=": "plus", "backspace": "bksp", "tab": "tab", "[": "lqu", "]": "rqu",
    "\\": "k29", "delete": "delete", "caps lock": "caps", ";": "sem", "'": "quo", "enter": "enter",
    "page up": "pageup", "left shift": "lshift", ",": "comma", ".": "dot", "/": "qmark",
    "right shift": "rshift", "up": "up", "page down": "pagedown", "left ctrl": "lctrl",
    "left windows": "win", "left alt": "lalt", "space": "mspace", "right ctrl": "rctrl",
    "left": "left", "down": "down", "right": "right",
}
# Codes the firmware accepts as a custom key. Mute, calculator, search and the
# mouse wheel have no working code, so those stay software-only.
FIRMWARE_CODES = {
    **{c: 0x04 + i for i, c in enumerate("abcdefghijklmnopqrstuvwxyz")},
    **{c: 0x1E + i for i, c in enumerate("1234567890")},
    **{f"f{i}": 0x39 + i for i in range(1, 13)},
    "(disabled)": 0x00, "enter": 0x28, "esc": 0x29, "backspace": 0x2A, "tab": 0x2B, "space": 0x2C,
    "-": 0x2D, "=": 0x2E, "[": 0x2F, "]": 0x30, "\\": 0x31, ";": 0x33, "'": 0x34, "`": 0x35,
    ",": 0x36, ".": 0x37, "/": 0x38, "caps lock": 0x39, "print screen": 0x46, "scroll lock": 0x47,
    "pause": 0x48, "insert": 0x49, "home": 0x4A, "page up": 0x4B, "delete": 0x4C, "end": 0x4D,
    "page down": 0x4E, "right": 0x4F, "left": 0x50, "down": 0x51, "up": 0x52, "menu": 0x65,
    "left ctrl": 0xE0, "left shift": 0xE1, "left alt": 0xE2, "left windows": 0xE3,
    "right ctrl": 0xE4, "right shift": 0xE5, "right alt": 0xE6, "right windows": 0xE7,
    "play/pause media": 0xA1, "volume up": 0xA3, "volume down": 0xA4, "stop media": 0xA5,
    "previous track": 0xA6, "next track": 0xA7, "browser refresh": 0xA9, "browser back": 0xAB,
    "browser forward": 0xAC, "launch computer": 0xAF, "launch mail": 0xB1, "fn2": 0xFF,
}
LAYER_NAMES = ("Base", "Fn", "Fn2")

# Windows reports the stock knob as these media keys.
KNOB_SOURCES = {"ccw": 0xAE, "cw": 0xAF, "press": 0xAD}
KNOB_VKS = set(KNOB_SOURCES.values())
KNOB_USAGES = {0xEA: "ccw", 0xE9: "cw", 0xE2: "press"}
KNOB_MATCH_SECONDS = 0.03  # longest wait for the board's report matching a media key
SELF_INPUT_TAG = 0x534B3635  # marks key presses this app sends
KNOB_LABELS = {"ccw": "Rotate Counterclockwise", "cw": "Rotate Clockwise", "press": "Press Encoder"}
KNOB_DEFAULTS = {"ccw": "volume down", "cw": "volume up", "press": "volume mute"}
EFFECTS = ["Static", "Breathing", "Wave", "Spectrum", "Reactive", "Off"]
EFFECT_IDS = {"static": 1, "breathing": 2, "wave": 0, "spectrum": 4, "reactive": 6, "off": 1}
# The firmware's colour slots, in order. Slot 7 is multicolour.
SWATCHES = ["#FF0000", "#00FF00", "#0000FF", "#FFFF00", "#FF00FF", "#00FFFF", "#FFFFFF"]
MULTICOLOR = "multi"
LIGHT_DELAY_MS = 250  # settle time after a lighting change before sending


def app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent


# Settings live with the user, so they survive updates and uninstalling.
PROFILE_PATH = Path(os.environ.get("APPDATA") or app_dir()) / "SK65 Studio" / "sk65_profile.json"
LEGACY_PROFILE_PATH = app_dir() / "sk65_profile.json"
ICON_PATH = Path(getattr(sys, "_MEIPASS", app_dir())) / "assets" / "via.ico"


def empty_layers() -> list[dict]:
    return [{} for _ in LAYER_NAMES]


def empty_macros() -> list[dict]:
    """Steps are [key id, pressed?, ms since the step before]."""
    return [{"steps": [], "repeat": 1, "held": False, "fixed": False} for _ in MACRO_IDS]


def default_profile() -> dict:
    return {
        "vid": DEFAULT_VID,
        "pid": DEFAULT_PID,
        "usage_page": DEFAULT_USAGE_PAGE,
        "remap_on": False,
        "tray": False,
        "remaps": empty_layers(),  # one dict per layer, source key to keycode
        "flashed": empty_layers(),  # keymap last saved into the keyboard; it cannot be read back
        "macros": empty_macros(),
        "flashed_macros": empty_macros(),
        "knob": dict(KNOB_DEFAULTS),
        "lighting": {"effect": "wave", "color": MULTICOLOR, "brightness": 80, "speed": 45},
    }


def storable(layer: int, code: str) -> bool:
    """Can this keycode live on this layer of the keyboard's memory? Layers 1
    and 2 have no software fallback, and Fn2 is only reached from layer 0."""
    return (code in FIRMWARE_CODES or code in MACRO_IDS) and (layer == 0 or code != "fn2")


def macro_key(key: str) -> bool:
    """Macros hold plain keyboard usages. Media codes are translated separately
    by the firmware, so they are left out rather than trusted to work."""
    code = FIRMWARE_CODES.get(key, 0)
    return 0 < code < 0xA0 or 0xE0 <= code <= 0xE7


def clean_macros(value) -> list[dict]:
    clean = empty_macros()
    for macro, data in zip(clean, value if isinstance(value, list) else []):
        if not isinstance(data, dict):
            continue
        steps = data.get("steps")
        if isinstance(steps, list):
            macro["steps"] = [[s[0], bool(s[1]), max(0, int(s[2]))] for s in steps[:MACRO_MAX_STEPS]
                              if isinstance(s, list) and len(s) == 3 and macro_key(s[0])]
        macro["repeat"] = max(1, min(MACRO_MAX_REPEAT, int(data.get("repeat", 1))))
        macro["held"] = bool(data.get("held"))
        macro["fixed"] = bool(data.get("fixed"))
    return clean


def clean_layers(value) -> list[dict]:
    """Old profiles hold one dict for the base layer. Layers are a list, not
    keyed by number, because "1" and "2" are also key ids in those old dicts."""
    layers = [value] if isinstance(value, dict) else value if isinstance(value, list) else []
    clean = empty_layers()
    for n, layer in enumerate(layers[:len(clean)]):
        if isinstance(layer, dict):
            clean[n] = {s: d for s, d in layer.items() if s in KC and d in KC and (n == 0 or storable(n, d))}
    return clean


def read_profile(path: Path) -> dict:
    profile = default_profile()
    data = json.loads(path.read_text(encoding="utf-8"))
    profile.update({k: v for k, v in data.items() if k in profile})
    for field in ("remaps", "flashed"):
        profile[field] = clean_layers(profile[field])
    for field in ("macros", "flashed_macros"):
        profile[field] = clean_macros(profile[field])
    profile["knob"] = {**KNOB_DEFAULTS, **{k: v for k, v in profile["knob"].items()
                                           if k in KNOB_DEFAULTS and v in KC and v != "fn2" and v not in MACRO_IDS}}
    if profile["lighting"]["color"] not in SWATCHES:
        profile["lighting"]["color"] = MULTICOLOR
    return profile


def load_profile() -> dict:
    for path in (PROFILE_PATH, LEGACY_PROFILE_PATH):
        try:
            return read_profile(path)
        except (OSError, ValueError):
            pass
    return default_profile()


def save_profile(profile: dict, path: Path = PROFILE_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(profile, indent=2), encoding="utf-8")


def parse_hex_bytes(text: str) -> list[int]:
    parts = [p for p in re.split(r"[\s,]+", text.replace("0x", "").replace("0X", "")) if p]
    out = [int(p, 16) for p in parts]
    if any(b < 0 or b > 255 for b in out):
        raise ValueError("byte out of range")
    return out


def to_hex(data) -> str:
    return " ".join(f"{b:02X}" for b in data)


def layer_keys(layers: list[dict], layer: int) -> dict:
    """The custom keys a save stores on one layer. Like the vendor app, a key
    set to Fn2 on layer 0 also holds Fn2 on layer 2, so it is still Fn2 when
    released; that position follows layer 0 and cannot be edited on layer 2."""
    if layer != 2:
        return layers[layer]
    return {**layers[2], **{src: dst for src, dst in layers[0].items() if dst == "fn2"}}


def fn2_reachable(layers: list[dict]) -> bool:
    """No key holds Fn2 from the factory, so layer 2 needs one on layer 0."""
    return "fn2" in layers[0].values()


def build_keymap_packet(layers: list[dict]) -> bytes:
    """Report 7 "07 03": base, Fn and Fn2 layers, 102 positions of 2 bytes.

    Unchanged keys are "00 cc". On layers 0 and 1 that means the firmware's own
    default. Layer 2 takes the value as stored, so cc must stay the real code:
    that is what keeps the Fn key, 00 FE at position 95, working there. Nothing
    can map onto position 95, because "fn" is not in KEY_TO_MATRIX."""
    def name(code):
        return KC[code][4] if code in KC else code

    refused = [f"{LAYER_NAMES[n]} layer {name(src)} to {name(dst)}" for n, remaps in enumerate(layers)
               for src, dst in remaps.items() if src not in KEY_TO_MATRIX or not storable(n, dst)]
    if refused:
        raise ValueError("Keyboard memory cannot store: " + ", ".join(refused))
    slots = macro_slots(layers)
    packet = bytearray(PROTOCOL["config_size"])
    packet[:3] = bytes((7, 3, 1))
    stride = len(MATRIX) * 2
    for layer in range(len(LAYER_NAMES)):
        start = 8 + layer * stride
        for key in MATRIX_BUTTONS:
            position = MATRIX.index(key)
            packet[start + position * 2 + 1] = MATRIX_DEFAULTS[position]
        for src, dst in layer_keys(layers, layer).items():
            offset = start + MATRIX.index(KEY_TO_MATRIX[src]) * 2
            code = 0xF0 + slots[dst] if dst in slots else FIRMWARE_CODES[dst]
            packet[offset:offset + 2] = bytes((0x80, code))
    return bytes(packet)


def macro_slots(layers: list[dict]) -> dict:
    """Firmware slots are numbered in order of use, as the vendor app does, so
    only macros that a key runs are stored."""
    slots = {}
    for layer in range(len(LAYER_NAMES)):
        for dst in layer_keys(layers, layer).values():
            if dst in MACRO_IDS and dst not in slots:
                slots[dst] = len(slots)
    return slots


def macro_playback(macro: dict) -> tuple[int, list]:
    """The repeat value and steps that play this macro. Measured on the board,
    with a macro that outlasts a tap: value 1 plays once but stops when the key
    is released, 2 plays once in full, and 3 and 4 play that many times. So
    "once" is sent as 2, and "twice" has no value of its own and needs the
    steps sent twice."""
    steps = macro["steps"]
    if macro["held"]:
        return MACRO_REPEAT_HELD, steps
    if macro["repeat"] == 1:
        return 2, steps
    if macro["repeat"] > 2:
        return macro["repeat"], steps
    if len(steps) * 2 > MACRO_MAX_STEPS:
        raise ValueError(f"A macro over {MACRO_MAX_STEPS // 2} steps can't run exactly twice. "
                         "Use Once, or 3 times or more.")
    return 2, steps + steps


def build_macro_packet(slot: int, macro: dict) -> bytes:
    """Report 7 "07 05": at offset 8, a 2-byte repeat value, then 3 bytes per
    step. The top bit of a step's delay means the key goes down."""
    packet = bytearray(PROTOCOL["config_size"])
    packet[:4] = bytes((7, 5, 1, slot))
    repeat, steps = macro_playback(macro)
    data = bytearray((repeat >> 8, repeat & 0xFF))
    for key, down, delay in steps:
        delay = MACRO_MIN_DELAY if macro["fixed"] else max(MACRO_MIN_DELAY, min(MACRO_MAX_DELAY, delay))
        data += bytes(((delay >> 8) | (0x80 if down else 0), delay & 0xFF, FIRMWARE_CODES[key]))
    packet[8:8 + len(data)] = data
    return bytes(packet)


def build_save_packets(layers: list[dict], macros: list[dict]) -> list[bytes]:
    """Everything one save sends: the keymap first, then each macro a key uses."""
    keymap = build_keymap_packet(layers)
    slots = macro_slots(layers)
    empty = [KC[code][4] for code in slots if not macros[MACRO_IDS.index(code)]["steps"]]
    if empty:
        raise ValueError(f"Record these before saving, because a key uses them: {', '.join(empty)}")
    return [keymap] + [build_macro_packet(slot, macros[MACRO_IDS.index(code)]) for code, slot in slots.items()]


def build_lighting_packet(light: dict) -> bytes:
    """Report 7 "07 02": effect, brightness 0-4, speed 1-5, colour slot per effect."""
    packet = bytearray(PROTOCOL["config_size"])
    packet[:3] = bytes((7, 2, 1))
    effect = light["effect"]
    packet[9] = EFFECT_IDS[effect]
    packet[10] = 0 if effect == "off" else round(light["brightness"] / 25)
    packet[11] = 1 + round(light["speed"] / 25)
    if light["color"] in SWATCHES:
        slot = SWATCHES.index(light["color"])
    else:
        slot = 0 if effect in ("static", "off") else 7  # Fixed-on has no multicolour mode
    packet[12:32] = bytes((slot,)) * 20
    return bytes(packet)


def write_report(devices: list[dict], page: int, packet: bytes) -> None:
    target = next((d for d in devices if d["usage_page"] == page), None)
    if not target:
        raise OSError(f"Interface {page:04X} not found")
    dev = hid.device()
    try:
        dev.open_path(target["path"])
        result = dev.send_feature_report(packet)
        if result is not None and result < 0:
            raise OSError(dev.error() or "Keyboard refused the report")
    finally:
        dev.close()


# ---------- Windows input ----------
user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
LRESULT = ctypes.c_ssize_t
HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
user32.SetWindowsHookExW.argtypes = (ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD)
user32.SetWindowsHookExW.restype = wintypes.HHOOK
user32.CallNextHookEx.argtypes = (wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
user32.CallNextHookEx.restype = LRESULT
user32.UnhookWindowsHookEx.argtypes = (wintypes.HHOOK,)
user32.GetMessageW.argtypes = (ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT)
user32.PostThreadMessageW.argtypes = (wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
user32.MapVirtualKeyW.argtypes = (wintypes.UINT, wintypes.UINT)
user32.MapVirtualKeyW.restype = wintypes.UINT
user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetAncestor.argtypes = (wintypes.HWND, wintypes.UINT)
user32.GetAncestor.restype = wintypes.HWND
kernel32.GetModuleHandleW.argtypes = (wintypes.LPCWSTR,)
kernel32.GetModuleHandleW.restype = wintypes.HMODULE
kernel32.CreateMutexW.argtypes = (wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR)
kernel32.CreateMutexW.restype = wintypes.HANDLE
kernel32.CreateEventW.argtypes = (wintypes.LPVOID, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR)
kernel32.CreateEventW.restype = wintypes.HANDLE
kernel32.OpenEventW.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR)
kernel32.OpenEventW.restype = wintypes.HANDLE
kernel32.SetEvent.argtypes = (wintypes.HANDLE,)
kernel32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
kernel32.WaitForSingleObject.restype = wintypes.DWORD

# installer.iss names the same mutex, so Setup can ask to close a running copy.
INSTANCE_NAME = "WomierSK65Studio"
ERROR_ALREADY_EXISTS, EVENT_MODIFY_STATE, INFINITE = 183, 0x0002, 0xFFFFFFFF
_instance_mutex = None


def first_instance() -> bool:
    """False when a copy already runs, after asking that copy to show itself."""
    global _instance_mutex
    _instance_mutex = kernel32.CreateMutexW(None, False, INSTANCE_NAME)
    if ctypes.get_last_error() != ERROR_ALREADY_EXISTS:
        return True
    event = kernel32.OpenEventW(EVENT_MODIFY_STATE, False, INSTANCE_NAME + "Show")
    if event:
        kernel32.SetEvent(event)
    return False

WH_KEYBOARD_LL, WM_QUIT, GA_ROOT = 13, 0x0012, 2
WM_KEYDOWN, WM_SYSKEYDOWN = 0x0100, 0x0104
LLKHF_INJECTED = 0x10
KEYEVENTF_EXTENDEDKEY, KEYEVENTF_KEYUP = 0x1, 0x2


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("vkCode", wintypes.DWORD), ("scanCode", wintypes.DWORD), ("flags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


def send_key(vk: int, down: bool) -> None:
    flags = 0 if down else KEYEVENTF_KEYUP
    if vk in EXTENDED_VKS:
        flags |= KEYEVENTF_EXTENDEDKEY
    key = KEYBDINPUT(vk, user32.MapVirtualKeyW(vk, 0), flags, 0, SELF_INPUT_TAG)
    event = INPUT(type=1, u=_INPUTUNION(ki=key))
    user32.SendInput(1, ctypes.byref(event), ctypes.sizeof(INPUT))


def send_wheel(flag: int, delta: int) -> None:
    event = INPUT(type=0, u=_INPUTUNION(mi=MOUSEINPUT(0, 0, ctypes.c_ulong(delta).value, flag, 0, 0)))
    user32.SendInput(1, ctypes.byref(event), ctypes.sizeof(INPUT))


class Engine:
    """Low-level keyboard hook. Maps by virtual key, so media keys sent by the
    knob never collide with letter keys that share a scan code."""

    def __init__(self, post):
        self.post = post
        self.map = {}
        self.remap_on = False
        self.capture = False
        self.accept_injected = False  # tests only
        self.record_window = None  # while recording a macro, keys typed in this window go to it
        self.knob_open = False
        self.knob_time = 0.0
        self.knob_signal = threading.Event()
        self.held = set()
        self._proc = HOOKPROC(self._on_event)
        self._thread = None
        self._tid = None

    def note_knob(self) -> None:
        self.knob_time = time.monotonic()
        self.knob_signal.set()

    def _from_this_board(self) -> bool:
        """Windows reports media keys from every device the same way, with no
        source. The board's own HID report lands within a millisecond either
        side of the key, so wait briefly for it. Without this a headset's
        volume wheel would be remapped too."""
        if not self.knob_open:
            return True
        self.knob_signal.wait(KNOB_MATCH_SECONDS)
        self.knob_signal.clear()
        return time.monotonic() - self.knob_time < KNOB_MATCH_SECONDS * 3

    def _should_remap(self, vk: int, injected: bool, down: bool) -> bool:
        if vk not in KNOB_VKS:
            return self.accept_injected or not injected
        if down and vk not in self.held and self._from_this_board():
            self.held.add(vk)
        if down:
            return vk in self.held
        was_held = vk in self.held
        self.held.discard(vk)
        return was_held

    def set_map(self, remaps: dict, knob: dict) -> None:
        mapping = {}
        for src, dst in remaps.items():
            vk = KC[src][2]
            if vk is not None and dst != src:
                mapping[vk] = dst
        for slot, dst in knob.items():
            if dst != KNOB_DEFAULTS[slot]:
                mapping[KNOB_SOURCES[slot]] = dst
        self.map = mapping

    def sync(self) -> None:
        want = self.remap_on or self.capture or self.record_window
        if want and not self._thread:
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()
        elif not want and self._thread:
            self.stop()

    def stop(self) -> None:
        if self._thread and self._tid:
            user32.PostThreadMessageW(self._tid, WM_QUIT, 0, 0)
            self._thread.join(1)
        self._thread = None
        self._tid = None

    def _run(self):
        self._tid = kernel32.GetCurrentThreadId()
        handle = user32.SetWindowsHookExW(WH_KEYBOARD_LL, self._proc, kernel32.GetModuleHandleW(None), 0)
        if not handle:
            self.post(("hook_error", ctypes.get_last_error()))
            return
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            pass
        user32.UnhookWindowsHookEx(handle)

    def _on_event(self, code, wparam, lparam):
        try:
            if code == 0:
                event = ctypes.cast(lparam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                vk = event.vkCode
                injected = bool(event.flags & LLKHF_INJECTED)
                down = wparam in (WM_KEYDOWN, WM_SYSKEYDOWN)
                # Keys this app sends must not be remapped again: the knob's
                # own events are injected too, so a swap would loop forever.
                if event.dwExtraInfo != SELF_INPUT_TAG:
                    # Swallowed so a recorded Win or Alt+Tab doesn't also act on the desktop.
                    if self.record_window and not injected and user32.GetForegroundWindow() == self.record_window:
                        self.post(("record", vk, down, event.time))
                        return 1
                    if self.capture and (self.accept_injected or not injected):
                        self.post(("key", vk, down))
                    if self.remap_on and vk in self.map and self._should_remap(vk, injected, down):
                        self._perform(self.map[vk], down)
                        return 1
        except Exception as e:  # must never escape into the OS hook chain
            self.post(("hook_error", str(e)))
        return user32.CallNextHookEx(None, code, wparam, lparam)

    @staticmethod
    def _perform(dst: str, down: bool) -> None:
        if dst in WHEEL_ACTIONS:
            if down:
                send_wheel(*WHEEL_ACTIONS[dst])
        elif KC[dst][2] is not None:
            send_key(KC[dst][2], down)


class KnobReader(threading.Thread):
    """Reads the board's consumer-control interface. Windows still gets the
    same reports, so this only observes."""

    def __init__(self, path: bytes, post, on_press):
        super().__init__(daemon=True)
        self.path = path
        self.post = post
        self.on_press = on_press  # called right here: the keyboard hook is waiting on it
        self.stopping = False

    def run(self):
        dev = hid.device()
        try:
            dev.open_path(self.path)
            self.post(("knob_channel", True, ""))
            while not self.stopping:
                data = dev.read(8, 200)
                if data:
                    if len(data) >= 3 and data[0] == PROTOCOL["consumer_report"] and (data[1] or data[2]):
                        self.on_press()
                    self.post(("knob_report", list(data)))
        except (OSError, ValueError) as e:
            self.post(("knob_channel", False, str(e)))
        finally:
            dev.close()


class Monitor(threading.Thread):
    """Polls USB presence so the status follows plug and unplug."""

    def __init__(self, post, ids):
        super().__init__(daemon=True)
        self.post = post
        self.ids = ids
        self.wake = threading.Event()
        self.stopping = False

    def run(self):
        while not self.stopping:
            try:
                vid, pid = self.ids()
                self.post(("devices", hid.enumerate(vid, pid)))
            except (OSError, ValueError) as e:
                self.post(("devices", []))
                self.post(("status", f"USB scan failed: {e}"))
            self.wake.wait(2.0)
            self.wake.clear()


class Tray:
    """Tray icon on its own thread, so clicks reach the app through the event queue."""

    def __init__(self, post):
        self.post = post
        self.icon = None

    def start(self):
        if not self.icon:
            menu = pystray.Menu(pystray.MenuItem(f"Open {APP_NAME}", lambda: self.post(("show",)), default=True),
                                pystray.MenuItem("Exit", lambda: self.post(("exit",))))
            self.icon = pystray.Icon("sk65_studio", Image.open(ICON_PATH).convert("RGBA"), APP_NAME, menu)
            self.icon.run_detached()

    def stop(self):
        if self.icon:
            self.icon.stop()
            self.icon = None


def probe(devices: list[dict]) -> dict:
    """Read-only self-test. Opens the config interfaces and asks the firmware
    for a feature report; nothing is written to the keyboard."""
    result = {"config": (False, "Interface not found"), "firmware": (False, "Interface not found")}
    by_page = {d["usage_page"]: d for d in devices}
    dev = hid.device()
    config = by_page.get(PROTOCOL["config_page"])
    if config:
        try:
            dev.open_path(config["path"])
            result["config"] = (True, f"Opened page {PROTOCOL['config_page']:04X}")
        except (OSError, ValueError) as e:
            result["config"] = (False, str(e))
        finally:
            dev.close()
    aux = by_page.get(PROTOCOL["aux_page"])
    if aux:
        dev = hid.device()
        try:
            dev.open_path(aux["path"])
            dev.get_feature_report(PROTOCOL["aux_report"], 8)
            result["firmware"] = (True, f"Answered report {PROTOCOL['aux_report']}")
        except (OSError, ValueError) as e:
            result["firmware"] = (False, str(e))
        finally:
            dev.close()
    return result


# ---------- drawing ----------
_fonts, _masks, _knobs, _rings = {}, {}, {}, {}


def font(size: float, bold=False, name=None) -> ImageFont.FreeTypeFont:
    key = (round(size), bold, name)
    if key not in _fonts:
        file = name or ("segoeuib.ttf" if bold else "segoeui.ttf")
        _fonts[key] = ImageFont.truetype(str(FONT_DIR / file), max(6, round(size)))
    return _fonts[key]


def rgb(color) -> tuple:
    return ImageColor.getrgb(color) if isinstance(color, str) else color


def scale_color(color, factor: float) -> tuple:
    return tuple(round(c * factor) for c in rgb(color))


def blend(a, b, t: float) -> tuple:
    a, b = rgb(a), rgb(b)
    return tuple(round(x + (y - x) * t) for x, y in zip(a, b))


def rr_mask(w: int, h: int, r: int) -> Image.Image:
    key = (w, h, r)
    if key not in _masks:
        big = Image.new("L", (w * 4, h * 4), 0)
        ImageDraw.Draw(big).rounded_rectangle((0, 0, w * 4 - 1, h * 4 - 1), r * 4, fill=255)
        _masks[key] = big.resize((w, h), Image.LANCZOS)
    return _masks[key]


def rrect(img, box, r, fill=None, outline=None, width=0):
    x0, y0, x1, y1 = [round(v) for v in box]
    w, h = x1 - x0, y1 - y0
    if w < 1 or h < 1:
        return
    r = round(min(r, w / 2, h / 2))
    if outline:
        img.paste(rgb(outline), (x0, y0), rr_mask(w, h, r))
        rrect(img, (x0 + width, y0 + width, x1 - width, y1 - width), max(0, r - width), fill or img.getpixel((x0, y0)))
    elif isinstance(fill, Image.Image):
        img.paste(fill.crop((0, 0, w, h)), (x0, y0), rr_mask(w, h, r))
    else:
        img.paste(rgb(fill), (x0, y0), rr_mask(w, h, r))


def css_gradient(w: int, h: int, angle: float, stops: list) -> Image.Image:
    """CSS linear-gradient, computed small and scaled up."""
    sw, sh = 64, max(8, round(64 * h / max(w, 1)))
    a = math.radians(angle)
    dx, dy = math.sin(a), -math.cos(a)
    length = abs(sw * dx) + abs(sh * dy)
    small = Image.new("RGB", (sw, sh))
    px = small.load()
    for y in range(sh):
        for x in range(sw):
            t = ((x + 0.5 - sw / 2) * dx + (y + 0.5 - sh / 2) * dy) / length + 0.5
            if t <= stops[0][0]:
                px[x, y] = stops[0][1]
            elif t >= stops[-1][0]:
                px[x, y] = stops[-1][1]
            else:
                for (p0, c0), (p1, c1) in zip(stops, stops[1:]):
                    if p0 <= t <= p1:
                        px[x, y] = blend(c0, c1, (t - p0) / (p1 - p0))
                        break
    return small.resize((w, h), Image.BICUBIC)


def disc_mask(size: int, radius: float) -> Image.Image:
    mask = Image.new("L", (size, size), 0)
    c, r = size / 2, size / 2 * radius
    ImageDraw.Draw(mask).ellipse((c - r, c - r, c + r, c + r), fill=255)
    return mask


def knob_sprite(size: int) -> Image.Image:
    """The SK65's knob: a smooth rose metal disc with a raised rim, lit from the
    lower right. Colours are sampled from Womier's product render."""
    if size not in _knobs:
        s = size * 4
        rim = css_gradient(s, s, 135, [(0.15, (150, 126, 113)), (0.85, (231, 206, 188))])
        lip = css_gradient(s, s, 135, [(0.2, (140, 117, 105)), (0.8, (235, 211, 194))])
        img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
        img.paste(rim, (0, 0), disc_mask(s, 0.98))
        img.paste(lip, (0, 0), disc_mask(s, 0.8))
        img.paste((197, 167, 150), (0, 0), disc_mask(s, 0.74))
        _knobs[size] = img.resize((size, size), Image.LANCZOS)
    return _knobs[size]


def knob_ring(size: int) -> Image.Image:
    """Mask for the highlight drawn around the knob's edge."""
    if size not in _rings:
        s = size * 4
        ring = disc_mask(s, 1.0)
        ring.paste(0, (0, 0), disc_mask(s, 0.86))
        _rings[size] = ring.resize((size, size), Image.LANCZOS)
    return _rings[size]


class Surface(tk.Canvas):
    """Pillow-painted area with hit regions, for antialiased VIA-accurate UI."""

    def __init__(self, master, app, painter, bg=LIGHT_JET, height=None, width=None):
        super().__init__(master, highlightthickness=0, bd=0, bg=bg)
        if height:
            self.configure(height=height)
        if width:
            self.configure(width=width)
        self.app, self.painter, self.bg = app, painter, bg
        self.S = app.S
        self.hits, self.hover = [], None
        self.scroll, self.content_h = 0, 0
        self._drag = None
        self._pending = False
        self._photo = None
        self._image = self.create_image(0, 0, anchor="nw")
        self.bind("<Configure>", lambda e: self.invalidate())
        self.bind("<Button-1>", self._press)
        self.bind("<B1-Motion>", self._dragged)
        self.bind("<ButtonRelease-1>", lambda e: setattr(self, "_drag", None))
        self.bind("<Motion>", self._move)
        self.bind("<Leave>", lambda e: self._set_hover(None))
        self.bind("<MouseWheel>", self._wheel)

    def invalidate(self):
        if not self._pending:
            self._pending = True
            self.after_idle(self.render)

    def render(self):
        self._pending = False
        w, h = self.winfo_width(), self.winfo_height()
        if w < 2 or h < 2:
            return
        self.hits = []
        img = Image.new("RGB", (w, h), rgb(self.bg))
        self.painter(self, img, w, h)
        self._photo = ImageTk.PhotoImage(img)
        self.itemconfigure(self._image, image=self._photo)

    def hit(self, box, on_click=None, key=None, tip="", drag=False):
        self.hits.append((box, on_click, key, tip, drag))

    def _find(self, x, y):
        for entry in reversed(self.hits):
            (x0, y0, x1, y1) = entry[0]
            if x0 <= x < x1 and y0 <= y < y1:
                return entry
        return None

    def _press(self, e):
        entry = self._find(e.x, e.y)
        if entry and entry[1]:
            entry[1](e.x, e.y)
            if entry[4]:
                self._drag = entry[1]

    def _dragged(self, e):
        if self._drag:
            self._drag(e.x, e.y)

    def _move(self, e):
        entry = self._find(e.x, e.y)
        self._set_hover(entry)

    def _set_hover(self, entry):
        key = entry[2] if entry else None
        if key != self.hover:
            self.hover = key
            self.configure(cursor="hand2" if entry and entry[1] else "")
            self.app.show_tip(entry[3] if entry else "")
            self.invalidate()

    def _wheel(self, e):
        limit = max(0, self.content_h - self.winfo_height())
        self.scroll = min(limit, max(0, self.scroll - round(e.delta / 120 * 40 * self.S)))
        self.invalidate()


class KeyboardView(Surface):
    """The board: VIA's gradient, case, key well, keycaps and encoder."""

    def __init__(self, master, app):
        super().__init__(master, app, self._paint, bg="#969696")
        self.selected = None
        self.tested = {}
        self.knob_flash = 0.0
        self.phase = 0.0
        self._cache_key = None
        self._base = None
        self.rects = {}

    def _geometry(self, w, h):
        S = self.S
        pitch = min((w - 60 * S) / (UNITS_W + 0.7), (h - 66 * S) / (UNITS_H + 0.7), 72 * S)
        pitch = max(24, int(pitch))
        gap = max(1, round(pitch * 2 / 54))
        border = round(pitch * 10 / 54)
        kw, kh = UNITS_W * pitch - gap, UNITS_H * pitch - gap
        outer_w, outer_h = kw + border * 3, kh + border * 3
        ox, oy = (w - outer_w) // 2, (h - outer_h) // 2 + round(14 * S)
        return pitch, gap, border, kw, kh, ox, oy, outer_w, outer_h

    def _paint(self, surf, img, w, h):
        app = self.app
        pitch, gap, border, kw, kh, ox, oy, outer_w, outer_h = self._geometry(w, h)
        kx, ky = ox + border + border // 2, oy + border + border // 2
        cache_key = (w, h, app.page, self._layer(), json.dumps(app.profile["remaps"], sort_keys=True))
        if cache_key != self._cache_key:
            self._cache_key = cache_key
            base = css_gradient(w, h, *CANVAS_GRADIENT)
            rrect(base, (ox, oy, ox + outer_w, oy + outer_h), round(8 * self.S), ACCENT)
            ImageDraw.Draw(base).rounded_rectangle((ox, oy, ox + outer_w - 1, oy + outer_h - 1), round(8 * self.S),
                                                   outline=blend(ACCENT, "#FFFFFF", 0.2))
            well = css_gradient(kw + border, kh + border, *WELL_GRADIENT)
            base.paste(well, (kx - border // 2, ky - border // 2))
            self.rects = {}
            for key in KEYS:
                x0, y0 = kx + round(key["x"] * pitch), ky + key["y"] * pitch
                rect = (x0, y0, x0 + round(key["w"] * pitch) - gap, y0 + pitch - gap)
                self.rects[key["id"]] = (rect, key)
                if key["id"] != "knob":
                    self._draw_key(base, rect, key, pitch, self._cap(key), ink=self._ink(key))
            self._base = base
        img.paste(self._base)
        if app.page == "configure":
            self._paint_layers(img)
        for kid, (rect, key) in self.rects.items():
            caps = self._state_colors(kid, key)
            if kid == "knob":
                self._draw_knob(img, rect, *caps)
            elif caps:
                self._draw_key(img, rect, key, pitch, self._cap(key), *caps)
            tip = self._tip(kid)
            self.hit(rect, (lambda x, y, k=kid: app.key_clicked(k)) if app.page == "test" or app.selectable(kid) else None,
                     key=kid, tip=tip)

    def _paint_layers(self, img):
        """VIA's layer control: the selected layer on the accent, the rest bare."""
        S, app = self.S, self.app
        draw = ImageDraw.Draw(img)
        f = font(20 * S)
        draw.text((round(15 * S), round(12 * S)), "LAYER", fill=rgb(LIGHT_GREY), font=f)
        bw, lx = round(22 * S), round(84 * S)
        top, bottom = round(13 * S), round(39 * S)
        for n, name in enumerate(LAYER_NAMES):
            box = (lx + n * bw, top, lx + (n + 1) * bw, bottom)
            hover = f"layer {n}"
            if n == app.layer:
                rrect(img, box, 0, ACCENT)
            elif self.hover == hover:
                rrect(img, box, 0, LIGHT_JET)
            color = INSIDE_ACCENT if n == app.layer else LIGHT_GREY
            draw.text((box[0] + round(6 * S), round(12 * S)), str(n), fill=rgb(color), font=f)
            self.hit(box, lambda _x, _y, layer=n: app.set_layer(layer), key=hover, tip=f"Layer {n}: {name}")
        if app.fn2_warning():
            small = font(15 * S, True)
            x0 = lx + len(LAYER_NAMES) * bw + round(12 * S)
            text = "FN2 UNREACHABLE: NO KEY ON LAYER 0 IS SET TO FN2"
            rrect(img, (x0, top, x0 + small.getlength(text) + round(20 * S), bottom), round(6 * S), JET)
            draw.text((x0 + round(10 * S), (top + bottom) / 2), text, fill=rgb(ERROR), font=small, anchor="lm")

    def _layer(self):
        """The layer the board shows. The key tester always shows the base layer."""
        return self.app.layer if self.app.page == "configure" else 0

    def _custom(self, kid):
        return layer_keys(self.app.profile["remaps"], self._layer()).get(kid)

    def _cap(self, key):
        kid = key["id"]
        if kid == "fn":
            return "Fn"
        if self.app.page == "configure":
            dst = self._custom(kid)
            if dst:
                return KC[dst][1]
        return KEY_LEGENDS.get(kid, KC[kid][1])

    def _ink(self, key):
        """Dims keys left at their default on layers 1 and 2, so the custom ones
        stand out. On layer 1 that default is the firmware's, not this legend."""
        if not self._layer() or key["id"] == "fn" or self._custom(key["id"]):
            return None
        face, ink = (ACCENT, INSIDE_ACCENT) if key["accent"] else (INSIDE_ACCENT, ACCENT)
        return blend(face, ink, 0.4)

    def _tip(self, kid):
        app = self.app
        layer = self._layer()
        if kid == "knob":
            if layer:
                return "The knob is remapped in software, so it only has layer 0."
            k = app.profile["knob"]
            return f"Knob: left {KC[k['ccw']][1]}, right {KC[k['cw']][1]}, press {KC[k['press']][1]}"
        if kid == "fn":
            return "Fn runs inside the keyboard's firmware. Windows never sees it, so it cannot be remapped here."
        dst = self._custom(kid)
        if layer == 0:
            return f"{KC[kid][4]}  remapped to  {KC[dst][4]}" if dst else KC[kid][4]
        if not app.selectable(kid):
            return "This key is your Fn2 key. Change it on layer 0."
        held = f"{LAYER_NAMES[layer]} + {KC[kid][4]}"
        if dst:
            return f"{held}  sends  {KC[dst][4]}"
        return f"{held}: firmware default" if layer == 1 else f"{held}: sends {KC[kid][4]}"

    def _state_colors(self, kid, key):
        """Returns (face, legend) overrides, or None for the cached look."""
        app = self.app
        accent_key = key["accent"]
        if app.page == "configure" and self.selected == kid:
            t = 0.5 + 0.5 * math.sin(self.phase)
            c = ACCENT if not accent_key else "#FFFFFF"
            return blend(INSIDE_ACCENT, c, 0.45 + 0.55 * t), INSIDE_ACCENT
        if app.page == "test" and kid in self.tested:
            return (ACCENT, INSIDE_ACCENT) if self.tested[kid] else ("#796C6C", ACCENT)
        if self.hover == kid and kid != "knob":
            return blend(ACCENT if accent_key else INSIDE_ACCENT, "#FFFFFF", 0.14), (INSIDE_ACCENT if accent_key else ACCENT)
        if kid == "knob":
            return (None, None)
        return None

    def _draw_key(self, img, rect, key, pitch, label, face=None, ink=None):
        x0, y0, x1, y1 = rect
        fx = round(pitch * 6 / 54)
        ft, fb = round(pitch * 2 / 54), round(pitch * 10 / 54)
        base_color = ACCENT if key["accent"] else INSIDE_ACCENT
        face = face or base_color
        ink = ink or (INSIDE_ACCENT if key["accent"] else ACCENT)
        radius = round(pitch * 6 / 54)
        rrect(img, rect, radius, scale_color(face, 0.8))
        rrect(img, (x0 + fx, y0 + ft, x1 - fx, y1 - fb), radius, face)
        draw = ImageDraw.Draw(img)
        tx = x0 + fx + round(pitch * 4 / 54)
        cy = (y0 + ft + y1 - fb) / 2
        ink = rgb(ink)
        if "\n" in label:
            top, bottom = label.split("\n")
            f = font(pitch * 0.26, True)
            draw.text((tx, y0 + ft + pitch * 0.02), top, fill=ink, font=f)
            draw.text((tx, cy + pitch * 0.02), bottom, fill=ink, font=f)
        elif len(label) == 1:
            draw.text((tx, cy - pitch * 0.07), label, fill=ink, font=font(pitch * 0.33, True), anchor="lm")
        else:
            draw.text((tx, cy), label, fill=ink, font=font(pitch * 0.2, True), anchor="lm")

    def _draw_knob(self, img, rect, face, _ink):
        x0, y0, x1, y1 = rect
        size = min(x1 - x0, y1 - y0)
        px, py = x0 + (x1 - x0 - size) // 2, y0 + (y1 - y0 - size) // 2
        app = self.app
        sprite = knob_sprite(size)
        img.paste(sprite, (px, py), sprite)
        if app.page == "configure" and self.selected == "knob":
            strength = 0.45 + 0.55 * (0.5 + 0.5 * math.sin(self.phase))
        else:
            strength = max(self.knob_flash, 0.5 if self.hover == "knob" else 0.0)
        if strength:
            ring = knob_ring(size).point(lambda v: round(v * strength))
            img.paste((255, 255, 255), (px, py), ring)


class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        ctk.set_appearance_mode("dark")
        self.title(APP_NAME)
        try:
            self.iconbitmap(str(ICON_PATH))
        except tk.TclError:
            pass
        self.S = max(1.0, self.winfo_fpixels("1i") / 96)
        self.geometry("1280x800")
        self.minsize(1000, 680)
        self.configure(fg_color=JET)

        self.events = queue.Queue()
        self.profile = load_profile()
        self.engine = Engine(self.events.put_nowait)
        self.page = "configure"
        self.menu_item = {"configure": "KEYMAP", "test": "KEY TEST", "device": "CONNECTION", "settings": "SETTINGS"}
        self.tray = Tray(self.events.put_nowait)
        self.hidden = False
        self.category = "basic"
        self.macro_index = 0
        self.recording = False
        self.record_held = set()
        self.record_last = None
        self.writing_macros = empty_macros()
        self.layer = 0
        self.enc_slot = None
        self.status_text = ""
        self.confirm = None
        self.writing = False
        self.writing_layers = empty_layers()
        self.light_job = None
        self.light_pending = False
        self.light_writing = None
        self.light_sent = None
        self.light_status = ("Changes apply instantly", MEDIUM_GREY)

        self.devices = []
        self.connected = False
        self.reader = None
        self.product = ""
        self.checks = {"config": None, "firmware": None}
        self.knob_channel = None
        self.knob_seen = {"ccw": None, "cw": None, "press": None}
        self.knob_last = ""
        self.key_seen = ""
        self.console_log = []

        S = self.S
        self.nav = Surface(self, self, self.paint_nav, bg=DARK_GREY, height=round(49 * S))
        self.nav.pack(fill="x")
        self.tip = tk.Label(self, bg=DARK_GREY, fg=LIGHT_GREY, anchor="w", padx=round(6 * S),
                            font=("Segoe UI", 10))
        self.tip.pack(fill="x", side="bottom")
        self.body = tk.Frame(self, bg=JET)
        self.body.pack(fill="both", expand=True)
        self.body.columnconfigure(0, weight=1)
        self.kb = KeyboardView(self.body, self)
        self.lower = tk.Frame(self.body, bg=JET)
        self.lower.columnconfigure(2, weight=1)
        self.lower.rowconfigure(0, weight=1)
        self.menu = Surface(self.lower, self, self.paint_menu, width=round(216 * S))
        self.sub = Surface(self.lower, self, self.paint_sub, bg=DARK_GREY, width=round(118 * S))
        self.content = Surface(self.lower, self, self.paint_content, bg=JET)
        self.console = self._build_console()

        self.set_page("configure")
        self.sync_engine()
        self.monitor = Monitor(self.events.put_nowait, self._ids)
        self.monitor.start()
        self.after(30, self._pump)
        self.after(50, self._animate)
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.bind("<Unmap>", self._on_unmap)
        self.apply_tray()
        threading.Thread(target=self._wait_for_second_launch, daemon=True).start()

    def _wait_for_second_launch(self):
        event = kernel32.CreateEventW(None, False, False, INSTANCE_NAME + "Show")
        while event and kernel32.WaitForSingleObject(event, INFINITE) == 0:
            self.events.put_nowait(("show",))

    def apply_tray(self):
        if self.profile["tray"]:
            try:
                self.tray.start()
            except (OSError, ValueError) as e:
                self.say(f"Could not add the tray icon: {e}")
        else:
            self.tray.stop()
            if self.hidden:
                self.show_window()

    def toggle_tray(self):
        self.profile["tray"] = not self.profile["tray"]
        self.save_quiet()
        self.apply_tray()
        self.content.invalidate()
        self.say("The minimize button now sends the app to the system tray." if self.profile["tray"]
                 else "The minimize button now works normally.")

    def _on_unmap(self, event):
        if event.widget is self:
            self.after(20, self.hide_if_minimized)

    def hide_if_minimized(self):
        if self.recording:
            self.stop_record()
        if self.tray.icon and self.state() == "iconic":
            self.withdraw()
            self.hidden = True

    def show_window(self):
        self.hidden = False
        self.deiconify()
        self.state("normal")
        self.lift()
        self.focus_force()

    def _ids(self):
        return int(self.profile["vid"], 16), int(self.profile["pid"], 16)

    # ---------- pages ----------
    def set_page(self, page):
        if self.recording:
            self.stop_record()
        self.page = page
        full_pane = page in ("device", "settings")
        group = "" if full_pane else "body"
        self.body.rowconfigure(0, weight=0 if full_pane else 5, uniform=group)
        self.body.rowconfigure(1, weight=1 if full_pane else 3, uniform=group)
        if full_pane:
            self.kb.grid_remove()
        else:
            self.kb.grid(row=0, column=0, sticky="nsew")
        self.lower.grid(row=1, column=0, sticky="nsew")
        self.layout_lower()
        self.engine.capture = page in ("test", "device")
        self.engine.sync()
        self.nav.invalidate()
        self.kb.invalidate()

    def layout_lower(self):
        item = self.menu_item[self.page]
        self.menu.grid(row=0, column=0, sticky="ns")
        if self.page == "configure" and item == "KEYMAP":
            self.sub.grid(row=0, column=1, sticky="ns")
        else:
            self.sub.grid_remove()
        if item == "HID CONSOLE":
            self.content.grid_remove()
            self.console.grid(row=0, column=2, sticky="nsew")
        else:
            self.console.grid_remove()
            self.content.grid(row=0, column=2, sticky="nsew")
        self.content.scroll = 0
        for s in (self.menu, self.sub, self.content):
            s.invalidate()

    def show_tip(self, text):
        self.tip.configure(text=text or self.status_text)

    def say(self, text):
        self.status_text = text
        self.tip.configure(text=text)

    # ---------- nav ----------
    def paint_nav(self, surf, img, w, h):
        S = self.S
        draw = ImageDraw.Draw(img)
        items = [("configure", GLYPH_KEYBOARD, "Configure"), ("test", GLYPH_TEST, "Key Tester"),
                 ("device", GLYPH_USB, "Device and connection"), ("settings", GLYPH_SETTINGS, "Settings")]
        bw, bh, gap = round(40 * S), round(35 * S), round(20 * S)
        x = (w - (bw * len(items) + gap * (len(items) - 1))) // 2
        y = (h - bh) // 2
        for page, glyph, tip in items:
            selected = page == self.page
            box = (x, y, x + bw, y + bh)
            if selected:
                rrect(img, box, round(10 * S), ACCENT)
            color = ACCENT if surf.hover == page and not selected else INSIDE_ACCENT
            draw.text((x + bw / 2, y + bh / 2), glyph, fill=rgb(color), font=font(20 * S, name=ICON_FONT), anchor="mm")
            surf.hit(box, lambda _x, _y, p=page: self.set_page(p), key=page, tip=tip)
            x += bw + gap

        # Connection pill, always visible.
        f = font(14 * S, True)
        text = "CONNECTED" if self.connected else "NOT CONNECTED"
        color = ACCENT if self.connected else ERROR
        tw = f.getlength(text)
        rx = w - round(14 * S)
        px0 = rx - tw - round(34 * S)
        rrect(img, (px0, y + 3, rx, y + bh - 3), round(8 * S), JET)
        cy = y + bh / 2
        draw.ellipse((px0 + 11 * S, cy - 5 * S, px0 + 21 * S, cy + 5 * S), fill=rgb(color))
        draw.text((px0 + round(27 * S), cy), text, fill=rgb(color), font=f, anchor="lm")
        surf.hit((px0, y, rx, y + bh), lambda _x, _y: self.set_page("device"), key="pill",
                 tip="Open the connection self-test")

        # Remap switch.
        sw = round(46 * S)
        sx1 = px0 - round(14 * S)
        sx0 = sx1 - sw
        self._toggle(img, sx0, y + (bh - round(23 * S)) // 2, self.profile["remap_on"])
        draw.text((sx0 - round(8 * S), cy), "REMAP", fill=rgb(LIGHT_GREY), font=font(14 * S), anchor="rm")
        lbl = font(14 * S).getlength("REMAP")
        surf.hit((sx0 - lbl - 10 * S, y, sx1, y + bh), lambda _x, _y: self.toggle_remap(), key="remap",
                 tip="Turn software remapping of keys and knob on or off")

    def _toggle(self, img, x, y, on):
        S = self.S
        w, h = round(46 * S), round(23 * S)
        if on:
            rrect(img, (x, y, x + w, y + h), round(5 * S), ACCENT)
            rrect(img, (x + w - round(20 * S), y + round(3 * S), x + w - round(3 * S), y + h - round(3 * S)),
                  round(3 * S), INSIDE_ACCENT)
        else:
            rrect(img, (x, y, x + w, y + h), round(5 * S), MEDIUM_GREY)
            rrect(img, (x + round(3 * S), y + round(3 * S), x + round(20 * S), y + h - round(3 * S)),
                  round(3 * S), LIGHT_JET)

    # ---------- left menu and submenu ----------
    def menu_items(self):
        return {
            "configure": [("KEYMAP", GLYPH_KEYBOARD), ("MACROS", GLYPH_MACRO), ("LIGHTING", GLYPH_LIGHT),
                          ("SAVE + LOAD", GLYPH_SAVE)],
            "test": [("KEY TEST", GLYPH_TEST)],
            "device": [("CONNECTION", GLYPH_USB), ("HID CONSOLE", GLYPH_CONSOLE)],
            "settings": [("SETTINGS", GLYPH_SETTINGS)],
        }[self.page]

    def paint_menu(self, surf, img, w, h):
        S = self.S
        draw = ImageDraw.Draw(img)
        y = round(15 * S)
        for name, glyph in self.menu_items():
            selected = name == self.menu_item[self.page]
            color = LIGHT_GREY if selected or surf.hover == name else MEDIUM_GREY
            draw.text((round(20 * S), y + round(10 * S)), glyph, fill=rgb(color), font=font(18 * S, name=ICON_FONT), anchor="lm")
            draw.text((round(52 * S), y + round(10 * S)), name, fill=rgb(color), font=font(20 * S), anchor="lm")
            surf.hit((0, y - round(6 * S), w, y + round(28 * S)),
                     lambda _x, _y, n=name: self.set_menu(n), key=name)
            y += round(35 * S)
        draw.line((w - 1, 0, w - 1, h), fill=rgb(DARK_GREY))
        draw.line((0, 0, w, 0), fill=rgb(DARK_GREY))

    def set_menu(self, name):
        if self.recording:
            self.stop_record()
        self.menu_item[self.page] = name
        self.layout_lower()

    def paint_sub(self, surf, img, w, h):
        S = self.S
        draw = ImageDraw.Draw(img)
        y = round(12 * S)
        for cid, label in CATEGORIES:
            selected = cid == self.category
            box = (round(8 * S), y, w - round(8 * S), y + round(30 * S))
            if selected:
                rrect(img, box, round(12 * S), MEDIUM_GREY)
            color = LIGHT_GREY if selected or surf.hover == cid else MEDIUM_GREY
            draw.text((box[0] + round(10 * S), y + round(15 * S)), label, fill=rgb(color), font=font(20 * S), anchor="lm")
            surf.hit(box, lambda _x, _y, c=cid: self.set_category(c), key=cid)
            y += round(42 * S)

    def set_category(self, cid):
        self.category = cid
        self.content.scroll = 0
        self.sub.invalidate()
        self.content.invalidate()

    # ---------- right pane ----------
    def paint_content(self, surf, img, w, h):
        S = self.S
        surf.hits = []
        surf.top = round(6 * S) - surf.scroll
        item = self.menu_item[self.page]
        if item == "KEYMAP":
            if self.kb.selected == "knob":
                self.paint_encoder(surf, img, w, h)
            self.paint_keycodes(surf, img, w, h)
        elif item == "MACROS":
            self.paint_macros(surf, img, w, h)
        elif item == "LIGHTING":
            self.paint_lighting(surf, img, w, h)
        elif item == "SAVE + LOAD":
            self.paint_saveload(surf, img, w, h)
        elif item == "KEY TEST":
            self.paint_test(surf, img, w, h)
        elif item == "CONNECTION":
            self.paint_connection(surf, img, w, h)
        elif item == "SETTINGS":
            self.paint_settings(surf, img, w, h)
        surf.content_h = surf.top + surf.scroll + round(12 * S)

    def paint_settings(self, surf, img, w, h):
        S = self.S
        x0, x1, cy = self.control_row(surf, img, w, "Minimize To System Tray")
        self._toggle(img, x1 - round(46 * S), cy - round(23 * S) // 2, self.profile["tray"])
        surf.hit((x0, cy - round(25 * S), x1, cy + round(25 * S)), lambda _x, _y: self.toggle_tray(), key="tray",
                 tip="Minimizing hides the app in the system tray instead of the taskbar. Click the tray icon to open it.")
        self.control_row(surf, img, w, "Version", APP_VERSION)

    def control_row(self, surf, img, w, label, detail=None, color=None, mark=None):
        """VIA ControlRow: 50px line, label left, detail right, hairline below."""
        S = self.S
        row_w = min(w - round(40 * S), round(960 * S))
        x0 = (w - row_w) // 2
        y = surf.top
        h = round(50 * S)
        draw = ImageDraw.Draw(img)
        draw.text((x0 + round(5 * S), y + h / 2), label, fill=rgb(MEDIUM_GREY), font=font(20 * S), anchor="lm")
        draw.line((x0, y + h - 1, x0 + row_w, y + h - 1), fill=rgb(DARK_GREY))
        x1 = x0 + row_w - round(5 * S)
        if detail is not None:
            f = font(18 * S)
            draw.text((x1, y + h / 2), detail, fill=rgb(color or ACCENT), font=f, anchor="rm")
            if mark:
                draw.text((x1 - f.getlength(detail) - round(12 * S), y + h / 2), mark, fill=rgb(color or ACCENT),
                          font=font(15 * S, name=ICON_FONT), anchor="rm")
        surf.top += h
        return x0, x1, y + h // 2

    def accent_button(self, surf, img, x1, cy, text, on_click, enabled=True, key=None):
        S = self.S
        f = font(18 * S)
        bw = max(round(100 * S), round(f.getlength(text)) + round(30 * S))
        bh = round(36 * S)
        box = (x1 - bw, cy - bh // 2, x1, cy + bh // 2)
        color = ACCENT if enabled else DARK_GREY
        hover = enabled and surf.hover == (key or text)
        rrect(img, box, round(5 * S), DARK_GREY if enabled else JET, outline=scale_color(color, 0.7 if hover else 1), width=1)
        ImageDraw.Draw(img).text(((box[0] + box[2]) / 2, cy), text, fill=rgb(color), font=f, anchor="mm")
        if enabled:
            surf.hit(box, lambda _x, _y: on_click(), key=key or text)
        return box

    def paint_keycodes(self, surf, img, w, h):
        S = self.S
        draw = ImageDraw.Draw(img)
        cell, cap = round(64 * S), round(50 * S)
        items = [k for k in KEYCODES if k[3] == self.category]
        cols = max(1, (w - round(24 * S)) // (cell))
        x0 = (w - cols * cell) // 2
        y0 = surf.top
        armed = self.kb.selected != "knob" or self.enc_slot
        for i, k in enumerate(items):
            x = x0 + (i % cols) * cell + (cell - cap) // 2
            y = y0 + (i // cols) * cell + (cell - cap) // 2
            refusal = self.code_refusal(k[0])
            hover = surf.hover == k[0] and armed and not refusal
            if hover:
                y -= round(2 * S)
            if -cap < y < h:
                box = (x, y, x + cap, y + cap)
                rrect(img, box, round(10 * S), DARK_GREY, outline=ACCENT if hover else MEDIUM_GREY, width=round(4 * S))
                label = k[1]
                ink = rgb(MEDIUM_GREY if refusal else LIGHT_GREY)
                if "\n" in label:
                    a, b = label.split("\n")
                    draw.text((x + cap / 2, y + cap / 2 - 8 * S), a, fill=ink, font=font(13 * S), anchor="mm")
                    draw.text((x + cap / 2, y + cap / 2 + 8 * S), b, fill=ink, font=font(13 * S), anchor="mm")
                else:
                    f = font(14 * S, name=SYMBOL_FONT if label == "▽" else None)
                    draw.text((x + cap / 2, y + cap / 2), label, fill=ink, font=f, anchor="mm")
                surf.hit(box, (lambda _x, _y, c=k[0]: self.assign(c)) if armed and not refusal else None,
                         key=k[0], tip=refusal or k[4])
        rows = (len(items) + cols - 1) // cols
        surf.top = y0 + rows * cell

    def code_refusal(self, code):
        """Why this keycode cannot go where the user is editing, or "" if it can."""
        if self.kb.selected == "knob":
            if code in MACRO_IDS:
                return "Macros run from the keyboard's memory. The knob is remapped in software."
            return "Fn2 only works from the keyboard's memory. The knob is remapped in software." if code == "fn2" else ""
        if code == "(default)" or self.layer == 0 or storable(self.layer, code):
            return ""
        if code == "fn2":
            return "Fn2 goes on a key on layer 0. The app adds that key to layer 2 for you."
        return f"Layers 1 and 2 live in the keyboard, and it has no code for {KC[code][4]}."

    def paint_encoder(self, surf, img, w, h):
        S = self.S
        draw = ImageDraw.Draw(img)
        for slot in ("ccw", "cw", "press"):
            x0, x1, cy = self.control_row(surf, img, w, KNOB_LABELS[slot])
            cap = round(42 * S)
            box = (x1 - cap, cy - cap // 2, x1, cy + cap // 2)
            armed = self.enc_slot == slot
            outline = ACCENT if armed or surf.hover == slot else MEDIUM_GREY
            rrect(img, box, round(9 * S), DARK_GREY, outline=outline, width=round(3 * S))
            label = KC[self.profile["knob"][slot]][1].replace("\n", " ")
            draw.text(((box[0] + box[2]) / 2, cy), label, fill=rgb(LIGHT_GREY), font=font(12 * S), anchor="mm")
            surf.hit((x0, cy - round(25 * S), x1, cy + round(25 * S)),
                     lambda _x, _y, s=slot: self.arm_slot(s), key=slot,
                     tip=f"{KNOB_LABELS[slot]}: click, then pick a keycode below")
        surf.top += round(8 * S)

    def arm_slot(self, slot):
        self.enc_slot = slot
        self.content.invalidate()

    # ---------- macros ----------
    def macro(self):
        return self.profile["macros"][self.macro_index]

    def option_chips(self, surf, img, x1, cy, options):
        """Right-aligned chips: (key, text, selected, on_click, tip). Returns the left edge."""
        S = self.S
        draw = ImageDraw.Draw(img)
        f = font(16 * S)
        x = x1
        for key, text, selected, on_click, tip in reversed(options):
            bw = round(f.getlength(text)) + round(18 * S)
            box = (x - bw, cy - round(15 * S), x, cy + round(15 * S))
            if selected:
                rrect(img, box, round(5 * S), ACCENT)
            color = INSIDE_ACCENT if selected else (LIGHT_GREY if surf.hover == key else MEDIUM_GREY)
            draw.text(((box[0] + box[2]) / 2, cy), text, fill=rgb(color), font=f, anchor="mm")
            surf.hit(box, on_click and (lambda _x, _y, fn=on_click: fn()), key=key, tip=tip)
            x -= bw + round(4 * S)
        return x

    def paint_macros(self, surf, img, w, h):
        S = self.S
        macro = self.macro()
        _x0, x1, cy = self.control_row(surf, img, w, "Macro")
        chips = []
        for i, code in enumerate(MACRO_IDS):
            steps = len(self.profile["macros"][i]["steps"])
            chips.append((code, f"M{i}", i == self.macro_index, lambda n=i: self.select_macro(n),
                          f"Macro {i}: {steps} steps" if steps else f"Macro {i}: empty"))
        self.option_chips(surf, img, x1, cy, chips)

        _x0, x1, cy = self.control_row(surf, img, w, "Stop Recording" if self.recording else "Record")
        draw = ImageDraw.Draw(img)
        box = self.accent_button(surf, img, x1, cy, "Stop" if self.recording else "Record", self.toggle_record,
                                 key="record")
        if not self.recording:
            box = self.accent_button(surf, img, box[0] - round(10 * S), cy, "Clear", self.clear_macro,
                                     enabled=bool(macro["steps"]), key="clear macro")
        keys = sum(1 for step in macro["steps"] if step[1])
        status = f"Type now: {len(macro['steps'])} of {MACRO_MAX_STEPS} steps" if self.recording \
            else f"{keys} keys, {len(macro['steps'])} steps"
        draw.text((box[0] - round(14 * S), cy), status, fill=rgb(ACCENT if self.recording else MEDIUM_GREY),
                  font=font(16 * S), anchor="rm")

        _x0, x1, cy = self.control_row(surf, img, w, "Repeat")
        count = "-" if macro["held"] else str(macro["repeat"])
        self.option_chips(surf, img, x1, cy, [
            ("less", "-", False, lambda: self.change_repeat(-1), "Run it fewer times"),
            ("count", f"{count} times" if count != "1" else "Once", False, None, ""),
            ("more", "+", False, lambda: self.change_repeat(1), "Run it more times"),
            ("held", "While Held", macro["held"], self.toggle_held, "Repeat until you let go of the key"),
        ])

        _x0, x1, cy = self.control_row(surf, img, w, "Timing")
        self.option_chips(surf, img, x1, cy, [
            ("recorded", "As Recorded", not macro["fixed"], lambda: self.set_fixed(False),
             f"Keep your typing speed. The keyboard's shortest gap is {MACRO_MIN_DELAY} ms."),
            ("fixed", f"Fixed {MACRO_MIN_DELAY} ms", macro["fixed"], lambda: self.set_fixed(True),
             "Run every step as fast as the keyboard allows"),
        ])
        self.paint_macro_steps(surf, img, w, macro)

    def paint_macro_steps(self, surf, img, w, macro):
        S = self.S
        draw = ImageDraw.Draw(img)
        row_w = min(w - round(40 * S), round(960 * S))
        x0, y0 = (w - row_w) // 2, surf.top + round(10 * S)
        cw, ch = round(112 * S), round(34 * S)
        cols = max(1, row_w // cw)
        for i, (key, down, delay) in enumerate(macro["steps"]):
            x, y = x0 + (i % cols) * cw, y0 + (i // cols) * ch
            rrect(img, (x, y, x + cw - round(6 * S), y + ch - round(6 * S)), round(5 * S), DARK_GREY)
            mid = y + (ch - round(6 * S)) / 2
            draw.text((x + round(8 * S), mid), f"{KC[key][1].split(chr(10))[-1]} {'down' if down else 'up'}",
                      fill=rgb(LIGHT_GREY), font=font(13 * S), anchor="lm")
            ms = MACRO_MIN_DELAY if macro["fixed"] else max(MACRO_MIN_DELAY, min(MACRO_MAX_DELAY, delay))
            draw.text((x + cw - round(13 * S), mid), f"{ms}", fill=rgb(MEDIUM_GREY), font=font(11 * S), anchor="rm")
        rows = (len(macro["steps"]) + cols - 1) // cols
        surf.top = y0 + rows * ch

    def select_macro(self, index):
        if not self.recording:
            self.macro_index = index
            self.content.invalidate()

    def toggle_record(self):
        if self.recording:
            self.stop_record()
        else:
            self.macro()["steps"] = []
            self.recording = True
            self.record_held.clear()
            self.record_last = None
            self.engine.record_window = user32.GetAncestor(self.winfo_id(), GA_ROOT)
            self.engine.sync()
            self.say("Recording. Type your macro here, then click Stop.")
            self.content.invalidate()

    def stop_record(self):
        self.recording = False
        self.engine.record_window = None
        self.engine.sync()
        steps = self.macro()["steps"]
        for vk in sorted(self.record_held):  # a key still down at the end must come back up
            steps.append([KC_BY_VK[vk][0], False, MACRO_MIN_DELAY])
        self.record_held.clear()
        keys = sum(1 for step in steps if step[1])
        self.say(f"Macro {self.macro_index} recorded: {keys} keys. To use it, pick a key in KEYMAP, "
                 f"choose M{self.macro_index} under MACRO, then Save in SAVE + LOAD.")
        self.after_change()

    def on_record(self, vk, down, ms):
        if not self.recording:
            return
        code = KC_BY_VK.get(vk)
        if not code or not macro_key(code[0]):
            if down:
                self.say(f"{code[4] if code else 'That key'} can't go in a macro.")
            return
        if down == (vk in self.record_held):
            return  # Windows repeats a held key, and a key held before recording only comes up
        self.record_held.add(vk) if down else self.record_held.discard(vk)
        delay = 0 if self.record_last is None else (ms - self.record_last) & 0xFFFFFFFF
        self.record_last = ms
        steps = self.macro()["steps"]
        steps.append([code[0], down, delay])
        if len(steps) + len(self.record_held) >= MACRO_MAX_STEPS:
            self.stop_record()
        else:
            self.content.invalidate()

    def clear_macro(self):
        self.macro()["steps"] = []
        self.after_change()

    def change_repeat(self, delta):
        macro = self.macro()
        macro["held"] = False
        macro["repeat"] = max(1, min(MACRO_MAX_REPEAT, macro["repeat"] + delta))
        self.after_change()

    def toggle_held(self):
        self.macro()["held"] = not self.macro()["held"]
        self.after_change()

    def set_fixed(self, fixed):
        self.macro()["fixed"] = fixed
        self.after_change()

    def paint_lighting(self, surf, img, w, h):
        S = self.S
        light = self.profile["lighting"]
        draw = ImageDraw.Draw(img)
        x0, x1, cy = self.control_row(surf, img, w, "Effect")
        f = font(16 * S)
        x = x1
        for name in reversed(EFFECTS):
            bw = round(f.getlength(name)) + round(18 * S)
            box = (x - bw, cy - round(15 * S), x, cy + round(15 * S))
            selected = light["effect"] == name.lower()
            if selected:
                rrect(img, box, round(5 * S), ACCENT)
            color = INSIDE_ACCENT if selected else (LIGHT_GREY if surf.hover == name else MEDIUM_GREY)
            draw.text(((box[0] + box[2]) / 2, cy), name, fill=rgb(color), font=f, anchor="mm")
            surf.hit(box, lambda _x, _y, n=name: self.set_light(effect=n.lower()), key=name)
            x -= bw + round(4 * S)
        for key in ("brightness", "speed"):
            self.slider_row(surf, img, w, key.title(), key)
        x0, x1, cy = self.control_row(surf, img, w, "Color")
        x = x1
        r = round(12 * S)
        for c in [MULTICOLOR] + SWATCHES[::-1]:
            box = (x - r * 2, cy - r, x, cy + r)
            if light["color"] == c:
                rrect(img, (box[0] - 3, box[1] - 3, box[2] + 3, box[3] + 3), r + 3, LIGHT_GREY)
            fill = c
            if c == MULTICOLOR:
                fill = css_gradient(r * 2, r * 2, 90, [(0, (255, 0, 0)), (0.5, (0, 255, 0)), (1, (0, 0, 255))])
            rrect(img, box, r, fill)
            surf.hit(box, lambda _x, _y, col=c: self.set_light(color=col), key=c,
                     tip="Multicolour" if c == MULTICOLOR else c)
            x -= r * 2 + round(10 * S)
        _x0, x1, cy = self.control_row(surf, img, w, "Keyboard Lighting")
        box = self.accent_button(surf, img, x1, cy, "Send Again", self.send_lighting, enabled=self.can_write())
        text, color = self.light_status
        draw.text((box[0] - round(14 * S), cy), text, fill=rgb(color), font=font(18 * S), anchor="rm")

    def slider_row(self, surf, img, w, label, key):
        S = self.S
        x0, x1, cy = self.control_row(surf, img, w, label)
        value = self.profile["lighting"][key]
        tw = round(300 * S)
        tx0 = x1 - tw
        k = tx0 + tw * value // 100
        h = round(3 * S)
        rrect(img, (tx0, cy - h, x1, cy + h), h, DARK_GREY)
        rrect(img, (tx0, cy - h, k, cy + h), h, ACCENT)
        r = round(9 * S)
        rrect(img, (k - r, cy - r, k + r, cy + r), r, ACCENT)

        def drag(x, _y):
            self.set_light(**{key: max(0, min(100, round((x - tx0) * 100 / tw)))})

        surf.hit((tx0 - r, cy - round(20 * S), x1 + r, cy + round(20 * S)), drag, key=key, drag=True, tip=f"{value}%")

    def set_light(self, **patch):
        light = self.profile["lighting"]
        light.update(patch)
        if light["effect"] == "static" and light["color"] == MULTICOLOR:
            light["color"] = SWATCHES[0]  # the firmware's Fixed-on has no multicolour mode
        self.save_quiet()
        self.content.invalidate()
        if self.light_job:
            self.after_cancel(self.light_job)
        self.light_job = self.after(LIGHT_DELAY_MS, self.apply_light)

    def apply_light(self, force=False):
        """Sends the lighting like VIA does, as soon as it changes. Each write
        lands in the keyboard's flash, so repeats of the last packet are skipped."""
        self.light_job = None
        packet = build_lighting_packet(self.profile["lighting"])
        if packet == self.light_sent and not force:
            return
        if not self.connected:
            self.light_status = ("Keyboard not connected", ERROR)
            self.content.invalidate()
        elif self.writing:
            self.light_pending = True
        else:
            self.light_writing = packet
            self.light_status = ("Sending", MEDIUM_GREY)
            self.start_write("lighting", packet)

    def paint_saveload(self, surf, img, w, h):
        self.control_row(surf, img, w, "Keyboard Memory", self.memory_status())
        if self.fn2_warning():
            self.control_row(surf, img, w, "Layer 2 (Fn2)", "Unreachable: no key on layer 0 is set to Fn2", ERROR)
        self.write_row(surf, img, w, "keymap", "Save Into Keyboard Memory", "Save",
                       "Replace all 3 layers in the keyboard?")
        self.write_row(surf, img, w, "clear", "Restore Factory Keymap", "Restore",
                       "Remove saved keys from the keyboard?")
        _x0, x1, cy = self.control_row(surf, img, w, "Save Current Layout")
        self.accent_button(surf, img, x1, cy, "Save", self.export_profile)
        _x0, x1, cy = self.control_row(surf, img, w, "Load Saved Layout")
        self.accent_button(surf, img, x1, cy, "Load", self.import_profile)
        _x0, x1, cy = self.control_row(surf, img, w, "Reset Remaps And Knob")
        self.accent_button(surf, img, x1, cy, "Reset", self.reset_remaps)

    def write_row(self, surf, img, w, kind, label, action, question):
        confirming = self.confirm == kind
        _x0, x1, cy = self.control_row(surf, img, w, question if confirming else label)
        if confirming:
            box = self.accent_button(surf, img, x1, cy, "Confirm", lambda: self.confirm_write(kind), key=f"yes {kind}")
            self.accent_button(surf, img, box[0] - round(10 * self.S), cy, "Cancel", self.cancel_write, key="no")
        else:
            self.accent_button(surf, img, x1, cy, action, lambda: self.ask_write(kind), enabled=self.can_write())

    def memory_status(self):
        profile = self.profile
        saved = sum(map(len, profile["flashed"]))
        text = f"{saved} key{"s" if saved > 1 else ""} saved" if saved else "Factory keymap"
        macros_changed = any(profile["macros"][MACRO_IDS.index(code)] != profile["flashed_macros"][MACRO_IDS.index(code)]
                             for code in macro_slots(profile["remaps"]))
        if profile["remaps"] != profile["flashed"] or macros_changed:
            text += ", unsaved changes"
        return text

    def paint_test(self, surf, img, w, h):
        _x0, x1, cy = self.control_row(surf, img, w, "Reset Keyboard")
        self.accent_button(surf, img, x1, cy, "Reset", self.reset_test)
        self.control_row(surf, img, w, "Last Key", self.key_seen or "Press any key", ACCENT if self.key_seen else MEDIUM_GREY)
        self.control_row(surf, img, w, "Keys Tested", f"{len(self.kb.tested)} of {len(KEYS) - 2}")
        self.knob_row(surf, img, w)

    def knob_row(self, surf, img, w):
        S = self.S
        x0, x1, cy = self.control_row(surf, img, w, "Knob")
        draw = ImageDraw.Draw(img)
        f = font(16 * S)
        x = x1
        for slot, name in (("press", "PRESS"), ("cw", "RIGHT"), ("ccw", "LEFT")):
            seen = self.knob_seen[slot]
            bw = round(f.getlength(name)) + round(20 * S)
            box = (x - bw, cy - round(15 * S), x, cy + round(15 * S))
            rrect(img, box, round(5 * S), ACCENT if seen else DARK_GREY)
            draw.text(((box[0] + box[2]) / 2, cy), name, fill=rgb(INSIDE_ACCENT if seen else MEDIUM_GREY), font=f, anchor="mm")
            x -= bw + round(6 * S)
        if self.knob_last:
            draw.text((x - round(8 * S), cy), self.knob_last, fill=rgb(MEDIUM_GREY), font=font(14 * S), anchor="rm")

    def paint_connection(self, surf, img, w, h):
        S = self.S
        draw = ImageDraw.Draw(img)
        x0, x1, cy = self.control_row(surf, img, w, "")
        draw.ellipse((x0 + 8 * S, cy - 8 * S, x0 + 24 * S, cy + 8 * S), fill=rgb(ACCENT if self.connected else ERROR))
        title = "Connected" if self.connected else "Not connected"
        draw.text((x0 + 34 * S, cy), title, fill=rgb(LIGHT_GREY), font=font(24 * S, True), anchor="lm")
        self.accent_button(surf, img, x1, cy, "Run Check", self.run_checks)

        def row(label, state, detail):
            mark = {True: GLYPH_CHECK, False: GLYPH_CROSS, None: None}[state]
            color = {True: ACCENT, False: ERROR, None: MEDIUM_GREY}[state]
            self.control_row(surf, img, w, label, detail, color, mark)

        d = self.devices[0] if self.devices else None
        if d:
            fw = d.get("release_number", 0)
            row("USB Device", True, f"{d['vendor_id']:04X}:{d['product_id']:04X}  {d.get('product_string') or ''}  fw {fw >> 8}.{fw & 255:02d}")
        else:
            row("USB Device", False, f"{self.profile['vid'][2:]}:{self.profile['pid'][2:]} not found")
        for key, label in (("config", "Config Interface"), ("firmware", "Firmware Response")):
            c = self.checks[key]
            row(label, c[0] if c else None, c[1] if c else "Waiting")
        ch = self.knob_channel
        row("Knob Channel", ch[0] if ch else None, ch[1] if ch else "Waiting")
        row("Key Input", True if self.key_seen else None, f"Seen {self.key_seen}" if self.key_seen else "Press any key")
        self.knob_row(surf, img, w)
        count = len(self.software_remaps()) + sum(self.profile["knob"][s] != KNOB_DEFAULTS[s] for s in KNOB_DEFAULTS)
        on = self.profile["remap_on"]
        row("Software Remap", True if on else None, f"On, {count} active" if on else f"Off, {count} ready")
        self.control_row(surf, img, w, "Keyboard Memory", self.memory_status())

    # ---------- HID console ----------
    def _build_console(self):
        frame = tk.Frame(self.lower, bg=JET)
        top = tk.Frame(frame, bg=JET)
        top.pack(fill="x", padx=14, pady=(12, 6))
        self.iface = ctk.CTkOptionMenu(top, values=["FF02 feature 7", "FF03 feature 8", "FF01 output 4", "FF55 output 6"],
                                       width=160, fg_color=DARK_GREY, button_color=DARK_GREY, button_hover_color=MEDIUM_GREY,
                                       text_color=ACCENT, dropdown_fg_color=LIGHT_JET, dropdown_text_color=LIGHT_GREY)
        self.iface.pack(side="left")
        self.packet = ctk.CTkEntry(top, placeholder_text="Hex bytes, first byte = report ID", fg_color=LIGHT_JET,
                                   border_color=DARK_GREY, text_color=LIGHT_GREY, font=("Consolas", 12))
        self.packet.pack(side="left", fill="x", expand=True, padx=8)
        for text, cmd in (("Send", self._console_send), ("Read", self._console_read)):
            ctk.CTkButton(top, text=text, width=70, fg_color=DARK_GREY, hover_color=MEDIUM_GREY, text_color=ACCENT,
                          border_color=ACCENT, border_width=1, corner_radius=5, command=cmd).pack(side="left", padx=(0, 6))
        self.log = ctk.CTkTextbox(frame, fg_color=LIGHT_JET, text_color=LIGHT_GREY, font=("Consolas", 12), corner_radius=0)
        self.log.pack(fill="both", expand=True, padx=14, pady=(0, 12))
        return frame

    def _console_target(self):
        page, kind, rid = self.iface.get().split()
        match = next((d for d in self.devices if d["usage_page"] == int(page, 16)), None)
        return match, kind, int(rid)

    def _console_log(self, kind, text):
        self.log.insert("end", f"{kind:<4}{text}\n")
        self.log.see("end")

    def _console_io(self, fn):
        d, kind, rid = self._console_target()
        if not d:
            return self._console_log("ERR", "Interface not connected")
        dev = hid.device()
        try:
            dev.open_path(d["path"])
            fn(dev, kind, rid)
        except (OSError, ValueError) as e:
            self._console_log("ERR", str(e))
        finally:
            dev.close()

    def _console_send(self):
        try:
            data = parse_hex_bytes(self.packet.get())
        except ValueError as e:
            return self._console_log("ERR", f"Bad hex: {e}")
        if not data:
            return

        def go(dev, kind, _rid):
            dev.send_feature_report(data) if kind == "feature" else dev.write(data)
            self._console_log("TX", f"{kind} {to_hex(data)}")

        self._console_io(go)

    def _console_read(self):
        def go(dev, kind, rid):
            size = 620 if rid == 7 else 8 if rid == 8 else 64
            resp = dev.get_feature_report(rid, size) if kind == "feature" else dev.read(size, 500)
            self._console_log("RX", f"{kind} {to_hex(resp)}" if resp else f"{kind} (no data)")

        self._console_io(go)

    # ---------- actions ----------
    def key_clicked(self, kid):
        if self.page != "configure":
            return
        if self.menu_item["configure"] != "KEYMAP":
            self.menu_item["configure"] = "KEYMAP"
            self.layout_lower()
        self.kb.selected = kid
        self.enc_slot = "ccw" if kid == "knob" else None
        self.content.scroll = 0
        self.kb.invalidate()
        self.content.invalidate()

    def set_layer(self, layer):
        self.layer = layer
        if not self.selectable(self.kb.selected):
            self.kb.selected = self.enc_slot = None
        self.kb.invalidate()
        self.content.invalidate()

    def selectable(self, kid):
        """Fn stays in firmware, the knob is software so it is layer 0 only, and
        a Fn2 key's position on layer 2 follows layer 0."""
        if kid == "fn" or (kid == "knob" and self.layer):
            return False
        return not (self.layer == 2 and self.profile["remaps"][0].get(kid) == "fn2")

    def fn2_warning(self):
        """Editing or holding keys on layer 2, with no key that reaches it."""
        layers = self.profile["remaps"]
        return (self.layer == 2 or bool(layers[2])) and not fn2_reachable(layers)

    def assign(self, code):
        profile = self.profile
        if self.kb.selected == "knob":
            if self.enc_slot:
                profile["knob"][self.enc_slot] = KNOB_DEFAULTS[self.enc_slot] if code == "(default)" else code
                order = ["ccw", "cw", "press"]
                self.enc_slot = order[(order.index(self.enc_slot) + 1) % 3]
        elif self.kb.selected:
            src = self.kb.selected
            remaps = profile["remaps"][self.layer]
            # On Fn a key's own code overrides the firmware's Fn default, so it is a real change.
            if code == "(default)" or (code == src and self.layer != 1):
                remaps.pop(src, None)
            else:
                remaps[src] = code
            if code == "fn2":
                profile["remaps"][2].pop(src, None)
            i = REMAPPABLE.index(src)
            self.kb.selected = next((k for k in REMAPPABLE[i + 1:] if self.selectable(k)), src)
        self.after_change()

    def after_change(self):
        self.sync_engine()
        self.save_quiet()
        self.kb.invalidate()
        self.content.invalidate()
        self.nav.invalidate()

    def software_remaps(self):
        # Base layer only, because Windows never sees the Fn key. Once that layer
        # lives in the keyboard, a second software pass would act on keys the
        # firmware already changed. Fn2 is a firmware layer, and in software a
        # key set to it would just go dead. Macros only run from the keyboard too.
        if self.profile["flashed"][0]:
            return {}
        return {src: dst for src, dst in self.profile["remaps"][0].items() if dst != "fn2" and dst not in MACRO_IDS}

    def sync_engine(self):
        self.engine.set_map(self.software_remaps(), self.profile["knob"])
        self.engine.remap_on = self.profile["remap_on"]
        self.engine.sync()

    def can_write(self):
        return self.connected and not self.writing

    def ask_write(self, kind):
        if kind == "keymap":
            try:
                build_save_packets(self.profile["remaps"], self.profile["macros"])
            except ValueError as e:
                self.say(str(e))
                return
        self.confirm = kind
        self.content.invalidate()

    def cancel_write(self):
        self.confirm = None
        self.content.invalidate()

    def confirm_write(self, kind):
        layers = self.profile["remaps"] if kind == "keymap" else empty_layers()
        self.writing_layers = [dict(layer) for layer in layers]  # exactly what this packet holds
        self.writing_macros = json.loads(json.dumps(self.profile["macros"]))
        self.start_write(kind, *build_save_packets(layers, self.profile["macros"]))

    def send_lighting(self):
        self.apply_light(force=True)

    def start_write(self, kind, *packets):
        self.confirm = None
        self.writing = True
        self.content.invalidate()
        devices = list(self.devices)

        def worker():
            try:
                for i, packet in enumerate(packets):
                    if i:
                        time.sleep(WRITE_GAP_SECONDS)
                    write_report(devices, PROTOCOL["config_page"], packet)
                self.events.put_nowait(("written", kind, ""))
            except (OSError, ValueError) as e:
                self.events.put_nowait(("written", kind, str(e) or "Write failed"))

        threading.Thread(target=worker, daemon=True).start()

    def on_written(self, kind, error):
        self.writing = False
        if kind == "lighting":
            self.light_status = ("Failed", ERROR) if error else ("Sent", ACCENT)
            self.light_sent = None if error else self.light_writing
        if error:
            self.say(f"Keyboard did not accept the write: {error}")
        elif kind == "lighting":
            p = self.light_writing
            self.say(f"Lighting sent: effect {p[9]}, brightness {p[10]} of 4, speed {p[11]} of 5, colour slot {p[12]}.")
        else:
            self.profile["flashed"] = self.writing_layers
            self.profile["flashed_macros"] = self.writing_macros
            self.say("Saved to the keyboard. Press a changed key to check it, then try with this app closed."
                     if kind == "keymap" else "Saved keys removed. The keyboard is back on its factory keymap.")
        self.after_change()
        if self.light_pending:
            self.light_pending = False
            self.apply_light()

    def toggle_remap(self):
        self.profile["remap_on"] = not self.profile["remap_on"]
        self.after_change()
        self.say("Software remap on. It stays active while this app is open." if self.profile["remap_on"]
                 else "Software remap off.")

    def reset_remaps(self):
        self.profile["remaps"] = empty_layers()
        self.profile["knob"] = dict(KNOB_DEFAULTS)
        self.after_change()
        self.say("Remaps and knob reset.")

    def reset_test(self):
        self.kb.tested.clear()
        self.knob_seen = {"ccw": None, "cw": None, "press": None}
        self.key_seen, self.knob_last = "", ""
        self.kb.invalidate()
        self.content.invalidate()

    def export_profile(self):
        path = filedialog.asksaveasfilename(defaultextension=".json", initialfile="sk65_layout.json",
                                            filetypes=[("JSON", "*.json")])
        if path:
            try:
                save_profile(self.profile, Path(path))
                self.say(f"Saved {path}")
            except OSError as e:
                self.say(f"Save failed: {e}")

    def import_profile(self):
        path = filedialog.askopenfilename(filetypes=[("JSON", "*.json")])
        if path:
            try:
                self.profile = read_profile(Path(path))
                self.after_change()
                self.say(f"Loaded {path}")
            except (OSError, ValueError) as e:
                self.say(f"Load failed: {e}")

    def save_quiet(self):
        try:
            save_profile(self.profile)
        except OSError as e:
            self.say(f"Could not save profile: {e}")

    def run_checks(self):
        devices = list(self.devices)
        self.checks = {"config": None, "firmware": None}
        self.content.invalidate()
        self.monitor.wake.set()

        def worker():
            self.events.put_nowait(("checks", probe(devices)))

        threading.Thread(target=worker, daemon=True).start()

    # ---------- events ----------
    def _pump(self):
        try:
            while True:
                self._handle(self.events.get_nowait())
        except queue.Empty:
            pass
        self.after(30, self._pump)

    def _handle(self, ev):
        kind = ev[0]
        if kind == "devices":
            self._on_devices(ev[1])
        elif kind == "checks":
            self.checks = ev[1]
            self.content.invalidate()
        elif kind == "status":
            self.say(ev[1])
        elif kind == "hook_error":
            self.say(f"Keyboard hook failed: {ev[1]}")
        elif kind == "knob_channel":
            self.knob_channel = (ev[1], f"Listening on report {PROTOCOL['consumer_report']}" if ev[1] else ev[2])
            self.engine.knob_open = ev[1]
            if not ev[1]:
                self.reader = None
            self.content.invalidate()
        elif kind == "knob_report":
            self._on_knob(ev[1])
        elif kind == "written":
            self.on_written(ev[1], ev[2])
        elif kind == "show":
            self.show_window()
        elif kind == "exit":
            self._on_close()
        elif kind == "record":
            self.on_record(ev[1], ev[2], ev[3])
        elif kind == "key":
            self._on_key(ev[1], ev[2])

    def _on_devices(self, devices):
        was = self.connected
        self.devices = sorted(devices, key=lambda d: d["usage_page"])
        self.connected = bool(devices)
        if self.connected != was:
            self.nav.invalidate()
            self.content.invalidate()
            if self.connected:
                self.say("Keyboard connected.")
                self.run_checks()
                self._start_reader()
            else:
                self.say("Keyboard disconnected.")
                self.light_sent = None
                self._stop_reader()
                self.checks = {"config": None, "firmware": None}
                self.knob_channel = None
                self.engine.knob_open = False

    def _start_reader(self):
        match = next((d for d in self.devices if d["usage_page"] == PROTOCOL["consumer_page"]), None)
        if match and not self.reader:
            self.reader = KnobReader(match["path"], self.events.put_nowait, self.engine.note_knob)
            self.reader.start()

    def _stop_reader(self):
        if self.reader:
            self.reader.stopping = True
            self.reader = None

    def _on_knob(self, data):
        if len(data) < 3 or data[0] != PROTOCOL["consumer_report"]:
            self.knob_last = f"Report {to_hex(data)}"
        else:
            usage = data[1] | data[2] << 8
            slot = KNOB_USAGES.get(usage)
            if usage == 0:
                return
            if slot:
                self.knob_seen[slot] = True
                self.knob_last = f"Usage {usage:04X}"
                self.kb.knob_flash = 1.0
            else:
                self.knob_last = f"Other usage {usage:04X}"
        self.kb.invalidate()
        self.content.invalidate()

    def _on_key(self, vk, down):
        k = KC_BY_VK.get(vk)
        name = k[4] if k else f"VK {vk:02X}"
        if down:
            self.key_seen = name
        if k and k[0] in self.kb.rects or (k and k[0] in REMAPPABLE):
            self.kb.tested[k[0]] = down
        self.kb.invalidate()
        self.content.invalidate()

    def _animate(self):
        kb = self.kb
        if self.hidden:
            pass
        elif kb.selected and self.page == "configure":
            kb.phase += 0.35
            kb.invalidate()
        if kb.knob_flash > 0:
            kb.knob_flash = max(0.0, kb.knob_flash - 0.08)
            kb.invalidate()
        self.after(50, self._animate)

    def _on_close(self):
        if self.recording:
            self.stop_record()
        self.monitor.stopping = True
        self.monitor.wake.set()
        self._stop_reader()
        self.engine.remap_on = self.engine.capture = False
        self.engine.stop()
        self.tray.stop()
        self.save_quiet()
        self.destroy()


if __name__ == "__main__" and first_instance():
    App().mainloop()
