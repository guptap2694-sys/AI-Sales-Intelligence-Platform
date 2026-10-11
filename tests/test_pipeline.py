"""Schema mapping + normalisation tests (pipeline/schema.py, pipeline/ingest.py)."""

from pathlib import Path

import pytest

from pipeline import ingest
from pipeline.schema import CANONICAL_FIELDS, complete_record, detect_mapping, norm_key

FIX = Path(__file__).parent / "fixtures"
CSV = str(FIX / "synthetic_companies.csv")
JSONL = str(FIX / "alt_schema_companies.jsonl")


# ---- mapping ---------------------------------------------------------------
def test_norm_key_is_case_space_underscore_insensitive():
    assert norm_key("Employee Count") == norm_key("employee_count") == norm_key("EMPLOYEE-COUNT") == "employeecount"


def test_firmable_style_columns_map_exactly():
    m = detect_mapping(["Company Name", "Website", "Industry", "Employee Count", "HQ State", "Country",
                        "Description", "Founded", "LinkedIn", "Technologies", "ABN"])
    assert m.fields["name"] == "Company Name"
    assert m.fields["website"] == "Website"
    assert m.fields["employee_count"] == "Employee Count"
    assert m.fields["state"] == "HQ State"
    assert m.fields["founded_year"] == "Founded"
    assert m.fields["linkedin_url"] == "LinkedIn"
    assert m.unmapped_columns == ["ABN"]
    assert all(h == "exact" for h in m.how.values())


def test_snake_case_aliases_map():
    m = detect_mapping(["organization_name", "company_domain", "num_employees", "tech_stack", "hq_region", "tags"])
    assert m.fields == {"name": "organization_name", "domain": "company_domain", "employee_count": "num_employees",
                        "technologies": "tech_stack", "state": "hq_region", "keywords": "tags"}


def test_fuzzy_match_catches_near_misses():
    m = detect_mapping(["Compnay Name", "Employe Count", "Totally Unrelated"])
    assert m.fields["name"] == "Compnay Name" and m.how["name"].startswith("fuzzy")
    assert m.fields["employee_count"] == "Employe Count"
    assert "Totally Unrelated" in m.unmapped_columns


def test_each_raw_column_used_once_and_overrides_win():
    m = detect_mapping(["Name", "Size"], overrides={"employee_count": "Size"})
    assert m.fields["employee_count"] == "Size" and m.how["employee_count"] == "override"
    assert "employee_range" not in m.fields
    assert len(set(m.fields.values())) == len(m.fields)


def test_mapping_reports_unmapped_canonical():
    m = detect_mapping(["Company Name"])
    assert "domain" in m.unmapped_canonical and "extra" not in m.unmapped_canonical
    assert 0 < m.coverage() < 1


# ---- normalisers -------------------------------------------------------------
@pytest.mark.parametrize("raw,expected", [
    ("https://www.Example.com.au/about?x=1", "example.com.au"),
    ("http://shop.example.co.nz:8080/", "shop.example.co.nz"),
    ("EXAMPLE.COM", "example.com"),
    ("info@example.org", "example.org"),
    ("not a domain", None),
    ("linkedin.com/company/foo", None),
    (None, None), ("", None), ("N/A", None),
])
def test_clean_domain(raw, expected):
    assert ingest.clean_domain(raw) == expected


@pytest.mark.parametrize("raw,count,rng", [
    ("51-200", 126, "51-200"), ("1,001-5,000", 3000, "1,001-5,000"), ("10,001+", 10001, "10,001+"),
    ("11 to 50 employees", 30, "11 to 50 employees"), (42, 42, None), ("120", 120, None), (12.0, 12, None),
    ("Self-employed", 1, "Self-employed"), (None, None, None), ("", None, None), ("unknown", None, None),
])
def test_parse_employees(raw, count, rng):
    assert ingest.parse_employees(raw) == (count, rng)


@pytest.mark.parametrize("raw,value", [
    ("$1M-$10M", 5.5e6), ("10M", 1e7), ("750K", 7.5e5), ("$1.2B", 1.2e9), ("<$1M", 5e5),
    ("$1-10M", 5.5e6), ("2.5 million", 2.5e6), (3_000_000, 3e6), ("12000000", 1.2e7), ("", None), (None, None),
])
def test_parse_money(raw, value):
    assert ingest.parse_money(raw)[0] == pytest.approx(value) if value else ingest.parse_money(raw)[0] is None


@pytest.mark.parametrize("raw,year", [("2015", 2015), (1999, 1999), ("01/07/2011", 2011), ("Founded in 1987", 1987),
                                      ("3000", None), ("", None), (None, None), (2015.0, 2015)])
def test_parse_year(raw, year):
    assert ingest.parse_year(raw) == year


@pytest.mark.parametrize("raw,expected", [
    ("WordPress; PHP", ["WordPress", "PHP"]), ("a, b, c", ["a", "b", "c"]), ('["AWS", "Azure"]', ["AWS", "Azure"]),
    (["X", "x", " Y "], ["X", "Y"]), ("solo", ["solo"]), ("", []), (None, []), ("a | b", ["a", "b"]),
    ('[{"name": "Shopify"}]', ["Shopify"]),
])
def test_parse_list(raw, expected):
    assert ingest.parse_list(raw) == expected


@pytest.mark.parametrize("raw,iso", [("2026-03-15", "2026-03-15"), ("15/08/2026", "2026-08-15"),
                                     ("2026-05-02T10:00:00Z", "2026-05-02"), ("May 2024", "2024-05-01"),
                                     ("garbage", None), (None, None)])
def test_parse_date(raw, iso):
    assert ingest.parse_date(raw) == iso


def test_parse_float_percent():
    assert ingest.parse_float("25%") == 25.0
    assert ingest.parse_float("-3.5") == -3.5
    assert ingest.parse_float("n/a") is None


@pytest.mark.parametrize("n,band", [(None, "unknown"), (1, "1-10"), (10, "1-10"), (11, "11-50"), (200, "51-200"),
                                    (201, "201-500"), (1000, "501-1000"), (5000, "1001-5000"), (5001, "5000+")])
def test_size_band(n, band):
    assert ingest.size_band(n) == band


# ---- end-to-end ingest -------------------------------------------------------
def _records(path, **kw):
    mapping, it = ingest.iter_records(path, **kw)
    return mapping, list(it)


def test_csv_ingest_produces_complete_canonical_records():
    mapping, recs = _records(CSV)
    assert len(recs) == 150
    for r in recs:
        assert set(CANONICAL_FIELDS) <= set(r)
        assert isinstance(r["technologies"], list) and isinstance(r["keywords"], list)
        assert isinstance(r["extra"], dict)
        assert r["size_band"] in ("1-10", "11-50", "51-200", "201-500", "501-1000", "1001-5000", "5000+", "unknown")
        assert r["id"] and r["name"]
    assert len({r["id"] for r in recs}) == len(recs)
    assert {"ABN", "Phone"} == set(mapping.unmapped_columns)


def test_csv_edge_cases():
    _, recs = _records(CSV)
    by_name = {r["name"]: r for r in recs}
    blank = by_name["Edge Case: All Blank"]
    assert blank["domain"] is None and blank["employee_count"] is None and blank["technologies"] == []
    nameless = by_name["nameless-example.com.au"]  # no name -> falls back to domain
    assert nameless["domain"] == "nameless-example.com.au"
    bank = by_name["Edge Case Megacorp Bank"]
    assert bank["employee_count"] == 10001 and bank["size_band"] == "5000+"
    assert bank["revenue_usd"] == 1e9 and bank["founded_year"] == 1817
    assert bank["technologies"][:2] == ["Microsoft Azure", "CrowdStrike"]
    sole = by_name["Edge Case Sole Trader Plumbing"]
    assert sole["employee_count"] == 1
    # Postcodes keep leading zeros because CSV is read as text.
    assert any(r["postcode"] == "0800" for r in recs) or not any(r["state"] == "NT" for r in recs)


def test_jsonl_alt_schema_maps_and_normalises():
    mapping, recs = _records(JSONL)
    assert mapping.fields["name"] == "organization_name"
    assert mapping.fields["city"] == "hq_town"
    assert mapping.unmapped_columns == ["abn_number"]
    by_name = {r["name"]: r for r in recs}
    opal = by_name["Opal Ledger Pty Ltd"]
    assert opal["technologies"] == ["AWS", "Kubernetes", "Stripe"]  # JSON-in-a-string
    assert opal["revenue_usd"] == 3e7 and opal["revenue_range"] == "$10M-$50M"
    assert opal["funding_total_usd"] == 1.8e7 and opal["last_funding_date"] == "2026-05-02"
    kestrel = by_name["Kestrel Dental Studio"]
    assert kestrel["domain"] == "kestreldental.example.com.au"
    assert kestrel["employee_count"] == 30 and kestrel["employee_range"] == "11-50"
    assert kestrel["keywords"] == ["dental", "patients", "online booking"]
    assert kestrel["extra"] == {"abn_number": "12 345 678 901"}


def test_limit_sample_where():
    assert len(_records(CSV, limit=10)[1]) == 10
    s1 = [r["id"] for r in _records(CSV, sample="20")[1]]
    s2 = [r["id"] for r in _records(CSV, sample="20")[1]]
    assert len(s1) == 20 and s1 == s2  # repeatable
    pct = _records(CSV, sample="50%", where="Country = 'Australia'")[1]
    assert 0 < len(pct) < 150 and all(r["country"] == "Australia" for r in pct)
    au = _records(CSV, where="Country = 'Australia'")[1]
    assert au and all(r["country"] == "Australia" for r in au)


def test_parquet_roundtrip(tmp_path):
    import duckdb
    pq = tmp_path / "x.parquet"
    duckdb.sql(f"COPY (SELECT * FROM read_csv('{CSV}', all_varchar=true)) TO '{pq}' (FORMAT parquet)")
    _, recs = _records(str(pq))
    assert len(recs) == 150


def test_profile_runs(capsys):
    rep = ingest.profile(CSV, rows=None)
    out = capsys.readouterr().out
    assert "Mapping coverage" in out and rep["rows_profiled"] == 150
    col = next(c for c in rep["columns"] if c["name"] == "Company Name")
    assert col["maps_to"] == "name" and 0 <= col["null_rate"] <= 1 and col["top_values"]


def test_complete_record_fills_missing_keys():
    r = complete_record({"name": "X", "technologies": None})
    assert set(CANONICAL_FIELDS) <= set(r) and r["technologies"] == [] and r["extra"] == {}


def test_clean_country_normalises_codes():
    from pipeline.ingest import clean_country
    assert clean_country("AU") == "Australia"
    assert clean_country(" usa ") == "United States"
    assert clean_country("Narnia") == "Narnia"
    assert clean_country(None) is None
