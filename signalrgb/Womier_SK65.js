// SignalRGB plugin for the Womier SK65 V1 (USB 0603:0351, product string "lucky65").
//
// Drives the keyboard's live LED stream, decoded from Womier's own driver
// (see PROTOCOL.md in SK65 Studio):
//   1. Feature report 8 on usage page FF03: "08 06" starts the stream.
//   2. Output report 6 on usage page FF55: five packets "06 nn" + 62 data bytes,
//      nn = 1..5, carrying R, G, B for each of the 102 matrix positions in order.
// Nothing here writes to the keyboard's memory, so its saved lighting is untouched.

export function Name() { return "Womier SK65"; }
export function VendorId() { return 0x0603; }
export function ProductId() { return 0x0351; }
export function Publisher() { return "SK65 Studio"; }
export function Size() { return [16, 5]; }
export function DefaultPosition() { return [120, 80]; }
export function DefaultScale() { return 8.0; }
export function DeviceType() { return "keyboard"; }
export function ImageUrl() { return "https://assets.signalrgb.com/devices/default/keyboards/65-keyboard-render.png"; }

/* global
shutdownColor:readonly
LightingMode:readonly
forcedColor:readonly
streamStart:readonly
*/
export function ControllableParameters() {
	return [
		{property:"shutdownColor", group:"lighting", label:"Shutdown Color", description:"Color shown when SignalRGB or the system shuts down", min:"0", max:"360", type:"color", default:"#000000"},
		{property:"LightingMode", group:"lighting", label:"Lighting Mode", description:"Canvas follows the active effect, Forced holds one color", type:"combobox", values:["Canvas", "Forced"], default:"Canvas"},
		{property:"forcedColor", group:"lighting", label:"Forced Color", description:"Color used when Lighting Mode is Forced", min:"0", max:"360", type:"color", default:"#009bde"},
		{property:"streamStart", group:"", label:"Stream Start Command", description:"Once sends the start command when the keyboard connects. Pick Every Frame if the keys show one frame and then stop changing.", type:"combobox", values:["Once", "Every Frame"], default:"Once"},
	];
}

// HID endpoints, all on interface 1.
const STREAM = {iface: 1, usage: 0x0202, page: 0xFF55};   // output report 6, 63 bytes
const COMMAND = {iface: 1, usage: 0x0001, page: 0xFF03};  // feature report 8, 7 bytes

export function Validate(endpoint) {
	if (endpoint.interface !== 1) { return false; }

	return (endpoint.usage_page === STREAM.page && endpoint.usage === STREAM.usage) ||
		(endpoint.usage_page === COMMAND.page && endpoint.usage === COMMAND.usage);
}

// Firmware key matrix, 6 rows of 17, from the vendor software. "." is unused.
const MATRIX = `
. . . . . . . . . . . . . . . . .
esc n1 n2 n3 n4 n5 n6 n7 n8 n9 n0 minus plus . bksp . .
tab q w e r t y u i o p lqu rqu k29 delete . .
caps a s d f g h j k l sem quo k42 enter pageup . .
lshift . z x c v b n m comma dot qmark rshift up pagedown . .
lctrl win lalt lspace ralt . mspace rspace mute rspace fn rctrl left down right . .
`.trim().split(/\s+/);

// Physical layout: [LED name, matrix id, width in units]. A null id is a gap or the knob, which has no LED.
// Space lights through "mspace", the position the vendor's per-key colors use.
const LAYOUT = [
	[["Esc", "esc", 1], ["1", "n1", 1], ["2", "n2", 1], ["3", "n3", 1], ["4", "n4", 1], ["5", "n5", 1], ["6", "n6", 1],
		["7", "n7", 1], ["8", "n8", 1], ["9", "n9", 1], ["0", "n0", 1], ["-_", "minus", 1], ["=+", "plus", 1],
		["Backspace", "bksp", 2], ["Knob", null, 1]],
	[["Tab", "tab", 1.5], ["Q", "q", 1], ["W", "w", 1], ["E", "e", 1], ["R", "r", 1], ["T", "t", 1], ["Y", "y", 1],
		["U", "u", 1], ["I", "i", 1], ["O", "o", 1], ["P", "p", 1], ["[", "lqu", 1], ["]", "rqu", 1], ["\\", "k29", 1.5],
		["Del", "delete", 1]],
	[["CapsLock", "caps", 1.75], ["A", "a", 1], ["S", "s", 1], ["D", "d", 1], ["F", "f", 1], ["G", "g", 1], ["H", "h", 1],
		["J", "j", 1], ["K", "k", 1], ["L", "l", 1], [";", "sem", 1], ["'", "quo", 1], ["Enter", "enter", 2.25],
		["Page Up", "pageup", 1]],
	[["Left Shift", "lshift", 2.25], ["Z", "z", 1], ["X", "x", 1], ["C", "c", 1], ["V", "v", 1], ["B", "b", 1],
		["N", "n", 1], ["M", "m", 1], [",", "comma", 1], [".", "dot", 1], ["/", "qmark", 1], ["Right Shift", "rshift", 1.75],
		["Up Arrow", "up", 1], ["Page Down", "pagedown", 1]],
	[["Left Ctrl", "lctrl", 1.25], ["Left Win", "win", 1.25], ["Left Alt", "lalt", 1.25], ["Space", "mspace", 6.25],
		["Fn", "fn", 1.25], ["Right Ctrl", "rctrl", 1.25], ["", null, 0.5],
		["Left Arrow", "left", 1], ["Down Arrow", "down", 1], ["Right Arrow", "right", 1]],
];

const vLedNames = [];
const vLedPositions = [];
const vLedMatrix = [];

LAYOUT.forEach((row, y) => {
	let x = 0;

	for (const [name, id, width] of row) {
		if (id) {
			vLedNames.push(name);
			vLedPositions.push([Math.round(x + width / 2 - 0.5), y]);
			vLedMatrix.push(MATRIX.indexOf(id));
		}

		x += width;
	}
});

export function LedNames() { return vLedNames; }
export function LedPositions() { return vLedPositions; }

const PACKETS = 5;
const PACKET_DATA = 62;
const KEEPALIVE_MS = 500;  // resend an unchanged frame this often

let lastFrame = "";
let lastSent = 0;

export function Initialize() {
	device.setName("Womier SK65");
	device.setSize(Size());
	device.setControllableLeds(vLedNames, vLedPositions);
	device.setImageFromUrl(ImageUrl());
	lastFrame = "";
	lastSent = 0;
	startStream();
	device.log(`Womier SK65: ${vLedNames.length} LEDs, live stream started`, {toFile: true});
}

export function Render() {
	sendFrame();
}

export function Shutdown(SystemSuspending) {
	sendFrame(SystemSuspending ? "#000000" : shutdownColor, true);
}

function startStream() {
	device.set_endpoint(COMMAND.iface, COMMAND.usage, COMMAND.page);
	device.send_report([0x08, 0x06, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00], 8);
	device.set_endpoint(STREAM.iface, STREAM.usage, STREAM.page);
}

function sendFrame(overrideColor, force) {
	const data = new Array(PACKETS * PACKET_DATA).fill(0);
	const fixed = overrideColor ? hexToRgb(overrideColor) : (LightingMode === "Forced" ? hexToRgb(forcedColor) : null);

	for (let i = 0; i < vLedNames.length; i++) {
		const color = fixed || device.color(vLedPositions[i][0], vLedPositions[i][1]);
		const offset = vLedMatrix[i] * 3;

		data[offset] = color[0];
		data[offset + 1] = color[1];
		data[offset + 2] = color[2];
	}

	const frame = data.join(",");
	const now = Date.now();

	if (!force && frame === lastFrame && now - lastSent < KEEPALIVE_MS) { return; }

	if (streamStart === "Every Frame") {
		startStream();
	}

	const results = [];

	for (let n = 0; n < PACKETS; n++) {
		results.push(device.write([0x06, n + 1].concat(data.slice(n * PACKET_DATA, (n + 1) * PACKET_DATA)), 64));
		device.pause(1);
	}

	if (!lastSent) {
		device.log(`Womier SK65: first frame sent, write results ${JSON.stringify(results)}`, {toFile: true});
	}

	lastFrame = frame;
	lastSent = now;
}

function hexToRgb(hex) {
	const result = /^#?([a-f\d]{2})([a-f\d]{2})([a-f\d]{2})$/i.exec(hex);

	return result ? [parseInt(result[1], 16), parseInt(result[2], 16), parseInt(result[3], 16)] : [0, 0, 0];
}
