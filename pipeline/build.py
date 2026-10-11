"""Build accounts.json: ingest -> signals -> score -> top N + facets (contract §3).

  python -m pipeline.build --input data/sample.parquet --out app/data/accounts.json [--top 5000]
                           [--limit N] [--sample N|P%] [--where SQL] [--countries Australia,NZ]
                           [--llm-top N] [--synthetic]

Rows are streamed; only the current top-N accounts are held in memory (a heap),
so this works on inputs far larger than RAM when combined with --where/--sample.
"""

from __future__ import annotations

import argparse
import datetime as dt
import heapq
import json
import os
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

from pipeline.ingest import iter_records
from pipeline.scoring import SCORING_CONFIG, score_company
from pipeline.signals import compute_signals

ACCOUNT_FIELDS = ("id", "name", "domain", "description", "industry", "sub_industry", "country", "state", "city",
                  "employee_count", "employee_range", "size_band", "revenue_usd", "founded_year",
                  "company_age_years", "technologies", "keywords", "linkedin_url", "website")
DEFAULT_PROMPT_VERSIONS = {"need-classifier": "v2", "account-brief": "v1", "outreach-draft": "v1"}


def to_account(rec: dict[str, Any], scored: dict[str, Any]) -> dict[str, Any]:
    acc = {k: rec.get(k) for k in ACCOUNT_FIELDS}
    acc.update(scored)
    acc["llm"] = None
    return acc


def build_facets(accounts: list[dict[str, Any]]) -> dict[str, list]:
    def distinct(key: str) -> list:
        return sorted({a[key] for a in accounts if a.get(key)}, key=str)

    band_order = ["1-10", "11-50", "51-200", "201-500", "501-1000", "1001-5000", "5000+", "unknown"]
    present_bands = {a["size_band"] for a in accounts}
    return {
        "industries": distinct("industry"),
        "countries": distinct("country"),
        "states": distinct("state"),
        "size_bands": [b for b in band_order if b in present_bands],
        "signal_ids": sorted({s["id"] for a in accounts for s in a["signals"]}),
        "segments": distinct("segment"),
        "plays": distinct("play"),
        "tiers": distinct("tier"),
    }


def _llm_company(acc: dict[str, Any], rec: dict[str, Any]) -> dict[str, Any]:
    """Canonical record for the LLM, with `extra` trimmed to short scalar values."""
    company = dict(rec)
    extra = {k: (v[:200] if isinstance(v, str) else v) for k, v in (rec.get("extra") or {}).items()
             if isinstance(v, (str, int, float, bool))}
    company["extra"] = dict(list(extra.items())[:20])
    return company


def _as_dict(obj: Any) -> dict[str, Any]:
    if isinstance(obj, dict):
        return obj
    for attr in ("model_dump", "to_dict", "dict"):
        fn = getattr(obj, attr, None)
        if callable(fn):
            return fn()
    if hasattr(obj, "__dataclass_fields__"):
        import dataclasses

        return dataclasses.asdict(obj)
    return dict(vars(obj))


def run_llm(accounts: list[dict[str, Any]], records: dict[str, dict[str, Any]], n: int,
            versions: dict[str, str]) -> dict[str, Any]:
    """Enrich the top-n accounts via llm.features. Never fails the build."""
    meta: dict[str, Any] = {"enabled": False, "prompt_versions": versions, "calls": 0, "cost_usd": 0.0}
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("[llm] skipped: ANTHROPIC_API_KEY is not set (rules-only build).", file=sys.stderr)
        meta["skipped_reason"] = "no ANTHROPIC_API_KEY"
        return meta
    try:
        from llm.features.account_brief import brief
        from llm.features.need_classifier import classify
        from llm.features.outreach import draft
    except ImportError as e:
        print(f"[llm] skipped: llm.features not available ({e}).", file=sys.stderr)
        meta["skipped_reason"] = f"import error: {e}"
        return meta

    trace_path = Path(os.environ.get("LLM_TRACE_PATH", "traces/llm_calls.jsonl"))
    offset = trace_path.stat().st_size if trace_path.exists() else 0
    meta["enabled"] = True
    errors = 0
    for i, acc in enumerate(accounts[:n], 1):
        company = _llm_company(acc, records[acc["id"]])
        sigs = acc["signals"]
        try:
            need = _as_dict(classify(company, signals=sigs, version=versions["need-classifier"]))
            text = brief(company, sigs, need=need, version=versions["account-brief"])
            mail = _as_dict(draft(company, sigs, acc["persona"], version=versions["outreach-draft"]))
            acc["llm"] = {"need_level": need.get("need_level"), "confidence": need.get("confidence"),
                          "drivers": need.get("drivers", []), "rationale": need.get("rationale"),
                          "brief": text if isinstance(text, str) else str(text),
                          "outreach": {k: mail.get(k) for k in ("subject", "body", "persona", "cited_signals")},
                          "model": need.get("model"), "prompt_versions": dict(versions)}
        except Exception as e:  # noqa: BLE001 - one bad account must not kill the build
            errors += 1
            print(f"[llm] {acc['id']} failed: {type(e).__name__}: {e}", file=sys.stderr)
        if i % 25 == 0:
            print(f"[llm] {i}/{min(n, len(accounts))} accounts", file=sys.stderr)

    # Count calls and cost from the trace lines this run appended (contract §6).
    models: Counter[str] = Counter()
    if trace_path.exists():
        with trace_path.open() as f:
            f.seek(offset)
            for line in f:
                try:
                    t = json.loads(line)
                except ValueError:
                    continue
                meta["calls"] += 1
                meta["cost_usd"] += float(t.get("cost_usd") or 0)
                models[t.get("model") or "?"] += 1
    meta["cost_usd"] = round(meta["cost_usd"], 4)
    meta["models"] = dict(models)
    meta["errors"] = errors
    for acc in accounts[:n]:
        if acc["llm"] and not acc["llm"].get("model") and models:
            acc["llm"]["model"] = models.most_common(1)[0][0]
    return meta


def build(input_path: str, out_path: str, *, top: int = 5000, limit: int | None = None, sample: str | None = None,
          where: str | None = None, countries: list[str] | None = None, weights: dict[str, float] | None = None,
          llm_top: int = 0, synthetic: bool | None = None, as_of: dt.date | None = None,
          prompt_versions: dict[str, str] | None = None, quiet: bool = False) -> dict[str, Any]:
    t0 = time.time()
    as_of = as_of or dt.date.today()
    weights = weights or dict(SCORING_CONFIG["weights"])
    mapping, records_iter = iter_records(input_path, where=where, sample=sample, limit=limit)

    heap: list[tuple[int, int, str]] = []  # (priority, -seq, id): min-heap of the current top N
    kept: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
    tiers: Counter[str] = Counter()
    signal_counts: Counter[str] = Counter()
    total = 0
    seen_ids: set[str] = set()
    for rec in records_iter:
        total += 1
        if rec["id"] in seen_ids:  # duplicate ids: make unique rather than drop
            rec["id"] = f"{rec['id']}-{total}"
        seen_ids.add(rec["id"])
        scored = score_company(rec, compute_signals(rec, as_of=as_of), weights=weights,
                               target_countries=countries, as_of=as_of)
        tiers[scored["tier"]] += 1
        signal_counts.update(s["id"] for s in scored["signals"])
        item = (scored["priority_score"], -total, rec["id"])
        if len(heap) < top:
            heapq.heappush(heap, item)
            kept[rec["id"]] = (rec, scored)
        elif item > heap[0]:
            _, _, dropped = heapq.heapreplace(heap, item)
            kept.pop(dropped, None)
            kept[rec["id"]] = (rec, scored)

    ordered = sorted(heap, reverse=True)  # priority desc, then input order
    accounts = [to_account(*kept[i]) for _, _, i in ordered]
    records = {i: kept[i][0] for _, _, i in ordered}

    llm_meta: dict[str, Any] = {"enabled": False, "prompt_versions": {}, "calls": 0, "cost_usd": 0.0}
    if llm_top:
        llm_meta = run_llm(accounts, records, llm_top, prompt_versions or DEFAULT_PROMPT_VERSIONS)

    if synthetic is None:
        synthetic = "synthetic" in input_path.lower() or "fixtures" in input_path.lower()
    out = {
        "meta": {
            "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            "source": input_path, "synthetic": bool(synthetic),
            "rows_total": total, "rows_included": len(accounts),
            "scoring_version": SCORING_CONFIG["version"], "weights": weights,
            "as_of": as_of.isoformat(),
            "target_countries": countries or SCORING_CONFIG["fit"]["geo"]["target_countries"],
            "tier_counts_all_rows": dict(sorted(tiers.items())),
            "mapping": mapping.to_dict(),
            "llm": llm_meta,
        },
        "facets": build_facets(accounts),
        "accounts": accounts,
    }
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_text(json.dumps(out, ensure_ascii=False, default=str, separators=(",", ":")))

    if not quiet:
        print(f"Built {out_path} in {time.time() - t0:.1f}s")
        print(f"  rows scanned: {total:,}   included: {len(accounts):,}   synthetic: {bool(synthetic)}")
        print("  tiers (all rows): " + ", ".join(f"{t}={tiers.get(t, 0):,}" for t in "ABCD"))
        print("  top signals: " + ", ".join(f"{k} ({v:,})" for k, v in signal_counts.most_common(8)))
        print("  " + mapping.describe().replace("\n", "\n  "))
        if llm_top:
            print(f"  llm: enabled={llm_meta['enabled']} calls={llm_meta['calls']} cost=${llm_meta['cost_usd']}")
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m pipeline.build", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--input", default="data/sample.parquet")
    ap.add_argument("--out", default="app/data/accounts.json")
    ap.add_argument("--top", type=int, default=5000, help="accounts to keep (by priority)")
    ap.add_argument("--limit", type=int, help="read at most N raw rows")
    ap.add_argument("--sample", help="repeatable random sample: row count or percentage (e.g. 50000 or 5%%)")
    ap.add_argument("--where", help="SQL filter on raw columns, e.g. \"Country = 'Australia'\"")
    ap.add_argument("--countries", help="comma-separated target countries for geo fit (default: Australia)")
    ap.add_argument("--fit-weight", type=float, default=SCORING_CONFIG["weights"]["fit"])
    ap.add_argument("--llm-top", type=int, default=0, help="run LLM features on the top N accounts")
    ap.add_argument("--need-version", default=DEFAULT_PROMPT_VERSIONS["need-classifier"])
    ap.add_argument("--as-of", help="reference date for recency signals (YYYY-MM-DD; default today)")
    ap.add_argument("--synthetic", action="store_true", default=None, help="mark output meta.synthetic = true")
    a = ap.parse_args(argv)

    versions = {**DEFAULT_PROMPT_VERSIONS, "need-classifier": a.need_version}
    build(a.input, a.out, top=a.top, limit=a.limit, sample=a.sample, where=a.where,
          countries=[c.strip() for c in a.countries.split(",")] if a.countries else None,
          weights={"fit": a.fit_weight, "intent": round(1 - a.fit_weight, 4)}, llm_top=a.llm_top,
          synthetic=a.synthetic, as_of=dt.date.fromisoformat(a.as_of) if a.as_of else None,
          prompt_versions=versions)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
