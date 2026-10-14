"""Problem 2: count how many tokens the shop handbook is, two ways.

1. Offline: tiktoken's o200k_base encoding (the GPT-4o / GPT-5 family tokenizer).
2. Live: ask gpt-6-luna and read the token counts it bills us for.
   A tiny call without the handbook is the baseline; the same call with the
   handbook as instructions minus that baseline is the handbook's real token count.
   A third call repeats the handbook so we can see how many tokens come back cached.

Prints counts only, never the API key. Results also go to output/handbook_tokens.json.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import tiktoken
from dotenv import load_dotenv
from openai import OpenAI

APP_DIR = Path(__file__).resolve().parent
HANDBOOK = APP_DIR / "data" / "shop_handbook.md"
OUT = APP_DIR / "output" / "handbook_tokens.json"
MODEL = "gpt-6-luna"


def client() -> OpenAI:
    load_dotenv(APP_DIR.parent / ".env")
    key = os.environ["PORTKEY_API_KEY"]
    return OpenAI(
        api_key=key,
        base_url="https://api.portkey.ai/v1",
        default_headers={"x-portkey-api-key": key, "x-portkey-provider": "openai"},
    )


def call(c: OpenAI, user: str, instructions: str | None = None) -> dict:
    kwargs = {"instructions": instructions} if instructions else {}
    r = c.responses.create(model=MODEL, input=user, max_output_tokens=16, **kwargs)
    u = r.usage
    return {
        "input_tokens": u.input_tokens,
        "cached_tokens": u.input_tokens_details.cached_tokens,
        "output_tokens": u.output_tokens,
    }


def main() -> None:
    text = HANDBOOK.read_text(encoding="utf-8")
    offline = len(tiktoken.get_encoding("o200k_base").encode(text))
    print(f"Handbook: {len(text):,} characters, {len(text.split()):,} words")
    print(f"tiktoken o200k_base: {offline:,} tokens")

    c = client()
    # Different user text on each call so Portkey's own response cache never replays an old answer.
    baseline = call(c, "Reply with OK. (baseline)")
    first = call(c, "Reply with OK. (run 1)", instructions=text)
    second = call(c, "Reply with OK. (run 2)", instructions=text)
    live = first["input_tokens"] - baseline["input_tokens"]

    for name, u in [("baseline (no handbook)", baseline), ("with handbook, run 1", first), ("with handbook, run 2", second)]:
        print(f"{name:24s} input={u['input_tokens']:,}  cached={u['cached_tokens']:,}  output={u['output_tokens']:,}")
    print(f"Handbook tokens measured on {MODEL}: {first['input_tokens']:,} - {baseline['input_tokens']:,} = {live:,}")

    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps({
        "model": MODEL,
        "characters": len(text),
        "words": len(text.split()),
        "tiktoken_o200k_base": offline,
        "live_calls": {"baseline": baseline, "handbook_run_1": first, "handbook_run_2": second},
        "handbook_tokens_live": live,
    }, indent=2), encoding="utf-8")
    print(f"Saved {OUT.relative_to(APP_DIR)}")


if __name__ == "__main__":
    main()
