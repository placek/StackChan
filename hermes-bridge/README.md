# Hermes Bridge

A small self-hosted server that lets a StackChan run **fully independent of the
default cloud services**, with a [Hermes agent](https://hermes-agent.nousresearch.com/)
(or any OpenAI-compatible chat completions server) as its brain.

It pairs with the firmware's **HERMES** app (`firmware/main/apps/app_hermes_agent`):
the app points the on-device agent runtime at this bridge instead of the default
cloud, and the bridge speaks the same open device protocol — an OTA-style config
endpoint plus a websocket carrying Opus audio and JSON control messages.

```
StackChan device                      Hermes bridge (this server)          Local services
┌───────────────────┐   Opus/JSON    ┌──────────────────────────┐
│ wake word, VAD,   │◄──websocket───►│ VAD (end of utterance)   │──► STT   (OpenAI-compatible
│ mic, speaker,     │                │ conversation loop        │           /v1/audio/transcriptions)
│ expressions,      │   HTTP (OTA    │ device MCP tool bridging │──► Agent (Hermes agent
│ servos via MCP    │   config)      │ sentence-by-sentence TTS │           /v1/chat/completions)
└───────────────────┘                └──────────────────────────┘──► TTS   (OpenAI-compatible
                                                                           /v1/audio/speech)
```

Everything runs on your own hardware: speech-to-text, the agent, and
text-to-speech are all local HTTP services you point the bridge at.

## Features

- **OTA config endpoint** (`/xiaozhi/ota/`): answers the firmware's startup
  check with the bridge's websocket endpoint — no activation, no firmware
  upgrade, no cloud account.
- **Device websocket protocol** (`/xiaozhi/v1/`): Opus audio in both
  directions, `listen` / `stt` / `tts` / `llm` / `abort` / `mcp` control
  messages, server-side end-of-utterance detection for hands-free mode.
- **Hermes agent as the brain**: talks to the agent's OpenAI-compatible
  [API server](https://hermes-agent.nousresearch.com/docs/user-guide/features/api-server/)
  with streaming responses; sentences are synthesized and streamed to the
  robot as they are generated.
- **Robot control by the agent**: the device's MCP tools (servos,
  expressions, volume, ...) are forwarded to the agent as tool calls, so the
  agent can nod, look around, or change the expression while answering.
- **Expressions**: emoji in the agent's replies are mapped to the robot's
  facial expressions (and stripped from the spoken audio).
- **Pluggable STT/TTS**: any OpenAI-compatible `/v1/audio/transcriptions`
  and `/v1/audio/speech` server works — e.g.
  [speaches](https://github.com/speaches-ai/speaches),
  [faster-whisper-server](https://github.com/fedirz/faster-whisper-server),
  [kokoro-fastapi](https://github.com/remsky/Kokoro-FastAPI),
  [openedai-speech](https://github.com/matatonic/openedai-speech),
  [LocalAI](https://github.com/mudler/LocalAI).

## Requirements

- Python 3.11+
- The Opus native library: `sudo apt install libopus0` (Debian/Ubuntu) or
  `brew install opus` (macOS)
- A running Hermes agent with its API server enabled, plus local STT and TTS
  services (see above)

## Setup

```bash
cd hermes-bridge
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt

cp config.example.yaml config.yaml
# edit config.yaml: point hermes/stt/tts at your services

python -m hermes_bridge -c config.yaml
```

The bridge listens on port `8100` by default. Check it responds:

```bash
curl http://<bridge-host>:8100/
```

## Pointing the StackChan at the bridge

On the robot:

1. `SETUP` → `AI.Agent` → `Local Server`
2. Enter the bridge's OTA URL, e.g. `http://192.168.1.10:8100/xiaozhi/ota/`,
   and confirm with the keyboard's checkmark.
3. Leave setup and open the **HERMES** app from the launcher.

The device fetches its websocket endpoint from the bridge and the
conversation loop runs entirely on your network. Opening the regular
**AI.AGENT** app switches back to the default cloud at any time; the choice
is remembered across reboots (including "start AI agent on boot").

If the device and bridge are on different networks or behind a proxy, set
`server.advertise_url` in `config.yaml` to the websocket URL the device
should use (e.g. `ws://192.168.1.10:8100/xiaozhi/v1/`); otherwise it is
derived from the OTA request's `Host` header.

## Configuration notes

- `server.token`: optional shared secret. It is delivered to the device in
  the OTA response and checked on the websocket handshake — nothing to type
  on the device.
- `vad.*`: end-of-utterance tuning for hands-free (auto) mode. Raise
  `energy_threshold` in noisy rooms; lower it if the robot cuts you off.
- `audio.pacing`: how fast audio frames are streamed to the device relative
  to real time (`1.0` = real time). Lower values buffer more on the device.
- `hermes.bridge_device_tools`: set to `false` if your chat endpoint does
  not support tool calling.

## Protocol reference

The device protocol implemented here is documented in the
[xiaozhi-esp32 websocket protocol notes](https://github.com/78/xiaozhi-esp32/blob/main/docs/websocket.md)
(the StackChan firmware embeds that runtime; this bridge is a self-hosted
replacement for its default cloud backend).
