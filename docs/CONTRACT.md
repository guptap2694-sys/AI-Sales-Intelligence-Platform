# Interface Contract

Single source of truth for how the pieces talk to each other. Every module codes
against this file. The real dataset's columns are **not yet known**, so all
downstream code depends only on the *canonical schema* below; the one place that
knows about raw column names is `pipeline/schema.py` (`COLUMN_ALIASES`).

## Repo layout

```
pipeline/            Python: ingest → normalise → signals → scoring → build accounts.json
  schema.py          Canonical Company fields + raw-column alias mapping
  ingest.py          DuckDB loader (parquet/csv/json, any size, streaming) → canonical rows
  signals.py         Rule-based buying/need signals (deterministic, explainable)
  scoring.py         ICP fit + intent → priority score, tier, segment, play
  build.py           CLI: python -m pipeline.build --input data/sample.parquet --out app/data/accounts.json [--llm-top N]
llm/                 Python: every LLM call goes through here
  client.py          LLMClient (providers: anthropic | fake), auto-traces every call
  tracing.py         Tracer → traces/llm_calls.jsonl (+ optional SQLite mirror)
  registry.py        load_prompt(prompt_id, version) from prompts/
  cost.py            PRICING table, cost_usd(model, in_tok, out_tok), volume estimator
  features/
    need_classifier.py   classify(company, version) -> NeedAssessment   (core LLM feature, evaluated)
    account_brief.py     brief(company, signals, version) -> str
    outreach.py          draft(company, signals, persona, version) -> {subject, body, persona}
prompts/<prompt_id>/v1.md, v2.md   YAML front-matter + system + user template
skills/<skill>/SKILL.md            account-scoring, outreach-draft
evals/
  datasets/need_classifier.jsonl   hand-labelled cases (20–30)
  run_evals.py                     one command: python -m evals.run_evals
  results/                         timestamped JSON + latest.md comparison
app/                 Static SPA (no build step), reads app/data/accounts.json
scripts/bundle_app.py   Inlines CSS/JS/data into dist/index.html (single-file, hostable anywhere)
docs/                PLANNING, ARCHITECTURE, RESEARCH, COST_MODEL, HOW_I_BUILD
tests/               pytest; tests/fixtures/ holds SYNTHETIC data for code tests only
```

Python 3.11+. Deps: `duckdb`, `pyarrow`, `pyyaml`, `anthropic`, `pytest`. Run everything from repo root as modules (`python -m ...`).

## 1. Canonical company record (dict, all keys present, unknown = None / [])

| field | type | notes |
|---|---|---|
| id | str | stable; raw id column, else hash of domain/name |
| name | str | |
| domain | str\|None | lowercased, no scheme/www |
| description | str\|None | free text (best LLM input) |
| industry | str\|None | top-level industry label as in source |
| sub_industry | str\|None | |
| industry_code | str\|None | NAICS/SIC/ANZSIC if present |
| employee_count | int\|None | if only a range is given, use the midpoint |
| employee_range | str\|None | raw range text |
| revenue_usd | float\|None | |
| revenue_range | str\|None | |
| founded_year | int\|None | |
| country, state, city, postcode | str\|None | |
| linkedin_url, website | str\|None | |
| technologies | list[str] | tech stack if present |
| keywords | list[str] | tags/specialties if present |
| employee_growth_pct | float\|None | any headcount growth field |
| funding_total_usd | float\|None | |
| last_funding_date | str\|None | ISO date |
| job_postings | int\|None | open roles count if present |
| extra | dict | every unmapped raw column, untouched (so nothing is lost) |

Derived in `pipeline` (added to the record): `size_band` ∈ {"1-10","11-50","51-200","201-500","501-1000","1001-5000","5000+", "unknown"}, `company_age_years`.

## 2. Signal (dict)

```json
{"id": "regulated_industry", "label": "Regulated industry (healthcare)", "category": "regulatory",
 "weight": 15, "evidence": "industry = 'Hospital & Health Care'"}
```
`category` ∈ `regulatory | data_sensitivity | attack_surface | growth | maturity_gap | timing`.
Every signal MUST carry human-readable `evidence` quoting the field that triggered it.

## 3. accounts.json (pipeline output, app input)

```json
{
  "meta": {
    "generated_at": "ISO", "source": "data/sample.parquet", "synthetic": false,
    "rows_total": 200000, "rows_included": 5000,
    "scoring_version": "rules-v1",
    "weights": {"fit": 0.5, "intent": 0.5},
    "llm": {"enabled": true, "prompt_versions": {"need-classifier": "v2"}, "calls": 300, "cost_usd": 0.41}
  },
  "facets": {"industries": [], "countries": [], "states": [], "size_bands": [], "signal_ids": [], "segments": []},
  "accounts": [
    {
      "id": "...", "name": "...", "domain": "...", "description": "...",
      "industry": "...", "country": "...", "state": "...", "city": "...",
      "employee_count": 120, "size_band": "51-200", "revenue_usd": null, "founded_year": 2015,
      "technologies": [], "linkedin_url": null, "website": null,
      "fit_score": 72, "intent_score": 64, "priority_score": 68, "tier": "A",
      "fit_breakdown": {"size": 25, "industry": 30, "geo": 10, "data": 7},
      "intent_breakdown": {"regulatory": 15, "growth": 20, "attack_surface": 10, "...": 0},
      "segment": "Mid-market Healthcare", "play": "compliance",
      "persona": "Head of IT / IT Manager",
      "signals": [ /* Signal */ ],
      "llm": null | {
        "need_level": "high|medium|low", "confidence": 0.8, "rationale": "...",
        "brief": "...", "outreach": {"subject": "...", "body": "...", "persona": "..."},
        "model": "...", "prompt_versions": {"need-classifier": "v2", "account-brief": "v1", "outreach-draft": "v1"}
      }
    }
  ]
}
```
Tiers: A ≥ 75, B ≥ 60, C ≥ 40, else D (on priority_score). `play` ∈ `compliance | scale_up | exposed_surface | modernise | nurture`.
The app recomputes `priority = wf*fit + wi*intent` client-side when the user moves the ICP weight sliders, so fit/intent and their breakdowns must be present.

## 4. Prompt files — `prompts/<prompt_id>/<version>.md`

```markdown
---
id: need-classifier
version: v2
model: claude-haiku-5-5
max_tokens: 400
temperature: 0
output: json            # json | text
changelog: "v2: added explicit negative examples for low-risk micro businesses"
---
<!-- system -->
...system prompt...
<!-- user -->
...user template with {{company_json}} {{signals_json}} placeholders...
```
`llm.registry.load_prompt(id, version=None)` → `Prompt(id, version, model, max_tokens, temperature, output, system, user_template, sha256)`; `version=None` = highest vN. `prompt.render(**vars) -> str`.

Prompt ids: `need-classifier` (v1, v2), `account-brief` (v1), `outreach-draft` (v1).
Model policy: Haiku 5.5 (`claude-haiku-5-5`) for classification; Sonnet 5.5 (`claude-sonnet-5-5`) for brief + outreach.

## 5. LLM client

```python
from llm.client import LLMClient, LLMResult
client = LLMClient(provider="anthropic" | "fake", tracer=None, run_id=None)   # provider default: "anthropic" if ANTHROPIC_API_KEY set else raise
res: LLMResult = client.run(prompt: Prompt, variables: dict, entity_id: str | None = None, decision_key: str | None = None)
# LLMResult: text, parsed (dict|None for output=json), model, input_tokens, output_tokens, latency_ms, cost_usd, trace_id
```
`fake` provider is deterministic and exists ONLY for unit tests / dry runs; its outputs must never be reported as eval results.

## 6. Trace record — one JSON object per line in `traces/llm_calls.jsonl`

```json
{"trace_id": "uuid", "run_id": "uuid|null", "ts": "ISO8601", "feature": "need-classifier",
 "prompt_id": "need-classifier", "prompt_version": "v2", "prompt_sha256": "...",
 "provider": "anthropic", "model": "claude-haiku-5-5",
 "request": {"system": "...", "messages": [...], "max_tokens": 400, "temperature": 0},
 "response": {"text": "...", "parsed": {...}, "stop_reason": "end_turn"},
 "usage": {"input_tokens": 812, "output_tokens": 95, "cache_read_tokens": 0, "cache_write_tokens": 0},
 "latency_ms": 640, "cost_usd": 0.00129, "entity_id": "company id",
 "decision": "high", "status": "ok|error|parse_error", "error": null, "eval_case_id": null}
```

## 7. Need classifier output (core evaluated feature)

```json
{"need_level": "high|medium|low", "confidence": 0.0-1.0,
 "drivers": ["regulatory", "data_sensitivity", ...], "rationale": "≤ 2 sentences citing fields"}
```

## 8. Eval case — `evals/datasets/need_classifier.jsonl`

```json
{"case_id": "nc-001", "company": { /* canonical record */ }, "label": "high|medium|low",
 "labeller": "human", "rationale": "why", "tags": ["edge:micro-business"]}
```
Systems evaluated: `rules` (pipeline scoring mapped to a need level — always runs, no key needed) and `llm:<version>`.
Metrics: per-class precision/recall/F1, macro-F1, accuracy, "high"-class precision@k, confusion matrix, cost & latency per case.
Results: `evals/results/need-classifier/<system>_<timestamp>.json`, and `evals/results/latest.md` comparing to the previous run of each system.
