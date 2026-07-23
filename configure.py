"""
Interactive setup: learn MIDI controls one-by-one and map them to
Lightroom parameters or actions.  Saves to mappings.json when done.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Optional

import rtmidi
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, IntPrompt, Prompt
from rich.rule import Rule
from rich.table import Table

from lr_client import CONNECTIONS_FILE, LightroomClient
from params import (
    ACTIONS,
    PARAMETERS,
)

MAPPINGS_FILE = Path("mappings.json")
console = Console()


# ─────────────────────────────────────────── config helpers ──────────────────

def load_config() -> dict:
    if MAPPINGS_FILE.exists():
        return json.loads(MAPPINGS_FILE.read_text())
    return {"midi_port": None, "client_guid": None, "mappings": []}


def save_config(config: dict):
    MAPPINGS_FILE.write_text(json.dumps(config, indent=2))
    console.print(f"\n[green]✓[/green] Saved to [bold]{MAPPINGS_FILE}[/bold]")


# ─────────────────────────────────────────── MIDI helpers ────────────────────

def _midi_key(event: dict) -> str:
    """Stable string key that uniquely identifies a physical MIDI control."""
    t = event["type"]
    if t == "cc":
        return f"cc:{event['channel']}:{event['cc']}"
    if t in ("note_on", "note_off"):
        return f"note:{event['channel']}:{event['note']}"
    if t == "pitch_bend":
        return f"pb:{event['channel']}"
    return f"other:{event.get('raw', '')}"


def _related_midi_keys(event: dict) -> set[str]:
    """Return all MIDI keys that may represent the same physical control."""
    key = _midi_key(event)
    keys = {key}

    if event["type"] != "cc":
        return keys

    cc = event["cc"]
    channel = event["channel"]
    if 0 <= cc <= 31:
        keys.add(f"cc:{channel}:{cc + 32}")
    elif 32 <= cc <= 63:
        keys.add(f"cc:{channel}:{cc - 32}")

    return keys


def _describe_midi(event: dict) -> str:
    t = event["type"]
    if t == "cc":
        return f"CC #{event['cc']}  ch {event['channel']}"
    if t == "note_on":
        return f"Note {event['note']}  ch {event['channel']}"
    if t == "pitch_bend":
        return f"Pitch Bend  ch {event['channel']}"
    return str(event)


def _parse_raw(msg: list) -> Optional[dict]:
    status  = msg[0] & 0xF0
    channel = (msg[0] & 0x0F) + 1
    if status == 0xB0:
        return {"type": "cc",         "channel": channel, "cc":   msg[1], "value": msg[2]}
    if status == 0x90 and msg[2] > 0:
        return {"type": "note_on",    "channel": channel, "note": msg[1], "velocity": msg[2]}
    if status == 0x80 or (status == 0x90 and msg[2] == 0):
        return {"type": "note_off",   "channel": channel, "note": msg[1]}
    if status == 0xE0:
        return {"type": "pitch_bend", "channel": channel,
                "value": ((msg[2] << 7) | msg[1]) - 8192}
    return None


# ─────────────────────────────────────────── MidiWatcher ─────────────────────

class MidiWatcher:
    """Keeps a MIDI input port open for the duration of the configure session."""

    def __init__(self, port_name: str):
        self._port_name = port_name
        self._midi_in   = rtmidi.MidiIn()
        self._queue:    asyncio.Queue = None
        self._loop:     asyncio.AbstractEventLoop = None

    def _queue_event(self, msg: list):
        if self._queue is not None:
            self._queue.put_nowait(msg)

    def open(self):
        ports = self._midi_in.get_ports()
        idx   = next((i for i, p in enumerate(ports) if p == self._port_name), None)
        if idx is None:
            raise RuntimeError(f"MIDI port '{self._port_name}' not found. Available: {ports}")
        self._loop  = asyncio.get_running_loop()
        self._queue = asyncio.Queue()
        self._midi_in.ignore_types(sysex=True, timing=True, active_sense=True)
        self._midi_in.open_port(idx)
        self._midi_in.set_callback(self._callback)

    def _callback(self, message, _):
        msg, _ = message
        self._loop.call_soon_threadsafe(self._queue_event, list(msg))

    def flush_pending(self):
        if self._queue is None:
            return
        while True:
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break

    async def flush_burst(self, settle_ms: int = 120):
        """Drain trailing events from the same physical control gesture."""
        if self._queue is None:
            return

        settle_s = settle_ms / 1000.0
        while True:
            try:
                await asyncio.wait_for(self._queue.get(), timeout=settle_s)
            except asyncio.TimeoutError:
                break

    async def next_event(
        self,
        timeout: float = 30.0,
        ignored_keys: Optional[set[str]] = None,
    ) -> Optional[dict]:
        """Wait for the next meaningful MIDI message and return a parsed event."""
        deadline = asyncio.get_running_loop().time() + timeout
        ignored_keys = ignored_keys or set()
        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                return None
            try:
                raw = await asyncio.wait_for(self._queue.get(), timeout=remaining)
            except asyncio.TimeoutError:
                return None
            event = _parse_raw(raw)
            if not event or event["type"] == "note_off":
                continue
            if _related_midi_keys(event) & ignored_keys:
                continue
            if event:
                return event

    def close(self):
        try:
            self._midi_in.close_port()
        except Exception:
            pass
        del self._midi_in


# ─────────────────────────────────────────── interactive mapping ──────────────

def _mapping_choices() -> list[dict]:
    choices: list[dict] = []

    for param in PARAMETERS:
        choices.append({
            "action_type": "setValue",
            "label": param.label,
            "category": param.category,
            "search_text": " ".join([
                param.label.lower(),
                param.name.lower(),
                param.category.lower(),
                "slider develop parameter",
            ]),
            "mapping": {
                "action_type": "setValue",
                "parameter": param.name,
                "label": param.label,
                "min_val": param.min_val,
                "max_val": param.max_val,
                "sensitivity": param.sensitivity,
            },
        })

    for action, label, category in ACTIONS:
        choices.append({
            "action_type": "action",
            "label": label,
            "category": category,
            "search_text": " ".join([
                label.lower(),
                action.lower(),
                category.lower(),
                "action",
            ]),
            "mapping": {
                "action_type": "action",
                "action": action,
                "label": label,
            },
        })

    return choices


def _match_choices(query: str, choices: list[dict], limit: int = 10) -> list[dict]:
    query = query.strip().lower()
    if not query:
        return sorted(choices, key=lambda item: (item["action_type"], item["label"]))[:limit]

    terms = query.split()
    scored: list[tuple[tuple[int, int, int, str], dict]] = []

    for choice in choices:
        haystack = choice["search_text"]
        if not all(term in haystack for term in terms):
            continue

        label = choice["label"].lower()
        category = choice["category"].lower()
        starts_with = 0 if label.startswith(query) else 1
        label_hits = sum(label.find(term) if term in label else len(label) for term in terms)
        category_hits = sum(category.find(term) if term in category else len(category) for term in terms)
        score = (starts_with, label_hits + category_hits, len(label), label)
        scored.append((score, choice))

    scored.sort(key=lambda item: item[0])
    return [choice for _, choice in scored[:limit]]


def _show_choice_matches(matches: list[dict], query: str):
    title = "Matches" if query else "Popular mappings"
    table = Table(title=title, show_header=True, header_style="bold dim", box=None, padding=(0, 2))
    table.add_column("#", style="cyan", justify="right")
    table.add_column("Type")
    table.add_column("Target")
    table.add_column("Details", overflow="fold")

    for idx, choice in enumerate(matches, 1):
        if choice["action_type"] == "setValue":
            mapping = choice["mapping"]
            details = (
                f"{choice['category']} slider  "
                f"[dim]{mapping['min_val']} … {mapping['max_val']}[/dim]"
            )
            kind = "Slider"
        else:
            details = f"{choice['category'].title()} action"
            kind = "Action"

        table.add_row(str(idx), kind, choice["label"], details)

    console.print(table)


def _choose_target() -> Optional[dict]:
    choices = _mapping_choices()
    query = ""

    console.print()
    console.print(
        "[bold]Bind to:[/bold] [dim]search by name or category "
        "(examples: exposure, next, flag, zoom, color)[/dim]"
    )

    while True:
        query = Prompt.ask("  Search", default=query).strip()
        matches = _match_choices(query, choices)
        if not matches:
            console.print("[yellow]No matches. Try a broader search.[/yellow]")
            continue

        _show_choice_matches(matches, query)
        raw = console.input("  Select number, or press Enter to refine search: ").strip()
        if not raw:
            continue
        if raw.lower() in {"q", "quit", "cancel"}:
            return None
        if not raw.isdigit():
            console.print("[yellow]Enter a result number, or press Enter to search again.[/yellow]")
            continue

        idx = int(raw)
        if not 1 <= idx <= len(matches):
            console.print("[yellow]Selection out of range.[/yellow]")
            continue

        return matches[idx - 1]


def _choose_slider_mode(mapping: dict) -> dict:
    console.print()
    console.print("[bold]Mode:[/bold]")
    console.print(
        "  [cyan]1[/cyan]  Absolute  "
        "[dim]CC 0→127 maps linearly across the full range  "
        "(faders / pots)[/dim]"
    )
    console.print(
        "  [cyan]2[/cyan]  Relative  "
        "[dim]turn/nudge to increase or decrease  "
        "(endless encoders)[/dim]"
    )
    mode = (
        "absolute"
        if Prompt.ask("  Mode", choices=["1", "2"], default="1") == "1"
        else "relative"
    )

    result = dict(mapping)
    result["mode"] = mode
    sensitivity = result["sensitivity"]
    if mode == "relative":
        console.print(
            f"\n  [dim]Default sensitivity: {sensitivity} units per encoder tick. "
            f"(Range is {result['min_val']}…{result['max_val']})[/dim]"
        )
        if Confirm.ask("  Adjust sensitivity?", default=False):
            raw = Prompt.ask("  Sensitivity", default=str(sensitivity))
            try:
                sensitivity = float(raw)
            except ValueError:
                pass
        result["sensitivity"] = sensitivity

    return result

def _choose_mapping() -> Optional[dict]:
    """Ask the user what Lightroom function to bind to a control."""
    choice = _choose_target()
    if not choice:
        return None

    mapping = choice["mapping"]
    if mapping["action_type"] == "setValue":
        return _choose_slider_mode(mapping)
    return mapping


# ─────────────────────────────────────────── main flow ───────────────────────

async def run_configure(lr_port: Optional[int] = None):
    console.print(Panel.fit(
        "[bold blue]Lightroom MIDI Bridge[/bold blue]  ·  Setup",
        border_style="blue",
    ))

    # ── 1. Check feature is enabled ──────────────────────────────────────────
    if not CONNECTIONS_FILE.exists():
        console.print(Panel(
            "[yellow]External controllers are not yet enabled in Lightroom.[/yellow]\n\n"
            "To enable:\n"
            "  1. Open [bold]Adobe Lightroom[/bold]\n"
            "  2. [bold]Lightroom → Preferences → Interface[/bold]\n"
            "  3. Tick [bold]'Enable external controllers'[/bold]\n"
            "  4. Restart Lightroom\n"
            "  5. Run setup again.",
            title="[yellow]Action required[/yellow]",
            border_style="yellow",
        ))
        return

    config = load_config()

    # ── 2. Pick MIDI port ────────────────────────────────────────────────────
    console.print(Rule("[bold]MIDI input[/bold]"))
    _tmp = rtmidi.MidiIn()
    ports = _tmp.get_ports()
    del _tmp

    if not ports:
        console.print("[red]No MIDI input devices found. Connect one and try again.[/red]")
        return

    for i, p in enumerate(ports, 1):
        current = " [green]← current[/green]" if p == config.get("midi_port") else ""
        console.print(f"  [cyan]{i}[/cyan]  {p}{current}")

    default_idx = (
        ports.index(config["midi_port"]) + 1
        if config.get("midi_port") in ports
        else 1
    )
    choice    = IntPrompt.ask("Select port", default=default_idx)
    midi_port = ports[max(0, min(choice - 1, len(ports) - 1))]
    config["midi_port"] = midi_port
    console.print(f"[green]✓[/green] {midi_port}")

    # ── 3. Connect to Lightroom ──────────────────────────────────────────────
    console.print(Rule("[bold]Lightroom[/bold]"))
    lr = LightroomClient(lr_port)
    try:
        await lr.connect()
    except Exception as e:
        console.print(
            f"[red]✗ Could not connect to Lightroom at {lr.url}[/red]\n"
            f"  {e}\n"
            "  Make sure Lightroom is open and 'Enable external controllers' is on."
        )
        return

    console.print(f"  Registering with Lightroom… ", end="")
    try:
        ok = await lr.register(client_guid=config.get("client_guid"))
    except Exception as e:
        console.print(f"[red]✗ {e}[/red]")
        await lr.close()
        return

    if not ok:
        console.print("[red]✗ Registration failed.[/red]")
        await lr.close()
        return

    config["client_guid"] = lr.client_guid
    console.print(
        "[green]✓ Connected[/green]  "
        "[dim](accept the pairing dialog in Lightroom if prompted)[/dim]"
    )

    # ── 4. Show existing mappings ────────────────────────────────────────────
    if config["mappings"]:
        console.print(Rule("[bold]Current mappings[/bold]"))
        t = Table(show_header=True, header_style="bold dim", box=None, padding=(0, 2))
        t.add_column("MIDI control")
        t.add_column("→  Parameter / Action")
        t.add_column("Mode")
        for m in config["mappings"]:
            t.add_row(
                m.get("midi_desc", m.get("midi_key", "?")),
                m.get("label",     m.get("action",   "?")),
                m.get("mode",      m.get("action_type", "?")),
            )
        console.print(t)

    # ── 5. Mapping loop ──────────────────────────────────────────────────────
    console.print(Rule("[bold]Map controls[/bold]"))

    watcher = MidiWatcher(midi_port)
    watcher.open()
    watcher.flush_pending()
    ignored_keys: set[str] = set()

    try:
        while True:
            console.print(
                "\n[bold]Move or press a control on your MIDI device…[/bold]  "
                "[dim](Ctrl+C to finish)[/dim]"
            )

            try:
                event = await watcher.next_event(timeout=30.0, ignored_keys=ignored_keys)
            except KeyboardInterrupt:
                break

            await watcher.flush_burst()

            if event is None:
                console.print("[yellow]Timed out — no input detected.[/yellow]")
                if not Confirm.ask("Try again?", default=True):
                    break
                continue

            key  = _midi_key(event)
            related_keys = _related_midi_keys(event)
            desc = _describe_midi(event)
            console.print(f"\n[green]▶  Detected:[/green] [bold]{desc}[/bold]", end="")

            existing = next(
                (m for m in config["mappings"] if m.get("midi_key") == key), None
            )
            if existing:
                lbl = existing.get("label", existing.get("action", "?"))
                console.print(f"  [dim](currently → {lbl})[/dim]")
                if not Confirm.ask("  Remap this control?", default=True):
                    if not Confirm.ask("\nMap another control?", default=True):
                        break
                    watcher.flush_pending()
                    ignored_keys = set(related_keys)
                    continue
                config["mappings"] = [
                    m for m in config["mappings"] if m.get("midi_key") != key
                ]
            else:
                console.print()

            mapping = _choose_mapping()
            if mapping:
                mapping.update({
                    "midi_key":  key,
                    "midi_type": event["type"],
                    "midi_desc": desc,
                })
                if event["type"] == "cc":
                    mapping["cc"]      = event["cc"]
                    mapping["channel"] = event["channel"]
                elif event["type"] == "note_on":
                    mapping["note"]    = event["note"]
                    mapping["channel"] = event["channel"]
                elif event["type"] == "pitch_bend":
                    mapping["channel"] = event["channel"]

                config["mappings"].append(mapping)
                target = mapping.get("label", mapping.get("action", "?"))
                mode_hint = mapping.get("mode", mapping.get("action_type", ""))
                console.print(
                    f"[green]✓  Mapped:[/green] {desc} → [bold]{target}[/bold]"
                    f"  [dim]({mode_hint})[/dim]"
                )

            if not Confirm.ask("\nMap another control?", default=True):
                break

            watcher.flush_pending()
            ignored_keys = set(related_keys)

    except KeyboardInterrupt:
        pass
    finally:
        watcher.close()

    save_config(config)
    console.print(f"[dim]{len(config['mappings'])} mapping(s) total.[/dim]")
    await lr.close()
