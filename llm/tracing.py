"""LLM call tracing (contract §6).

Every LLMClient call appends one JSON object per line to ``traces/llm_calls.jsonl``
(override with ``Tracer(path=...)`` or env ``LLM_TRACE_PATH``). Optionally mirrors
each record into SQLite (``traces/llm_calls.sqlite``) with the same columns
(nested objects stored as JSON text) so traces can be queried with SQL.

CLI:
    python -m llm.tracing summary [--path traces/llm_calls.jsonl] [--run-id ID]
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import threading
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

DEFAULT_TRACE_PATH = Path(os.environ.get("LLM_TRACE_PATH", "traces/llm_calls.jsonl"))
DEFAULT_SQLITE_PATH = Path("traces/llm_calls.sqlite")

# Exact top-level keys of a trace record, in contract order (§6).
TRACE_KEYS: tuple[str, ...] = (
    "trace_id", "run_id", "ts", "feature", "prompt_id", "prompt_version", "prompt_sha256",
    "provider", "model", "request", "response", "usage", "latency_ms", "cost_usd",
    "entity_id", "decision", "status", "error", "eval_case_id",
)
REQUEST_KEYS = ("system", "messages", "max_tokens", "temperature")
RESPONSE_KEYS = ("text", "parsed", "stop_reason")
USAGE_KEYS = ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens")
STATUSES = ("ok", "error", "parse_error")

_JSON_COLUMNS = {"request", "response", "usage"}


class Tracer:
    """Thread-safe JSONL appender with an optional SQLite mirror."""

    def __init__(self, path: str | Path | None = None, sqlite_path: str | Path | bool | None = None,
                 enabled: bool = True):
        self.path = Path(path) if path else DEFAULT_TRACE_PATH
        if sqlite_path is True:
            sqlite_path = self.path.with_suffix(".sqlite")
        self.sqlite_path = Path(sqlite_path) if sqlite_path else None
        self.enabled = enabled
        self._lock = threading.Lock()
        self._sqlite_ready = False

    def record(self, rec: dict[str, Any]) -> dict[str, Any]:
        missing = [k for k in TRACE_KEYS if k not in rec]
        extra = [k for k in rec if k not in TRACE_KEYS]
        if missing or extra:
            raise ValueError(f"trace record schema mismatch: missing={missing} extra={extra}")
        if rec["status"] not in STATUSES:
            raise ValueError(f"bad trace status {rec['status']!r}")
        if not self.enabled:
            return rec
        ordered = {k: rec[k] for k in TRACE_KEYS}
        line = json.dumps(ordered, ensure_ascii=False, default=str)
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
            if self.sqlite_path:
                self._write_sqlite(ordered)
        return ordered

    def _write_sqlite(self, rec: dict[str, Any]) -> None:
        self.sqlite_path.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(self.sqlite_path)
        try:
            if not self._sqlite_ready:
                cols = ", ".join(
                    f"{k} {'TEXT PRIMARY KEY' if k == 'trace_id' else _sql_type(k)}" for k in TRACE_KEYS
                )
                con.execute(f"CREATE TABLE IF NOT EXISTS llm_calls ({cols})")
                con.execute("CREATE INDEX IF NOT EXISTS ix_llm_calls_run ON llm_calls(run_id)")
                con.execute("CREATE INDEX IF NOT EXISTS ix_llm_calls_feat ON llm_calls(feature, prompt_version, model)")
                self._sqlite_ready = True
            vals = [json.dumps(rec[k], default=str)
                    if k in _JSON_COLUMNS or isinstance(rec[k], (dict, list)) else rec[k]
                    for k in TRACE_KEYS]
            con.execute(
                f"INSERT OR REPLACE INTO llm_calls ({', '.join(TRACE_KEYS)}) VALUES ({', '.join('?' * len(TRACE_KEYS))})",
                vals,
            )
            con.commit()
        finally:
            con.close()


def _sql_type(k: str) -> str:
    return {"latency_ms": "INTEGER", "cost_usd": "REAL"}.get(k, "TEXT")


def read_traces(path: str | Path | None = None) -> list[dict[str, Any]]:
    p = Path(path) if path else DEFAULT_TRACE_PATH
    if not p.exists():
        return []
    out = []
    with p.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return out


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    k = (len(s) - 1) * pct / 100
    lo, hi = int(k), min(int(k) + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def summarize(records: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group by feature x prompt_version x model (x provider so fake never mixes with real)."""
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for r in records:
        groups[(r.get("feature"), r.get("prompt_version"), r.get("model"), r.get("provider"))].append(r)
    rows = []
    for (feat, ver, model, provider), rs in sorted(groups.items(), key=lambda kv: tuple(str(x) for x in kv[0])):
        lat = [float(r.get("latency_ms") or 0) for r in rs]
        usage = [r.get("usage") or {} for r in rs]
        errs = sum(1 for r in rs if r.get("status") != "ok")
        rows.append({
            "feature": feat, "prompt_version": ver, "model": model, "provider": provider,
            "calls": len(rs),
            "cost_usd": round(sum(float(r.get("cost_usd") or 0) for r in rs), 6),
            "p50_ms": round(_percentile(lat, 50)), "p95_ms": round(_percentile(lat, 95)),
            "error_rate": round(errs / len(rs), 4),
            "parse_errors": sum(1 for r in rs if r.get("status") == "parse_error"),
            "in_tokens": sum(int(u.get("input_tokens") or 0) for u in usage),
            "out_tokens": sum(int(u.get("output_tokens") or 0) for u in usage),
            "cache_read_tokens": sum(int(u.get("cache_read_tokens") or 0) for u in usage),
        })
    return rows


def format_summary(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "no traces"
    hdr = (f"{'feature':<18} {'ver':<4} {'model':<18} {'provider':<9} {'calls':>6} {'cost_usd':>10} "
           f"{'p50_ms':>7} {'p95_ms':>7} {'err%':>6} {'parse':>5} {'in_tok':>9} {'out_tok':>8} {'cache_rd':>9}")
    lines = [hdr, "-" * len(hdr)]
    for r in rows:
        lines.append(
            f"{str(r['feature']):<18} {str(r['prompt_version']):<4} {str(r['model']):<18} {str(r['provider']):<9} "
            f"{r['calls']:>6} {r['cost_usd']:>10.4f} {r['p50_ms']:>7} {r['p95_ms']:>7} "
            f"{r['error_rate'] * 100:>5.1f}% {r['parse_errors']:>5} {r['in_tokens']:>9} {r['out_tokens']:>8} {r['cache_read_tokens']:>9}"
        )
    tot_calls = sum(r["calls"] for r in rows)
    tot_cost = sum(r["cost_usd"] for r in rows)
    lines.append("-" * len(hdr))
    lines.append(f"TOTAL calls={tot_calls} cost_usd={tot_cost:.4f}")
    if any(r["provider"] == "fake" for r in rows):
        lines.append("note: provider=fake rows are synthetic test/dry-run calls (simulated cost), not real model output")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m llm.tracing")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("summary", help="calls, cost, p50/p95 latency, error rate by feature x prompt_version x model")
    s.add_argument("--path", default=str(DEFAULT_TRACE_PATH))
    s.add_argument("--run-id", default=None)
    s.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    recs = read_traces(args.path)
    if args.run_id:
        recs = [r for r in recs if r.get("run_id") == args.run_id]
    rows = summarize(recs)
    print(json.dumps(rows, indent=2) if args.json else format_summary(rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())
