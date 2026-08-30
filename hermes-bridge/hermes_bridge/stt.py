"""Client for an OpenAI-compatible /v1/audio/transcriptions server."""

import logging

import aiohttp

from .audio import make_wav

log = logging.getLogger(__name__)


class SttClient:
    def __init__(self, config: dict, session: aiohttp.ClientSession):
        self.base_url = config["base_url"].rstrip("/")
        self.api_key = config["api_key"]
        self.model = config["model"]
        self.language = config.get("language") or ""
        self._session = session

    async def transcribe(self, pcm: bytes, sample_rate: int) -> str:
        form = aiohttp.FormData()
        form.add_field(
            "file", make_wav(pcm, sample_rate),
            filename="utterance.wav", content_type="audio/wav",
        )
        form.add_field("model", self.model)
        form.add_field("response_format", "json")
        if self.language:
            form.add_field("language", self.language)

        headers = {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        url = f"{self.base_url}/audio/transcriptions"
        async with self._session.post(
            url, data=form, headers=headers,
            timeout=aiohttp.ClientTimeout(total=120),
        ) as resp:
            if resp.status != 200:
                body = (await resp.text())[:500]
                raise RuntimeError(f"transcription failed: {resp.status} {body}")
            result = await resp.json(content_type=None)
        return (result.get("text") or "").strip()
