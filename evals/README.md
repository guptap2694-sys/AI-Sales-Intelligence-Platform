# Evals: need classifier

The need classifier answers one question with high, medium or low: does this company need cybersecurity
software now? It is the core LLM feature and the main thing this harness evaluates. Two kinds of system are
compared:

- `rules`: `pipeline.scoring.need_level_from_rules(account)`. It is deterministic, needs no API key and
  always runs.
- `llm:<version>`: `llm.features.need_classifier.classify(...)` with `prompts/need-classifier/<version>.md`.

## Run

```bash
python -m evals.run_evals                      # rules + every prompts/need-classifier/vN.md
make eval                                      # same, if the Makefile target exists
python -m evals.run_evals --systems rules,llm:v2 --budget-usd 0.50 --concurrency 8
python -m evals.run_evals --provider fake      # dry run: LLM plumbing only, results tagged FAKE
```

| flag | default | notes |
|---|---|---|
| `--dataset` | `evals/datasets/need_classifier.jsonl` | If the file is missing or has no labelled cases, the run falls back to `smoke_synthetic.jsonl` and prints a loud warning. |
| `--systems` | `rules` + `llm:vN` for each prompt file found | A comma-separated list. |
| `--provider` | `anthropic` | Without `ANTHROPIC_API_KEY`, LLM systems are reported as `SKIPPED: no ANTHROPIC_API_KEY`. With `fake`, LLM runs are saved as `*_FAKE.json`, are never used as baselines, and their numbers are never printed or tabulated. |
| `--budget-usd` | none | Once this much has been spent, no further LLM calls are made. Cases not run are excluded from the metrics, and the run notes how many. |
| `--concurrency` | 4 | Number of threads for LLM calls. |
| `--results-dir` | `evals/results` | |
| `--gate-macro-f1-drop` | 0.05 | Exit code 1 if macro-F1 falls by more than this compared with the previous non-FAKE run of the same system on the same dataset hash. |
| `--no-gate` | off | Still reports regressions but always exits 0. |

### Outputs

- `evals/results/need-classifier/<system>_<UTCts>.json`. Each file holds:
  - per-case predictions: label, prediction, confidence, rationale, drivers, `trace_id`, cost, latency
    and status;
  - all metrics;
  - the prompt version, sha256 and model;
  - the dataset path, size and sha256;
  - the implementation actually used (`impl`), plus the `smoke` and `fake` flags.
- `evals/results/latest.md`, rebuilt on every run. It contains:
  - the status of each system in this invocation, including SKIPPED rows;
  - the latest run of each system, with Wilson 95% intervals;
  - the latest run against the previous run, with deltas marked ▲ or ▼ and labelled better or worse;
  - the newest prompt version against the one before it (for example v2 against v1);
  - rules-vs-LLM agreement (raw rate and Cohen's κ);
  - the misclassified cases (under-calls flagged) and the cases where systems disagree, for error
    analysis.

  Each trace id links to the full call record in `traces/llm_calls.jsonl`.

## Add cases

Follow `evals/datasets/README.md`: the labelling rubric, stratified sampling with
`python -m evals.make_labelling_set`, the hard edge cases, and the inter-rater check. Only hand-labelled
real rows belong in `need_classifier.jsonl`. When the dataset changes, its hash changes, and a new baseline
starts for every system.

## Metrics, and why two of them matter most

| metric | meaning |
|---|---|
| **high precision** | Of the accounts predicted "high", the share that really are high. Reps only call "high", so this is the cost of wasted calls and of reps losing trust in the list. **Primary business metric.** |
| **under-call rate** | Of the accounts that really are high, the share predicted "low". The account never surfaces and the deal is lost silently. This is the most expensive error, because unlike a wasted call nobody notices it. **Primary risk metric.** |
| high precision@k | The share of truly high accounts among the top k (default 10), ranked by predicted level and then confidence. This is how a rep works down a list. |
| macro-F1 | Unweighted mean of the per-class F1 scores. It is the overall health number and the CI regression gate. |
| accuracy | The share of cases predicted exactly right, with a Wilson 95% interval. |
| per-class P/R/F1, confusion matrix | Shows where the errors come from. The matrix has a `none` column for missing predictions. |
| parse-error rate | The share of cases with no valid high/medium/low output. These always count as wrong. |
| cost / latency per case | Mean, p50 and p95, taken from `LLMResult`. They decide whether the prompt is viable for the full ~200k-row batch (see `docs/COST_MODEL.md`). |
| rules-vs-LLM agreement | Raw agreement rate and κ. Low agreement on a labelled case shows where one system adds value. |

When choosing a prompt, maximise high precision subject to the under-call rate staying close to 0. Macro-F1
is a guard against general degradation; it is not the objective.

## Known limitations

- **Small n means wide intervals.** With 25–30 cases, an accuracy of 0.80 has a Wilson 95% interval of about
  0.62–0.91. High precision often rests on fewer than 10 predicted-high cases. A delta that falls inside the
  interval is noise, which is why the report prints the intervals and why the gate threshold is
  deliberately coarse (0.05 macro-F1).
- The labels are one person's judgement until the inter-rater check has been done.
- The LLM is run with temperature 0, but outputs can still drift slightly between model snapshots.
  Re-running the same prompt can therefore move the metrics a little.
- **Smoke set:** `smoke_synthetic.jsonl` is fictional, and its labels were written alongside the rules
  heuristics. Its numbers only prove that the harness works, and every report built on it is marked
  `HARNESS SMOKE TEST`.
- **SHIM tag:** runs tagged `SHIM` used a stand-in implementation in `evals/adapters.py`, because the real
  `pipeline` or `llm` module could not be imported. Such a run measures the shim, not the product.

## Code

- `metrics.py`: pure metric functions, unit-tested in `tests/test_evals_metrics.py`.
- `adapters.py`: the only place that imports `pipeline` and `llm` (lazily), plus the clearly named shims.
- `run_evals.py`: the CLI, result persistence, the `latest.md` renderer and the regression gate.
- `make_labelling_set.py`: the stratified candidate sampler.
- `tests/test_evals_harness.py`: end-to-end runs on the smoke set with a temporary results directory.
