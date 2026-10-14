# Problem 2: Size up the handbook

The vendor desk agent sends the whole Campus Customs Shop Handbook (`data/shop_handbook.md`) to `gpt-6-luna` on every call. This page works out how big that handbook is in tokens and what it costs us on its own, before any vendor document or answer is added.

## 1. How many tokens is the handbook?

**Answer: 10,064 tokens.**

| What we measured | Result |
|---|---|
| Characters | 28,903 |
| Words | 6,127 |
| Tokens, offline count (tiktoken `o200k_base`) | 10,058 |
| **Tokens, billed by gpt-6-luna** | **10,064** |

### How I got it

I counted it two ways with `count_handbook_tokens.py`. The raw numbers are saved in `output/handbook_tokens.json`.

1. **Offline count.** I ran the handbook through `tiktoken` with `o200k_base`, the tokenizer used by the GPT-4o and GPT-5 family of models. Result: 10,058 tokens. This costs nothing, but it is an estimate, because the gpt-6-luna tokenizer is not published.
2. **Live count from luna (the number we use).** I made two small calls to `gpt-6-luna` and read the token counts the API bills us for:

   | Call | What was sent | Input tokens billed |
   |---|---|---|
   | A. Baseline | just "Reply with OK." | 13 |
   | B. Handbook | the handbook + "Reply with OK." | 10,077 |

   ```
   handbook tokens = call B - call A
                   = 10,077 - 13
                   = 10,064 tokens
   ```

The two methods are only 6 tokens apart (10,064 vs 10,058, about 0.06%). The gap is likely the few tokens luna adds to mark the start and end of the instructions.

**Rule of thumb:** 6,127 words became 10,064 tokens, which is about **1.6 tokens per word**. That is more than the usual 1.3 for plain English because the handbook is mostly tables: SKUs like `CC-HD-001`, prices like `$29.48`, and `|` separators all split into several tokens each.

## 2. Price list (from `data/prices.json`)

| gpt-6-luna price | per 1 million tokens | per 1 token |
|---|---|---|
| Regular input | $0.10 | $0.0000001 |
| Cache write (first time the handbook is stored) | $0.125 | $0.000000125 |
| Cached input (handbook reused from cache) | $0.01 | $0.00000001 |

Cached input is **10 times cheaper** than regular input. Writing to the cache costs 25% extra, but only once.

## 3. Cost of the handbook alone, per call

The formula is the same every time:

```
cost = tokens × (price per 1M tokens) ÷ 1,000,000
```

| Case | Math | Cost per call |
|---|---|---|
| **No caching** | 10,064 × $0.10 ÷ 1,000,000 | **$0.0010064** (about 1/10 of a cent) |
| **Cached, first call** (cache write) | 10,064 × $0.125 ÷ 1,000,000 | **$0.001258** (about 1/8 of a cent) |
| **Cached, every later call** (cache read) | 10,064 × $0.01 ÷ 1,000,000 | **$0.00010064** (about 1/100 of a cent) |

## 4. Cost of the handbook alone, for 1,000 calls

### Without caching

Every call pays full price for all 10,064 tokens.

```
1,000 calls × $0.0010064 = $1.0064   ≈ $1.01
```

### With caching

The first call writes the handbook into the cache. The other 999 calls read it from the cache.

```
  1 call    × $0.001258     = $0.001258     (cache write)
+ 999 calls × $0.00010064   = $0.10053936   (cache reads)
  ---------------------------------------
  total                     = $0.10179736   ≈ $0.10
```

### Side by side

| | 1 call | 1,000 calls |
|---|---|---|
| No caching | $0.0010064 | **$1.01** |
| With caching | $0.001258 first, then $0.00010064 | **$0.10** |
| **Savings** | | **$0.90 (about 90% less)** |

**In plain words:** without caching, every 1,000 calls spend about $1 just re-reading the handbook. With caching, the same 1,000 calls spend about 10 cents. The handbook is the same on every call, so we only pay full price for it once.

## 5. Caching works in practice

I sent the handbook twice in a row. On the second call, luna reported **10,061 of the 10,077 input tokens as cached**, so almost all of the handbook came from the cache. The design rule for later problems: **always put the handbook first in the prompt, word for word, and put the vendor document after it.** The cache only works on a matching start of the prompt, so any change before or inside the handbook would mean paying full price again.

## Assumptions

- These are handbook-only costs. Each real call also pays for the vendor document (input) and luna's answer (output, $0.50 per 1M tokens). Those are measured in later problems.
- The "with caching" total assumes the 1,000 calls run close together, so the cache stays warm. If calls are spread out and the cache expires, each new burst pays one more cache write ($0.001258). That is still far below paying full price on every call.
- The cache-write price comes from our `prices.json` (MGT 409 Lecture 11).
