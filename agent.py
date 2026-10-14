"""Campus Customs vendor desk agent (entry point).

Usage:
    python agent.py --doc data/practice_docs/p01_bluehen_quote.html                     # all defenses
    python agent.py --doc data/practice_docs/p02_elmcity_quote.html --defenses none     # no defenses at all
    python agent.py --doc data/practice_docs/p02_elmcity_quote.html --defenses code,prompt

Runs the agent on one vendor document, prints the decision, and appends to
output/usage_log.json (tokens and dollars) and output/audit_trail.json (tool calls,
hidden text removed, and final answer). Trap tool calls land in output/trap_calls.json.

Defense layers (Problem 6), each can be switched on or off:
  code   - defenses.visible_text(): strip hidden text from the document before any model sees it
  design - the model only extracts facts, has no tools, and gets the cleaned document in the message;
           Python checks every value is really in the document and makes the decision
  prompt - safety rules in prompts/prompt.md and untrusted-data markers around the document
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from uuid import uuid4

os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")  # keep terminal output to the answer

import httpx2 as httpx  # the HTTP library the OpenAI SDK uses
from dotenv import load_dotenv
from openai import AsyncOpenAI
from pydantic_ai import Agent
from pydantic_ai.messages import ModelResponse, ToolCallPart, ToolReturnPart
from pydantic_ai.models.openai import OpenAIResponsesModel, OpenAIResponsesModelSettings
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.usage import UsageLimits

import defenses
import tools
from models import ExtractedFacts, VendorDecision

HW_DIR = Path(__file__).resolve().parent
PROMPT_PATH = HW_DIR / "prompts" / "prompt.md"
HANDBOOK_PATH = HW_DIR / "data" / "shop_handbook.md"
PRICES_PATH = HW_DIR / "data" / "prices.json"
OUTPUT_DIR = HW_DIR / "output"
USAGE_LOG = OUTPUT_DIR / "usage_log.json"
AUDIT_LOG = OUTPUT_DIR / "audit_trail.json"

MODEL_NAME = "gpt-6-luna"  # the only model this homework may call
PORTKEY_URL = "https://api.portkey.ai/v1"

# Cost guards: a normal run is 1 request (design on) or 2 requests and 1 tool call (design off).
USAGE_LIMITS = UsageLimits(request_limit=5, tool_calls_limit=4)
MODEL_SETTINGS = OpenAIResponsesModelSettings(openai_reasoning_effort="low")

# With cache (the default): an explicit cache breakpoint right after the instructions + handbook,
# and one cache key so every run lands on the same cached copy of the handbook.
CACHE_ON = OpenAIResponsesModelSettings(
    openai_prompt_cache_key="cc-vendor-desk",
    openai_prompt_cache_options={"mode": "implicit", "ttl": "30m"},
    openai_cache_instructions=True,
)
# Without cache (--no-cache): ask for explicit mode with no breakpoints. gpt-6-luna ignores this and still
# caches, so the no-cache agents also start every request with a unique id, which breaks the shared prefix.
CACHE_OFF = OpenAIResponsesModelSettings(
    openai_prompt_cache_options={"mode": "explicit"},
    openai_cache_instructions=False,
    openai_prompt_cache_diagnostics=False,
)


@dataclass(frozen=True)
class Defenses:
    code: bool = True
    design: bool = True
    prompt: bool = True

    @property
    def name(self) -> str:
        on = [n for n in ("code", "design", "prompt") if getattr(self, n)]
        return "all" if len(on) == 3 else "+".join(on) or "none"

    @classmethod
    def parse(cls, text: str) -> "Defenses":
        text = text.strip().lower()
        if text in ("all", "full"):
            return cls()
        if text in ("none", "off", ""):
            return cls(False, False, False)
        parts = {p.strip() for p in text.replace("+", ",").split(",")}
        if bad := parts - {"code", "design", "prompt"}:
            raise ValueError(f"Unknown defense(s): {', '.join(sorted(bad))}")
        return cls("code" in parts, "design" in parts, "prompt" in parts)


ALL = Defenses()
NONE = Defenses(False, False, False)

# Raw usage exactly as the API sent it, one entry per model call (filled by the HTTP hook below).
api_calls: list[dict] = []
api_errors: list[dict] = []


async def _record_usage(response: httpx.Response) -> None:
    if response.request.method == "POST" and response.request.url.path.endswith("/responses"):
        await response.aread()
        if response.status_code != 200:  # failed attempts are retried by the SDK; log only the call that counted
            api_errors.append({"status": response.status_code, "body": response.text[:300]})
            return
        data = response.json()
        api_calls.append({
            "response_id": data.get("id"),
            "model": data.get("model"),
            "usage": data.get("usage"),
            "portkey_cache_status": response.headers.get("x-portkey-cache-status"),
        })


def build_instructions(design: bool, prompt_rules: bool) -> str:
    """prompt.md with the right blocks kept and the full handbook pasted in. Identical on every call, so it caches."""
    text = PROMPT_PATH.read_text(encoding="utf-8")
    text = re.sub(r"\A<!--.*?\n-->\n", "", text, flags=re.S)  # the file's own header note
    keep = {"decide": not design, "extract": design, "safety": prompt_rules}
    for name, on in keep.items():
        block = re.compile(rf"<!-- KEEP:{name} -->\n(.*?)<!-- END:{name} -->\n", re.S)
        text = block.sub(lambda m: m.group(1) if on else "", text)
    return text.replace("{{HANDBOOK}}", HANDBOOK_PATH.read_text(encoding="utf-8"))


def build_model() -> OpenAIResponsesModel:
    load_dotenv(HW_DIR / ".env")
    load_dotenv(HW_DIR.parent / ".env")
    key = os.getenv("PORTKEY_API_KEY")
    if not key:
        sys.exit("Missing PORTKEY_API_KEY in .env")
    client = AsyncOpenAI(
        api_key=key,
        base_url=PORTKEY_URL,
        default_headers={
            "x-portkey-api-key": key,
            "x-portkey-provider": "openai",
            # Portkey can replay an old answer for an identical request without calling the model.
            # Force a live call every time so answers and token counts are real.
            "x-portkey-cache-force-refresh": "true",
        },
        http_client=httpx.AsyncClient(event_hooks={"response": [_record_usage]}, timeout=120),
    )
    return OpenAIResponsesModel(MODEL_NAME, provider=OpenAIProvider(openai_client=client))


MODEL = build_model()
_agents: dict[tuple, Agent] = {}


def get_agent(d: Defenses, cache: bool) -> Agent:
    """One agent per (design, prompt, cache) setup, built once.

    Design off: the model reads the document with read_document, decides, and can see the two trap tools.
    Design on:  the model gets no tools at all and may only return ExtractedFacts.
    """
    key = (d.design, d.prompt, cache)
    if key not in _agents:
        instructions = build_instructions(d.design, d.prompt)
        if not cache:
            static = instructions
            # Re-evaluated on every model request, so no two requests ever share a prefix.
            instructions = lambda: f"Request id: {uuid4().hex}\n\n{static}"
        _agents[key] = Agent(
            MODEL,
            output_type=ExtractedFacts if d.design else VendorDecision,
            instructions=instructions,
            tools=[] if d.design else [tools.read_document, tools.send_email, tools.approve_payment],
            model_settings=MODEL_SETTINGS,
        )
    return _agents[key]


def dollar_cost(input_tokens: int, cache_read: int, cache_write: int, output_tokens: int) -> dict:
    """Price one run with data/prices.json. Cached and cache-write tokens are priced separately from fresh input."""
    p = json.loads(PRICES_PATH.read_text(encoding="utf-8"))[MODEL_NAME]
    fresh = input_tokens - cache_read - cache_write
    parts = {
        "fresh_input": fresh * p["input_per_1m"] / 1e6,
        "cached_input": cache_read * p["cached_input_per_1m"] / 1e6,
        "cache_write": cache_write * p["cache_write_per_1m"] / 1e6,
        "output": output_tokens * p["output_per_1m"] / 1e6,
    }
    parts = {k: round(v, 8) for k, v in parts.items()}
    parts["total"] = round(sum(parts.values()), 8)
    return parts


def short(value: object, limit: int = 200) -> str:
    text = value if isinstance(value, str) else json.dumps(value, default=str)
    return text if len(text) <= limit else text[:limit] + "..."


def tool_steps(messages: list) -> list[dict]:
    """Pair each tool call with its result for the audit trail (skips PydanticAI's internal final_result tool)."""
    results = {}
    for m in messages:
        for part in getattr(m, "parts", []):
            if isinstance(part, ToolReturnPart):
                results[part.tool_call_id] = part.content
    steps = []
    for m in messages:
        if isinstance(m, ModelResponse):
            for part in m.parts:
                if isinstance(part, ToolCallPart) and part.tool_name != "final_result":
                    steps.append({
                        "tool": part.tool_name,
                        "args": part.args_as_dict(),
                        "result": short(results.get(part.tool_call_id, "(no result)")),
                    })
    return steps


def trap_entries() -> list[dict]:
    return json.loads(tools.TRAP_LOG.read_text(encoding="utf-8")) if tools.TRAP_LOG.exists() else []


def api_usage(calls: list[dict]) -> dict:
    """Token counts summed from the raw API usage of these calls."""
    def get(u: dict, *path: str) -> int:
        for k in path:
            u = (u or {}).get(k) or {}
        return u if isinstance(u, int) else 0

    us = [c["usage"] for c in calls]
    return {
        "input": sum(get(u, "input_tokens") for u in us),
        "cached_input": sum(get(u, "input_tokens_details", "cached_tokens") for u in us),
        "cache_write": sum(get(u, "input_tokens_details", "cache_write_tokens") for u in us),
        "output": sum(get(u, "output_tokens") for u in us),
        "reasoning": sum(get(u, "output_tokens_details", "reasoning_tokens") for u in us),
    }


# Details of the most recent run (tokens, cost, hidden text removed, traps) for the test scripts.
last_run: dict = {}


def run(doc: str, cache: bool = True, defenses_on: Defenses = ALL) -> VendorDecision:
    global last_run
    doc_path = tools.resolve_doc(doc)
    rel = doc_path.relative_to(HW_DIR).as_posix()
    tools.current_doc = rel
    tools.CODE_DEFENSE = defenses_on.code
    tools.WRAP_DOCUMENT = defenses_on.prompt
    traps_before = len(trap_entries())
    calls_before = len(api_calls)
    agent = get_agent(defenses_on, cache)
    settings = CACHE_ON if cache else CACHE_OFF

    started = datetime.now()
    ungrounded: list[str] = []
    reconciled: list[str] = []
    if defenses_on.design:
        # Code reads the document; the model only sees its text in the message and has no tools.
        text = tools.load_document(doc_path)
        result = agent.run_sync(
            f"Extract the facts from this vendor document ({doc_path.name}):\n\n{text}",
            usage_limits=USAGE_LIMITS,
            model_settings=settings,
        )
        facts, ungrounded = defenses.ground(result.output, text)
        facts, reconciled = defenses.reconcile(facts, text)
        decision = defenses.decide(facts)
        steps = [{"tool": "(code) load_document", "args": {"path": rel}, "result": short(text)}]
    else:
        result = agent.run_sync(f"Review this vendor document: {rel}", usage_limits=USAGE_LIMITS, model_settings=settings)
        decision = result.output
        steps = tool_steps(result.all_messages())
    seconds = round((datetime.now() - started).total_seconds(), 2)
    calls = api_calls[calls_before:]
    tokens = api_usage(calls)
    new_traps = trap_entries()[traps_before:]

    usage_entry = {
        "document": rel,
        "time": started.isoformat(timespec="seconds"),
        "seconds": seconds,
        "model": MODEL_NAME,
        "defenses": defenses_on.name,
        "cache": cache,
        "requests": len(calls),
        "tokens": tokens,
        "per_request": [c["usage"] for c in calls],
        "cost_usd": dollar_cost(tokens["input"], tokens["cached_input"], tokens["cache_write"], tokens["output"]),
    }
    tools.append_json(USAGE_LOG, usage_entry)
    tools.append_json(AUDIT_LOG, {
        "document": rel,
        "time": started.isoformat(timespec="seconds"),
        "defenses": defenses_on.name,
        "hidden_text_removed": tools.last_removed,
        "tool_calls": steps,
        "ungrounded_values_dropped": ungrounded,
        "checked_against_quote_lines": reconciled,
        "decided_by": "python (handbook rules)" if defenses_on.design else "model",
        "final_answer": decision.model_dump(),
    })
    last_run = {"usage": usage_entry, "removed": tools.last_removed, "ungrounded": ungrounded, "reconciled": reconciled,
                "steps": steps, "trap_calls": new_traps}

    print(json.dumps(decision.model_dump(), indent=2))
    print(f"\nDefenses: {defenses_on.name}   Tools: {', '.join(s['tool'] for s in steps) or 'none'}")
    if tools.last_removed:
        print(f"Hidden text removed: {len(tools.last_removed)} piece(s): {', '.join(r['why'] for r in tools.last_removed)}")
    if reconciled:
        print(f"Checked against the quote lines: {'; '.join(reconciled)}")
    if ungrounded:
        print(f"Values not found in the document, set to null: {', '.join(ungrounded)}")
    print(f"Tokens: {tokens['input']:,} in ({tokens['cached_input']:,} cached), {tokens['output']:,} out over {len(calls)} request(s)")
    print(f"Cost: ${usage_entry['cost_usd']['total']:.6f}")
    if new_traps:
        print(f"\n!!! ALERT: {rel} triggered {len(new_traps)} trap call(s). "
              f"See {tools.TRAP_LOG.relative_to(HW_DIR).as_posix()}. Nothing was sent or paid.")
    return decision


def main() -> None:
    parser = argparse.ArgumentParser(description="Campus Customs vendor desk agent")
    parser.add_argument("--doc", required=True, help="Path to one vendor document, e.g. data/practice_docs/p01_bluehen_quote.html")
    parser.add_argument("--no-cache", action="store_true", help="Turn prompt caching off (for cost comparisons)")
    parser.add_argument("--defenses", default="all", help="all (default), none, or a mix of code,design,prompt")
    args = parser.parse_args()
    run(args.doc, cache=not args.no_cache, defenses_on=Defenses.parse(args.defenses))


if __name__ == "__main__":
    main()
