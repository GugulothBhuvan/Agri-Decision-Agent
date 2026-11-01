"""Sarvam speech: Saaras v3 speech to text, Bulbul v3 text to speech.

Browser audio (webm/opus) is converted to 16 kHz mono WAV in memory with ffmpeg,
transcribed, and discarded; only the transcript is stored.
"""
from __future__ import annotations

import asyncio
import logging

import httpx

from ..config import settings
from ..sources import switch

log = logging.getLogger("fosol.speech")
STT_URL = "https://api.sarvam.ai/speech-to-text"
TTS_URL = "https://api.sarvam.ai/text-to-speech"
LANG_CODES = {"bn": "bn-IN", "en": "en-IN", "hi": "hi-IN"}


class SpeechError(Exception):
    pass


def available() -> bool:
    return bool(settings().sarvam_api_key) and switch.is_enabled("sarvam")


async def to_wav16k(audio: bytes) -> bytes:
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-i", "pipe:0",
        "-ac", "1", "-ar", "16000", "-f", "wav", "pipe:1",
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    out, err = await asyncio.wait_for(proc.communicate(audio), timeout=20)
    if proc.returncode != 0 or not out:
        raise SpeechError(f"ffmpeg failed: {err.decode(errors='ignore')[:200]}")
    return out


async def stt(audio: bytes, lang: str = "bn") -> dict:
    """Return {"transcript": str, "language_code": str|None}. Raises SpeechError."""
    if not available():
        raise SpeechError("speech to text unavailable")
    wav = await to_wav16k(audio)
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.post(
                STT_URL, headers={"api-subscription-key": settings().sarvam_api_key},
                files={"file": ("audio.wav", wav, "audio/wav")},
                data={"model": "saaras:v3", "mode": "transcribe", "language_code": "unknown"})
        r.raise_for_status()
        data = r.json()
    except Exception as e:  # noqa: BLE001
        raise SpeechError(f"Sarvam STT error: {e}") from e
    text = (data.get("transcript") or "").strip()
    if not text:
        raise SpeechError("empty transcript")
    return {"transcript": text, "language_code": data.get("language_code")}


async def tts(text: str, lang: str = "bn") -> str | None:
    """Base64 WAV, or None if speech is unavailable (the reply is then text only)."""
    if not available() or not text:
        return None
    body = {"text": text[:2400], "language_code": LANG_CODES.get(lang, "bn-IN"), "model": "bulbul:v3"}
    if settings().sarvam_speaker:
        body["speaker"] = settings().sarvam_speaker
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.post(TTS_URL, headers={"api-subscription-key": settings().sarvam_api_key}, json=body)
        r.raise_for_status()
        audios = r.json().get("audios") or []
        return audios[0] if audios else None  # replies are under 90 words: one chunk
    except Exception as e:  # noqa: BLE001
        log.warning("Sarvam TTS error: %s", e)
        return None

