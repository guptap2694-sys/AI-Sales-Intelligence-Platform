# Perimeter Radar: sales intelligence for a cybersecurity vendor

**Which of thousands of businesses need cybersecurity help right now, and who do we call first?**

Perimeter Radar turns raw company data into a ranked, explained target list for a security
sales team. Deterministic rules score every company on **ICP fit** and **buying signals**
(regulatory exposure, sensitive data, attack surface, growth, security-maturity gaps, timing).
LLMs add a need assessment, an account brief and a first-touch email for the accounts a rep
will actually work.

- **Hosted app (preview):** https://claude.ai/artifact/A1YJ7TaXcQQ5yCG91Fgdr1. It currently runs on **synthetic demo data** while the real dataset sample is being added. See [Status](#status).
- **Docs:** [Planning](docs/PLANNING.md) · [Research](docs/RESEARCH.md) · [Architecture](docs/ARCHITECTURE.md) · [Cost model](docs/COST_MODEL.md) · [Interface contract](docs/CONTRACT.md) · [How I build](docs/HOW_I_BUILD.md)

## What a rep can do

| Use case | Where |
|---|---|
| "Who do I call first in my territory?" Ranked list with country/state, industry, size, tier, signal and play filters, shareable via the URL | Priorities |
| "Why is this account hot?" Score formula, fit/intent breakdown, every signal with the field evidence that triggered it | Account drawer |
| "What do I say?" Recommended persona and play, AI need assessment, brief and outreach draft (copy / open in mail) | Account drawer |
| "Tune the ICP." Re-weight fit vs intent and each signal category; see accounts move into tier A live | ICP tuner |
| "Where is the opportunity?" Tier mix, territory breakdown, industry × size heatmap, signal coverage, all click-to-filter | Market |
| "Get it into the CRM." Shortlist and CSV export with scores, signals, persona and draft | Export |

## Repository map

```
pipeline/   ingest (DuckDB, schema-adaptive) → signals → scoring → accounts.json
llm/        client (retries, caching, budget guard), tracing, prompt registry, cost model, features
prompts/    versioned prompts: need-classifier v1/v2, account-brief v1, outreach-draft v1
skills/     account-scoring/SKILL.md, outreach-draft/SKILL.md
evals/      labelling protocol, harness (rules vs prompt versions), metrics, results
app/        static single-page app; scripts/bundle_app.py → dist/index.html
traces/     llm_calls.jsonl, one line per model call
docs/       planning, research, architecture, cost model, reflection
tests/      pytest (fixtures are synthetic, for code tests only)
```

## Run it

```bash
make install                                   # duckdb, pyarrow, pyyaml, anthropic, pytest
make test                                      # full test suite
make profile INPUT=data/sample.parquet         # inspect columns + detected mapping (run first on new data)
make build   INPUT=data/sample.parquet         # → app/data/accounts.json (top 5,000, rules only)
ANTHROPIC_API_KEY=... make build LLM_TOP=300   # + AI need/brief/outreach for the top 300, traced and budget-capped
make eval                                      # rules vs need-classifier v1 vs v2 on the labelled set → evals/results/latest.md
make bundle                                    # → dist/index.html (single file, host anywhere)
python -m llm.cost estimate                    # production cost model
python -m llm.tracing summary                  # cost / latency / error rate per feature × prompt version × model
cd app && python -m http.server                # or open the app unbundled
```

## Status

| Piece | State |
|---|---|
| Pipeline, signals, scoring | Built and tested on synthetic fixtures; column mapping still to be confirmed against the real dataset |
| LLM layer, prompts, skills, tracing, cost model | Built and tested with a fake provider; **no real model calls yet** (no API key in the build environment) |
| Eval harness | Built and tested; **no quality numbers yet**: the 25–30 hand-labelled cases will be drawn from the real dataset |
| App | Built; preview runs on synthetic data, and the AI text in it is labelled placeholder text |

The provided dataset (11 GB) is being sampled locally and committed as `data/sample.parquet`.
Once it lands: profile → confirm mapping → build → label eval set → run evals → republish the app.
