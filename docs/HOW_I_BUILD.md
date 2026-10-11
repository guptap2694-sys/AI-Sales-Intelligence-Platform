# How I Build

## Dev loop

I built this with **Claude Code** in a cloud session, working as an orchestrator over parallel sub-agents.

1. **Contract first.** Before any code, I wrote [`CONTRACT.md`](CONTRACT.md): the canonical company
   schema, signal shape, `accounts.json` format, prompt file format, LLM client interface, trace schema
   and eval case format. This was the single highest-leverage step. It let five agents work at once
   without stepping on each other, and it isolated the one unknown (the dataset's columns, which weren't
   available yet) behind a single module (`pipeline/schema.py`).
2. **Fan out.** Five background agents, each with a strict file scope and "don't commit" rule:
   research + planning, data pipeline, LLM layer (prompts, skills, tracing, cost), eval harness, and the
   frontend. Each had to write and pass its own tests; the frontend agent drove the app with Playwright and
   reviewed its own screenshots (light/dark, desktop/phone).
3. **Integrate and review.** As each agent reported back I ran the full test suite, read the hand-off
   notes for contract deviations, routed findings between agents (research → new signals in the
   pipeline; an integration test that leaked fake traces into the real trace file → pipeline fix), fixed
   small seams myself (e.g. `AU`/`Australia` country normalisation), and committed per component.
4. **Measure before claiming.** Every LLM path has a `fake` provider for tests, and its outputs are tagged
   and kept out of eval results and baselines. Real numbers come only from real calls on human labels.

## Where AI saved the most time

- **Breadth in parallel.** Pipeline, LLM scaffolding, eval harness, a polished frontend and sourced
  research were built concurrently in well under an hour of wall-clock time. Sequentially that's days.
- **Boilerplate with high correctness bar:** metric functions + Wilson intervals, retry/backoff, JSONL +
  SQLite tracing, DuckDB streaming, CSV export, a virtualised table. All well-trodden, all tested.
- **Research synthesis**, turning regulatory detail (CPS 234, SOCI, NDB, PCI DSS 4.0) into concrete
  signal definitions with evidence strings a rep can read.

## Where it cost more than doing it by hand

- **Integration seams between agents.** Two agents both treated `app/data/accounts.sample.json` as
  theirs, and the frontend initially had duplicate territories because country codes weren't normalised.
  Each was quick to fix, but only because I read every hand-off note carefully. A tighter contract
  (file ownership, normalised enums) would have prevented them.
- **Blocked inputs.** The environment's egress policy blocked the dataset host, so everything
  data-dependent waits on a manual sample. Agents will happily "fill in" missing data; I had to be explicit
  everywhere that synthetic data is for code tests only and that no metric may be reported from it.
- **Verifying claims.** Sourced statistics and model pricing came back from agents with caveats (pages
  that couldn't be fetched directly; a pricing table from the tooling's cache). Checking those is still a
  human job.

## Known weakness to flag to a teammate

**The rules scorer's weights and thresholds are hand-set from research, not fitted to outcomes.** The
need-level thresholds (high ≥ 58, medium ≥ 36) and per-category caps are reasoned guesses. They're
explicit, versioned (`rules-v1`) and easy to change, but until they're tuned against the labelled set and,
eventually, closed-won/lost data from the CRM, tier A means "matches our hypothesis of need", not
"proven to convert". Also: the eval set is 25–30 cases, so treat any metric as directional (the
report prints Wilson intervals for exactly this reason).
