---
name: outreach-draft
description: >-
  Draft a short, signal-grounded first-touch email (and optional 3–4 bullet account brief)
  for a scored cybersecurity sales account, tailored to the recipient persona. Uses
  prompts/outreach-draft and prompts/account-brief on Claude Sonnet 5.5. Use when a rep or
  agent asks to write, draft or personalise outreach, a cold email, an opener, or a call
  prep brief for a specific account.
version: 1.0.0
triggers:
  - "draft an email / outreach / opener to <company>"
  - "personalise outreach for this account for the CFO"
  - "give me a call-prep brief for <company>"
  - "why should I call <company> now?"
---

# Outreach draft skill

## When to trigger
- A rep (or agent acting for one) wants a first-touch email or call-prep brief for **one specific, already-scored account**.
- Ideally the account is tier A/B (see `skills/account-scoring`). For unscored companies, run account-scoring first so the draft has signals to ground on.
Do **not** trigger for bulk sequences to unscored lists, follow-ups that need thread history, or anything that must be sent without human review.

## Inputs
```json
{"company": {"...canonical record, contract §1...": "", "persona": "optional, from scoring"},
 "signals": [{"id": "str", "label": "str", "category": "str", "evidence": "str"}],
 "persona": "optional override, e.g. 'CFO', 'Head of IT / IT Manager', 'Practice Manager'",
 "need": {"need_level": "high", "confidence": 0.8, "drivers": [], "rationale": "..."}}
```
`need` (classifier output) is optional and used by the brief only.

## Outputs
Outreach (`llm.features.outreach.draft`):
```json
{"subject": "<= 8 words", "body": "<= 120 words, one CTA", "persona": "str",
 "cited_signals": ["signal ids / fields used"], "word_count": 0,
 "trace_id": "uuid", "status": "ok|error|parse_error"}
```
Brief (`llm.features.account_brief.brief`): plain text, 3–4 bullets — `**Why now:**`, `**Risk:**`, `**Angle:**`, optional `**Ask:**`.

## Steps
1. Get the account with its `signals` and `persona` (from `pipeline/scoring.py` output / `accounts.json`).
2. Trim input (`llm.features.trim_company`) — drops `extra`, truncates description; keeps tokens ~450.
3. (Optional) `brief(company, signals, need)` → rep-facing bullets.
4. `draft(company, signals, persona)` → subject/body grounded in 1–2 signals.
5. Post-checks in code: JSON schema, word count (> 120 words → `warning`), status. Failures return empty subject/body.
6. Human review: the rep edits and sends. Nothing is auto-sent.

## Dependent prompts
| prompt | path | version | model |
|---|---|---|---|
| outreach-draft | `prompts/outreach-draft/v1.md` | v1 | claude-sonnet-5-5 |
| account-brief | `prompts/account-brief/v1.md` | v1 | claude-sonnet-5-5 |

## Rules vs LLM split
| Rules / code | LLM |
|---|---|
| Which accounts get drafts (tier A/B, budget) | Choosing the most relevant 1–2 signals |
| Persona default (scoring `persona`) | Persona-appropriate framing and wording |
| Signals + evidence (the only facts allowed) | Writing a specific, honest, ≤ 120-word email |
| Word-count and schema checks, budget guard | Subject line, one clear CTA |

## Guardrails
- No invented facts: only fields/signals in the input; no fake customers, stats, incidents or tool claims.
- No fear-mongering or unjustified urgency; plain professional tone; no placeholders/signature (appended by the app).
- ≤ 120 words, exactly one CTA, subject ≤ 8 words; checked in code.
- Sonnet 5.5 safety classifiers can decline security-themed content (`stop_reason: refusal`, category `cyber`). The client opts into the server-side fallback and reports any refusal as `status="error"` — never retry by rewording to evade it.
- Budget guard: `BudgetExceeded` → UI shows the rules-based signals and the brief/draft button is disabled.
- `provider="fake"` returns placeholder text for tests only.

## How to run
CLI:
```bash
python -m llm.features.outreach --company-json account.json --persona "CFO"       # needs ANTHROPIC_API_KEY
python -m llm.features.account_brief --company-json account.json
python -m llm.features.outreach --company-json account.json --provider fake       # offline dry run
python -m llm.tracing summary                                                      # cost / latency / errors
```
Python:
```python
from llm.client import LLMClient
from llm.features.outreach import draft
from llm.features.account_brief import brief
client = LLMClient(provider="anthropic", budget_usd=2.0)
email = draft(account, account["signals"], persona="Practice Manager", client=client)
bullets = brief(account, account["signals"], need=account.get("llm"), client=client)
```
Raw API from any agent (Claude Code, Cursor, plain HTTP) — render `prompts/outreach-draft/v1.md` yourself (replace `{{persona}}`, `{{sender_context}}`, `{{company_json}}`, `{{signals_json}}`):
```bash
curl https://api.anthropic.com/v1/messages \
  -H "x-api-key: $ANTHROPIC_API_KEY" -H "anthropic-version: 2023-06-01" -H "content-type: application/json" \
  -d '{"model": "claude-sonnet-5-5", "max_tokens": 500,
       "thinking": {"type": "between_tools"}, "output_config": {"effort": "low"},
       "system": [{"type": "text", "text": "<system section of v1.md>", "cache_control": {"type": "ephemeral"}}],
       "messages": [{"role": "user", "content": "<rendered user section>"}]}'
```
Parse `{"subject", "body", "cited_signals"}` from the text; reject drafts over 120 words.

## Worked example (ILLUSTRATIVE — hand-written to show the target quality, not real model output)
Input (synthetic):
```json
{"company": {"id": "ex-42", "name": "Coastal Physio Group", "industry": "Health, Wellness & Fitness",
  "employee_count": 65, "state": "QLD", "employee_growth_pct": 22,
  "description": "Eight physiotherapy clinics with online booking, telehealth sessions and digital patient notes."},
 "signals": [{"id": "regulated_industry", "category": "regulatory", "label": "Health provider", "evidence": "description mentions 'patient notes'"},
  {"id": "headcount_growth", "category": "growth", "label": "Headcount growth 22%", "evidence": "employee_growth_pct = 22"}],
 "persona": "Practice Manager"}
```
Invocation: `python -m llm.features.outreach --company-json account.json --persona "Practice Manager"`

Example output:
```json
{"subject": "Patient notes across eight clinics",
 "body": "Hi there,\n\nCoastal Physio has grown to eight clinics with telehealth and digital patient notes — and about 22% more staff this year. Every new login and device is another way into those records.\n\nWe help practice teams keep patient data protected and audit-ready without adding IT work: staff accounts, laptops and email secured from one console, set up in days.\n\nWould a 15-minute call next week be useful to see whether it fits how your clinics run?",
 "persona": "Practice Manager", "cited_signals": ["regulated_industry", "headcount_growth"], "word_count": 78,
 "trace_id": "…", "status": "ok"}
```
Example brief:
```
- **Why now:** Headcount up 22% (`employee_growth_pct`) across eight clinics — onboarding new staff and devices quickly.
- **Risk:** Digital patient notes and telehealth sessions (`description`) mean regulated health data on many endpoints and accounts.
- **Angle:** Simple, centrally managed identity + endpoint protection with audit evidence; approach the Practice Manager.
- **Ask:** "How do you set up access for a new physio on day one today?"
```
