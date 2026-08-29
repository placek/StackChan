import copy
import logging
import os

import yaml

log = logging.getLogger(__name__)

DEFAULTS = {
    "server": {
        "host": "0.0.0.0",
        "port": 8100,
        "advertise_url": "",
        "token": "",
    },
    "hermes": {
        "base_url": "http://127.0.0.1:3000/v1",
        "api_key": "",
        "model": "hermes",
        "system_prompt": (
            "You are StackChan, a small cheerful desktop robot. Answer briefly "
            "and conversationally; your replies are spoken out loud."
        ),
        "history_turns": 20,
        "bridge_device_tools": True,
    },
    "stt": {
        "base_url": "http://127.0.0.1:8000/v1",
        "api_key": "",
        "model": "Systran/faster-whisper-small",
        "language": "",
    },
    "tts": {
        "base_url": "http://127.0.0.1:8880/v1",
        "api_key": "",
        "model": "kokoro",
        "voice": "af_heart",
        "speed": 1.0,
        "response_format": "wav",
        "pcm_sample_rate": 24000,
    },
    "audio": {
        "output_sample_rate": 24000,
        "pacing": 0.6,
    },
    "vad": {
        "energy_threshold": 500,
        "silence_ms": 900,
        "min_speech_ms": 250,
        "max_utterance_ms": 30000,
    },
}


def _merge(base: dict, override: dict) -> dict:
    result = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge(result[key], value)
        else:
            result[key] = value
    return result


def load_config(path: str | None) -> dict:
    """Load YAML config merged over defaults.

    ``HERMES_BRIDGE_CONFIG`` overrides the path argument when set.
    """
    path = os.environ.get("HERMES_BRIDGE_CONFIG", path)
    override = {}
    if path and os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            override = yaml.safe_load(f) or {}
        log.info("loaded config from %s", path)
    elif path:
        raise FileNotFoundError(f"config file not found: {path}")
    else:
        log.warning("no config file given, using built-in defaults")
    return _merge(DEFAULTS, override)
