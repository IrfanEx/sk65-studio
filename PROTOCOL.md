# Womier SK65 V1 protocol

Decoded from Womier's `SK65_Keyboard_Driver_V1.3` (Electron app, `JupengSeries.js`,
`SKLocation.js`, `SupportData.js`), read statically and never run. Interface sizes
come from the HID descriptors of a live board.

Tested on a real SK65 V1 (2026-11-06): the `07 03` keymap write, moving `PgDn` to
`End` through the app's Save Into Keyboard Memory, a Fn layer key, `Fn+J` to `Home`,
and a Fn2 layer key reached through a key set to Fn2, the same way. The `07 02` lighting
write is confirmed too: effects, speed and colour slots. Every other write below is
decoded but not yet confirmed on hardware.

## Interfaces (USB 0603:0351, product string "lucky65")

| Usage page | Report | Direction | Size (without ID) | Purpose |
|---|---|---|---|---|
| `FF02` | 7 | Feature | 619 | Keymap, lighting, macros, per-key colour |
| `FF03` | 8 | Feature | 7 | Short commands |
| `FF55` | 6 | Output, Input | 63 | Live LED stream |
| `FF01` | 4 | Output, Input | 8 | Key index events (`04 F8 index state`) |
| `000C` | 3 | Input | 2 | Consumer usage, 16-bit little endian |

Reading these feature reports returns no data, so a written keymap cannot be read back.

## Save sequence in the vendor app

1. `07 03` keymap (report 7, wait 100 ms)
2. `07 05` macros, only if a key uses one (200 ms each, 10 maximum)
3. `07 02` lighting (100 ms)
4. `08 09` debounce (report 8, 10 ms)
5. `08 0B` light-off and hibernate timers (10 ms)

The rest of the vendor's "profile save" is a local database. Nothing else goes to the keyboard.

## Packets

All report 7 buffers are 620 bytes including the ID, zero filled.

### Keymap: `07 03`

| Offset | Value |
|---|---|
| 0 | `07` |
| 1 | `03` |
| 2 | Layer selected in the vendor UI + 1, normally `01` |
| 8 | 612 bytes: 3 layers (base, Fn, Fn2) of 102 positions, 2 bytes each |

Each position is two bytes:

- `00 cc`: use the firmware default. The vendor app writes the default code, and
  `00 00` for positions it does not list.
- `80 cc`: custom code `cc`.
- `8m cc`: key combination; `m` bits are Ctrl=1, Shift=2, Alt=4, Win=8.
- `80 Fn`: macro number n (`F0` + n).

All three layers go in one report, so a save replaces the entire keymap.

Layers, from the vendor app and the hjs-weikav-keymap-patch notes on the firmware.
Custom keys on layers 1 and 2 are confirmed on this board. The rest is as read from
those sources.

- Base and Fn: the firmware only uses values with the `80` flag. Anything else falls
  back to its factory keymap, so `00 cc` on Fn is the factory Fn function, whatever `cc`
  says, and that table is not in the vendor app.
- Fn2: the firmware takes every value as stored, so `00 cc` is code `cc` and `00 00` is
  no key. Writing the base layer's factory codes there is what keeps the Fn key,
  `00 FE` at position 95, working on that layer. Positions 93 and 94 are not listed, so
  they stay `00 00` and send nothing on Fn2.
- No key holds Fn2 from the factory, so its layer is unreachable until a key has `FF`.
- The vendor app writes a Fn2 key to the same position on the base and Fn2 layers, and
  refuses edits to that position on Fn2. It does the same for Fn on the base and Fn
  layers. The source gives no reason. Presumably the key has to still read as Fn2 while
  its layer is held, or the release is missed. Written this way, a Fn2 key reached its
  layer and released cleanly on this board. Writing it to the base layer alone is untested.
- Byte 2: the vendor app sends the selected layer + 1. This app always sends `01`, and
  a layer 1 key was stored with that value, so byte 2 does not limit the write to the
  base layer.

Codes `cc` are USB HID usages, plus: Fn `FE`, Fn2 `FF`, mouse `B4` left, `B5` right,
`B6` middle, `B7` back, `B8` forward, and media:

| Code | Function | Code | Function |
|---|---|---|---|
| `A0` | Media player | `A7` | Next |
| `A1` | Play/Pause | `A8` | Homepage |
| `A3` | Volume up | `A9` | Refresh |
| `A4` | Volume down | `AB` | Page back |
| `A5` | Stop | `AC` | Page forward |
| `A6` | Previous | `AF` | My Computer |

Mute `A2` is not usable as a custom code (firmware does not translate it).
Source: [hjs-weikav-keymap-patch](https://github.com/tiger42/hjs-weikav-keymap-patch).

### Matrix positions (rows of 17)

```
  0  (empty row)
 17  esc 1 2 3 4 5 6 7 8 9 0 - = . bksp . .
 34  tab q w e r t y u i o p [ ] \ delete . .
 51  caps a s d f g h j k l ; ' iso enter pageup . .
 68  lshift . z x c v b n m , . / rshift up pagedown . .
 85  lctrl win lalt lspace ralt . space rspace MUTE rspace fn rctrl left down right . .
```

`.` is unused. Position 93, `MUTE`, default `A2`, is not exposed by the vendor app and
is probably the knob press. This is inferred, not tested.

### Knob

Knob rotation appears nowhere in the vendor app or its 102 position matrix, so it has
no known firmware setting. Software remapping is the only known way to change it.

Confirmed on the board: each step sends report 3 with usage `EA` (left) or `E9` (right),
then `00` on release, and pressing sends `E2`. The knob has no LEDs.

Windows turns these into `VK_VOLUME_DOWN`, `VK_VOLUME_UP` and `VK_VOLUME_MUTE`, and a
low-level keyboard hook sees them flagged as injected, with scan code 0 and no sign of
which device sent them. Any device's volume keys look the same, so the app matches each
key against the board's own report, which lands within a millisecond of it.

### Lighting: `07 02`

| Offset | Value |
|---|---|
| 0, 1, 2 | `07 02 01` |
| 9 | Effect |
| 10 | Brightness, 0 to 4 |
| 11 | Speed, 1 to 5 |
| 12 to 31 | Colour slot for each effect: 0 red, 1 green, 2 blue, 3 yellow, 4 magenta, 5 cyan, 6 white, 7 multicolour |

Effects: 0 Neon stream, 1 Fixed on, 2 Respire, 3 Ripples, 4 Rainbow wheel,
5 Ripple band up/down, 6 Reaction, 7 Two block, 8 Random colour, 9 Double wave,
10 Retro snake, 11 Double spiral, 12 Ripple band, 13 Kamehameha, 14 Wave 90,
15 Intersect, 16 Shadow disappear, 17 Follow, 18 Snake up/down, 19 Customize.

Confirmed on the board: Respire, Neon stream and Fixed on; speed 5 is the fastest;
slots 0, 2, 3 and 5 show the listed colours. The other slots come from the vendor's
colour list.

Fn+Space switches RGB off in the firmware, and `07 02` does not turn it back on. While
it is off the keyboard accepts the write and shows nothing, and Fn+\ and Fn+Up have no
visible effect. The vendor app has no command for this, so only Fn+Space restores it.

### Per-key colour: `07 07`

Offset 8: 3 bytes R, G, B per matrix position (`8 + 3 * position`). Used with effect 19.

### Short commands (report 8, page FF03)

| Bytes | Purpose |
|---|---|
| `08 08` | Reset to factory |
| `08 09 n` | Debounce time |
| `08 0B a b` | Light-off index + 1, hibernate index + 1 |
| `08 06` | Start live LED stream |

### Live LED stream (report 6, page FF55)

After `08 06`: 5 packets `06 nn` + 62 data bytes, with `nn` 1 to 5. Data is 3 bytes per
matrix position, 10 ms apart.

## Recovery

Hold Fn+Esc for 3 seconds to factory reset, or send `08 08`.
