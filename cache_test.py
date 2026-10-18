"""Problem 4: run every practice document with and without prompt caching.

    python cache_test.py                    # the final defended agent (default)
    python cache_test.py --defenses none    # the undefended agent

Saves every model call (setup, document, every token count the API reported, dollars
from data/prices.json) to output/cache_test.json. The no-cache setup runs first, so
it can't benefit from anything the cache setup stores.

The top-level "calls" and "documents" in cache_test.json are the original Problem 4 run
(the Problem 3 agent: 2 model calls per document). Reruns are stored under
"reruns" -> <defenses>, so the original numbers are never overwritten.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime

import agent

DOCS = sorted((agent.HW_DIR / "data" / "practice_docs").iterdir())
KEY = {k["file"]: k for k in json.loads((agent.HW_DIR / "data" / "practice_key.json").read_text(encoding="utf-8"))}
OUT = agent.OUTPUT_DIR / "cache_test.json"
FIELDS = ["vendor", "sku", "unit_price", "lead_time_days", "min_order", "decision"]


def call_entry(setup: str, doc: str, n: int, raw: dict) -> dict:
    u = raw["usage"]
    if not u:
        raise RuntimeError(f"{setup} {doc} call {n}: API sent no usage (Portkey cache status {raw['portkey_cache_status']})")
    inp = u["input_tokens"]
    cached = u["input_tokens_details"].get("cached_tokens", 0)
    write = u["input_tokens_details"].get("cache_write_tokens", 0)
    out = u["output_tokens"]
    return {
        "setup": setup,
        "document": doc,
        "call": n,
        "response_id": raw["response_id"],
        "model": raw["model"],
        "portkey_cache_status": raw["portkey_cache_status"],
        "api_usage": u,  # exactly as the API sent it
        "tokens": {
            "input": inp,
            "cached_input": cached,
            "cache_write": write,
            "fresh_input": inp - cached - write,
            "output": out,
            "reasoning": u.get("output_tokens_details", {}).get("reasoning_tokens", 0),
            "total": u["total_tokens"],
        },
        "cost_usd": agent.dollar_cost(inp, cached, write, out),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--defenses", default="all", help="all (default), none, or a mix of code,design,prompt")
    defenses_on = agent.Defenses.parse(parser.parse_args().defenses)
    calls, docs = [], []
    for setup, cache in (("no_cache", False), ("cache", True)):
        print(f"\n===== {setup} =====")
        for path in DOCS:
            rel = path.relative_to(agent.HW_DIR).as_posix()
            start = len(agent.api_calls)
            decision = agent.run(rel, cache=cache, defenses_on=defenses_on).model_dump()
            stale = [r["portkey_cache_status"] for r in agent.api_calls[start:] if r["portkey_cache_status"] == "HIT"]
            if stale:
                raise RuntimeError(f"{path.name}: Portkey replayed a cached answer, so the token counts are not live")
            new = [call_entry(setup, path.name, i + 1, raw) for i, raw in enumerate(agent.api_calls[start:])]
            calls += new
            expected = KEY.get(path.name, {})
            wrong = [f for f in FIELDS if decision.get(f) != expected.get(f)]
            docs.append({
                "setup": setup,
                "document": path.name,
                "calls": len(new),
                "cached_input": sum(c["tokens"]["cached_input"] for c in new),
                "cost_usd": round(sum(c["cost_usd"]["total"] for c in new), 8),
                "decision": decision["decision"],
                "matches_key": not wrong,
                "fields_off": wrong,
            })
            print(f"--- {path.name}: {docs[-1]['cost_usd']:.6f} USD, matches key: {not wrong} {wrong or ''}\n")

    data = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else {}
    data.setdefault("reruns", {})[defenses_on.name] = {
        "run_at": datetime.now().isoformat(timespec="seconds"),
        "model": agent.MODEL_NAME,
        "defenses": defenses_on.name,
        "calls": calls,
        "documents": docs,
    }
    tmp = OUT.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(OUT)
    print(f"Saved {len(calls)} calls to {OUT.relative_to(agent.HW_DIR).as_posix()} under reruns/{defenses_on.name}")


if __name__ == "__main__":
    main()
