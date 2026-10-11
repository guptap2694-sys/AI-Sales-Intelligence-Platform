# LLM cost model

All numbers below are produced by `python -m llm.cost estimate` (change any volume with flags,
e.g. `--reps 80 --prefilter-pass-rate 0.2`, or `--json` for machine-readable output). They are
**estimates from list prices and measured prompt sizes**, not billed usage — real usage is in
`traces/llm_calls.jsonl` (`python -m llm.tracing summary`).

## Prices used

USD per 1M tokens, Anthropic first-party API. Source: the Anthropic `claude-api` skill bundled
with Claude Code 2.1.296 ("Current Models", cached 2026-10-06) and its prompt-caching / cost
guides. Re-check https://www.anthropic.com/pricing before committing budget.

| model | input | output | cache read | cache write (5 min) | role here |
|---|---|---|---|---|---|
| `claude-haiku-5-5` | $0.10 | $0.50 | $0.01 | $0.125 | need classification (high volume) |
| `claude-sonnet-5-5` | $2.00 | $10.00 | $0.20 | $2.50 | account brief, outreach draft |
| `claude-opus-5-5` | $4.00 | $20.00 | $0.20 | $5.00 | reference only — not used |

Cache write = 1.25× input, cache read ≈ 0.1× input. Message Batches API = 50% off every token
(stacks with caching). Haiku 5.5 prices apply to prompts ≤ 100K tokens (ours are ~2K).

## Token sizes (measured, with a stated assumption)

Each prompt was rendered with a synthetic but realistic company (a 320-person diagnostic imaging
group with a ~500-char description and 4 signals) after `trim_company()`; tokens ≈ characters / 4.
Exact counts would come from `messages.count_tokens`; chars/4 is typically within ±15% for
English + JSON. Output tokens are the expected size of each output contract with thinking off.

| prompt | model | system (cached) | user (per company) | output |
|---|---|---|---|---|
| need-classifier v2 | Haiku 5.5 | ~1,680 | ~400 | ~110 |
| account-brief v1 | Sonnet 5.5 | ~520 | ~445 | ~170 |
| outreach-draft v1 | Sonnet 5.5 | ~630 | ~435 | ~210 |

The classifier's system prompt (rubric + examples) is 80% of its input and identical for every
company, which is why caching matters. Every system prompt is above the 512-token minimum
cacheable prefix for the 5.5 family, and contains no `{{variables}}` (enforced by a test) so the
cached bytes never change between companies.

## Volume assumptions

| assumption | value | why |
|---|---|---|
| dataset rows (initial backfill) | 200,000 | companies after ingest/dedupe |
| rules pre-filter pass rate | 30% → 60,000 classified | only plausible accounts reach an LLM |
| tier A/B accounts with brief + outreach in backfill | 5,000 | what reps can realistically work |
| weekly changed & pre-filter-passing rows re-classified | 8,000 / week | refresh only what changed |
| weekly changed tier A/B briefs refreshed | 600 / week | |
| reps × on-demand drafts / briefs per day | 40 × 15 drafts + 10 briefs, 21 workdays | interactive, rep is waiting |
| cache hit rate (bulk / interactive) | 98% / 80% | continuous streams keep the 5-min cache warm |

## Results

**One-off backfill**

| task | model | in tok | out tok | calls | standard | +cache | +batch | cache+batch |
|---|---|---|---|---|---|---|---|---|
| Need classifier | Haiku 5.5 | 2,077 | 110 | 60,000 | $15.76 | $6.92 | $7.88 | **$3.46** |
| Account brief (tier A/B) | Sonnet 5.5 | 968 | 170 | 5,000 | $18.18 | $13.59 | $9.09 | **$6.79** |
| Outreach draft (tier A/B) | Sonnet 5.5 | 1,061 | 210 | 5,000 | $21.11 | $15.60 | $10.55 | **$7.80** |
| **Total** | | | | | $55.05 | $36.11 | $27.52 | **$18.05** |

**Steady state, per month**

| task | model | calls | standard | +cache | +batch | cache+batch |
|---|---|---|---|---|---|---|
| Need classifier (weekly refresh) | Haiku 5.5 | 34,640 | $9.10 | $4.00 | $4.55 | **$2.00** |
| Account brief (weekly refresh) | Sonnet 5.5 | 2,598 | $9.45 | $7.06 | $4.72 | **$3.53** |
| Account brief (on-demand) | Sonnet 5.5 | 8,400 | $30.54 | $24.64 | n/a* | **$24.64** |
| Outreach draft (on-demand) | Sonnet 5.5 | 12,600 | $53.20 | $42.60 | n/a* | **$42.60** |
| **Total / month** | | | $102.29 | $78.30 | $93.01 | **$72.77** |

\* interactive requests can't wait for a batch (up to 24h), so they pay standard price.

Per call: classifier ≈ $0.00026 standard / $0.00006 cached+batched; outreach draft ≈ $0.0042
standard / $0.0034 cached. A rep's daily usage (15 drafts + 10 briefs) is ≈ $0.10.

## Why this model per task

- **Need classification → Haiku 5.5.** A bounded 3-class decision with a rubric, a JSON schema
  and few-shot anchors; volume is tens of thousands of calls. Haiku is 20× cheaper than Sonnet on
  both input and output; the eval harness (`python -m evals.run_evals`) is where we confirm the
  cheap model holds quality — if v2 on Haiku under-performs, the first lever is the prompt, the
  second is Sonnet on only the low-confidence cases (a cascade), not Sonnet for everything.
- **Brief + outreach → Sonnet 5.5.** Low volume, read by humans, quality of writing and judgement
  directly affects reply rates and brand. A few cents per draft is negligible next to a rep's time.
  `effort: low` and thinking off keep latency and output tokens down for these short tasks.
- **Opus 5.5** is listed for reference only; nothing here needs it.

## Rules pre-filter: don't send 11 GB to an LLM

The raw dataset is ~11 GB. Rules (`pipeline/signals.py`, `pipeline/scoring.py`) run on **every**
row in DuckDB in minutes for $0 and produce the ranking, tiers and explanations. The LLM only
sees rows that (a) passed a rules pre-filter and (b) have something a model adds value on —
usually a free-text description. Rough comparison for the 200K-row backfill:

| approach | cost |
|---|---|
| Sonnet 5.5 on every row, no caching | ≈ $1,050 |
| Haiku 5.5 on every row, no caching | ≈ $52 |
| Haiku 5.5 on 30% pre-filtered, cached + batched (**chosen**) | ≈ $3.46 |

If the full 11 GB is in the millions of rows, the first two lines scale linearly (10M rows ≈ $52K
on Sonnet); the chosen design scales with the number of *plausible* accounts instead. Inputs are
also trimmed (`extra` dropped, description ≤ 1,200 chars, lists ≤ 15 items), which caps the
per-company tokens no matter how wide the raw rows are.

## Caching and batching

- **Prompt caching** (`cache_control: ephemeral` on the system block in `llm/client.py`): system
  prompts are static and byte-identical across companies, so after the first call each subsequent
  call in a 5-minute window reads them at 0.1×. Verify with `usage.cache_read_tokens` in traces /
  the `cache_rd` column of `python -m llm.tracing summary` — a zero there means something is
  invalidating the prefix.
- **Message Batches API** (50% off): backfill and weekly refresh are unattended, so they belong in
  batches. `LLMClient.build_request()` returns exactly the `params` a batch request needs; results
  map back by `custom_id` (= company id). Interactive drafts stay on the synchronous API.
- Combined, the optimised design costs about two-thirds less than the naive one (backfill $55 → $18).

## Ceiling, alerts and what happens when it's hit

| control | value | mechanism |
|---|---|---|
| Expected monthly spend | ≈ $73 | this model |
| **Monthly ceiling** | **$150** | 2× expected (headroom for more reps / bigger refreshes); also above the un-optimised $102 so a caching regression alone doesn't trip it |
| Warn | $75 (50%) | Slack/email to owner |
| Page | $120 (80%) | investigate: cache hit rate, retry storms, volume spikes |
| Hard stop | $150 (100%) | LLM features switch off → **rules-only** |
| Daily spike alert | > $10.40 / day (3× normal) | catches runaway loops within a day |
| Per-run budget (backfill) | $83 | `LLMClient(budget_usd=83)` |
| Per-run budget (weekly refresh) | $7 | `LLMClient(budget_usd=7)` |
| Per-rep daily cap | $0.20 | protects against a script hammering the draft button |

Enforcement is layered:
1. **Per-run budget guard** (`llm/client.py`): before each call the client reserves the
   worst-case cost (input estimate + full `max_tokens` output at uncached prices); if
   spent + reserved + this call would exceed `budget_usd` it raises `BudgetExceeded` **before**
   spending. The pipeline catches it and continues **rules-only** for the remaining accounts:
   ranking, tiers, signals and explanations are unaffected; the `llm` block is simply `null` and
   `meta.llm` records how many calls were made and their cost.
2. **Monthly ceiling**: summed from traces (or the Anthropic Usage & Cost API) by a scheduled job;
   at 100% it sets the budget for new runs to 0 (rules-only) and disables the on-demand
   draft/brief buttons in the app until the owner raises the ceiling.
3. **Workspace spend limit** in the Anthropic Console set slightly above the ceiling, as the
   backstop if our own accounting is wrong.

Degrading to rules-only is safe by design: the LLM adds judgement and copy on top of a ranking
that already works without it.
