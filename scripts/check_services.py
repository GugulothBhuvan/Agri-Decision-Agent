"""Check every outside service with the keys in .env, before a demo.

    python -m scripts.check_services

Calls each service once: Open-Meteo, Nominatim, Sarvam text to speech then speech to text on that audio,
and Claude (one small extraction). Sarvam and Claude calls spend a little credit.
"""
import asyncio
import base64
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings  # noqa: E402
from app.db import init_db  # noqa: E402
from app.llm import client as llm  # noqa: E402
from app.llm.prompts import EXTRACT_SCHEMA, EXTRACT_SYSTEM  # noqa: E402
from app.sources import geocode, openmeteo  # noqa: E402
from app.speech import sarvam  # noqa: E402

OK, FAIL, SKIP = "PASS", "FAIL", "SKIP"


async def main() -> int:
    init_db()
    results = []

    async def check(name, coro):
        t0 = time.perf_counter()
        try:
            status, note = await coro
        except Exception as e:  # noqa: BLE001
            status, note = FAIL, f"{type(e).__name__}: {e}"
        results.append((name, status, note, int((time.perf_counter() - t0) * 1000)))

    async def om():
        fc = await openmeteo.fetch_live(22.42, 87.32, retries=[])
        if fc.status != "live":
            return FAIL, fc.error
        problem = openmeteo.validate(fc, 6)
        return (FAIL, problem) if problem else (OK, f"{len(fc.raw['hourly']['time'])} hourly values")

    async def nom():
        p = await geocode.geocode_village("Salboni", "Paschim Medinipur")
        return (OK, f"{p.lat:.3f},{p.lon:.3f} {p.display_name[:50]}") if p else (FAIL, "no result (check network / user agent)")

    async def speech():
        if not settings().sarvam_api_key:
            return SKIP, "SARVAM_API_KEY not set"
        audio = await sarvam.tts("নমস্কার, আমি ফসল সাথী। আপনার ধান কবে কাটবেন?", "bn")
        if not audio:
            return FAIL, "text to speech returned no audio (see log above)"
        r = await sarvam.stt(base64.b64decode(audio), "bn")
        return OK, f"round trip transcript: {r['transcript'][:60]}"

    async def claude():
        if not llm.available():
            return SKIP, "LLM_API_KEY not set (LLM_PROVIDER=openrouter|anthropic)"
        r = await llm.complete_json(EXTRACT_SYSTEM, "Current profile: {}\nFarmer said: আমার ৩ বিঘা আমন ধান পেকে গেছে, শালবনি গ্রাম।",
                                    EXTRACT_SCHEMA)
        if not r:
            return FAIL, "no structured reply (see log above: key, model, network)"
        return OK, f"{llm.provider()}:{llm.model()} crop={r.get('crop')} stage={r.get('stage')} area={r.get('area_bigha')} village={r.get('village')}"

    await check("Open-Meteo forecast", om())
    await check("Nominatim geocoding", nom())
    await check("Sarvam TTS + STT", speech())
    await check("LLM extraction", claude())
    width = max(len(r[0]) for r in results)
    for name, status, note, ms in results:
        print(f"{status:4s}  {name:{width}s}  {ms:5d} ms  {note}")
    return 1 if any(r[1] == FAIL for r in results) else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
