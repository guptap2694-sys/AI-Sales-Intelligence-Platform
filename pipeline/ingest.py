"""DuckDB-based loader: any-size parquet / csv / json(l) -> canonical records.

The raw file is never loaded into memory as a whole: DuckDB scans it lazily,
applies optional SQL filtering (`where`), sampling (`sample`) and `limit`, and
rows are streamed to Python in batches and normalised one at a time.

CLI:
  python -m pipeline.ingest profile --input FILE [--rows 200000 | --full]
  python -m pipeline.ingest preview --input FILE [--limit 5]
"""

from __future__ import annotations

import argparse
import datetime as dt
import decimal
import hashlib
import json
import math
import re
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import duckdb

from pipeline.schema import COLUMN_ALIASES, Mapping, detect_mapping, empty_record

BATCH_SIZE = 10_000
CURRENT_YEAR = dt.date.today().year


# --------------------------------------------------------------------------- #
# Source access
# --------------------------------------------------------------------------- #
def _sql_str(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


def detect_format(path: str) -> str:
    p = path.lower()
    for suffix in (".gz", ".zst", ".bz2"):
        if p.endswith(suffix):
            p = p[: -len(suffix)]
    if p.endswith((".parquet", ".pq")) or p.endswith("*"):
        return "parquet"
    if p.endswith((".csv", ".tsv", ".txt")):
        return "csv"
    if p.endswith((".json", ".jsonl", ".ndjson")):
        return "json"
    raise ValueError(f"Cannot infer format of {path!r}; expected parquet/csv/json(l)")


def source_sql(path: str, all_varchar: bool = False) -> str:
    """A DuckDB table function expression for the raw file (globs allowed)."""
    fmt = detect_format(path)
    q = _sql_str(path)
    if fmt == "parquet":
        return f"read_parquet({q}, union_by_name=true)"
    if fmt == "csv":
        opts = "header=true, ignore_errors=true, sample_size=100000"
        if all_varchar:
            opts += ", all_varchar=true"
        return f"read_csv({q}, {opts})"
    return f"read_json_auto({q}, format='auto', ignore_errors=true, sample_size=100000)"


def build_query(path: str, *, where: str | None = None, sample: str | None = None,
                limit: int | None = None, all_varchar: bool = False) -> str:
    """SELECT over the source with optional filter, reservoir sample and limit.

    `sample` is a row count ("50000") or a percentage ("10%"); sampling is
    REPEATABLE so builds are deterministic.
    """
    sql = f"SELECT * FROM {source_sql(path, all_varchar)}"
    if where:
        sql += f" WHERE {where}"
    if sample:
        s = str(sample).strip()
        clause = (f"{float(s[:-1])} PERCENT (bernoulli, 42)" if s.endswith("%")
                  else f"reservoir({int(s)} ROWS) REPEATABLE (42)")
        sql = f"SELECT * FROM ({sql}) USING SAMPLE {clause}"
    if limit:
        sql += f" LIMIT {int(limit)}"
    return sql


def connect() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    con.execute("SET preserve_insertion_order=true")
    return con


def raw_columns(path: str, con: duckdb.DuckDBPyConnection | None = None) -> list[tuple[str, str]]:
    con = con or connect()
    rows = con.execute(f"DESCRIBE SELECT * FROM {source_sql(path)}").fetchall()
    return [(r[0], r[1]) for r in rows]


def count_rows(path: str, where: str | None = None, con: duckdb.DuckDBPyConnection | None = None) -> int:
    con = con or connect()
    sql = f"SELECT count(*) FROM {source_sql(path)}" + (f" WHERE {where}" if where else "")
    return int(con.execute(sql).fetchone()[0])


def iter_raw(path: str, *, where: str | None = None, sample: str | None = None, limit: int | None = None,
             con: duckdb.DuckDBPyConnection | None = None) -> Iterator[dict[str, Any]]:
    """Stream raw rows as dicts, BATCH_SIZE at a time."""
    con = con or connect()
    # CSV is read as text so postcodes ("0800"), ABNs and phone numbers keep their leading zeros;
    # the normalisers parse numbers themselves.
    all_varchar = detect_format(path) == "csv"
    cur = con.execute(build_query(path, where=where, sample=sample, limit=limit, all_varchar=all_varchar))
    cols = [d[0] for d in cur.description]
    while True:
        batch = cur.fetchmany(BATCH_SIZE)
        if not batch:
            break
        for row in batch:
            yield dict(zip(cols, row))


def iter_records(path: str, *, where: str | None = None, sample: str | None = None, limit: int | None = None,
                 overrides: dict[str, str] | None = None) -> tuple[Mapping, Iterator[dict[str, Any]]]:
    """Return (mapping, iterator of canonical records)."""
    con = connect()
    cols = raw_columns(path, con)
    mapping = detect_mapping([c for c, _ in cols], overrides)
    json_cols = [c for c, t in cols if t.upper() == "JSON"]
    it = (normalise_row(decode_json_cols(r, json_cols), mapping)
          for r in iter_raw(path, where=where, sample=sample, limit=limit, con=con))
    return mapping, it


def decode_json_cols(row: dict[str, Any], json_cols: list[str]) -> dict[str, Any]:
    """DuckDB returns JSON-typed columns (mixed-type JSON fields) as JSON text; decode them."""
    for c in json_cols:
        v = row.get(c)
        if isinstance(v, str):
            try:
                row[c] = json.loads(v)
            except ValueError:
                pass
    return row


# --------------------------------------------------------------------------- #
# Normalisation helpers (all tolerant: bad input -> None, never raise)
# --------------------------------------------------------------------------- #
_NULL_STRINGS = {"", "null", "none", "nan", "n/a", "na", "-", "--", "unknown", "not available", "[]", "{}"}


def is_blank(v: Any) -> bool:
    if v is None:
        return True
    if isinstance(v, float) and math.isnan(v):
        return True
    if isinstance(v, str) and v.strip().lower() in _NULL_STRINGS:
        return True
    if isinstance(v, (list, tuple, dict)) and len(v) == 0:
        return True
    return False


def clean_str(v: Any) -> str | None:
    if is_blank(v):
        return None
    s = re.sub(r"\s+", " ", str(v)).strip()
    return s or None


def clean_domain(v: Any) -> str | None:
    """'https://www.Example.com.au/about?x=1' -> 'example.com.au'."""
    s = clean_str(v)
    if not s:
        return None
    s = s.lower().strip().strip("/")
    s = re.sub(r"^[a-z][a-z0-9+.-]*://", "", s)  # scheme
    s = s.split("/")[0].split("?")[0].split("#")[0]
    s = s.split("@")[-1]  # emails / credentials
    s = s.split(":")[0]  # port
    s = re.sub(r"^www\d*\.", "", s).strip(".")
    if not re.fullmatch(r"[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+", s):
        return None
    if "linkedin.com" in s:  # a LinkedIn URL is not the company's domain
        return None
    return s


def clean_url(v: Any) -> str | None:
    s = clean_str(v)
    if not s:
        return None
    if not re.match(r"^[a-z]+://", s, re.I):
        s = "https://" + s
    return s


_MULT = {"k": 1e3, "thousand": 1e3, "m": 1e6, "mm": 1e6, "mn": 1e6, "million": 1e6,
         "b": 1e9, "bn": 1e9, "billion": 1e9, "t": 1e12, "trillion": 1e12}
_NUM_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(thousand|million|billion|trillion|mm|mn|bn|k|m|b|t)?(?![a-z])", re.I)


def _numbers(s: str) -> list[float]:
    """All numbers in a string, with K/M/B suffixes applied; '1,000' -> 1000."""
    s = s.lower().replace(",", "")
    out = []
    for num, suf in _NUM_RE.findall(s):
        out.append(float(num) * _MULT.get(suf.lower(), 1.0) if suf else float(num))
    # A trailing suffix applies to a bare lower bound: "$1-10M" -> 1M-10M.
    m = _NUM_RE.findall(s)
    if len(m) == 2 and not m[0][1] and m[1][1]:
        out[0] = float(m[0][0]) * _MULT[m[1][1].lower()]
    return out


def parse_range(v: Any) -> tuple[float | None, str | None]:
    """Parse a number or range text -> (point estimate, raw range text or None).

    '51-200' -> (125.5, '51-200');  '10,001+' -> (10001, '10,001+');
    '<$1M' -> (500000, '<$1M');  '$1M-$10M' -> (5.5e6, ...);  '10M' -> (1e7, None).
    """
    if is_blank(v):
        return None, None
    if isinstance(v, bool):
        return None, None
    if isinstance(v, (int, float, decimal.Decimal)):
        f = float(v)
        return (None, None) if math.isnan(f) else (f, None)
    s = str(v).strip()
    nums = _numbers(s)
    if not nums:
        return None, s if s else None
    is_range = len(nums) >= 2 or bool(re.search(r"[+<>]|less than|under|over|more than|above|below|up to", s, re.I))
    if len(nums) >= 2:
        return (nums[0] + nums[1]) / 2, s
    n = nums[0]
    if re.search(r"<|less than|under|below|up to", s, re.I):
        return n / 2, s
    return n, (s if is_range else None)


def parse_employees(v: Any) -> tuple[int | None, str | None]:
    if isinstance(v, str) and re.search(r"self[- ]?employed|sole trader", v, re.I):
        return 1, v.strip()
    n, rng = parse_range(v)
    if n is None:
        return None, rng
    return max(0, int(round(n))), rng


def parse_money(v: Any) -> tuple[float | None, str | None]:
    n, rng = parse_range(v)
    return (float(n) if n is not None and n >= 0 else None), rng


def parse_float(v: Any) -> float | None:
    """'12.5%' -> 12.5; '0.4' -> 0.4. Percent fields are kept in source units."""
    if is_blank(v) or isinstance(v, bool):
        return None
    if isinstance(v, (int, float, decimal.Decimal)):
        f = float(v)
        return None if math.isnan(f) else f
    m = re.search(r"-?\d+(?:\.\d+)?", str(v).replace(",", ""))
    return float(m.group()) if m else None


def parse_int(v: Any) -> int | None:
    f = parse_float(v)
    return int(round(f)) if f is not None else None


def parse_year(v: Any) -> int | None:
    if is_blank(v):
        return None
    if isinstance(v, (dt.date, dt.datetime)):
        y = v.year
    elif isinstance(v, (int, float, decimal.Decimal)):
        y = int(v)
    else:
        m = re.search(r"\b(1[6-9]\d\d|20\d\d)\b", str(v))
        if not m:
            return None
        y = int(m.group(1))
    return y if 1600 <= y <= CURRENT_YEAR else None


_DATE_FORMATS = ("%Y-%m-%d", "%Y/%m/%d", "%d/%m/%Y", "%d-%m-%Y", "%m/%d/%Y", "%Y-%m", "%b %Y", "%B %Y",
                 "%d %b %Y", "%d %B %Y", "%b %d, %Y", "%B %d, %Y", "%Y")


def parse_date(v: Any) -> str | None:
    """Return an ISO date string (YYYY-MM-DD). Day-first wins for ambiguous AU dates."""
    if is_blank(v):
        return None
    if isinstance(v, dt.datetime):
        return v.date().isoformat()
    if isinstance(v, dt.date):
        return v.isoformat()
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        if v > 1e11:  # epoch milliseconds
            v = v / 1000
        if v > 1e8:  # epoch seconds
            return dt.datetime.fromtimestamp(v, tz=dt.timezone.utc).date().isoformat()
        y = parse_year(v)
        return f"{y}-01-01" if y else None
    s = str(v).strip()
    s10 = s[:10] if re.match(r"\d{4}-\d{2}-\d{2}", s) else s
    for fmt in _DATE_FORMATS:
        try:
            return dt.datetime.strptime(s10, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def parse_list(v: Any) -> list[str]:
    """Lists from native arrays, JSON strings, or comma/semicolon/pipe separated text."""
    if is_blank(v):
        return []
    items: list[Any]
    if isinstance(v, (list, tuple, set)):
        items = list(v)
    elif isinstance(v, dict):
        items = list(v.keys())
    else:
        s = str(v).strip()
        items = []
        if s[:1] in "[{":
            try:
                parsed = json.loads(s)
                items = list(parsed.keys()) if isinstance(parsed, dict) else list(parsed) \
                    if isinstance(parsed, list) else [parsed]
            except (ValueError, TypeError):
                s = s.strip("[]{}")
        if not items:
            sep = ";" if ";" in s else "|" if "|" in s else ","
            items = s.split(sep)
    out: list[str] = []
    seen: set[str] = set()
    for it in items:
        if isinstance(it, dict):  # e.g. [{"name": "Shopify", "category": ...}]
            it = it.get("name") or it.get("value") or next(iter(it.values()), None)
        s = clean_str(it)
        if s:
            s = s.strip("'\"")
            if s and s.lower() not in seen:
                seen.add(s.lower())
                out.append(s)
    return out


def size_band(n: int | None) -> str:
    if n is None:
        return "unknown"
    for upper, band in ((10, "1-10"), (50, "11-50"), (200, "51-200"), (500, "201-500"),
                        (1000, "501-1000"), (5000, "1001-5000")):
        if n <= upper:
            return band
    return "5000+"


def _json_safe(v: Any) -> Any:
    if isinstance(v, decimal.Decimal):
        return float(v)
    if isinstance(v, (dt.date, dt.datetime)):
        return v.isoformat()
    if isinstance(v, float) and math.isnan(v):
        return None
    if isinstance(v, (list, tuple)):
        return [_json_safe(x) for x in v]
    if isinstance(v, dict):
        return {str(k): _json_safe(x) for k, x in v.items()}
    if isinstance(v, (bytes, bytearray)):
        return v.hex()
    return v


def stable_id(rec: dict[str, Any]) -> str:
    basis = rec.get("domain") or "|".join(str(rec.get(k) or "") for k in ("name", "country", "state", "city"))
    return hashlib.sha1(basis.lower().encode()).hexdigest()[:16]


# --------------------------------------------------------------------------- #
# Row normalisation
# --------------------------------------------------------------------------- #
def normalise_row(raw: dict[str, Any], mapping: Mapping, as_of_year: int = CURRENT_YEAR) -> dict[str, Any]:
    """Raw source row -> canonical record (contract §1) + derived fields."""
    rec = empty_record()
    get = lambda f: raw.get(mapping.fields[f]) if f in mapping.fields else None  # noqa: E731

    for f in ("name", "description", "industry", "sub_industry", "country", "state", "city"):
        rec[f] = clean_str(get(f))
    code = get("industry_code")
    rec["industry_code"] = clean_str(int(code) if isinstance(code, float) and code.is_integer() else code)
    pc = get("postcode")
    rec["postcode"] = clean_str(int(pc) if isinstance(pc, float) and pc.is_integer() else pc)

    rec["website"] = clean_url(get("website"))
    rec["domain"] = clean_domain(get("domain")) or clean_domain(get("website"))
    if rec["website"] is None and rec["domain"]:
        rec["website"] = f"https://{rec['domain']}"
    li = clean_str(get("linkedin_url"))
    rec["linkedin_url"] = clean_url(li) if li else None

    # Employees: either column may hold a number or a range; count wins, range fills gaps.
    cnt, cnt_rng = parse_employees(get("employee_count"))
    rng_cnt, rng_txt = parse_employees(get("employee_range"))
    rec["employee_count"] = cnt if cnt is not None else rng_cnt
    rec["employee_range"] = clean_str(get("employee_range")) or cnt_rng or rng_txt

    rev, rev_rng = parse_money(get("revenue_usd"))
    rrng_val, rrng_txt = parse_money(get("revenue_range"))
    rec["revenue_usd"] = rev if rev is not None else rrng_val
    rec["revenue_range"] = clean_str(get("revenue_range")) or rev_rng or rrng_txt

    rec["founded_year"] = parse_year(get("founded_year"))
    rec["technologies"] = parse_list(get("technologies"))
    rec["keywords"] = parse_list(get("keywords"))
    rec["employee_growth_pct"] = parse_float(get("employee_growth_pct"))
    rec["funding_total_usd"] = parse_money(get("funding_total_usd"))[0]
    rec["last_funding_date"] = parse_date(get("last_funding_date"))
    rec["job_postings"] = parse_int(get("job_postings"))

    rec["extra"] = {c: _json_safe(raw.get(c)) for c in mapping.unmapped_columns if not is_blank(raw.get(c))}
    raw_id = clean_str(get("id"))
    if not rec["name"]:
        rec["name"] = rec["domain"] or "(unnamed)"
    rec["id"] = raw_id or stable_id(rec)
    return add_derived(rec, as_of_year)


def add_derived(rec: dict[str, Any], as_of_year: int = CURRENT_YEAR) -> dict[str, Any]:
    rec["size_band"] = size_band(rec.get("employee_count"))
    fy = rec.get("founded_year")
    rec["company_age_years"] = max(0, as_of_year - fy) if isinstance(fy, int) else None
    return rec


# --------------------------------------------------------------------------- #
# Profile command: run this first on the real data
# --------------------------------------------------------------------------- #
def profile(path: str, rows: int | None = 200_000, top: int = 5, out=None) -> dict[str, Any]:
    """Columns, types, null rates, distinct counts, top values + detected mapping.

    By default profiles a repeatable reservoir sample of `rows` rows (fast on
    an 11 GB file); pass rows=None to scan everything.
    """
    out = out or sys.stdout
    con = connect()
    cols = raw_columns(path, con)
    src = build_query(path, sample=str(rows) if rows else None)
    con.execute(f"CREATE TEMP VIEW src AS {src}")
    n = int(con.execute("SELECT count(*) FROM src").fetchone()[0])
    mapping = detect_mapping([c for c, _ in cols])
    reverse = {raw: canon for canon, raw in mapping.fields.items()}

    report: dict[str, Any] = {"input": path, "rows_profiled": n, "sampled": bool(rows), "columns": []}
    print(f"Profile of {path}  ({n:,} rows {'sampled' if rows else 'scanned'}; {len(cols)} columns)\n", file=out)
    for name, typ in cols:
        q = '"' + name.replace('"', '""') + '"'
        # Treat empty strings as null for the null rate.
        nulls, distinct = con.execute(
            f"SELECT count(*) FILTER (WHERE {q} IS NULL OR trim(CAST({q} AS VARCHAR)) = ''), "
            f"approx_count_distinct({q}) FROM src").fetchone()
        tops = con.execute(
            f"SELECT left(CAST({q} AS VARCHAR), 60) v, count(*) c FROM src WHERE {q} IS NOT NULL "
            f"GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT {top}").fetchall()
        null_rate = nulls / n if n else 0.0
        info = {"name": name, "type": typ, "null_rate": round(null_rate, 4), "approx_distinct": int(distinct),
                "top_values": [[v, int(c)] for v, c in tops], "maps_to": reverse.get(name)}
        report["columns"].append(info)
        print(f"- {name!r} [{typ}] nulls={null_rate:.1%} distinct~{distinct:,} -> "
              f"{reverse.get(name) or 'extra'}", file=out)
        print("    top: " + "; ".join(f"{v!r} ({c})" for v, c in tops), file=out)
    report["mapping"] = mapping.to_dict()
    print("\n" + mapping.describe(), file=out)

    # Show a few normalised rows so we can eyeball parsing quality.
    print("\nNormalised preview (3 rows):", file=out)
    for raw in con.execute("SELECT * FROM src LIMIT 3").fetchall():
        json_cols = [c for c, t in cols if t.upper() == "JSON"]
        rec = normalise_row(decode_json_cols(dict(zip([c for c, _ in cols], raw)), json_cols), mapping)
        rec["extra"] = f"<{len(rec['extra'])} cols>"
        print(json.dumps(rec, default=str, ensure_ascii=False)[:1500], file=out)
    return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m pipeline.ingest", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("profile", help="profile a raw file and show the detected column mapping")
    p.add_argument("--input", required=True)
    p.add_argument("--rows", type=int, default=200_000, help="reservoir-sample size (default 200k)")
    p.add_argument("--full", action="store_true", help="scan every row instead of sampling")
    p.add_argument("--top", type=int, default=5)
    p.add_argument("--json-out", help="also write the profile as JSON here")
    v = sub.add_parser("preview", help="print normalised canonical records")
    v.add_argument("--input", required=True)
    v.add_argument("--limit", type=int, default=5)
    v.add_argument("--sample", help="row count or percentage, e.g. 1000 or 5%%")
    v.add_argument("--where", help="SQL filter on raw columns")
    a = ap.parse_args(argv)

    if a.cmd == "profile":
        rep = profile(a.input, rows=None if a.full else a.rows, top=a.top)
        if a.json_out:
            Path(a.json_out).write_text(json.dumps(rep, indent=2, default=str))
    else:
        mapping, it = iter_records(a.input, where=a.where, sample=a.sample, limit=a.limit)
        print(mapping.describe(), file=sys.stderr)
        for rec in it:
            print(json.dumps(rec, default=str, ensure_ascii=False))
    return 0


__all__ = ["COLUMN_ALIASES", "iter_records", "normalise_row", "profile", "build_query", "size_band"]

if __name__ == "__main__":
    raise SystemExit(main())
