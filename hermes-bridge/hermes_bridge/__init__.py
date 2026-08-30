"""Bridge between StackChan devices and a self-hosted Hermes agent.

Implements the open device protocol used by the StackChan firmware (an
OTA-style config endpoint plus a websocket carrying Opus audio and JSON
control messages) and runs the conversation loop against local services:
an OpenAI-compatible STT server, a Hermes agent chat completions endpoint,
and an OpenAI-compatible TTS server.
"""

__version__ = "0.1.0"
