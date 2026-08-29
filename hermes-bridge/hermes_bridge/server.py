"""aiohttp application implementing the StackChan device protocol.

Endpoints:
  POST /xiaozhi/ota/   config endpoint the firmware polls at startup; replies
                       with the websocket endpoint of this bridge (no cloud,
                       no activation, no firmware upgrade).
  GET  /xiaozhi/v1/    websocket carrying Opus audio and JSON control messages.
"""

import asyncio
import json
import logging
import re
import time
import uuid

from aiohttp import WSMsgType, web

from .audio import EnergyVad, OpusDecoderStream, OpusEncoderStream
from .llm import ChatClient
from .mcp import DeviceMcp
from .stt import SttClient
from .tts import TtsClient

log = logging.getLogger(__name__)

MAX_TOOL_ROUNDS = 4
SENTENCE_END_RE = re.compile(r"[.!?;:。！？；：\n]")
MIN_SENTENCE_CHARS = 4

# Map common emoji to the expression names understood by the device
EMOJI_EMOTIONS = {
    "🙂": "happy", "😊": "happy", "😀": "happy", "😄": "laughing",
    "😂": "laughing", "🤣": "laughing", "😆": "laughing", "😉": "winking",
    "😍": "loving", "🥰": "loving", "❤": "loving", "😘": "kissy",
    "😎": "cool", "😌": "relaxed", "😴": "sleepy", "🤤": "delicious",
    "😋": "delicious", "🤔": "thinking", "😕": "confused", "😳": "embarrassed",
    "😲": "surprised", "😱": "shocked", "😢": "sad", "😭": "crying",
    "😠": "angry", "😡": "angry", "🤪": "silly", "😜": "funny",
}
EMOJI_RE = re.compile(
    "[\U0001F000-\U0001FAFF☀-➿️‍]+"
)


def _find_emotion(text: str) -> str | None:
    for ch in text:
        if ch in EMOJI_EMOTIONS:
            return EMOJI_EMOTIONS[ch]
    return None


class DeviceSession:
    """One websocket connection from a StackChan device."""

    # Conversation history survives reconnects, keyed by device id
    histories: dict[str, list] = {}

    def __init__(self, app: web.Application, ws: web.WebSocketResponse, device_id: str):
        self.config = app["config"]
        self.chat: ChatClient = app["chat"]
        self.stt: SttClient = app["stt"]
        self.tts: TtsClient = app["tts"]
        self.ws = ws
        self.device_id = device_id
        self.session_id = uuid.uuid4().hex[:8]

        self.output_rate = int(self.config["audio"]["output_sample_rate"])
        self.pacing = float(self.config["audio"]["pacing"])

        self.decoder: OpusDecoderStream | None = None
        self.vad: EnergyVad | None = None
        self.listening = False
        self.listen_mode = "auto"
        self.capture = bytearray()
        self.input_rate = 16000
        self.frame_duration_ms = 60

        self.mcp: DeviceMcp | None = None
        self.response_task: asyncio.Task | None = None

    # ------------------------------------------------------------------ util

    async def send_json(self, message: dict):
        message.setdefault("session_id", self.session_id)
        await self.ws.send_str(json.dumps(message, ensure_ascii=False))

    @property
    def history(self) -> list:
        return self.histories.setdefault(self.device_id, [])

    def _trim_history(self):
        max_msgs = int(self.config["hermes"]["history_turns"]) * 2
        history = self.history
        # Never cut between a tool call and its tool results
        while len(history) > max_msgs and history:
            history.pop(0)
            while history and history[0].get("role") == "tool":
                history.pop(0)

    # ------------------------------------------------------------- lifecycle

    async def run(self):
        async for msg in self.ws:
            if msg.type == WSMsgType.BINARY:
                self._on_audio(msg.data)
            elif msg.type == WSMsgType.TEXT:
                try:
                    payload = json.loads(msg.data)
                except json.JSONDecodeError:
                    log.warning("bad json from device: %.200s", msg.data)
                    continue
                await self._on_message(payload)
            elif msg.type == WSMsgType.ERROR:
                break
        await self._abort_response()

    async def _on_message(self, message: dict):
        msg_type = message.get("type")

        if msg_type == "hello":
            await self._on_hello(message)
        elif msg_type == "listen":
            await self._on_listen(message)
        elif msg_type == "abort":
            log.info("[%s] abort (%s)", self.device_id, message.get("reason"))
            await self._abort_response()
        elif msg_type == "mcp":
            if self.mcp is not None:
                self.mcp.handle_message(message.get("payload") or {})
        elif msg_type == "goodbye":
            await self.ws.close()
        else:
            log.debug("[%s] unhandled message: %s", self.device_id, msg_type)

    async def _on_hello(self, message: dict):
        params = message.get("audio_params") or {}
        self.input_rate = int(params.get("sample_rate", 16000))
        self.frame_duration_ms = int(params.get("frame_duration", 60))
        self.decoder = OpusDecoderStream(self.input_rate, self.frame_duration_ms)
        self.vad = EnergyVad(self.config["vad"], self.frame_duration_ms)

        await self.send_json({
            "type": "hello",
            "transport": "websocket",
            "audio_params": {
                "format": "opus",
                "sample_rate": self.output_rate,
                "channels": 1,
                "frame_duration": self.frame_duration_ms,
            },
        })
        log.info(
            "[%s] hello: in %d Hz / %d ms, out %d Hz",
            self.device_id, self.input_rate, self.frame_duration_ms, self.output_rate,
        )

        features = message.get("features") or {}
        if features.get("mcp") and self.config["hermes"]["bridge_device_tools"]:
            self.mcp = DeviceMcp(self._send_mcp_payload)
            asyncio.create_task(self.mcp.initialize())

    async def _send_mcp_payload(self, payload: dict):
        await self.send_json({"type": "mcp", "payload": payload})

    async def _on_listen(self, message: dict):
        state = message.get("state")
        if state == "start":
            self.listen_mode = message.get("mode", "auto")
            self.listening = True
            self.capture.clear()
            if self.vad:
                self.vad.reset()
        elif state == "stop":
            if self.listening:
                self.listening = False
                self._finalize_utterance()
        elif state == "detect":
            # Wake word detected on the device; audio for the new utterance
            # will follow with a fresh listen start
            self.capture.clear()
            if self.vad:
                self.vad.reset()

    def _on_audio(self, payload: bytes):
        if not self.listening or self.decoder is None:
            return
        pcm = self.decoder.decode(payload)
        if not pcm:
            return
        self.capture.extend(pcm)
        # In auto/realtime mode the server decides when the utterance ends
        if self.listen_mode != "manual" and self.vad and self.vad.feed(pcm):
            self.listening = False
            self._finalize_utterance()

    def _finalize_utterance(self):
        pcm = bytes(self.capture)
        self.capture.clear()
        had_speech = self.vad.has_speech if self.vad else True
        if self.vad:
            self.vad.reset()
        if not pcm or (self.listen_mode != "manual" and not had_speech):
            return
        if self.response_task and not self.response_task.done():
            log.info("[%s] response already in flight, dropping utterance", self.device_id)
            return
        self.response_task = asyncio.create_task(self._respond(pcm))

    async def _abort_response(self):
        self.listening = False
        if self.response_task and not self.response_task.done():
            self.response_task.cancel()
            try:
                await self.response_task
            except (asyncio.CancelledError, Exception):
                pass
        self.response_task = None

    # -------------------------------------------------------------- pipeline

    async def _respond(self, pcm: bytes):
        try:
            transcript = await self.stt.transcribe(pcm, self.input_rate)
        except Exception as exc:
            log.error("[%s] STT failed: %s", self.device_id, exc)
            await self._speak_error("Sorry, I could not hear you.")
            return
        if not transcript:
            log.info("[%s] empty transcription, ignoring", self.device_id)
            await self.send_json({"type": "tts", "state": "start"})
            await self.send_json({"type": "tts", "state": "stop"})
            return

        log.info("[%s] user: %s", self.device_id, transcript)
        await self.send_json({"type": "stt", "text": transcript})

        system = {"role": "system", "content": self.config["hermes"]["system_prompt"]}
        self.history.append({"role": "user", "content": transcript})
        self._trim_history()

        tools = self.mcp.openai_tools if self.mcp else None
        tts_started = False
        assistant_text = []

        try:
            for _ in range(MAX_TOOL_ROUNDS):
                pending_tool_calls = None
                sentence_buf = ""

                async for delta in self.chat.stream_chat([system] + self.history, tools):
                    if delta.tool_calls:
                        pending_tool_calls = delta.tool_calls
                        continue
                    assistant_text.append(delta.text)
                    sentence_buf += delta.text
                    flush_at = self._sentence_break(sentence_buf)
                    while flush_at is not None:
                        sentence, sentence_buf = (
                            sentence_buf[:flush_at], sentence_buf[flush_at:]
                        )
                        tts_started = await self._speak(sentence, tts_started)
                        flush_at = self._sentence_break(sentence_buf)

                if sentence_buf.strip():
                    tts_started = await self._speak(sentence_buf, tts_started)

                if not pending_tool_calls:
                    break

                # Execute the requested device tools, then let the agent continue
                self.history.append({
                    "role": "assistant",
                    "content": "".join(assistant_text) or None,
                    "tool_calls": pending_tool_calls,
                })
                assistant_text = []
                for call in pending_tool_calls:
                    fn = call["function"]
                    log.info("[%s] tool call: %s(%s)", self.device_id,
                             fn["name"], fn["arguments"][:200])
                    result = await self.mcp.call(fn["name"], fn["arguments"])
                    self.history.append({
                        "role": "tool",
                        "tool_call_id": call["id"],
                        "content": result,
                    })
            else:
                log.warning("[%s] too many tool rounds, stopping", self.device_id)

            full_text = "".join(assistant_text).strip()
            if full_text:
                self.history.append({"role": "assistant", "content": full_text})
                log.info("[%s] assistant: %s", self.device_id, full_text)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.error("[%s] agent request failed: %s", self.device_id, exc)
            if not tts_started:
                await self._speak_error("Sorry, I could not reach my brain.")
                return
        finally:
            if tts_started:
                await self.send_json({"type": "tts", "state": "stop"})

        if not tts_started:
            # Nothing spoken (e.g. tool-only response): still cycle the device
            # state machine so it goes back to listening
            await self.send_json({"type": "tts", "state": "start"})
            await self.send_json({"type": "tts", "state": "stop"})

    @staticmethod
    def _sentence_break(buf: str) -> int | None:
        match = SENTENCE_END_RE.search(buf, MIN_SENTENCE_CHARS)
        if match is None:
            return None
        return match.end()

    async def _speak_error(self, text: str):
        try:
            started = await self._speak(text, False)
        except Exception:
            started = False
        if started:
            await self.send_json({"type": "tts", "state": "stop"})

    async def _speak(self, sentence: str, tts_started: bool) -> bool:
        """Synthesizes one sentence and streams it to the device."""
        emotion = _find_emotion(sentence)
        spoken = EMOJI_RE.sub("", sentence).strip()

        if not tts_started:
            await self.send_json({"type": "tts", "state": "start"})
            tts_started = True
        if emotion:
            await self.send_json({"type": "llm", "emotion": emotion, "text": sentence})
        if not spoken:
            return tts_started

        await self.send_json(
            {"type": "tts", "state": "sentence_start", "text": spoken}
        )
        try:
            pcm = await self.tts.synthesize(spoken, self.output_rate)
        except Exception as exc:
            log.error("[%s] TTS failed: %s", self.device_id, exc)
            return tts_started

        encoder = OpusEncoderStream(self.output_rate, self.frame_duration_ms)
        frame_delay = self.frame_duration_ms / 1000.0 * self.pacing
        for frame in encoder.encode(pcm, flush=True):
            await self.ws.send_bytes(frame)
            await asyncio.sleep(frame_delay)
        return tts_started


# ------------------------------------------------------------------ handlers


async def handle_ota(request: web.Request) -> web.Response:
    config = request.app["config"]

    device_version = "0.0.0"
    try:
        body = await request.json()
        device_version = (body.get("application") or {}).get("version") or device_version
    except Exception:
        pass

    advertise_url = config["server"]["advertise_url"]
    if not advertise_url:
        advertise_url = f"ws://{request.host}/xiaozhi/v1/"

    websocket_config = {"url": advertise_url, "version": 1}
    if config["server"]["token"]:
        websocket_config["token"] = config["server"]["token"]

    device_id = request.headers.get("Device-Id", "?")
    log.info("[%s] OTA check (firmware %s)", device_id, device_version)

    return web.json_response({
        # Echo the device's own version: no upgrade, no activation, no cloud
        "firmware": {"version": device_version, "url": ""},
        "websocket": websocket_config,
        "server_time": {
            "timestamp": int(time.time() * 1000),
            "timezone_offset": -time.timezone // 60,
        },
    })


async def handle_websocket(request: web.Request) -> web.WebSocketResponse:
    config = request.app["config"]
    token = config["server"]["token"]
    if token:
        auth = request.headers.get("Authorization", "")
        if auth.removeprefix("Bearer ").strip() != token:
            raise web.HTTPUnauthorized()

    ws = web.WebSocketResponse(heartbeat=30)
    await ws.prepare(request)

    device_id = request.headers.get("Device-Id", request.remote or "unknown")
    log.info("[%s] device connected", device_id)
    session = DeviceSession(request.app, ws, device_id)
    try:
        await session.run()
    finally:
        await session._abort_response()
        log.info("[%s] device disconnected", device_id)
    return ws


async def handle_health(request: web.Request) -> web.Response:
    return web.json_response({"status": "ok", "service": "hermes-bridge"})


def create_app(config: dict) -> web.Application:
    import aiohttp

    app = web.Application()
    app["config"] = config

    async def on_startup(app):
        session = aiohttp.ClientSession()
        app["client_session"] = session
        app["chat"] = ChatClient(config["hermes"], session)
        app["stt"] = SttClient(config["stt"], session)
        app["tts"] = TtsClient(config["tts"], session)

    async def on_cleanup(app):
        await app["client_session"].close()

    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)

    app.router.add_route("*", "/xiaozhi/ota/", handle_ota)
    app.router.add_get("/xiaozhi/v1/", handle_websocket)
    app.router.add_get("/", handle_health)
    return app
