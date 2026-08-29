"""Audio helpers: Opus codec, WAV parsing, resampling and a simple energy VAD."""

import io
import logging
import wave

import numpy as np

try:
    import opuslib
except Exception as exc:  # opuslib raises on missing libopus
    raise RuntimeError(
        "failed to import opuslib — make sure the libopus native library is "
        "installed (e.g. `apt install libopus0`) and `pip install opuslib`"
    ) from exc

log = logging.getLogger(__name__)


class OpusDecoderStream:
    """Decodes the Opus frames streamed by the device into 16-bit PCM."""

    def __init__(self, sample_rate: int, frame_duration_ms: int):
        self.sample_rate = sample_rate
        self.frame_size = sample_rate * frame_duration_ms // 1000
        self._decoder = opuslib.Decoder(sample_rate, 1)

    def decode(self, payload: bytes) -> bytes:
        try:
            return self._decoder.decode(payload, self.frame_size)
        except opuslib.OpusError as exc:
            log.warning("opus decode error: %s", exc)
            return b""


class OpusEncoderStream:
    """Encodes 16-bit mono PCM into Opus frames for the device."""

    def __init__(self, sample_rate: int, frame_duration_ms: int):
        self.sample_rate = sample_rate
        self.frame_duration_ms = frame_duration_ms
        self.frame_size = sample_rate * frame_duration_ms // 1000
        self._encoder = opuslib.Encoder(
            sample_rate, 1, opuslib.APPLICATION_AUDIO
        )
        self._pending = b""

    def encode(self, pcm: bytes, flush: bool = False) -> list[bytes]:
        """Returns complete Opus frames; buffers the PCM remainder."""
        self._pending += pcm
        frames = []
        frame_bytes = self.frame_size * 2
        while len(self._pending) >= frame_bytes:
            chunk = self._pending[:frame_bytes]
            self._pending = self._pending[frame_bytes:]
            frames.append(self._encoder.encode(chunk, self.frame_size))
        if flush and self._pending:
            chunk = self._pending.ljust(frame_bytes, b"\x00")
            self._pending = b""
            frames.append(self._encoder.encode(chunk, self.frame_size))
        return frames


def parse_wav(data: bytes) -> tuple[bytes, int]:
    """Returns (mono 16-bit PCM, sample_rate) from a WAV blob."""
    with wave.open(io.BytesIO(data), "rb") as w:
        rate = w.getframerate()
        channels = w.getnchannels()
        width = w.getsampwidth()
        pcm = w.readframes(w.getnframes())
    if width != 2:
        raise ValueError(f"unsupported WAV sample width: {width * 8} bit")
    if channels > 1:
        samples = np.frombuffer(pcm, dtype=np.int16).reshape(-1, channels)
        pcm = samples.mean(axis=1).astype(np.int16).tobytes()
    return pcm, rate


def make_wav(pcm: bytes, sample_rate: int) -> bytes:
    """Wraps mono 16-bit PCM into a WAV blob (for the STT upload)."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(pcm)
    return buf.getvalue()


def resample(pcm: bytes, src_rate: int, dst_rate: int) -> bytes:
    """Linear resampling of mono 16-bit PCM."""
    if src_rate == dst_rate or not pcm:
        return pcm
    samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32)
    dst_len = int(round(len(samples) * dst_rate / src_rate))
    if dst_len <= 0:
        return b""
    src_idx = np.linspace(0, len(samples) - 1, dst_len)
    resampled = np.interp(src_idx, np.arange(len(samples)), samples)
    return np.clip(resampled, -32768, 32767).astype(np.int16).tobytes()


def rms(pcm: bytes) -> float:
    if not pcm:
        return 0.0
    samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32)
    return float(np.sqrt(np.mean(samples * samples)))


class EnergyVad:
    """End-of-utterance detection over decoded PCM frames.

    Tracks speech/silence with an RMS threshold: an utterance ends after
    ``silence_ms`` of silence following at least ``min_speech_ms`` of speech.
    """

    def __init__(self, config: dict, frame_duration_ms: int):
        self.threshold = float(config["energy_threshold"])
        self.silence_ms = int(config["silence_ms"])
        self.min_speech_ms = int(config["min_speech_ms"])
        self.max_utterance_ms = int(config["max_utterance_ms"])
        self.frame_ms = frame_duration_ms
        self.reset()

    def reset(self):
        self.speech_ms = 0
        self.trailing_silence_ms = 0
        self.total_ms = 0

    def feed(self, pcm: bytes) -> bool:
        """Feed one frame; returns True when the utterance is complete."""
        self.total_ms += self.frame_ms
        if rms(pcm) >= self.threshold:
            self.speech_ms += self.frame_ms
            self.trailing_silence_ms = 0
        else:
            self.trailing_silence_ms += self.frame_ms

        if self.speech_ms >= self.min_speech_ms and self.trailing_silence_ms >= self.silence_ms:
            return True
        return self.total_ms >= self.max_utterance_ms

    @property
    def has_speech(self) -> bool:
        return self.speech_ms >= self.min_speech_ms
