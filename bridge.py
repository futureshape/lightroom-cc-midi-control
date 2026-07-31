"""
Real-time bridge: MIDI input → Lightroom WebSocket API calls.
Loads mappings.json written by configure.py.
"""
from __future__ import annotations

import asyncio
from typing import Optional

import rtmidi
from rich.console import Console
from rich.panel import Panel

from lr_client import CONNECTIONS_FILE, LightroomClient
from config_store import load_config, mappings_for

console = Console()


def _parse_midi(msg: list) -> Optional[tuple[str, dict]]:
    """Return (midi_key, event_dict) or None for unrecognised messages."""
    status  = msg[0] & 0xF0
    channel = (msg[0] & 0x0F) + 1
    if status == 0xB0:
        return (
            f"cc:{channel}:{msg[1]}",
            {"type": "cc",         "channel": channel, "cc":   msg[1], "value": msg[2]},
        )
    if status == 0x90 and msg[2] > 0:
        return (
            f"note:{channel}:{msg[1]}",
            {"type": "note_on",    "channel": channel, "note": msg[1], "velocity": msg[2]},
        )
    if status == 0x80 or (status == 0x90 and msg[2] == 0):
        return (
            f"note:{channel}:{msg[1]}",
            {"type": "note_off",   "channel": channel, "note": msg[1]},
        )
    if status == 0xE0:
        return (
            f"pb:{channel}",
            {"type": "pitch_bend", "channel": channel,
             "value": ((msg[2] << 7) | msg[1]) - 8192},
        )
    return None


class Bridge:
    """
    Connects one MIDI input port to Lightroom via WebSocket.

    CC controls map to:
      - absolute mode : CC 0→127 linearly covers the parameter's min/max range.
      - relative mode : signed-bit encoding (1–63 = clockwise / increment,
                        65–127 = counter-clockwise / decrement).
                        Most modern endless encoders use this encoding.

    Note-on messages trigger one-shot actions (next photo, undo, etc.).
    """

    def __init__(self, config: dict, lr_port: Optional[int] = None):
        self._config    = config
        self._lr        = LightroomClient(lr_port)
        self._midi_in   = rtmidi.MidiIn()
        self._queue:    asyncio.Queue              = None
        self._loop:     asyncio.AbstractEventLoop  = None
        # Fast lookup: midi_key → mapping dict
        self._mappings = mappings_for(config)
        self._index:    dict[str, dict] = {
            m["midi_key"]: m for m in self._mappings
        }
        # Cache of current LR parameter values (kept fresh via observer).
        self._lr_cache: dict[str, float] = {}
        # Soft-takeover state for absolute controls.
        self._pickup_state: dict[str, dict[str, float | bool]] = {}

    # ──────────────────────────────────────────────────────── MIDI ────────────

    def _open_midi(self) -> str:
        port_name = self._config.get("midi_port", "")
        ports     = self._midi_in.get_ports()
        idx       = next((i for i, p in enumerate(ports) if p == port_name), None)
        if idx is None:
            raise RuntimeError(
                f"MIDI port '{port_name}' not found.\n"
                f"Available: {ports}\n"
                "Re-run with --configure to update."
            )
        self._midi_in.ignore_types(sysex=True, timing=True, active_sense=True)
        self._midi_in.open_port(idx)
        self._midi_in.set_callback(self._on_midi)
        return port_name

    def _on_midi(self, message, _):
        msg, _ = message
        self._loop.call_soon_threadsafe(self._queue.put_nowait, list(msg))

    # ──────────────────────────────────────────────────── Lightroom ───────────

    def _on_lr_change(self, data: dict):
        """Observer callback — keep the value cache up to date."""
        response = data.get("response")
        if isinstance(response, dict):
            for k, v in response.items():
                try:
                    self._lr_cache[k] = float(v)
                except (TypeError, ValueError):
                    pass

    async def _dispatch(self, midi_key: str, event: dict):
        mapping = self._index.get(midi_key)
        if not mapping:
            return

        # Prevent duplicate triggers for controls that emit both note_on and
        # note_off by matching the configured MIDI event type.
        mapped_type = mapping.get("midi_type")
        event_type = event.get("type")
        if mapped_type:
            if event_type != mapped_type:
                return
        elif event_type == "note_off":
            # Backwards compatibility for older mappings that predate midi_type.
            # One-shot note actions should fire on press, not release.
            return

        action_type = mapping.get("action_type")

        if action_type == "setValue":
            await self._handle_slider(midi_key, mapping, event)

        elif action_type == "action":
            await self._handle_action(mapping)

    async def _current_value(self, param: str) -> float:
        current = self._lr_cache.get(param)
        if current is not None:
            return current

        try:
            raw = await self._lr.call("getValue", param)
            current = float(raw) if raw is not None else 0.0
        except Exception:
            current = 0.0

        self._lr_cache[param] = current
        return current

    def _absolute_value(self, cc_raw: int, min_val: float, max_val: float) -> float:
        value = min_val + (cc_raw / 127.0) * (max_val - min_val)
        return round(value, 3)

    def _pickup_ready(
        self,
        midi_key: str,
        current: float,
        target: float,
        min_val: float,
        max_val: float,
    ) -> bool:
        state = self._pickup_state.setdefault(midi_key, {})
        last_sent = state.get("last_sent")
        latched = bool(state.get("latched", False))

        step = abs(max_val - min_val) / 127.0
        tolerance = max(step / 2.0, 0.001)

        if latched and last_sent is not None and abs(current - float(last_sent)) > tolerance:
            latched = False
            state["latched"] = False

        if latched:
            state["last_target"] = target
            return True

        last_target = state.get("last_target")
        crossed = abs(target - current) <= tolerance
        if not crossed and last_target is not None:
            low = min(float(last_target), target)
            high = max(float(last_target), target)
            crossed = low <= current <= high

        state["last_target"] = target
        if crossed:
            state["latched"] = True

        return crossed

    async def _handle_slider(self, midi_key: str, mapping: dict, event: dict):
        param       = mapping["parameter"]
        min_val     = float(mapping["min_val"])
        max_val     = float(mapping["max_val"])
        mode        = mapping.get("mode", "absolute")
        label       = mapping.get("label", param)
        cc_raw      = event.get("value") if event["type"] == "cc" else None

        if cc_raw is None:
            return

        if mode == "absolute":
            current = await self._current_value(param)
            value = self._absolute_value(cc_raw, min_val, max_val)
            if not self._pickup_ready(midi_key, current, value, min_val, max_val):
                return

        elif mode == "relative":
            # Signed-bit: 0 or 64 = no movement, 1-63 = CW (+), 65-127 = CCW (-)
            if cc_raw == 0 or cc_raw == 64:
                return
            delta       = cc_raw if cc_raw < 64 else cc_raw - 128
            sensitivity = float(mapping.get("sensitivity", 1.0))
            step        = delta * sensitivity

            current = await self._current_value(param)

            value = max(min_val, min(max_val, current + step))
            value = round(value, 3)

        else:
            return

        try:
            await self._lr.call("setValue", param, value)
            self._lr_cache[param] = value
            if mode == "absolute":
                state = self._pickup_state.setdefault(midi_key, {})
                state["last_sent"] = value
            console.log(f"[green]▶[/green] {label:<28} = {value}")
        except Exception as e:
            console.log(f"[red]✗[/red] setValue({param}, {value}): {e}")

    async def _handle_action(self, mapping: dict):
        name  = mapping["action"]
        label = mapping.get("label", name)
        try:
            await self._lr.call(name)
            console.log(f"[cyan]▶[/cyan] {label}")
        except Exception as e:
            console.log(f"[red]✗[/red] {name}: {e}")

    # ────────────────────────────────────────────────────── main loop ─────────

    async def run(self):
        self._loop  = asyncio.get_running_loop()
        self._queue = asyncio.Queue()

        # Connect to Lightroom
        await self._lr.connect()
        ok = await self._lr.register(
            client_guid=self._config.get("client_guid")
        )
        if not ok:
            raise RuntimeError("Failed to register with Lightroom.")

        # Keep the edited panel visible while the bridge is active.
        try:
            await self._lr.call("revealAdjustedControls", True)
        except Exception as e:
            console.log(f"[yellow]warn[/yellow] revealAdjustedControls(True): {e}")

        # Subscribe to all parameter changes to keep the cache fresh
        await self._lr.subscribe("", self._on_lr_change)

        # Open MIDI
        port_name = self._open_midi()

        n = len(self._mappings)
        names = ", ".join(
            m.get("label", m.get("action", "?"))
            for m in self._mappings[:6]
        )
        if n > 6:
            names += "…"
        console.print(f"[green]✓[/green] MIDI:      {port_name}")
        console.print(f"[green]✓[/green] Lightroom: {self._lr.url}")
        console.print(f"[green]✓[/green] {n} mapping(s): {names}")
        console.print("\n[dim]Press Ctrl+C to stop.[/dim]\n")

        try:
            while True:
                raw    = await self._queue.get()
                parsed = _parse_midi(raw)
                if parsed:
                    midi_key, event = parsed
                    try:
                        await self._dispatch(midi_key, event)
                    except Exception as e:
                        console.log(f"[red]Error:[/red] {e}")
        except (asyncio.CancelledError, KeyboardInterrupt):
            pass
        finally:
            try:
                self._midi_in.close_port()
            except Exception:
                pass
            try:
                await self._lr.close()
            except Exception:
                pass


async def run_bridge(lr_port: Optional[int] = None):
    if not CONNECTIONS_FILE.exists():
        console.print(
            "[red]External controllers are not enabled in Lightroom.[/red]\n"
            "Go to [bold]Lightroom → Preferences → Interface[/bold] and enable "
            "'Enable external controllers', then restart Lightroom."
        )
        return

    config = load_config()
    if config is None or not mappings_for(config):
        console.print(
            "[yellow]No mappings found.[/yellow]  "
            "Run [bold]python main.py --configure[/bold] first."
        )
        return

    bridge = Bridge(config, lr_port)
    await bridge.run()
