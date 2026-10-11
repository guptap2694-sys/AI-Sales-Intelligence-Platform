"""Pricing table, per-call cost, and the production cost model.

PRICES — source: Anthropic `claude-api` skill bundled with Claude Code 2.1.296,
"Current Models (cached: 2026-10-06)" table (first-party Claude API list prices,
USD per 1M tokens) and shared/prompt-caching.md "Economics":
  * claude-haiku-5-5   $0.10 in / $0.50 out (prompts <= 100K tokens; $0.50/$2.50 beyond)
  * claude-sonnet-5-5  $2.00 in / $10.00 out, cache read $0.20
  * claude-opus-5-5    $4.00 in / $20.00 out, cache read $0.20   (reference only)
  * cache write (5-minute TTL) = 1.25x base input; cache read = ~0.1x base input
    (0.05x on Opus 5.5 -> $0.20); Message Batches API = 50% off every token,
    including cache reads/writes (shared/cost-optimization.md).
Verify against https://www.anthropic.com/pricing before committing to a budget.

CLI:
    python -m llm.cost estimate              # production cost model table
    python -m llm.cost estimate --json       # same numbers, machine-readable
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, asdict

# USD per 1M tokens.
PRICING: dict[str, dict[str, float]] = {
    "claude-haiku-5-5": {"input": 0.10, "output": 0.50, "cache_read": 0.01, "cache_write": 0.125},
    "claude-sonnet-5-5": {"input": 2.00, "output": 10.00, "cache_read": 0.20, "cache_write": 2.50},
    "claude-opus-5-5": {"input": 4.00, "output": 20.00, "cache_read": 0.20, "cache_write": 5.00},
}
BATCH_DISCOUNT = 0.50
PRICING_SOURCE = "claude-api skill (Claude Code 2.1.296), model table cached 2026-10-06"


def _rates(model: str) -> dict[str, float]:
    if model in PRICING:
        return PRICING[model]
    # tolerate provider prefixes / suffixes, e.g. "anthropic.claude-haiku-5-5"
    for key, rates in PRICING.items():
        if key in model:
            return rates
    raise KeyError(f"no pricing for model '{model}' (known: {sorted(PRICING)})")


def cost_usd(
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
    batch: bool = False,
) -> float:
    """Cost of one call. ``input_tokens`` = uncached input (API ``usage.input_tokens``)."""
    r = _rates(model)
    usd = (
        input_tokens * r["input"]
        + output_tokens * r["output"]
        + cache_read_tokens * r["cache_read"]
        + cache_write_tokens * r["cache_write"]
    ) / 1_000_000
    if batch:
        usd *= BATCH_DISCOUNT
    return round(usd, 8)


def estimate_tokens(text: str) -> int:
    """Rough token count: chars / 4 (English prose + JSON). Use messages.count_tokens for exact."""
    return max(1, len(text) // 4)


# ---------------------------------------------------------------------------
# Production cost model
# ---------------------------------------------------------------------------

# Illustrative, SYNTHETIC company used only to measure rendered prompt sizes.
EXAMPLE_COMPANY = {
    "id": "example-001",
    "name": "Harbourview Medical Imaging",
    "domain": "harbourviewimaging.com.au",
    "description": (
        "Harbourview Medical Imaging operates 14 diagnostic imaging clinics across New South Wales, "
        "offering MRI, CT, ultrasound and X-ray services. Referring GPs and specialists access results "
        "through our secure online portal, and patients can book and view reports via our mobile app. "
        "We are expanding into regional Queensland and recently integrated with My Health Record. "
        "Our team of 40 radiologists reports remotely via a cloud PACS platform."
    ),
    "industry": "Hospital & Health Care",
    "sub_industry": "Diagnostic Imaging",
    "employee_count": 320,
    "size_band": "201-500",
    "revenue_usd": 48000000.0,
    "founded_year": 2009,
    "country": "Australia",
    "state": "NSW",
    "city": "Sydney",
    "technologies": ["Microsoft 365", "Azure", "Salesforce Health Cloud", "Cloudflare"],
    "keywords": ["radiology", "telehealth", "patient portal"],
    "employee_growth_pct": 18.0,
    "job_postings": 12,
}
EXAMPLE_SIGNALS = [
    {"id": "regulated_industry", "label": "Regulated industry (healthcare)", "category": "regulatory",
     "evidence": "industry = 'Hospital & Health Care'"},
    {"id": "patient_data", "label": "Handles patient health records", "category": "data_sensitivity",
     "evidence": "description mentions 'patient portal', 'My Health Record'"},
    {"id": "customer_portal", "label": "Customer-facing portal / app", "category": "attack_surface",
     "evidence": "description mentions 'online portal', 'mobile app'"},
    {"id": "headcount_growth", "label": "Headcount growth 18%", "category": "growth",
     "evidence": "employee_growth_pct = 18.0"},
]

# Expected output tokens per call (from the prompts' output contracts; thinking disabled).
EXPECTED_OUTPUT_TOKENS = {"need-classifier": 110, "account-brief": 170, "outreach-draft": 210}


@dataclass
class VolumeAssumptions:
    universe_rows: int = 200_000           # companies in the dataset (initial backfill)
    prefilter_pass_rate: float = 0.30      # share passing the rules pre-filter -> sent to classifier
    top_ab_accounts: int = 5_000           # tier A/B accounts that get a brief + outreach in backfill
    weekly_changed_rows: int = 8_000       # changed & pre-filter-passing companies re-classified / week
    weekly_changed_ab: int = 600           # changed tier A/B accounts whose brief is refreshed / week
    reps: int = 40                         # sales reps
    drafts_per_rep_per_day: int = 15       # on-demand outreach drafts (interactive, not batchable)
    briefs_per_rep_per_day: int = 10       # on-demand brief regenerations (interactive)
    workdays_per_month: int = 21
    weeks_per_month: float = 4.33
    cache_hit_rate_bulk: float = 0.98      # system prompt cache hits in backfill/refresh streams
    cache_hit_rate_interactive: float = 0.80


def _prompt_token_sizes() -> dict[str, dict[str, int]]:
    """Render the real prompts on the example inputs; return system/user token estimates."""
    from llm.registry import load_prompt
    from llm.features import trim_company, trim_signals, to_json

    company = trim_company(EXAMPLE_COMPANY)
    signals = trim_signals(EXAMPLE_SIGNALS)
    variables = {
        "company_json": to_json(company),
        "signals_json": to_json(signals),
        "persona": "Head of IT / IT Manager",
        "need_json": to_json({"need_level": "high", "confidence": 0.85,
                              "drivers": ["regulatory", "data_sensitivity"],
                              "rationale": "Healthcare provider handling patient records via portal."}),
        "sender_context": "Firmable-style sender: cybersecurity software vendor (placeholder).",
    }
    sizes = {}
    for pid in ("need-classifier", "account-brief", "outreach-draft"):
        p = load_prompt(pid)
        vars_needed = {k: v for k, v in variables.items() if k in p.variables}
        sizes[pid] = {
            "version": p.version,
            "model": p.model,
            "system": estimate_tokens(p.render_system(**vars_needed)),
            "user": estimate_tokens(p.render(**vars_needed)),
            "output": EXPECTED_OUTPUT_TOKENS[pid],
        }
    return sizes


def _call_cost(model: str, sys_tok: int, user_tok: int, out_tok: int,
               cache: bool, hit_rate: float, batch: bool) -> float:
    if cache and sys_tok >= 512:  # min cacheable prefix for the 5.5 family is 512 tokens
        read = sys_tok * hit_rate
        write = sys_tok * (1 - hit_rate)
        return cost_usd(model, user_tok, out_tok, int(read), int(write), batch=batch)
    return cost_usd(model, sys_tok + user_tok, out_tok, batch=batch)


def estimate(v: VolumeAssumptions | None = None) -> dict:
    v = v or VolumeAssumptions()
    sizes = _prompt_token_sizes()
    classified_backfill = int(v.universe_rows * v.prefilter_pass_rate)
    interactive_days = v.reps * v.workdays_per_month

    # (task, prompt_id, phase, calls, batchable, cache_hit_rate)
    lines = [
        ("Need classifier (backfill)", "need-classifier", "one-off", classified_backfill, True, v.cache_hit_rate_bulk),
        ("Account brief (backfill, tier A/B)", "account-brief", "one-off", v.top_ab_accounts, True, v.cache_hit_rate_bulk),
        ("Outreach draft (backfill, tier A/B)", "outreach-draft", "one-off", v.top_ab_accounts, True, v.cache_hit_rate_bulk),
        ("Need classifier (weekly refresh)", "need-classifier", "monthly", int(v.weekly_changed_rows * v.weeks_per_month), True, v.cache_hit_rate_bulk),
        ("Account brief (weekly refresh)", "account-brief", "monthly", int(v.weekly_changed_ab * v.weeks_per_month), True, v.cache_hit_rate_bulk),
        ("Account brief (on-demand, reps)", "account-brief", "monthly", interactive_days * v.briefs_per_rep_per_day, False, v.cache_hit_rate_interactive),
        ("Outreach draft (on-demand, reps)", "outreach-draft", "monthly", interactive_days * v.drafts_per_rep_per_day, False, v.cache_hit_rate_interactive),
    ]
    rows = []
    for task, pid, phase, calls, batchable, hit in lines:
        s = sizes[pid]
        m = s["model"]
        base = _call_cost(m, s["system"], s["user"], s["output"], cache=False, hit_rate=0, batch=False)
        cached = _call_cost(m, s["system"], s["user"], s["output"], cache=True, hit_rate=hit, batch=False)
        batch = _call_cost(m, s["system"], s["user"], s["output"], cache=False, hit_rate=0, batch=batchable)
        both = _call_cost(m, s["system"], s["user"], s["output"], cache=True, hit_rate=hit, batch=batchable)
        rows.append({
            "task": task, "prompt": f"{pid}@{s['version']}", "model": m, "phase": phase,
            "in_tokens": s["system"] + s["user"], "system_tokens": s["system"], "out_tokens": s["output"],
            "calls": calls, "batchable": batchable,
            "usd_standard": round(base * calls, 2), "usd_cache": round(cached * calls, 2),
            "usd_batch": round(batch * calls, 2), "usd_cache_batch": round(both * calls, 2),
        })

    def total(phase: str, col: str) -> float:
        return round(sum(r[col] for r in rows if r["phase"] == phase), 2)

    cols = ("usd_standard", "usd_cache", "usd_batch", "usd_cache_batch")
    totals = {ph: {c: total(ph, c) for c in cols} for ph in ("one-off", "monthly")}
    expected_monthly = totals["monthly"]["usd_cache_batch"]
    # Ceiling: 2x the optimised expectation (headroom for more reps / bigger refreshes),
    # but never below the un-optimised standard cost so a caching regression alone can't trip it.
    ceiling = max(round(expected_monthly * 2, -1), round(totals["monthly"]["usd_standard"] + 5, -1))
    return {
        "assumptions": asdict(v) | {"token_estimate": "chars/4 on rendered prompts with a synthetic example company"},
        "prompt_sizes": sizes,
        "rows": rows,
        "totals": totals,
        "recommendation": {
            "expected_monthly_usd": expected_monthly,
            "monthly_ceiling_usd": ceiling,
            "alerts": {
                "warn_50pct_usd": round(ceiling * 0.5, 2),
                "page_80pct_usd": round(ceiling * 0.8, 2),
                "hard_stop_100pct_usd": ceiling,
                "daily_spike_usd": round(expected_monthly / v.workdays_per_month * 3, 2),
                # Per-run guards are set on *standard* (uncached, unbatched) cost x1.5 so a cache
                # miss storm doesn't abort a run, while a runaway loop still does.
                "per_run_budget_usd_backfill": round(totals["one-off"]["usd_standard"] * 1.5, 0),
                "per_run_budget_usd_weekly": round(
                    sum(r["usd_standard"] for r in rows if r["phase"] == "monthly" and r["batchable"])
                    / v.weeks_per_month * 1.5 + 1, 0),
                "per_rep_daily_usd": round(
                    sum(r["usd_standard"] for r in rows if r["phase"] == "monthly" and not r["batchable"])
                    / (v.reps * v.workdays_per_month) * 2, 2),
            },
        },
        "pricing": {"source": PRICING_SOURCE, "per_mtok_usd": PRICING, "batch_discount": BATCH_DISCOUNT},
    }


def _fmt_table(est: dict) -> str:
    out = []
    a = est["assumptions"]
    out.append("Production LLM cost model  (prices: %s)" % est["pricing"]["source"])
    out.append("Token sizes: %s" % a["token_estimate"])
    for pid, s in est["prompt_sizes"].items():
        out.append(f"  {pid}@{s['version']:<3} {s['model']:<18} system~{s['system']:>5} tok  user~{s['user']:>4} tok  out~{s['output']:>4} tok")
    out.append(
        f"Volumes: {a['universe_rows']:,} rows, pre-filter pass {a['prefilter_pass_rate']:.0%}, "
        f"top A/B {a['top_ab_accounts']:,}, weekly changed {a['weekly_changed_rows']:,} (A/B {a['weekly_changed_ab']:,}), "
        f"{a['reps']} reps x {a['drafts_per_rep_per_day']} drafts + {a['briefs_per_rep_per_day']} briefs/day x {a['workdays_per_month']} days"
    )
    hdr = f"{'task':<38} {'model':<18} {'in':>5} {'out':>4} {'calls':>8} {'standard':>10} {'+cache':>10} {'+batch':>10} {'cache+batch':>12}"
    for phase, title in (("one-off", "ONE-OFF BACKFILL"), ("monthly", "STEADY STATE / MONTH")):
        out.append("")
        out.append(title)
        out.append(hdr)
        out.append("-" * len(hdr))
        for r in est["rows"]:
            if r["phase"] != phase:
                continue
            b = "" if r["batchable"] else "*"
            out.append(
                f"{r['task']:<38} {r['model']:<18} {r['in_tokens']:>5} {r['out_tokens']:>4} {r['calls']:>8,} "
                f"{r['usd_standard']:>10,.2f} {r['usd_cache']:>10,.2f} {r['usd_batch']:>9,.2f}{b:1} {r['usd_cache_batch']:>12,.2f}"
            )
        t = est["totals"][phase]
        out.append("-" * len(hdr))
        out.append(f"{'TOTAL':<38} {'':<18} {'':>5} {'':>4} {'':>8} {t['usd_standard']:>10,.2f} {t['usd_cache']:>10,.2f} {t['usd_batch']:>10,.2f} {t['usd_cache_batch']:>12,.2f}")
    out.append("  * interactive (rep is waiting) -> not batchable; batch column = standard price")
    rec = est["recommendation"]
    al = rec["alerts"]
    out.append("")
    out.append(f"Expected monthly (cache+batch): ${rec['expected_monthly_usd']:,.2f}")
    out.append(f"Recommended monthly ceiling:    ${rec['monthly_ceiling_usd']:,.2f}")
    out.append(
        f"Alerts: warn at ${al['warn_50pct_usd']:,.2f} (50%), page at ${al['page_80pct_usd']:,.2f} (80%), "
        f"hard stop -> rules-only at ${al['hard_stop_100pct_usd']:,.2f}; daily spike > ${al['daily_spike_usd']:,.2f}"
    )
    out.append(
        f"Per-run budget guard (LLMClient budget_usd): backfill ${al['per_run_budget_usd_backfill']:,.0f}, "
        f"weekly refresh ${al['per_run_budget_usd_weekly']:,.0f}; per-rep daily cap ${al['per_rep_daily_usd']:,.2f}"
    )
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m llm.cost", description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("estimate", help="print the production cost model")
    e.add_argument("--json", action="store_true")
    for f, default in asdict(VolumeAssumptions()).items():
        e.add_argument("--" + f.replace("_", "-"), type=type(default), default=default)
    c = sub.add_parser("price", help="cost of one call")
    c.add_argument("model")
    c.add_argument("input_tokens", type=int)
    c.add_argument("output_tokens", type=int)
    args = ap.parse_args(argv)
    if args.cmd == "price":
        print(f"${cost_usd(args.model, args.input_tokens, args.output_tokens):.6f}")
        return 0
    v = VolumeAssumptions(**{f: getattr(args, f) for f in asdict(VolumeAssumptions())})
    est = estimate(v)
    print(json.dumps(est, indent=2) if args.json else _fmt_table(est))
    return 0


if __name__ == "__main__":
    sys.exit(main())
