"""
Lightroom CC WebSocket client.

Protocol reference: LrAppControllerDocumentation.lua (embedded in the app bundle).

Every request:
    {"requestId": "<uuid>", "message": "<method>", "params": [...]}

Every response:
    {"requestId": "<uuid>", "success": <bool>, "response": <value>}

Streaming responses (e.g. addAdjustmentChangeObserver) reuse the same
requestId each time the value changes; they never resolve the pending future —
they call the registered callback instead.
"""
from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path
from typing import Any, Callable, Optional

import websockets
from websockets.exceptions import ConnectionClosed

# Path where Lightroom writes the active port once "Enable external controllers"
# is switched on in Preferences → Interface.
CONNECTIONS_FILE = (
    Path.home()
    / "Library/Application Support/Adobe/Lightroom CC/Connections/connections.json"
)
DEFAULT_PORT = 7682


def get_lr_port() -> int:
    """Read the port from connections.json, fall back to 7682."""
    try:
        data = json.loads(CONNECTIONS_FILE.read_text())
        return int(data.get("port", DEFAULT_PORT))
    except Exception:
        return DEFAULT_PORT


class LightroomClient:
    """Async client for the Lightroom external controller WebSocket API."""

    def __init__(self, port: Optional[int] = None):
        self.port = port or get_lr_port()
        self.url  = f"ws://127.0.0.1:{self.port}"
        self._ws:          Any                        = None
        self._recv_task:   Optional[asyncio.Task]     = None
        self._pending:     dict[str, asyncio.Future]  = {}
        self._observers:   dict[str, Callable]        = {}
        self.client_guid:  Optional[str]              = None

    # ------------------------------------------------------------------ connect

    async def connect(self):
        self._ws = await websockets.connect(
            self.url,
            ping_interval=20,
            ping_timeout=10,
            open_timeout=5,
        )
        loop = asyncio.get_running_loop()
        self._recv_task = loop.create_task(self._recv_loop())

    async def _recv_loop(self):
        try:
            async for raw in self._ws:
                try:
                    self._dispatch(json.loads(raw))
                except Exception:
                    pass
        except ConnectionClosed:
            for fut in list(self._pending.values()):
                if not fut.done():
                    fut.cancel()

    def _dispatch(self, data: dict):
        req_id = data.get("requestId")
        if not req_id:
            return
        # Observers get every message for their requestId (streaming).
        if req_id in self._observers:
            try:
                self._observers[req_id](data)
            except Exception:
                pass
        # Pending futures resolve once on the first response.
        if req_id in self._pending:
            fut = self._pending.pop(req_id)
            if not fut.done():
                fut.set_result(data)

    async def close(self):
        if self._recv_task:
            self._recv_task.cancel()
            try:
                await self._recv_task
            except (asyncio.CancelledError, Exception):
                pass
        if self._ws:
            await self._ws.close()

    # ----------------------------------------------------------------- protocol

    async def _request(
        self,
        message: str,
        params: list,
        object_handle: Optional[str] = None,
        timeout: float = 10.0,
    ) -> dict:
        req_id  = str(uuid.uuid4())
        payload: dict = {"requestId": req_id, "message": message, "params": params}
        if object_handle is not None:
            payload["object"] = object_handle
        loop = asyncio.get_running_loop()
        fut: asyncio.Future = loop.create_future()
        self._pending[req_id] = fut
        await self._ws.send(json.dumps(payload))
        return await asyncio.wait_for(asyncio.shield(fut), timeout=timeout)

    async def register(
        self,
        app_name:    str           = "LR MIDI Bridge",
        version:     str           = "1.0",
        client_guid: Optional[str] = None,
    ) -> bool:
        """
        Pair with Lightroom.  On first call Lightroom shows a confirmation
        dialog.  Pass the returned client_guid on subsequent calls to skip it.
        """
        params = [app_name, version]
        if client_guid:
            params.append(client_guid)
        # Allow plenty of time for the user to accept the pairing dialog.
        resp = await self._request("register", params, timeout=60.0)
        if resp.get("success"):
            response = resp.get("response")
            if isinstance(response, dict):
                self.client_guid = response.get("clientGUID")
            elif isinstance(response, list) and response:
                self.client_guid = str(response[0])
            elif isinstance(response, str):
                self.client_guid = response
            return True
        return False

    async def call(self, method: str, *args) -> Any:
        """Call any LrAppController method and return its response value."""
        resp = await self._request(method, list(args))
        if not resp.get("success"):
            raise RuntimeError(f"Lightroom: {method}({list(args)}) failed — {resp}")
        return resp.get("response")

    async def subscribe(self, param: str, callback: Callable) -> str:
        """
        Subscribe to develop-adjustment changes.
        Pass an empty string to observe *all* parameters.
        Returns the requestId; pass to unsubscribe() when done.
        """
        req_id  = str(uuid.uuid4())
        params  = [param] if param else []
        payload = {
            "requestId": req_id,
            "message":   "addAdjustmentChangeObserver",
            "params":    params,
        }
        self._observers[req_id] = callback
        await self._ws.send(json.dumps(payload))
        return req_id

    def unsubscribe(self, req_id: str):
        self._observers.pop(req_id, None)
