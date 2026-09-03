"""
Prove frames are emitted as they are produced, not assembled then flushed.

A delay is injected into the narrative step; if the transport were buffering,
every frame would land together at the end instead of before that delay.
"""
import asyncio, json, sys, time, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import config
config.TAVILY_API_KEY = ""
config.GEMINI_API_KEY = ""

from routes import generate as gen
from models.schemas import GenerateRequest

DELAY = 0.4
real = gen._stream_section


async def slow(prompt, fallback):
    await asyncio.sleep(DELAY)
    return await real(prompt, fallback)


gen._stream_section = slow

body = json.loads((pathlib.Path(__file__).parent.parent /
                   "tests/fixtures/sample_request.json").read_text())


async def main():
    start = time.perf_counter()
    stamps = []
    async for frame in gen.run_pipeline(GenerateRequest(**body), "temenos"):
        if frame.startswith("data: "):
            stamps.append((time.perf_counter() - start,
                           json.loads(frame[6:])["section"]))

    total = stamps[-1][0]
    first_card = next(t for t, s in stamps if s == "scorecard")
    print(f"{len(stamps)} frames over {total:.2f}s "
          f"(injected {DELAY}s per narrative step)\n")
    for t, section in stamps:
        print(f"  {t:6.2f}s  {section}")
    print()
    print(f"first scorecard at {first_card:.2f}s, stream ends at {total:.2f}s")
    early = sum(1 for t, _ in stamps if t < total * 0.5)
    print(f"{early}/{len(stamps)} frames delivered in the first half of the run")
    print("PROGRESSIVE" if early >= len(stamps) * 0.4 and total > DELAY * 3
          else "BUFFERED")

asyncio.run(main())
