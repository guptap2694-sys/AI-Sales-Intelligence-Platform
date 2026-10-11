# Architecture

## One-line summary

A batch pipeline turns a large company file into a ranked, explained list of target
accounts. Deterministic rules score **every** company cheaply. LLMs are spent only where
rules can't do the job (reading free text, writing for a human) and only on the accounts
a rep will actually work. A static app puts the result in front of the rep.

```
 data/sample.parquet (sample of 11 GB source)
        │  DuckDB streaming, 10k-row batches
        ▼
 pipeline/ingest.py ── schema.py: raw columns ─► canonical Company (alias table + fuzzy match)
        │
        ▼
 pipeline/signals.py   rule-based buying signals, each with quoted evidence
        │
        ▼
 pipeline/scoring.py   fit (ICP) + intent (signals) ─► priority, tier, segment, play, persona
        │  top-N heap (default 5,000)
        ▼
 pipeline/build.py ──(--llm-top N)──► llm/features/*  ──► llm/client.py ──► Anthropic API
        │                               need-classifier (Haiku)      │
        │                               account-brief   (Sonnet)     ├─► traces/llm_calls.jsonl (+ SQLite)
        │                               outreach-draft  (Sonnet)     └─► budget guard → rules-only
        ▼
 app/data/accounts.json ──► app/ (static SPA)  ──scripts/bundle_app.py──► dist/index.html (hosted)

 prompts/<id>/vN.md ──► llm/registry.py     skills/*/SKILL.md ──► any agent (Claude Code, Cursor, API)
 evals/datasets/*.jsonl ──► evals/run_evals.py ──► evals/results/ (JSON per run + latest.md diff)
```

The interface contract between these pieces is [`CONTRACT.md`](CONTRACT.md).

## Components

| Component | Job | Key decisions |
|---|---|---|
| `pipeline/schema.py` | Map arbitrary source columns to one canonical record | The only module that knows raw column names. Alias table (Firmable/Apollo/ZoomInfo/Crunchbase/generic naming) + difflib fuzzy match ≥ 0.86, manual overrides win. Unmapped columns are kept in `extra`, never dropped. A new data vendor means changing aliases, not signals. |
| `pipeline/ingest.py` | Load any size file; `profile` command | DuckDB SQL instead of pandas, so an 11 GB file streams in constant memory and `--where`/`--sample` push down to the scan. CSV read as text (postcodes keep leading zeros). |
| `pipeline/signals.py` | Detect cybersecurity buying signals | Six categories (regulatory, data sensitivity, attack surface, growth, maturity gap, timing). Every signal quotes the field that fired it (the rep sees *why*). Vocabulary in one `SIGNAL_CONFIG` dict. Missing field ⇒ no signal, never a crash. Australian obligations (Privacy Act/NDB, CPS 234, SOCI, Essential Eight, Cyber Security Act 2024) are named explicitly. See [RESEARCH.md](RESEARCH.md). |
| `pipeline/scoring.py` | Fit, intent, priority, tier, play, persona | Fit and intent kept **separate** (as 6sense/HubSpot do) so a leader can re-weight them. Intent is capped per category (caps sum to 100) so one noisy category can't dominate. All weights in `SCORING_CONFIG` with a version string (`rules-v1`) stamped into the output. |
| `pipeline/build.py` | Orchestrate; write `accounts.json` | Keeps only the top N in a heap (memory-bounded). Optional LLM step on the top `--llm-top` accounts; one failed account never fails the build. |
| `llm/registry.py` | Versioned prompts | Prompts are files (`prompts/<id>/vN.md`) with YAML front-matter (model, max_tokens, output format, changelog). sha256 of the file goes into every trace, so a result can be tied to the exact prompt text. |
| `llm/client.py` | The one way to call a model | Retries with backoff, JSON validation (`parse_error` status, not an exception), prompt caching on the system prompt, per-run `budget_usd` guard, `fake` provider for tests only. |
| `llm/tracing.py` | Observability | One JSONL line per call: request, response, model, prompt version + hash, tokens, latency, cost, decision, status, entity id, eval case id. `python -m llm.tracing summary` gives cost / p50 / p95 / error rate per feature × prompt version × model. |
| `llm/cost.py` | Cost model | Price table + `python -m llm.cost estimate` (tokens × volume × frequency). See [COST_MODEL.md](COST_MODEL.md). |
| `evals/` | Measure the classifier | Hand-labelled set, rules baseline vs each prompt version, business metrics (high-class precision, under-call rate) plus macro-F1 and Wilson intervals, diff vs previous run, CI-style regression gate. See [evals/README.md](../evals/README.md). |
| `skills/` | Reusable agent workflows | `account-scoring` and `outreach-draft` packaged as versioned `SKILL.md` files any agent can load. |
| `app/` | What the rep uses | Static, no build step, no backend. See below. |

## Rules vs LLM split

The guiding question for each step: *is the input structured and the logic expressible,
and does the result need to be auditable?* If yes, rules.

| Task | Engine | Why |
|---|---|---|
| Column mapping, normalisation | Rules | Deterministic, testable, runs on millions of rows for free. |
| Regulated industry, size band, geography, funding/hiring recency | Rules | Structured fields with clear thresholds. A rep and a sales leader must be able to audit and tune them. An LLM adds cost and variance, nothing else. |
| Fit / intent / priority / tier | Rules | Must be explainable ("+15 regulatory: CPS 234") and stable between runs, or reps stop trusting the list. Also must be re-weightable live in the browser. |
| "Does this company need security *now*?" from free-text descriptions | **LLM (Haiku)** | Descriptions carry the strongest signal ("patient portal", "processes payments for councils") and keyword rules miss paraphrase and negation. Classification on a short input → cheapest model. Evaluated against labels. |
| Account brief ("why now / risk / angle") | **LLM (Sonnet)** | Synthesis + judgement for a human reader. Only for top accounts. |
| First-touch outreach draft | **LLM (Sonnet)** | Writing quality matters and directly affects reply rate. Grounded in the signals' evidence; prompt forbids invented facts. |

The LLM is an **overlay**, not the backbone: if the key is missing, the budget is hit or
a call fails, every account still has a rules score, and the app says "rules-only".

## Cost model (summary)

Full math in [COST_MODEL.md](COST_MODEL.md) and `python -m llm.cost estimate`.

- Never send the whole file to an LLM. Rules pre-filter to the top N; the classifier runs on those, briefs/outreach on tier A/B only.
- Haiku for classification, Sonnet for writing. Opus is not used: no task here needs it.
- The classifier's ~1.7k-token system prompt is cached, so per-company cost is mostly the ~400-token company record. Backfills go through the Batch API (−50%).
- Production ceiling: **$150/month**, alerts at 50%/80%, hard stop ⇒ rules-only. Per-run `budget_usd` in the client enforces it in code, not by policy.

## The app

- Static HTML/CSS/JS reading `accounts.json`. `scripts/bundle_app.py` inlines everything into one `dist/index.html` so it can be hosted anywhere (published as a claude.ai Artifact; works on GitHub Pages / S3 as is).
- Fit and intent breakdowns ship with each account, so the ICP tuner re-ranks **in the browser** without a backend.
- Filters live in the URL hash (shareable views); shortlist and tuner settings in `localStorage`.

## Trade-offs and what I'd change for production

| Decision | Trade-off | Production version |
|---|---|---|
| Batch, precomputed LLM output | No live "draft for this account" button in the hosted app (a static page can't hold an API key) | Small backend endpoint behind auth calling `llm.features.outreach.draft`, same tracing and budget guard |
| Work on a sample, top 5,000 in the app | Not every company is browsable | Pipeline already streams the full file; serve accounts from a DB (DuckDB/Postgres) with server-side filtering |
| Rules weights hand-set from research | Not yet fitted to outcomes | Fit weights with logistic regression on closed-won/lost from the CRM once outcomes exist; keep the rules as features so it stays explainable |
| Single snapshot | "Timing" signals are only as fresh as the file | Diff snapshots weekly; signal = *change* (headcount jump, new funding, new tech) |
| JSONL traces | Fine for a prototype, no dashboards | Ship the same schema to a warehouse / OpenTelemetry; alert on cost and parse-error rate |
| Eval set of 25–30 | Wide confidence intervals (reported) | Grow the set from rep feedback ("wrong tier" button) into a few hundred labels |
