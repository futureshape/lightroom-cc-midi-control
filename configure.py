"""Textual configuration UI for the Lightroom MIDI bridge."""
from __future__ import annotations

import asyncio
from typing import Optional

import rtmidi
from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import (
    Button,
    DataTable,
    Footer,
    Header,
    Input,
    Label,
    OptionList,
    Select,
    Static,
)
from textual.widgets.option_list import Option

from lr_client import LightroomClient
from params import ACTIONS, PARAMETERS
from config_store import clear_mappings, empty_config, load_config, mappings_for, save_config


def _midi_key(event: dict) -> str:
    event_type = event["type"]
    if event_type == "cc":
        return f"cc:{event['channel']}:{event['cc']}"
    if event_type in ("note_on", "note_off"):
        return f"note:{event['channel']}:{event['note']}"
    if event_type == "pitch_bend":
        return f"pb:{event['channel']}"
    return f"other:{event.get('raw', '')}"


def _related_midi_keys(event: dict) -> set[str]:
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
    if event["type"] == "cc":
        return f"CC #{event['cc']} · channel {event['channel']}"
    if event["type"] == "note_on":
        return f"Note {event['note']} · channel {event['channel']}"
    if event["type"] == "pitch_bend":
        return f"Pitch Bend · channel {event['channel']}"
    return str(event)


def _parse_raw(msg: list) -> Optional[dict]:
    status = msg[0] & 0xF0
    channel = (msg[0] & 0x0F) + 1
    if status == 0xB0:
        return {"type": "cc", "channel": channel, "cc": msg[1], "value": msg[2]}
    if status == 0x90 and msg[2] > 0:
        return {
            "type": "note_on",
            "channel": channel,
            "note": msg[1],
            "velocity": msg[2],
        }
    if status == 0x80 or (status == 0x90 and msg[2] == 0):
        return {"type": "note_off", "channel": channel, "note": msg[1]}
    if status == 0xE0:
        return {
            "type": "pitch_bend",
            "channel": channel,
            "value": ((msg[2] << 7) | msg[1]) - 8192,
        }
    return None


class MidiWatcher:
    """Keep a MIDI input open and expose its messages through an async queue."""

    def __init__(self, port_name: str):
        self._port_name = port_name
        self._midi_in = rtmidi.MidiIn()
        self._queue: Optional[asyncio.Queue] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def open(self) -> None:
        ports = self._midi_in.get_ports()
        index = next((i for i, name in enumerate(ports) if name == self._port_name), None)
        if index is None:
            raise RuntimeError(f"MIDI port '{self._port_name}' is no longer available.")
        self._loop = asyncio.get_running_loop()
        self._queue = asyncio.Queue()
        self._midi_in.ignore_types(sysex=True, timing=True, active_sense=True)
        self._midi_in.open_port(index)
        self._midi_in.set_callback(self._callback)

    def _callback(self, message, _) -> None:
        msg, _ = message
        if self._loop is not None and self._queue is not None:
            self._loop.call_soon_threadsafe(self._queue.put_nowait, list(msg))

    def flush_pending(self) -> None:
        if self._queue is None:
            return
        while True:
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                return

    async def next_event(self, timeout: float = 30.0) -> Optional[dict]:
        if self._queue is None:
            return None
        deadline = asyncio.get_running_loop().time() + timeout
        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                return None
            try:
                raw = await asyncio.wait_for(self._queue.get(), timeout=remaining)
            except asyncio.TimeoutError:
                return None
            event = _parse_raw(raw)
            if event and event["type"] != "note_off":
                return event

    async def flush_burst(self, settle_ms: int = 120) -> None:
        if self._queue is None:
            return
        while True:
            try:
                await asyncio.wait_for(self._queue.get(), timeout=settle_ms / 1000)
            except asyncio.TimeoutError:
                return

    def close(self) -> None:
        try:
            self._midi_in.close_port()
        except Exception:
            pass


def _mapping_choices() -> list[dict]:
    choices: list[dict] = []
    for param in PARAMETERS:
        choices.append(
            {
                "action_type": "setValue",
                "label": param.label,
                "category": param.category,
                "search_text": " ".join(
                    (param.label, param.name, param.category, "slider develop parameter")
                ).lower(),
                "mapping": {
                    "action_type": "setValue",
                    "parameter": param.name,
                    "label": param.label,
                    "min_val": param.min_val,
                    "max_val": param.max_val,
                    "sensitivity": param.sensitivity,
                },
            }
        )
    for action, label, category in ACTIONS:
        choices.append(
            {
                "action_type": "action",
                "label": label,
                "category": category,
                "search_text": " ".join((label, action, category, "action")).lower(),
                "mapping": {
                    "action_type": "action",
                    "action": action,
                    "label": label,
                },
            }
        )
    return choices


def _match_choices(query: str, choices: list[dict], limit: int = 50) -> list[dict]:
    query = query.strip().lower()
    if not query:
        return sorted(choices, key=lambda item: (item["category"], item["label"]))[:limit]
    terms = query.split()
    scored = []
    for choice in choices:
        if not all(term in choice["search_text"] for term in terms):
            continue
        label = choice["label"].lower()
        category = choice["category"].lower()
        score = (
            0 if label.startswith(query) else 1,
            sum(label.find(term) if term in label else len(label) for term in terms)
            + sum(
                category.find(term) if term in category else len(category)
                for term in terms
            ),
            len(label),
            label,
        )
        scored.append((score, choice))
    scored.sort(key=lambda item: item[0])
    return [choice for _, choice in scored[:limit]]


class ConfirmResetScreen(ModalScreen[bool]):
    """Confirmation dialog for clearing the active controller profile."""

    CSS = """
    ConfirmResetScreen { align: center middle; background: $background 60%; }
    #reset-dialog {
        width: 60; height: 12; padding: 1 2;
        border: round #9f4050; background: #111a28;
    }
    #reset-title { height: 2; color: #f5f8ff; text-style: bold; }
    #reset-message { height: 4; color: #aebdd0; }
    #reset-dialog-actions { height: 3; align-horizontal: right; }
    #reset-dialog-actions Button { margin-left: 1; }
    """
    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, controller: str, mapping_count: int):
        super().__init__()
        self.controller = controller
        self.mapping_count = mapping_count

    def compose(self) -> ComposeResult:
        noun = "mapping" if self.mapping_count == 1 else "mappings"
        with Vertical(id="reset-dialog"):
            yield Label("Reset mappings?", id="reset-title")
            yield Static(
                f"Clear all {self.mapping_count} {noun} for {self.controller}? "
                "This takes effect when you save.",
                id="reset-message",
            )
            with Horizontal(id="reset-dialog-actions"):
                yield Button("Cancel", id="reset-cancel")
                yield Button("Reset mappings", id="reset-confirm", variant="error")

    @on(Button.Pressed, "#reset-cancel")
    def cancel_pressed(self) -> None:
        self.dismiss(False)

    @on(Button.Pressed, "#reset-confirm")
    def confirm_pressed(self) -> None:
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)


class ConfigureApp(App[dict]):
    """Interactive, non-linear mapping editor."""

    TITLE = "Lightroom MIDI Bridge"
    SUB_TITLE = "Controller setup"
    CSS = """
    Screen { background: #0b1018; color: #d9e2f1; }
    Header { background: #111a28; color: #f5f8ff; }
    #status-bar { height: 3; padding: 1 2; background: #111a28; color: #91a4bd; }
    #status-bar .ok { color: #73dba4; }
    #topbar { height: 5; padding: 0 2 1 2; background: #111a28; }
    #port-select { width: 1fr; margin-right: 1; }
    #refresh { width: 18; margin-left: 1; }
    #connect { width: 20; margin-left: 1; background: #275f9f; }
    #workspace { height: 1fr; padding: 1 2; }
    .panel { border: round #26364d; background: #0e1622; }
    .panel-title { height: 2; padding: 0 1; color: #7cb8ff; text-style: bold; }
    #mapping-panel { width: 3fr; margin-right: 1; }
    #target-panel { width: 2fr; margin-left: 1; }
    #mapping-table { height: 1fr; }
    #empty-help { height: 3; padding: 1; color: #71849e; }
    #mapping-actions, #assignment-actions { height: 3; padding: 0 1; }
    #mapping-actions Button, #assignment-actions Button { margin-right: 1; }
    #learn { width: 1fr; background: #275f9f; }
    #delete { width: 1fr; background: #713641; }
    #reset-mappings { width: 1fr; background: #713641; }
    #search { margin: 0 1 1 1; }
    #targets { height: 1fr; margin: 0 1; border: tall #1d2b3e; }
    #selection { height: 4; padding: 1 2; color: #aebdd0; }
    #settings { height: 5; padding: 0 1; }
    #mode { width: 1fr; margin-right: 1; }
    #sensitivity { width: 1fr; }
    #assign { width: 18; background: #236548; }
    #save { width: 10; background: #275f9f; }
    #dirty { width: 15; padding: 1; color: #f0bd67; }
    Button.-primary { background: #2878d4; }
    Button.-success { background: #23865c; }
    Button.-error { background: #9f4050; }
    Footer { background: #111a28; }
    """
    BINDINGS = [
        Binding("ctrl+s", "save", "Save"),
        Binding("ctrl+l", "listen", "Listen"),
        Binding("delete", "delete_mapping", "Delete"),
        Binding("q", "quit", "Quit"),
    ]

    def __init__(self, lr_port: Optional[int] = None):
        super().__init__()
        self.lr_port = lr_port
        self.config = load_config() or empty_config()
        self.choices = _mapping_choices()
        self.filtered_choices: list[dict] = []
        self.selected_choice: Optional[dict] = None
        self.pending_event: Optional[dict] = None
        self.editing_index: Optional[int] = None
        self.watcher: Optional[MidiWatcher] = None
        self.lr: Optional[LightroomClient] = None
        self.dirty = False

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static("● MIDI not selected    ● Lightroom not connected", id="status-bar")
        with Horizontal(id="topbar"):
            yield Select([], prompt="Select a MIDI input", id="port-select")
            yield Button("Refresh devices", id="refresh")
            yield Button("Connect Lightroom", id="connect", variant="primary")
        with Horizontal(id="workspace"):
            with Vertical(classes="panel", id="mapping-panel"):
                yield Label("YOUR MAPPINGS", classes="panel-title")
                yield DataTable(id="mapping-table", cursor_type="row", zebra_stripes=True)
                yield Static(
                    "No mappings yet. Select a target, then listen for a hardware control.",
                    id="empty-help",
                )
                with Horizontal(id="mapping-actions"):
                    yield Button("Listen for control", id="learn", variant="primary")
                    yield Button("Delete selected", id="delete", variant="error")
                    yield Button("Reset mappings", id="reset-mappings", variant="error")
            with Vertical(classes="panel", id="target-panel"):
                yield Label("LIGHTROOM TARGETS", classes="panel-title")
                yield Input(
                    placeholder="Search parameters, actions, or categories…",
                    id="search",
                )
                yield OptionList(id="targets")
                yield Static("Choose a target from the list.", id="selection")
                with Horizontal(id="settings"):
                    yield Select(
                        [("Absolute · fader / knob", "absolute"),
                         ("Relative · signed-bit encoder", "relative"),
                         ("Relative CW · 0 CCW / 127 CW", "relative_cw")],
                        value="absolute",
                        id="mode",
                    )
                    yield Input(
                        placeholder="Sensitivity",
                        type="number",
                        id="sensitivity",
                    )
                with Horizontal(id="assignment-actions"):
                    yield Button("Assign mapping", id="assign", variant="success")
                    yield Button("Save", id="save", variant="primary")
                    yield Static("", id="dirty")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#mapping-table", DataTable)
        table.add_columns("MIDI control", "Lightroom target", "Mode")
        self.refresh_ports()
        self._refresh_mappings()
        self._refresh_targets("")
        self._refresh_status()

    def refresh_ports(self) -> None:
        midi_in = rtmidi.MidiIn()
        ports = midi_in.get_ports()
        del midi_in
        select = self.query_one("#port-select", Select)
        select.set_options([(name, name) for name in ports])
        current = self.config.get("midi_port")
        if current in ports:
            select.value = current
        elif ports:
            self._activate_port(ports[0])
            select.value = ports[0]
        self._refresh_status()

    def _activate_port(self, port: str) -> bool:
        """Switch to a controller and its independent mapping profile."""
        if port == self.config.get("midi_port"):
            return False
        self.config["midi_port"] = port
        mappings_for(self.config, port)
        self._close_watcher()
        self.pending_event = None
        self.editing_index = None
        self.query_one("#learn", Button).label = "Listen for control"
        self.query_one("#assign", Button).label = "Assign mapping"
        self._refresh_mappings()
        self._mark_dirty()
        return True

    def _refresh_status(self, message: Optional[str] = None) -> None:
        port = self.config.get("midi_port")
        midi = f"[green]●[/green] MIDI  {port}" if port else "[#71849e]●[/] MIDI not selected"
        lightroom = (
            f"[green]●[/green] Lightroom  {self.lr.url}"
            if self.lr
            else "[#71849e]●[/] Lightroom not connected"
        )
        suffix = f"    [#f0bd67]{message}[/]" if message else ""
        self.query_one("#status-bar", Static).update(f"{midi}    {lightroom}{suffix}")

    def _refresh_mappings(self) -> None:
        table = self.query_one("#mapping-table", DataTable)
        table.clear()
        mappings = mappings_for(self.config)
        for index, mapping in enumerate(mappings):
            table.add_row(
                mapping.get("midi_desc", mapping.get("midi_key", "?")),
                mapping.get("label", mapping.get("action", "?")),
                mapping.get("mode", "Action").title(),
                key=str(index),
            )
        self.query_one("#empty-help").display = not bool(mappings)

    def _refresh_targets(self, query: str) -> None:
        self.filtered_choices = _match_choices(query, self.choices)
        options = []
        for choice in self.filtered_choices:
            kind = "Slider" if choice["action_type"] == "setValue" else "Action"
            options.append(
                Option(
                    f"{choice['label']}  [#71849e]{choice['category']} · {kind}[/]",
                    id=choice["mapping"].get("parameter", choice["mapping"].get("action")),
                )
            )
        target_list = self.query_one("#targets", OptionList)
        target_list.clear_options()
        target_list.add_options(options)
        if options:
            with target_list.prevent(OptionList.OptionHighlighted):
                target_list.highlighted = 0
            self._select_choice(0)

    def _select_choice(self, index: int) -> None:
        if not 0 <= index < len(self.filtered_choices):
            return
        self.selected_choice = self.filtered_choices[index]
        choice = self.selected_choice
        mapping = choice["mapping"]
        if choice["action_type"] == "setValue":
            detail = (
                f"[bold]{choice['label']}[/bold]\n"
                f"{choice['category']} slider · {mapping['min_val']} to {mapping['max_val']}"
            )
            self.query_one("#settings").display = True
            self.query_one("#sensitivity", Input).value = str(mapping["sensitivity"])
        else:
            detail = f"[bold]{choice['label']}[/bold]\n{choice['category'].title()} action"
            self.query_one("#settings").display = False
        self.query_one("#selection", Static).update(detail)

    def _mark_dirty(self) -> None:
        self.dirty = True
        self.query_one("#dirty", Static).update("Unsaved changes")

    @on(Select.Changed, "#port-select")
    def port_changed(self, event: Select.Changed) -> None:
        if event.value is Select.BLANK:
            return
        port = str(event.value)
        self._activate_port(port)
        self._refresh_status()

    @on(Input.Changed, "#search")
    def search_changed(self, event: Input.Changed) -> None:
        self._refresh_targets(event.value)

    @on(Input.Submitted, "#search")
    def search_submitted(self, event: Input.Submitted) -> None:
        if len(self.filtered_choices) != 1:
            return
        self._select_choice(0)
        self.query_one("#targets", OptionList).highlighted = 0
        if self.pending_event is None:
            self.action_listen()

    @on(OptionList.OptionHighlighted, "#targets")
    def target_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        self._select_choice(event.option_index)

    @on(OptionList.OptionSelected, "#targets")
    def target_selected(self, event: OptionList.OptionSelected) -> None:
        self._select_choice(event.option_index)
        if self.pending_event is None:
            self.action_listen()

    @on(DataTable.RowSelected, "#mapping-table")
    def mapping_selected(self, event: DataTable.RowSelected) -> None:
        index = int(str(event.row_key.value))
        mappings = mappings_for(self.config)
        if not 0 <= index < len(mappings):
            return
        mapping = mappings[index]
        target_field = "parameter" if mapping.get("action_type") == "setValue" else "action"
        target = mapping.get(target_field)
        choice = next(
            (
                item for item in self.choices
                if item["mapping"].get(target_field) == target
            ),
            None,
        )
        if choice is None:
            self.notify("This mapping's Lightroom target is no longer available", severity="error")
            return

        search = self.query_one("#search", Input)
        # We refresh the exact target below; suppress the queued search event so
        # it cannot subsequently overwrite the mapping's saved settings.
        with search.prevent(Input.Changed):
            search.value = choice["label"]
        self._refresh_targets(choice["label"])
        choice_index = next(
            (i for i, item in enumerate(self.filtered_choices) if item is choice),
            0,
        )
        target_list = self.query_one("#targets", OptionList)
        with target_list.prevent(OptionList.OptionHighlighted):
            target_list.highlighted = choice_index
        self._select_choice(choice_index)

        if mapping.get("action_type") == "setValue":
            mode = mapping.get("mode", "absolute")
            self.query_one("#mode", Select).value = mode
            self.query_one("#sensitivity", Input).value = str(
                mapping.get("sensitivity", choice["mapping"].get("sensitivity", 1.0))
            )

        self.pending_event = self._event_from_mapping(mapping)
        self.editing_index = index
        self.query_one("#learn", Button).label = (
            f"Control: {mapping.get('midi_desc', mapping.get('midi_key', '?'))}"
        )
        self.query_one("#assign", Button).label = "Save changes"
        self._refresh_status("Editing selected mapping")

    @staticmethod
    def _event_from_mapping(mapping: dict) -> dict:
        event_type = mapping.get("midi_type", "cc" if "cc" in mapping else "note_on")
        event = {"type": event_type, "channel": int(mapping.get("channel", 1))}
        if event_type == "cc":
            event.update(cc=int(mapping["cc"]), value=0)
        elif event_type in ("note_on", "note_off"):
            event.update(note=int(mapping["note"]), velocity=127)
        elif event_type == "pitch_bend":
            event["value"] = 0
        return event

    @on(Button.Pressed, "#refresh")
    def refresh_pressed(self) -> None:
        self.refresh_ports()
        self.notify("MIDI device list refreshed")

    @on(Button.Pressed, "#connect")
    def connect_pressed(self) -> None:
        self.connect_lightroom()

    @work(exclusive=True, group="lightroom")
    async def connect_lightroom(self) -> None:
        self.query_one("#connect", Button).disabled = True
        self._refresh_status("Connecting…")
        client = LightroomClient(self.lr_port)
        try:
            await client.connect()
            ok = await client.register(client_guid=self.config.get("client_guid"))
            if not ok:
                raise RuntimeError("Lightroom rejected registration")
        except Exception as error:
            await client.close()
            self.notify(str(error), title="Could not connect", severity="error")
            self._refresh_status("Open Lightroom and enable external controllers")
        else:
            if self.lr:
                await self.lr.close()
            self.lr = client
            self.config["client_guid"] = client.client_guid
            self._mark_dirty()
            self._refresh_status("Connected")
            self.notify("Lightroom is connected")
        finally:
            self.query_one("#connect", Button).disabled = False

    def _ensure_watcher(self) -> bool:
        port = self.config.get("midi_port")
        if not port:
            self.notify("Select a MIDI input first", severity="warning")
            return False
        if self.watcher is None:
            try:
                self.watcher = MidiWatcher(port)
                self.watcher.open()
            except Exception as error:
                self.watcher = None
                self.notify(str(error), severity="error")
                return False
        return True

    def action_listen(self) -> None:
        if not self.selected_choice:
            self.notify("Choose a Lightroom target first", severity="warning")
            return
        if self._ensure_watcher():
            self.listen_for_control()

    @on(Button.Pressed, "#learn")
    def listen_pressed(self) -> None:
        self.action_listen()

    @work(exclusive=True, group="midi-listen")
    async def listen_for_control(self) -> None:
        assert self.watcher is not None
        button = self.query_one("#learn", Button)
        button.label = "Listening… move a control"
        button.disabled = True
        self.watcher.flush_pending()
        self._refresh_status("Listening for MIDI input…")
        event = await self.watcher.next_event(timeout=30)
        if event:
            await self.watcher.flush_burst()
            self.pending_event = event
            button.label = f"Detected: {_describe_midi(event)}"
            self._refresh_status("Control detected — assign when ready")
            self.notify(_describe_midi(event), title="MIDI control detected")
        else:
            button.label = "Listen for control"
            self._refresh_status("Listening timed out")
            self.notify("No MIDI input received", severity="warning")
        button.disabled = False

    @on(Button.Pressed, "#assign")
    def assign_pressed(self) -> None:
        if not self.pending_event:
            self.notify("Listen for a hardware control first", severity="warning")
            return
        if not self.selected_choice:
            self.notify("Choose a Lightroom target", severity="warning")
            return
        mapping = dict(self.selected_choice["mapping"])
        event = self.pending_event
        if mapping["action_type"] == "setValue":
            mapping["mode"] = str(self.query_one("#mode", Select).value)
            try:
                mapping["sensitivity"] = float(
                    self.query_one("#sensitivity", Input).value
                )
            except ValueError:
                self.notify("Sensitivity must be a number", severity="error")
                return
        key = _midi_key(event)
        mapping.update(
            midi_key=key,
            midi_type=event["type"],
            midi_desc=_describe_midi(event),
            channel=event["channel"],
        )
        if event["type"] == "cc":
            mapping["cc"] = event["cc"]
        elif event["type"] == "note_on":
            mapping["note"] = event["note"]
        mappings = mappings_for(self.config)
        editing = self.editing_index
        if editing is not None and 0 <= editing < len(mappings):
            mappings[:] = [
                mapping if index == editing else item
                for index, item in enumerate(mappings)
                if index == editing or item.get("midi_key") != key
            ]
            status = "Mapping updated"
        else:
            mappings[:] = [item for item in mappings if item.get("midi_key") != key]
            mappings.append(mapping)
            status = "Mapping added"
        self.pending_event = None
        self.editing_index = None
        self.query_one("#learn", Button).label = "Listen for control"
        self.query_one("#assign", Button).label = "Assign mapping"
        self._refresh_mappings()
        self._mark_dirty()
        self._refresh_status(status)
        self.notify(f"{mapping['midi_desc']} → {mapping['label']}")

    def action_delete_mapping(self) -> None:
        table = self.query_one("#mapping-table", DataTable)
        if table.row_count == 0:
            return
        row_key, _ = table.coordinate_to_cell_key(table.cursor_coordinate)
        index = int(str(row_key.value))
        removed = mappings_for(self.config).pop(index)
        self.pending_event = None
        self.editing_index = None
        self.query_one("#learn", Button).label = "Listen for control"
        self.query_one("#assign", Button).label = "Assign mapping"
        self._refresh_mappings()
        self._mark_dirty()
        self.notify(f"Removed {removed.get('label', 'mapping')}")

    @on(Button.Pressed, "#delete")
    def delete_pressed(self) -> None:
        self.action_delete_mapping()

    @on(Button.Pressed, "#reset-mappings")
    def reset_pressed(self) -> None:
        count = len(mappings_for(self.config))
        if not count:
            self.notify("This controller has no mappings", severity="warning")
            return
        controller = str(self.config.get("midi_port") or "this controller")
        self.push_screen(
            ConfirmResetScreen(controller, count),
            self._finish_reset_mappings,
        )

    def _finish_reset_mappings(self, confirmed: bool) -> None:
        if not confirmed:
            return
        removed = clear_mappings(self.config)
        self.pending_event = None
        self.editing_index = None
        self.query_one("#learn", Button).label = "Listen for control"
        self.query_one("#assign", Button).label = "Assign mapping"
        self._refresh_mappings()
        self._mark_dirty()
        self._refresh_status("Mappings reset — save to keep this change")
        self.notify(f"Cleared {removed} mappings")

    def action_save(self) -> None:
        save_config(self.config)
        self.dirty = False
        self.query_one("#dirty", Static).update("Saved")
        self.notify(f"Saved {len(mappings_for(self.config))} mappings")

    @on(Button.Pressed, "#save")
    def save_pressed(self) -> None:
        self.action_save()

    def action_quit(self) -> None:
        if self.dirty:
            save_config(self.config)
        self.exit(self.config)

    def _close_watcher(self) -> None:
        if self.watcher:
            self.watcher.close()
            self.watcher = None

    async def on_unmount(self) -> None:
        self._close_watcher()
        if self.lr:
            await self.lr.close()


def run_configure(lr_port: Optional[int] = None) -> None:
    """Run the configuration application."""
    ConfigureApp(lr_port).run()
