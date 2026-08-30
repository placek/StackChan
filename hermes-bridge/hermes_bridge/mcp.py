"""Bridges the device's MCP tools (servos, expressions, volume, ...) so the
Hermes agent can call them as chat completion tools.

The device embeds an MCP server reachable through ``{"type": "mcp"}`` websocket
messages whose payload is JSON-RPC 2.0.
"""

import asyncio
import json
import logging
import re

log = logging.getLogger(__name__)

CALL_TIMEOUT_S = 15


def _sanitize(name: str) -> str:
    # OpenAI-style tool names are limited to [a-zA-Z0-9_-]
    return re.sub(r"[^a-zA-Z0-9_-]", "_", name)


class DeviceMcp:
    """One instance per device websocket session."""

    def __init__(self, send_payload):
        """``send_payload`` is an async callable sending a JSON-RPC payload
        to the device wrapped in an mcp websocket message."""
        self._send_payload = send_payload
        self._next_id = 1
        self._pending: dict[int, asyncio.Future] = {}
        self._tools: list[dict] = []
        self._name_map: dict[str, str] = {}

    @property
    def openai_tools(self) -> list[dict]:
        return self._tools

    async def _request(self, method: str, params: dict | None = None):
        rpc_id = self._next_id
        self._next_id += 1
        future = asyncio.get_running_loop().create_future()
        self._pending[rpc_id] = future
        payload = {"jsonrpc": "2.0", "method": method, "id": rpc_id}
        if params is not None:
            payload["params"] = params
        await self._send_payload(payload)
        try:
            return await asyncio.wait_for(future, CALL_TIMEOUT_S)
        finally:
            self._pending.pop(rpc_id, None)

    def handle_message(self, payload: dict):
        """Feed JSON-RPC responses arriving from the device."""
        rpc_id = payload.get("id")
        future = self._pending.get(rpc_id)
        if future is None or future.done():
            return
        if "error" in payload:
            future.set_exception(RuntimeError(str(payload["error"])))
        else:
            future.set_result(payload.get("result"))

    async def initialize(self):
        """MCP handshake + tool discovery. Safe to call once after hello."""
        try:
            await self._request("initialize", {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "hermes-bridge", "version": "0.1.0"},
            })
        except Exception as exc:
            log.warning("device MCP initialize failed: %s", exc)
            return

        cursor = None
        tools = []
        for _ in range(16):  # paginated
            params = {"cursor": cursor} if cursor else {}
            try:
                result = await self._request("tools/list", params)
            except Exception as exc:
                log.warning("device MCP tools/list failed: %s", exc)
                break
            tools.extend(result.get("tools") or [])
            cursor = result.get("nextCursor")
            if not cursor:
                break

        self._tools = []
        self._name_map = {}
        for tool in tools:
            name = tool.get("name") or ""
            safe = _sanitize(name)
            self._name_map[safe] = name
            self._tools.append({
                "type": "function",
                "function": {
                    "name": safe,
                    "description": tool.get("description") or "",
                    "parameters": tool.get("inputSchema")
                    or {"type": "object", "properties": {}},
                },
            })
        log.info("device exposes %d MCP tools", len(self._tools))

    async def call(self, safe_name: str, arguments_json: str) -> str:
        """Executes one tool call on the device, returns a text result."""
        name = self._name_map.get(safe_name)
        if name is None:
            return f"Error: unknown tool {safe_name}"
        try:
            arguments = json.loads(arguments_json) if arguments_json.strip() else {}
        except json.JSONDecodeError as exc:
            return f"Error: bad tool arguments: {exc}"
        try:
            result = await self._request(
                "tools/call", {"name": name, "arguments": arguments}
            )
        except Exception as exc:
            return f"Error: {exc}"

        parts = []
        for item in (result or {}).get("content") or []:
            if item.get("type") == "text":
                parts.append(item.get("text") or "")
        text = "\n".join(parts) or json.dumps(result)
        if (result or {}).get("isError"):
            text = f"Error: {text}"
        return text
