# Eval datasets

| file | what | use |
|---|---|---|
| `need_classifier.jsonl` | **Hand-labelled real companies** (target 25–30). *Not created yet* — the real dataset is not available. | The only file whose results are quality metrics. |
| `need_classifier.candidates.jsonl` | Unlabelled candidates drawn from real rows by `make_labelling_set` (`label: null`). | Input to the labelling session. |
| `smoke_synthetic.jsonl` | 10 **synthetic**, fictional companies (`"synthetic": true`, ids `syn-…`). | Proves the harness runs end to end. Never a quality metric. |

Never add invented companies to `need_classifier.jsonl`. Every case there must be a real row from the
dataset, labelled by a person.

## Case format (CONTRACT.md §8)

```json
{"case_id": "nc-001", "company": { /* canonical record, CONTRACT.md §1 */ },
 "label": "high|medium|low", "labeller": "initials", "rationale": "why, citing fields",
 "tags": ["edge:regulated-micro"]}
```

The harness only scores lines whose `label` is `high`, `medium` or `low`. Lines with `label: null` are ignored,
so you can label part of a file and still run it.

## 1. Draw candidates

```bash
python -m evals.make_labelling_set --input data/sample.parquet --n 30 \
    --out evals/datasets/need_classifier.candidates.jsonl
```

The script reads rows through `pipeline.ingest`, which gives it canonical records. If `pipeline.ingest` is
missing, it falls back to a basic DuckDB reader and prints a warning. It reservoir-samples a pool of 20k rows
(`--pool`), then picks cases in two steps:

1. **Edge-case quota:** up to 2 rows from each detected bucket. The tags are heuristic suggestions, so
   confirm or fix them while labelling:
   - `edge:regulated-micro`: a business of 10 or fewer people in a regulated industry (law, accounting,
     health, finance).
   - `edge:large-low-risk`: 1000 or more employees in a low-risk industry (construction, agriculture,
     hospitality).
   - `edge:sparse-record`: two or fewer of description, industry, employee count, domain, country and
     revenue are filled.
   - `edge:security-vendor`: a security company. It either already has what we sell or is a competitor.
   - `edge:government`: councils, agencies, departments.
2. **Strata:** the rest of the slots go round-robin over the industry × size band strata, with
   industries not yet represented picked first.

Use `--seed` to make the draw repeatable and `--start-id` to number new cases after an existing set.

Before the session, check the class balance. Aim for roughly 30% high, 40% medium and 30% low. If a class
has fewer than 6 cases, draw more candidates.

## 2. Label: rubric

The question is: **"Does this company plausibly need cybersecurity software now, enough that a rep should
spend a call on it?"** Answer it only from the fields in the record, the same information the classifier
sees. Do not look the company up on the web. If you have outside knowledge, mention it in the rationale but
do not use it for the label.

This rubric matches the intent of the need-classifier v2 prompt (`prompts/need-classifier/v2.md`). If you
change one, change the other.

| label | meaning | typical evidence |
|---|---|---|
| **high** | Clear, current need and the ability to buy. A rep should call this quarter. | Regulated industry (health, finance, legal, government, critical infrastructure) **and** handles sensitive data (patients, payments, PII) **and** enough size to have IT spend (about 20+ staff), **or** strong timing signals (rapid headcount growth, recent funding, many open roles, a large or exposed tech footprint). |
| **medium** | Real but weaker or partial need. Worth nurturing; call if capacity allows. | Some regulatory or data exposure but a small business; or a mid-size company in a moderate-risk industry; or a SaaS/e-commerce company with customer data but no timing signal. |
| **low** | No meaningful need now, or not a fit. | Micro business in a low-risk industry with no sensitive data; a security vendor (already has it, or a competitor); a record too sparse to justify a call; a dormant or shell entity. |

How to call the hard cases:

- **Regulated micro-business** (for example a 3-person law firm): usually **medium**. The need and the
  obligation are real, but the budget and buying process are small. Label it **high** only with an extra
  signal such as growth or a sensitive data volume.
- **Large company in a low-risk industry** (for example a 2000-person construction firm): usually
  **medium**. Scale alone creates attack surface (ransomware, payroll fraud) even without regulation.
- **Sparse record:** label what the record supports, which is usually **low**. Add the
  `edge:sparse-record` tag. Do not reward or punish the model for guessing.
- **Security vendors / MSSPs:** **low**. Write "competitor" or "already has it" in the rationale.
- **Government entities:** usually **high** at council or agency size (resident data, critical services,
  mandated frameworks). Tiny boards and committees are **medium**.
- **Unsure between two labels:** pick the lower one and write `unsure:<other label>` in the tags. Under-calls
  are reviewed separately (see `evals/README.md`).

**Labeller notes.** Fill in `labeller` (your initials) and a one- or two-sentence `rationale` that names the
fields you used, for example: "industry=Hospital & Health Care, 320 staff, telehealth in description → high".
Write your own tags in the same style. Do not change `company`. Do not look at the rules or LLM predictions
before labelling; the candidates file deliberately leaves them out.

## 3. Inter-rater check (recommended)

Have a second person label a random 10 cases blind. Compute Cohen's κ with
`evals.metrics.agreement(preds_a, preds_b)`, where each argument is a `{case_id: label}` dict.

- If κ < 0.6, the rubric is ambiguous. Discuss the disagreements, tighten the rubric above (and the
  prompt), then relabel.
- Record the final adjudicated label and add the tag `adjudicated` to those cases.

## 4. Publish

Append the labelled lines to `need_classifier.jsonl`. Keep the `case_id` values stable and never reuse
one. Commit the file. Every result file records the dataset sha256, so runs are only compared on the same
dataset version.
