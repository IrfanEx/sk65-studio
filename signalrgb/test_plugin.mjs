// Checks the SignalRGB plugin with a fake `device`. Never touches a real keyboard.
// Run from the repo root: node signalrgb/test_plugin.mjs
import assert from "node:assert/strict";

const sent = [];
globalThis.LightingMode = "Canvas";
globalThis.forcedColor = "#009bde";
globalThis.shutdownColor = "#000000";
globalThis.streamStart = "Once";
globalThis.device = {
	setName() {}, setSize() {}, setControllableLeds() {}, setImageFromUrl() {}, log() {}, pause() {},
	set_endpoint(iface, usage, page) { sent.push(["endpoint", iface, usage, page]); },
	send_report(data, length) { sent.push(["feature", data.slice(), length]); },
	write(data, length) { sent.push(["write", data.slice(), length]); return length; },
	color(x, y) { return [x * 10 + 1, y * 10 + 2, 200]; },  // encodes the LED's grid cell
};
const writes = () => sent.filter(e => e[0] === "write");

const P = await import("./Womier_SK65.js");
const names = P.LedNames();
const positions = P.LedPositions();

// 66 keys, each in its own cell of the 16 x 5 grid. The knob has no LED.
assert.equal(names.length, 66);
assert.equal(positions.length, 66);
assert.equal(new Set(positions.map(p => p.join(","))).size, 66, "two LEDs share a grid cell");
for (const [x, y] of positions) {
	assert.ok(x >= 0 && x < 16 && y >= 0 && y < 5, `LED outside the grid at ${x},${y}`);
}

// Only the stream and command collections on interface 1.
assert.equal(P.Validate({interface: 1, usage: 0x0202, usage_page: 0xff55}), true);
assert.equal(P.Validate({interface: 1, usage: 0x0001, usage_page: 0xff03}), true);
assert.equal(P.Validate({interface: 1, usage: 0x0001, usage_page: 0xff02}), false);
assert.equal(P.Validate({interface: 0, usage: 0x0006, usage_page: 0x0001}), false);

// Start: "08 06" as an 8-byte feature report on FF03, then streaming on FF55.
P.Initialize();
const feature = sent.find(e => e[0] === "feature");
assert.deepEqual(feature[1], [0x08, 0x06, 0, 0, 0, 0, 0, 0]);
assert.equal(feature[2], 8);
assert.deepEqual(sent.filter(e => e[0] === "endpoint").at(-1), ["endpoint", 1, 0x0202, 0xff55]);

// A frame is five 64-byte output reports, "06 nn" + 62 data bytes.
P.Render();
assert.equal(writes().length, 5);
writes().forEach(([, data, length], n) => {
	assert.equal(length, 64);
	assert.equal(data.length, 64);
	assert.equal(data[0], 0x06);
	assert.equal(data[1], n + 1);
});

// Each key's colour sits at 3 * its matrix position (PROTOCOL.md).
const stream = writes().flatMap(([, data]) => data.slice(2));
const MATRIX = {"Esc": 17, "Backspace": 31, "\\": 47, "Del": 48, "Enter": 64, "Page Up": 65, "Left Shift": 68,
	"Z": 70, "Up Arrow": 81, "Page Down": 82, "Left Ctrl": 85, "Space": 91, "Fn": 95, "Right Ctrl": 96,
	"Right Arrow": 99};
for (const [name, position] of Object.entries(MATRIX)) {
	const [x, y] = positions[names.indexOf(name)];
	assert.deepEqual(stream.slice(position * 3, position * 3 + 3), [x * 10 + 1, y * 10 + 2, 200], name);
}
const lit = [];
for (let p = 0; p < 102; p++) {
	if (stream[p * 3 + 2] === 200) { lit.push(p); }
}
assert.equal(lit.length, 66);
assert.ok(lit.every(p => p >= 17), "nothing is sent to the empty first matrix row");

// An unchanged frame is skipped until the keepalive, a changed one is sent at once.
P.Render();
assert.equal(writes().length, 5);
globalThis.LightingMode = "Forced";
P.Render();
assert.equal(writes().length, 10);
const first = writes().at(-5)[1].slice(2);  // first packet of the forced frame
assert.ok(first.slice(0, 17 * 3).every(b => b === 0), "matrix row 0 stays dark");
assert.deepEqual(first.slice(17 * 3, 17 * 3 + 3), [0x00, 0x9b, 0xde]);  // Esc

// Every Frame resends the start command before each frame.
globalThis.streamStart = "Every Frame";
globalThis.forcedColor = "#ff0000";
const features = sent.filter(e => e[0] === "feature").length;
P.Render();
assert.equal(sent.filter(e => e[0] === "feature").length, features + 1);

// Shutdown always sends, in the shutdown colour, or black when the system suspends.
P.Shutdown(true);
assert.equal(writes().length, 20);
assert.ok(writes().slice(-5).every(([, data]) => data.slice(2).every(b => b === 0)));

console.log("SignalRGB plugin: all checks passed");
