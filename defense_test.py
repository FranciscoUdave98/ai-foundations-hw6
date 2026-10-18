"""Problem 6: rerun the practice documents with the defenses on.

    python defense_test.py            # every setup
    python defense_test.py all        # rerun one setup, reuse the saved runs of the others

Runs all 12 practice documents 3 times each with:
  all     - code + design + prompt (the defended agent)
  code    - only the code layer        \
  design  - only the design layer       > to see what each layer catches on its own and what it costs
  prompt  - only the prompt layer      /
"none" (zero defenses) comes from output/attack_results.json (Problem 5 replay), so it is not paid for twice.
Saves output/defense_results.json: same checks as Problem 5 plus tokens and dollars per document.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime

import agent
from attack_test import OUT as ATTACK_OUT, evaluate, summarize

OUT = agent.OUTPUT_DIR / "defense_results.json"
SETUPS = ["all", "code", "design", "prompt"]
REPEAT = 3


def main() -> None:
    before = json.loads(ATTACK_OUT.read_text(encoding="utf-8"))
    setups = {"none": {"per_document": before["per_document"], "runs": before["runs"]}}
    # `python defense_test.py all` reruns only that setup; the others are reused from their saved runs.
    only = sys.argv[1:] or SETUPS
    for name in SETUPS:
        saved = agent.OUTPUT_DIR / f"_defense_rows_{name}.json"
        if name in only or not saved.exists():
            saved.write_text(json.dumps(evaluate(agent.Defenses.parse(name), repeat=REPEAT)), encoding="utf-8")
        rows = json.loads(saved.read_text(encoding="utf-8"))
        setups[name] = {"per_document": summarize(rows), "runs": rows}

    totals = {}
    for name, s in setups.items():
        rows = s["runs"]
        totals[name] = {
            "runs": len(rows),
            "fooled_runs": sum(r["fooled"] for r in rows),
            "documents_fooled_at_least_once": sum(d["fooled_runs"] > 0 for d in s["per_document"]),
            "trap_calls": sum(r["trap_called"] for r in rows),
            "reveals_max_cost_runs": sum(r["reveals_max_cost"] for r in rows),
            "avg_requests_per_doc": round(sum(r["requests"] for r in rows) / len(rows), 2),
            "avg_input_tokens_per_doc": round(sum(r["tokens"]["input"] for r in rows) / len(rows)),
            "avg_cached_tokens_per_doc": round(sum(r["tokens"]["cached_input"] for r in rows) / len(rows)),
            "avg_output_tokens_per_doc": round(sum(r["tokens"]["output"] for r in rows) / len(rows)),
            "avg_cost_usd_per_doc": round(sum(r["cost_usd"] for r in rows) / len(rows), 8),
        }

    OUT.write_text(json.dumps({
        "run_at": datetime.now().isoformat(timespec="seconds"),
        "model": agent.MODEL_NAME,
        "runs_per_document": REPEAT,
        "totals": totals,
        "setups": setups,
    }, indent=2), encoding="utf-8")
    for name, t in totals.items():
        print(f"{name:7s} fooled {t['fooled_runs']}/{t['runs']} runs, {t['documents_fooled_at_least_once']} docs; "
              f"traps {t['trap_calls']}; {t['avg_input_tokens_per_doc']:,} in / {t['avg_output_tokens_per_doc']} out; "
              f"${t['avg_cost_usd_per_doc']:.6f}/doc")
    print(f"Saved {OUT.relative_to(agent.HW_DIR).as_posix()}")


if __name__ == "__main__":
    main()
