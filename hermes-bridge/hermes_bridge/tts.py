"""Client for an OpenAI-compatible /v1/audio/speech server."""

import logging

import aiohttp

from .audio import parse_wav, resample

log = logging.getLogger(__name__)


class TtsClient:
    def __init__(self, config: dict, session: aiohttp.ClientSession):
        self.base_url = config["base_url"].rstrip("/")
        self.api_key = config["api_key"]
        self.model = config["model"]
        self.voice = config["voice"]
        self.speed = float(config.get("speed", 1.0))
        self.response_format = config.get("response_format", "wav")
        self.pcm_sample_rate = int(config.get("pcm_sample_rate", 24000))
        if self.response_format not in ("wav", "pcm"):
            raise ValueError("tts.response_format must be 'wav' or 'pcm'")
        self._session = session

    async def synthesize(self, text: str, target_rate: int) -> bytes:
        """Returns mono 16-bit PCM at ``target_rate``."""
        payload = {
            "model": self.model,
            "voice": self.voice,
            "input": text,
            "response_format": self.response_format,
            "speed": self.speed,
        }
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        url = f"{self.base_url}/audio/speech"
        async with self._session.post(
            url, json=payload, headers=headers,
            timeout=aiohttp.ClientTimeout(total=120),
        ) as resp:
            if resp.status != 200:
                body = (await resp.text())[:500]
                raise RuntimeError(f"speech synthesis failed: {resp.status} {body}")
            data = await resp.read()

        if self.response_format == "wav":
            pcm, rate = parse_wav(data)
        else:
            pcm, rate = data, self.pcm_sample_rate
        return resample(pcm, rate, target_rate)
