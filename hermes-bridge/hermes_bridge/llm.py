"""Streaming client for the Hermes agent's OpenAI-compatible chat API."""

import json
import logging

import aiohttp

log = logging.getLogger(__name__)


class ChatDelta:
    """One streamed event: a piece of text, or the completed tool calls."""

    def __init__(self, text: str = "", tool_calls: list | None = None):
        self.text = text
        self.tool_calls = tool_calls


class ChatClient:
    def __init__(self, config: dict, session: aiohttp.ClientSession):
        self.base_url = config["base_url"].rstrip("/")
        self.api_key = config["api_key"]
        self.model = config["model"]
        self._session = session

    def _headers(self) -> dict:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    async def stream_chat(self, messages: list, tools: list | None = None):
        """Yields ChatDelta objects for a streamed chat completion.

        Text deltas are yielded as they arrive; if the model answers with tool
        calls, a final ChatDelta carrying the assembled tool_calls is yielded.
        """
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": True,
        }
        if tools:
            payload["tools"] = tools

        url = f"{self.base_url}/chat/completions"
        tool_calls: dict[int, dict] = {}

        async with self._session.post(
            url, json=payload, headers=self._headers(),
            timeout=aiohttp.ClientTimeout(total=300, sock_read=120),
        ) as resp:
            if resp.status != 200:
                body = (await resp.text())[:500]
                raise RuntimeError(f"chat completions failed: {resp.status} {body}")

            async for raw_line in resp.content:
                line = raw_line.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    event = json.loads(data)
                except json.JSONDecodeError:
                    log.warning("bad SSE chunk: %.200s", data)
                    continue

                choices = event.get("choices") or []
                if not choices:
                    continue
                delta = choices[0].get("delta") or {}

                content = delta.get("content")
                if content:
                    yield ChatDelta(text=content)

                for tc in delta.get("tool_calls") or []:
                    idx = tc.get("index", 0)
                    slot = tool_calls.setdefault(
                        idx,
                        {"id": "", "type": "function",
                         "function": {"name": "", "arguments": ""}},
                    )
                    if tc.get("id"):
                        slot["id"] = tc["id"]
                    fn = tc.get("function") or {}
                    if fn.get("name"):
                        slot["function"]["name"] += fn["name"]
                    if fn.get("arguments"):
                        slot["function"]["arguments"] += fn["arguments"]

        if tool_calls:
            ordered = [tool_calls[i] for i in sorted(tool_calls)]
            yield ChatDelta(tool_calls=ordered)
