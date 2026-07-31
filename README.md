# Lightroom MIDI Bridge

Control Adobe Lightroom CC with any MIDI controller — knobs, faders, buttons — via Lightroom's built-in external controller WebSocket API.

## Prerequisites

- macOS
- Adobe Lightroom CC (cloud version, **not** Classic)
- Python 3.10+
- A MIDI controller connected via USB or Bluetooth

## Enable the WebSocket server in Lightroom

This is a one-time step:

1. Open **Adobe Lightroom**
2. Go to **Lightroom → Preferences → Interface**
3. Tick **"Enable external controllers"**
4. **Restart Lightroom**

Once enabled, Lightroom starts a local WebSocket server (default port 7682) and writes its active port to:

```
~/Library/Application Support/Adobe/Lightroom CC/Connections/connections.json
```

## Installation

```bash
git clone <repo>
cd lightroom-control
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## Usage

### 1 — Interactive setup (run once, or whenever you want to change mappings)

```bash
.venv/bin/python main.py --configure
```

The setup app gives you a persistent workspace where you can:

- Select or refresh your MIDI input and connect to Lightroom
- Browse and search every Lightroom parameter and action
- See, replace, and delete all mappings in one table
- Choose a target, listen for a hardware control, then assign it
- Configure absolute faders/knobs or relative endless encoders

Use **Ctrl+S** to save, **Ctrl+L** to listen for a control, and **Q** to save and
quit. You can also use the on-screen buttons throughout.

Mappings are saved to `mappings.json` in the project folder. Each MIDI input has
its own mapping profile: selecting a new controller starts with a blank mapping
list, and switching back restores that controller's mappings.

### 2 — Run the bridge

```bash
.venv/bin/python main.py
```

The bridge loads `mappings.json`, connects to Lightroom, and translates MIDI messages to Lightroom API calls in real time. Press **Ctrl+C** to stop.

### Options

```
--configure / -c    Run interactive setup
--port PORT / -p    Override the Lightroom WebSocket port (default: read from connections.json)
```

## Controllable parameters

### Develop sliders

| Category | Parameters |
|---|---|
| **Light** | Exposure, Contrast, Highlights, Shadows, Whites, Blacks, Texture, Clarity, Dehaze |
| **Color** | Vibrance, Saturation, Temperature, Tint, Shadow Tint |
| **Detail** | Sharpening Amount/Radius/Detail/Masking, Luminance NR, Color NR |
| **Effects** | Vignette Amount/Midpoint/Feather, Grain Amount/Size/Roughness |

### Actions (triggered by buttons on press / note-on)

| Category | Actions |
|---|---|
| Navigation | Next / Previous Photo, Go Back / Forward |
| Editing | Auto Tone, Reset All Edits, Undo, Redo, Toggle B&W, Copy/Paste Edit Settings |
| View | Zoom In/Out, Zoom to Fit, Zoom 1:1, Toggle Zoom |
| Flag & Rating | Pick, Reject, Unflag, Toggle Pick/Reject, Rating +1/−1, Set Rating 0–5 |
| Color Label | Red, Yellow, Green, Blue, Purple, None |

## Control modes

**Absolute** — for faders and potentiometers: CC value 0→127 maps linearly across the parameter's full range, with value pickup (soft takeover) so the Lightroom slider does not jump until the hardware control crosses the current Lightroom value.

**Relative (signed-bit)** — for endless encoders that send values 1–63 for
clockwise/increase and 65–127 for counter-clockwise/decrease.

**Relative CW (0/127)** — for endless encoders that send 0 for one step
counter-clockwise and 127 for one step clockwise.

Sensitivity is configurable per control in either relative mode.

## How it works

Lightroom CC exposes a JSON-over-WebSocket API documented inside its own app bundle (`LrAppControllerDocumentation.lua`). When "Enable external controllers" is on, it listens at `ws://127.0.0.1:7682` (by default). This bridge:

1. Pairs with Lightroom via the `register` handshake
2. Subscribes to all parameter changes to keep a local value cache (needed for relative encoder mode)
3. On each MIDI message, looks up the mapping and calls `setValue(paramName, value)` or the relevant action method

## Project structure

```
lightroom-control/
├── main.py          Entry point and argument parsing
├── lr_client.py     Async WebSocket client for the Lightroom API
├── configure.py     Textual mapping editor
├── bridge.py        Real-time MIDI → Lightroom bridge
├── params.py        Parameter and action catalogue with ranges
├── requirements.txt
└── mappings.json    Your saved mappings (created by --configure)
```
