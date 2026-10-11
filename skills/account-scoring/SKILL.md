---
name: account-scoring
description: >-
  Rank companies by likelihood of needing cybersecurity software. Deterministic rules
  (pipeline/signals.py + pipeline/scoring.py) score every company; an LLM need-classifier
  (prompts/need-classifier) adds a judgement from free-text descriptions for accounts that
  pass the rules pre-filter. Use when asked to score, rank, prioritise, tier or qualify
  accounts/companies/leads for the cybersecurity sales team, or to explain why an account
  scored the way it did.
version: 1.0.0
triggers:
  - "score / rank / prioritise these companies (accounts, leads)"
  - "which accounts should reps work first"
  - "is <company> a good fit / does it need cybersecurity"
  - "explain this account's tier / score"
  - "re-score after the dataset refresh"
---

# Account scoring skill

## When to trigger
- A user or agent asks to score, rank, tier, qualify or prioritise companies for the cybersecurity sales motion.
- A single company needs a need assessment ("would Acme need us?").
- A score needs explaining (every point traces to a signal with `evidence`).
Do **not** trigger for writing outreach (use `skills/outreach-draft`) or for generic market research.

## Inputs
Canonical company record(s) — `docs/CONTRACT.md` §1. Minimum useful input:

```json
{"id": "str", "name": "str", "description": "str|null", "industry": "str|null",
 "employee_count": "int|null", "country": "str|null", "...": "all other §1 fields optional (null/[] if unknown)"}
```
Bulk input is a dataset file (parquet/csv/json) ingested by `pipeline/ingest.py`, which maps raw columns via `pipeline/schema.py`.

## Outputs
Per account (contract §3 `accounts[]`): `fit_score`, `intent_score`, `priority_score` (0–100), `tier` (A ≥ 75, B ≥ 60, C ≥ 40, else D), `fit_breakdown`, `intent_breakdown`, `segment`, `play`, `persona`, `signals[]` (each with `evidence`), and optionally `llm`:

```json
{"need_level": "high|medium|low", "confidence": 0.0, "drivers": ["regulatory", "..."],
 "rationale": "<= 2 sentences citing fields", "trace_id": "uuid", "status": "ok|error|parse_error",
 "model": "claude-haiku-5-5", "prompt_version": "v2"}
```

## Steps
1. **Ingest + normalise** (rules, no LLM): `pipeline/ingest.py` → canonical rows (`pipeline/schema.py`).
2. **Signals** (rules): `pipeline/signals.py` emits explainable signals (`regulatory`, `data_sensitivity`, `attack_surface`, `growth`, `maturity_gap`, `timing`), each quoting the triggering field.
3. **Score** (rules): `pipeline/scoring.py` → fit + intent → priority, tier, segment, play, persona. This runs on 100% of rows and is the source of truth for ranking.
4. **Pre-filter** (rules): only accounts that pass (e.g. tier A/B/C, or have a description and size ≥ threshold) go to the LLM. Do not send the whole dataset to a model.
5. **LLM need classification** (judgement): `llm.features.need_classifier.classify(company, signals)` on the pre-filtered set, using `prompts/need-classifier/v2.md`. It reads the free-text description that rules can only keyword-match.
6. **Merge**: attach `llm` to the account; the LLM result is shown beside the rules score (it can flag disagreements for review) — it does not silently overwrite the rules tier.
7. **Trace + cost check**: every call is in `traces/llm_calls.jsonl`; run `python -m llm.tracing summary`.

## Dependent prompts
| prompt | path | version | model |
|---|---|---|---|
| need-classifier | `prompts/need-classifier/v2.md` | v2 (default) | claude-haiku-5-5 |
| need-classifier (baseline) | `prompts/need-classifier/v1.md` | v1 | claude-haiku-5-5 |

Pin a version with `version="v1"` / `--version v1`. Evaluated by `python -m evals.run_evals` (`evals/datasets/need_classifier.jsonl`).

## Rules vs LLM split
| Rules (deterministic, all rows, free) | LLM (judgement, pre-filtered rows, metered) |
|---|---|
| Field parsing, size bands, industry mapping | Reading free-text descriptions |
| Regulated-industry, growth, funding, tech-stack signals | "Does this business actually handle sensitive data?" |
| Fit/intent/priority score, tier, play | Need level + calibrated confidence + cited rationale |
| Explanations (signal evidence) | Catching rules false positives/negatives (e.g. a "health" café) |

## Guardrails
- Missing data ≠ low need: absent fields are unknown; the prompt lowers confidence instead.
- The model may only cite fields present in the input; rationale must name fields.
- Inputs are trimmed (`llm.features.trim_company`): `extra` dropped, description ≤ 1,200 chars.
- Budget: `LLMClient(budget_usd=...)` raises `BudgetExceeded` before overspending → caller continues **rules-only**.
- Failures (`status` = `error` / `parse_error`) leave `need_level=None`; rules score stands.
- `provider="fake"` outputs are placeholders for tests/dry runs — never present them as assessments or eval results.

## How to run
CLI (from repo root):
```bash
# whole pipeline; LLM for the top N accounts (see pipeline/build.py)
python -m pipeline.build --input data/sample.parquet --out app/data/accounts.json --llm-top 300

# one company through the classifier
python -m llm.features.need_classifier --company-json company.json            # needs ANTHROPIC_API_KEY
python -m llm.features.need_classifier --company-json company.json --version v1 --provider fake

# observability
python -m llm.tracing summary
python -m llm.cost estimate
```

Python (any agent with the repo checked out — Claude Code, Cursor, scripts):
```python
from llm.client import LLMClient
from llm.features.need_classifier import classify
client = LLMClient(provider="anthropic", budget_usd=5.0, run_id="adhoc-2026-10-11")
result = classify(company_dict, signals_list, version="v2", client=client)
```

Raw API (any agent without this repo — copy the prompt file): load `prompts/need-classifier/v2.md`, send the `<!-- system -->` section as a cached system block and the rendered `<!-- user -->` section as the user message:
```bash
curl https://api.anthropic.com/v1/messages \
  -H "x-api-key: $ANTHROPIC_API_KEY" -H "anthropic-version: 2023-06-01" -H "content-type: application/json" \
  -d '{"model": "claude-haiku-5-5", "max_tokens": 400, "thinking": {"type": "disabled"},
       "system": [{"type": "text", "text": "<system section of v2.md>", "cache_control": {"type": "ephemeral"}}],
       "messages": [{"role": "user", "content": "Assess this company.\n\n<company>\n{...trimmed company JSON...}\n</company>\n\n<rule_signals>\n[...]\n</rule_signals>"}]}'
```
Parse the reply as JSON (strip code fences if present) and validate `need_level` ∈ {high, medium, low}. Log the prompt id, version and sha256 with the result.

## Worked example (ILLUSTRATIVE — hand-written, not real model output)
Input `company.json` (synthetic):
```json
{"company": {"id": "ex-42", "name": "Coastal Physio Group", "industry": "Health, Wellness & Fitness",
  "employee_count": 65, "country": "Australia", "state": "QLD", "employee_growth_pct": 22,
  "description": "Eight physiotherapy clinics with online booking, telehealth sessions and digital patient notes."},
 "signals": [{"id": "regulated_industry", "label": "Health provider", "category": "regulatory",
  "evidence": "description mentions 'patient notes'"},
  {"id": "headcount_growth", "label": "Headcount growth 22%", "category": "growth", "evidence": "employee_growth_pct = 22"}]}
```
Invocation: `python -m llm.features.need_classifier --company-json company.json`

Example output (illustrative of the shape and reasoning we expect):
```json
{"need_level": "high", "confidence": 0.82, "drivers": ["regulatory", "data_sensitivity", "growth"],
 "rationale": "A 65-person multi-site health provider storing digital patient notes and running telehealth (`description`), growing 22% (`employee_growth_pct`).",
 "evidence_fields": ["description", "employee_count", "employee_growth_pct"],
 "trace_id": "…", "status": "ok", "model": "claude-haiku-5-5", "provider": "anthropic", "prompt_version": "v2"}
```
