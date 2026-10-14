# Campus Customs Vendor Desk Agent · HW6

Francisco Udave · AI Foundations for Managers (Yale SOM)

A PydanticAI agent on **`gpt-6-luna`** that reads one vendor document (an HTML quote or catalog page, a PDF quote, or an `.eml` email) and pulls out vendor, SKU, unit price, lead time, and minimum order. It then recommends buy or don't buy using the Campus Customs Shop Handbook. It is built to be cheap (the handbook is cached) and safe against vendor documents that hide instructions aimed at the AI.

**Results page:** open `output/hw6_report.html` with a double-click. It works offline and needs no other files.

## Results at a glance

| | |
|---|---|
| Handbook size | 10,064 tokens, sent on every call |
| Caching | 88.7% cheaper per document |
| Practice docs fooled, no defenses | 4 of 12 (10 of 36 runs); max unit cost leaked in 20 of 36 answers |
| Practice docs fooled, all defenses | **0 of 12 (0 of 36 runs)**; all clean docs correct |
| Exam docs | 10 processed, 0 trap calls, $0.00016 each |
| Defended cost | $0.000143 per document, about **$4.28 a month** at 1,000 documents a day |

## Run it

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
copy .env.example .env        # then put your PORTKEY_API_KEY in .env
.venv\Scripts\python.exe agent.py --doc data/practice_docs/p01_bluehen_quote.html
```

On macOS or Linux, use `.venv/bin/python` and `cp`.

| Option | What it does |
|---|---|
| `--doc PATH` | The vendor document to review (must be inside `data/`). |
| `--defenses all` | Default. Code + design + prompt defenses. |
| `--defenses none` | No defenses at all: raw file text, no safety rules, the model decides, and trap tools are within reach. |
| `--defenses code,prompt` | Any mix of `code`, `design`, `prompt`. |
| `--no-cache` | Turn prompt caching off (for cost comparisons). |

Each run prints the answer, the tokens, and the cost, and appends to `output/usage_log.json` and `output/audit_trail.json`. `send_email` and `approve_payment` are **trap tools**: they never send or pay anything, they only write to `output/trap_calls.json`. Any entry there means a document took over the agent.

## Files

| File | What it is |
|---|---|
| `agent.py` | Entry point: builds the agent, switches defenses, writes the logs. |
| `tools.py` | `read_document` plus the two trap tools. |
| `models.py` | `ExtractedFacts` and `VendorDecision` (structured output). |
| `defenses.py` | Code layer (hidden-text filter for HTML, PDF, and email) and design layer (value check and handbook rules in Python). |
| `prompts/prompt.md` | Agent instructions; the full handbook is pasted in on every call. |
| `count_handbook_tokens.py` | Problem 2: handbook token count. |
| `cache_test.py` | Problem 4: with vs without caching. |
| `attack_test.py` | Problem 5: all practice docs, zero defenses. |
| `defense_test.py` | Problem 6: all practice docs with each defense layer. |
| `red_team.py` | Problem 7: our two attacks in `data/my_attacks/`, no defenses vs defended. |
| `exam_run.py` | Problem 8: the final defended agent on `data/exam_docs/` → `output/exam_results.json`. |
| `build_report.py` | Problem 9: rebuilds `output/hw6_report.html` from the JSON results. |
| `AI_prompts.md` | Every prompt used for this homework. |
| `data/` | Course data (handbook, prices, practice key, practice and exam docs) and `my_attacks/`. |
| `output/` | Results, logs, and write-ups for each problem. |

Every model call uses `gpt-6-luna` through Portkey with `PORTKEY_API_KEY`. The real `.env` is git-ignored.
