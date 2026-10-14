"""Problem 8: run the final defended agent (all three defenses, cache on) on every exam document.

    python exam_run.py

Saves output/exam_results.json with one entry per exam file. Details of what was removed
from each document and how values were checked are in output/audit_trail.json.
"""

from __future__ import annotations

import json
import time

import agent

DOCS = sorted((agent.HW_DIR / "data" / "exam_docs").iterdir())
OUT = agent.OUTPUT_DIR / "exam_results.json"


def run_one(rel: str):
    for attempt in range(1, 6):  # retry network drops instead of losing the run
        try:
            return agent.run(rel, cache=True, defenses_on=agent.ALL)
        except Exception as e:  # noqa: BLE001
            if attempt == 5 or "Connection" not in f"{type(e).__name__} {e}":
                raise
            time.sleep(10 * attempt)


def main() -> None:
    results = []
    for path in DOCS:
        rel = path.relative_to(agent.HW_DIR).as_posix()
        d = run_one(rel)
        u = agent.last_run["usage"]
        results.append({
            "file": path.name,
            "vendor": d.vendor,
            "sku": d.sku,
            "unit_price": d.unit_price,
            "lead_time_days": d.lead_time_days,
            "min_order": d.min_order,
            "decision": d.decision,
            "reason": d.reason,
            "trap_tool_called": bool(agent.last_run["trap_calls"]),
            "input_tokens": u["tokens"]["input"],
            "cached_tokens": u["tokens"]["cached_input"],
            "output_tokens": u["tokens"]["output"],
            "cost_usd": u["cost_usd"]["total"],
        })
        print(f"=== {path.name}: {d.decision}\n")
    OUT.write_text(json.dumps(results, indent=2), encoding="utf-8")
    total = sum(r["cost_usd"] for r in results)
    print(f"Saved {OUT.relative_to(agent.HW_DIR).as_posix()}: {len(results)} files, "
          f"{sum(r['trap_tool_called'] for r in results)} trap calls, ${total:.6f} total")


if __name__ == "__main__":
    main()
