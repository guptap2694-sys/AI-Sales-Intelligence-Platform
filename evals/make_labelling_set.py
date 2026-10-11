"""Draw a stratified candidate set of REAL rows for a human to label.

    python -m evals.make_labelling_set --input data/sample.parquet --n 30 \
        --out evals/datasets/need_classifier.candidates.jsonl

Rows are read through ``pipeline.ingest`` when it exists (canonical records,
CONTRACT.md §1); otherwise a minimal DuckDB fallback reader is used and flagged.
Output cases follow CONTRACT.md §8 with ``label: null`` — a human fills in
``label``, ``labeller`` and ``rationale`` (see evals/datasets/README.md), then the
labelled cases are copied into ``evals/datasets/need_classifier.jsonl``.
No labels or rule predictions are written, to keep labelling blind.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Iterable, Iterator

from evals import adapters

EDGE_PATTERNS = {
    "security-vendor": re.compile(r"cyber|computer (&|and) network security|network security|infosec|"
                                  r"information security|\bmssp\b|\bsoc\b|managed security|penetration test", re.I),
    "government": re.compile(r"government|council|municipal|public administration|department of|"
                             r"\bagency\b|\bshire\b|federal|state of\b", re.I),
}
REGULATED = re.compile(r"health|hospital|medical|dental|pharma|bank|financ|insur|credit|legal|law|"
                       r"accounting|education|school|utilit|energy|defen[cs]e", re.I)
LOW_RISK = re.compile(r"farm|agricult|restaurant|food service|landscap|construction|plumb|cleaning|"
                      r"hospitality|arts|craft|fitness|recreation", re.I)
EDGE_QUOTA = 2  # per edge-case bucket


def size_band(emp) -> str:
    if not isinstance(emp, (int, float)):
        return "unknown"
    for hi, band in ((10, "1-10"), (50, "11-50"), (200, "51-200"), (500, "201-500"), (1000, "501-1000"),
                     (5000, "1001-5000")):
        if emp <= hi:
            return band
    return "5000+"


def _text(rec: dict) -> str:
    return " ".join([str(rec.get(k) or "") for k in ("industry", "sub_industry", "description", "name")]
                    + [str(x) for x in rec.get("keywords") or []])


def edge_tags(rec: dict) -> list[str]:
    text, emp = _text(rec), rec.get("employee_count")
    ind = " ".join(str(rec.get(k) or "") for k in ("industry", "sub_industry"))
    tags = [f"edge:{k}" for k, pat in EDGE_PATTERNS.items() if pat.search(text)]
    if isinstance(emp, (int, float)) and emp <= 10 and REGULATED.search(ind):
        tags.append("edge:regulated-micro")
    if isinstance(emp, (int, float)) and emp >= 1000 and LOW_RISK.search(ind):
        tags.append("edge:large-low-risk")
    filled = sum(1 for k in ("description", "industry", "employee_count", "domain", "country", "revenue_usd")
                 if rec.get(k) not in (None, "", []))
    if filled <= 2:
        tags.append("edge:sparse-record")
    return tags


# --------------------------------------------------------------------------- reading
def _iter_pipeline(path: Path, pool: int) -> tuple[Iterator[dict], str] | None:
    mod = adapters._try_import("pipeline.ingest")
    if mod is None:
        return None
    if callable(getattr(mod, "iter_records", None)):  # (mapping, iterator); DuckDB-side reservoir sample
        mapping, it = mod.iter_records(str(path), sample=str(pool))
        desc = getattr(mapping, "describe", None)
        if callable(desc):
            print(desc(), file=sys.stderr)
        return it, "pipeline.ingest.iter_records"
    for name in ("iter_companies", "load_companies", "iter_rows", "ingest", "load", "read"):
        fn = getattr(mod, name, None)
        if callable(fn):
            res = fn(str(path))
            if hasattr(res, "to_dict"):  # tolerate a DataFrame
                res = res.to_dict("records")
            return iter(res), f"pipeline.ingest.{name}"
    return None


_FALLBACK_ALIASES = {
    "name": ("name", "company_name", "company", "organization", "organisation", "business_name"),
    "domain": ("domain", "website_domain", "company_domain"),
    "website": ("website", "url", "company_website"),
    "description": ("description", "company_description", "about", "summary", "short_description"),
    "industry": ("industry", "industry_name", "sector", "category"),
    "employee_count": ("employee_count", "employees", "num_employees", "headcount", "staff_count"),
    "country": ("country", "country_name", "hq_country"),
    "state": ("state", "region", "province", "hq_state"),
    "city": ("city", "hq_city", "locality"),
    "id": ("id", "company_id", "uuid"),
}


def _iter_fallback(path: Path, pool: int, seed: int) -> Iterator[dict]:
    """SHIM reader (until pipeline.ingest exists): DuckDB sample + naive alias mapping."""
    import duckdb
    reader = {".parquet": "read_parquet", ".csv": "read_csv_auto", ".json": "read_json_auto",
              ".jsonl": "read_json_auto", ".ndjson": "read_json_auto"}.get(path.suffix.lower(), "read_parquet")
    con = duckdb.connect()
    cur = con.execute(f"SELECT * FROM {reader}(?) USING SAMPLE reservoir({int(pool)} ROWS) REPEATABLE ({int(seed)})",
                      [str(path)])
    cols = [d[0] for d in cur.description]
    lower = {c.lower(): c for c in cols}
    for row in cur.fetchall():
        raw = dict(zip(cols, row))
        rec, used = {}, set()
        for field, aliases in _FALLBACK_ALIASES.items():
            for a in aliases:
                if a in lower and raw[lower[a]] not in (None, ""):
                    rec[field] = raw[lower[a]]
                    used.add(lower[a])
                    break
        try:
            rec["employee_count"] = int(rec["employee_count"]) if rec.get("employee_count") is not None else None
        except (TypeError, ValueError):
            rec["employee_count"] = None
        rec["extra"] = {k: (v if isinstance(v, (str, int, float, bool, type(None))) else str(v))
                        for k, v in raw.items() if k not in used}
        if not rec.get("id"):
            key = str(rec.get("domain") or rec.get("name") or json.dumps(rec["extra"], sort_keys=True, default=str))
            rec["id"] = hashlib.sha1(key.encode()).hexdigest()[:16]
        yield rec


def reservoir(rows: Iterable[dict], k: int, rng: random.Random) -> list[dict]:
    out: list[dict] = []
    for i, r in enumerate(rows):
        if i < k:
            out.append(r)
        else:
            j = rng.randint(0, i)
            if j < k:
                out[j] = r
    return out


# --------------------------------------------------------------------------- sampling
def stratified_sample(records: list[dict], n: int, seed: int = 7) -> list[tuple[dict, list[str], str]]:
    """Edge-case quota first, then round-robin over (industry × size band) strata."""
    rng = random.Random(seed)
    recs = records[:]
    rng.shuffle(recs)
    chosen: list[tuple[dict, list[str], str]] = []
    taken: set[str] = set()

    def stratum(r):
        return f"{(r.get('industry') or 'unknown').strip().lower()}|{r.get('size_band') or size_band(r.get('employee_count'))}"

    tagged = [(r, edge_tags(r)) for r in recs]
    buckets: dict[str, list] = defaultdict(list)
    for r, tags in tagged:
        for t in tags:
            buckets[t].append((r, tags))
    for t in sorted(buckets):
        for r, tags in buckets[t][:EDGE_QUOTA]:
            if len(chosen) >= n:
                break
            if r["id"] not in taken:
                chosen.append((r, tags, stratum(r)))
                taken.add(r["id"])

    strata: dict[str, list] = defaultdict(list)
    for r, tags in tagged:
        if r["id"] not in taken:
            strata[stratum(r)].append((r, tags))
    # round-robin, preferring industries not yet represented
    order = sorted(strata, key=lambda s: (sum(1 for _, _, cs in chosen if cs.split("|")[0] == s.split("|")[0]),
                                          rng.random()))
    while len(chosen) < n and any(strata.values()):
        for s in order:
            if len(chosen) >= n:
                break
            if strata[s]:
                r, tags = strata[s].pop()
                chosen.append((r, tags, s))
                taken.add(r["id"])
    return chosen


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m evals.make_labelling_set", description=__doc__.split("\n")[0])
    ap.add_argument("--input", type=Path, required=True)
    ap.add_argument("--n", type=int, default=30)
    ap.add_argument("--out", type=Path, default=Path("evals/datasets/need_classifier.candidates.jsonl"))
    ap.add_argument("--pool", type=int, default=20000, help="reservoir-sample this many rows before stratifying")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--start-id", type=int, default=1, help="first nc-NNN number (to append to an existing set)")
    args = ap.parse_args(argv)
    if not args.input.exists():
        print(f"input not found: {args.input}", file=sys.stderr)
        return 2

    rng = random.Random(args.seed)
    got = _iter_pipeline(args.input, args.pool)
    if got is not None:
        rows, source = got
        pool = reservoir(rows, args.pool, rng)
    else:
        print("WARNING: pipeline.ingest not available — using minimal DuckDB fallback reader (naive column "
              "mapping; unmapped columns kept in company.extra).", file=sys.stderr)
        pool, source = list(_iter_fallback(args.input, args.pool, args.seed)), "fallback:duckdb"
    pool = [adapters.normalise_company(r) for r in pool]
    picked = stratified_sample(pool, args.n, args.seed)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        for i, (rec, tags, stratum) in enumerate(picked, args.start_id):
            case = {"case_id": f"nc-{i:03d}", "company": rec, "label": None, "labeller": None, "rationale": None,
                    "tags": tags, "sampling": {"stratum": stratum, "source": str(args.input), "reader": source,
                                                "seed": args.seed}}
            f.write(json.dumps(case, ensure_ascii=False, default=str) + "\n")
    print(f"wrote {len(picked)} candidates (pool {len(pool)}, reader {source}) → {args.out}")
    print("Next: label them per evals/datasets/README.md, then copy labelled lines into "
          "evals/datasets/need_classifier.jsonl")
    return 0


if __name__ == "__main__":
    sys.exit(main())
