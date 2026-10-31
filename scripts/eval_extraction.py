"""Score the LLM extractor on the 20-utterance golden set. Needs LLM_API_KEY. Spends real API credits.

    LLM_API_KEY=... python -m scripts.eval_extraction
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tests"))

from app.llm import client as llm  # noqa: E402
from app.llm.prompts import EXTRACT_SCHEMA, EXTRACT_SYSTEM  # noqa: E402
from test_extraction import GOLD, score  # noqa: E402


async def main():
    if not llm.available():
        sys.exit("LLM not configured: set LLM_PROVIDER=anthropic and LLM_API_KEY")
    outs = {}
    for g in GOLD:
        r = await llm.complete_json(EXTRACT_SYSTEM, f"Current profile: {{}}\nFarmer said: {g['text']}", EXTRACT_SCHEMA)
        outs[g["text"]] = {k: v for k, v in (r or {}).items() if k != "confidence"}
        print(g["text"][:50], "->", outs[g["text"]])
    res = score(lambda t: outs.get(t, {}))
    print("\nLLM extractor accuracy per field:")
    for f, (h, n) in res.items():
        flag = "" if h / n >= 0.8 else "   <- below 80%: fix the prompt"
        print(f"  {f:13s} {h}/{n}{flag}")


if __name__ == "__main__":
    asyncio.run(main())
