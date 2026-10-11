"""Canonical company schema + raw-column alias mapping.

This is the ONLY module that knows about raw source column names. Everything
downstream (signals, scoring, app, evals, LLM features) works on the canonical
record defined in docs/CONTRACT.md §1.

Mapping strategy (see `detect_mapping`):
  1. normalise every raw column name and alias: lowercase, drop everything that
     is not a letter or digit ("Employee Count" == "employee_count" == "EmployeeCount");
  2. exact match against the alias table (first alias hit wins, each raw column
     is used at most once, canonical fields are claimed in priority order);
  3. fuzzy match (difflib ratio >= FUZZY_THRESHOLD) for whatever is still unmapped.
Columns that map to nothing are carried through untouched in `record["extra"]`.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from typing import Any

# Canonical fields in contract order, with their "empty" value.
CANONICAL_FIELDS: dict[str, Any] = {
    "id": None,
    "name": None,
    "domain": None,
    "description": None,
    "industry": None,
    "sub_industry": None,
    "industry_code": None,
    "employee_count": None,
    "employee_range": None,
    "revenue_usd": None,
    "revenue_range": None,
    "founded_year": None,
    "country": None,
    "state": None,
    "city": None,
    "postcode": None,
    "linkedin_url": None,
    "website": None,
    "technologies": [],
    "keywords": [],
    "employee_growth_pct": None,
    "funding_total_usd": None,
    "last_funding_date": None,
    "job_postings": None,
    "extra": {},
}
LIST_FIELDS = ("technologies", "keywords")
# Added by the pipeline after normalisation.
DERIVED_FIELDS = ("size_band", "company_age_years")
SIZE_BANDS = ("1-10", "11-50", "51-200", "201-500", "501-1000", "1001-5000", "5000+", "unknown")

# Generous alias table: canonical field -> raw names seen in Firmable, Apollo,
# ZoomInfo, Crunchbase, LinkedIn, PDL, D&B and generic CRM exports.
# Order inside a list does not matter (all are normalised); order of the dict
# matters only for tie-breaking (earlier fields claim a column first).
COLUMN_ALIASES: dict[str, list[str]] = {
    "id": ["id", "company id", "company_id", "firmable id", "organization id", "org id", "uuid",
           "record id", "account id", "entity id", "pdl id", "crunchbase uuid", "duns", "duns number"],
    "name": ["name", "company name", "company", "organization name", "organisation name", "org name",
             "account name", "business name", "trading name", "legal name", "entity name", "company legal name",
             "registered name", "display name"],
    "domain": ["domain", "company domain", "website domain", "primary domain", "root domain", "email domain",
               "web domain", "domain name"],
    "description": ["description", "company description", "short description", "long description", "about",
                    "about us", "overview", "summary", "company overview", "business description", "bio",
                    "tagline", "seo description", "linkedin description"],
    "industry": ["industry", "industry name", "primary industry", "main industry", "sector", "industry sector",
                 "linkedin industry", "industry group", "category", "vertical", "industries"],
    "sub_industry": ["sub industry", "subindustry", "secondary industry", "sub sector", "subsector",
                     "industry subcategory", "sub category", "subcategory", "niche", "industry detail"],
    "industry_code": ["industry code", "naics", "naics code", "primary naics", "sic", "sic code", "primary sic",
                      "anzsic", "anzsic code", "anzsic class", "isic", "nace"],
    "employee_count": ["employee count", "employees", "employee_count", "number of employees", "num employees",
                       "no of employees", "headcount", "staff count", "employee total", "total employees",
                       "estimated employees", "employees count", "size employees", "company headcount",
                       "linkedin employees", "employee number", "staff"],
    "employee_range": ["employee range", "employees range", "employee size", "company size", "size",
                       "size range", "headcount range", "employee band", "staff range", "employee count range",
                       "size band", "employees bucket"],
    "revenue_usd": ["revenue", "annual revenue", "revenue usd", "estimated revenue", "revenue amount",
                    "turnover", "annual turnover", "sales", "total revenue", "revenue estimate"],
    "revenue_range": ["revenue range", "revenue band", "turnover range", "estimated revenue range",
                      "annual revenue range", "revenue bucket", "turnover band"],
    "founded_year": ["founded", "founded year", "year founded", "founding year", "established",
                     "year established", "inception year", "founded date", "incorporation year",
                     "date founded", "incorporated", "registration date"],
    "country": ["country", "hq country", "headquarters country", "country name", "company country",
                "location country", "country code", "hq country code", "nation"],
    "state": ["state", "hq state", "region", "hq region", "province", "state province", "state region",
              "headquarters state", "location state", "county", "territory"],
    "city": ["city", "hq city", "headquarters city", "town", "hq town", "hq locality", "location town", "suburb", "locality", "location city", "hq suburb"],
    "postcode": ["postcode", "post code", "postal code", "zip", "zip code", "hq postcode", "hq postal code"],
    "linkedin_url": ["linkedin", "linkedin url", "linkedin profile", "company linkedin", "linkedin company url",
                     "linkedin link", "linkedin page", "li url"],
    "website": ["website", "website url", "url", "company website", "homepage", "web", "web address",
                "site", "company url", "domain url"],
    "technologies": ["technologies", "technology", "tech stack", "techstack", "tech", "technographics",
                     "technologies used", "installed technologies", "tools", "software used", "tech used"],
    "keywords": ["keywords", "tags", "specialties", "specialities", "services", "products", "categories",
                 "company keywords", "business keywords", "topics", "labels"],
    "employee_growth_pct": ["employee growth", "headcount growth", "employee growth pct", "growth rate",
                            "headcount growth 12m", "employee growth 12m", "headcount growth (12m)",
                            "headcount growth rate", "yoy headcount growth", "employee growth rate",
                            "12 month growth", "growth 12m", "headcount change", "staff growth"],
    "funding_total_usd": ["total funding", "funding total", "total funding usd", "funding", "total raised",
                          "funding amount", "total funding amount", "amount raised", "capital raised",
                          "total funding raised"],
    "last_funding_date": ["last funding date", "latest funding date", "last funding round date",
                          "last round date", "most recent funding date", "funding date", "last raised at",
                          "last funding at", "latest funding round date"],
    "job_postings": ["job postings", "open jobs", "jobs", "job openings", "open roles", "open positions",
                     "active job postings", "num job postings", "hiring count", "job count", "vacancies",
                     "current job openings"],
}

FUZZY_THRESHOLD = 0.86


def norm_key(name: str) -> str:
    """Case/space/underscore/punctuation-insensitive key for column matching."""
    return re.sub(r"[^a-z0-9]", "", str(name).lower())


@dataclass
class Mapping:
    """Result of mapping raw columns onto canonical fields."""

    fields: dict[str, str] = field(default_factory=dict)  # canonical -> raw column
    how: dict[str, str] = field(default_factory=dict)  # canonical -> "exact" | "fuzzy:0.91"
    unmapped_columns: list[str] = field(default_factory=list)  # raw columns -> extra

    @property
    def mapped_canonical(self) -> list[str]:
        return [f for f in CANONICAL_FIELDS if f in self.fields]

    @property
    def unmapped_canonical(self) -> list[str]:
        return [f for f in CANONICAL_FIELDS if f not in self.fields and f != "extra"]

    def coverage(self) -> float:
        total = len(CANONICAL_FIELDS) - 1  # exclude `extra`
        return len(self.fields) / total if total else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "fields": self.fields,
            "how": self.how,
            "mapped_canonical": self.mapped_canonical,
            "unmapped_canonical": self.unmapped_canonical,
            "unmapped_columns": self.unmapped_columns,
            "coverage": round(self.coverage(), 3),
        }

    def describe(self) -> str:
        lines = [f"Mapping coverage: {len(self.fields)}/{len(CANONICAL_FIELDS) - 1} canonical fields"]
        for f in self.mapped_canonical:
            lines.append(f"  {f:<20} <- {self.fields[f]!r} ({self.how[f]})")
        if self.unmapped_canonical:
            lines.append("  unmapped canonical: " + ", ".join(self.unmapped_canonical))
        if "domain" not in self.fields and "website" in self.fields:
            lines.append("  (domain is derived from website)")
        if self.unmapped_columns:
            lines.append("  raw columns -> extra: " + ", ".join(map(repr, self.unmapped_columns)))
        return "\n".join(lines)


def detect_mapping(columns: list[str], overrides: dict[str, str] | None = None) -> Mapping:
    """Map raw column names to canonical fields.

    `overrides` (canonical -> raw column) always wins; use it once the real
    dataset is known and the automatic mapping picks something wrong.
    """
    m = Mapping()
    used: set[str] = set()
    for canon, raw in (overrides or {}).items():
        if canon in CANONICAL_FIELDS and raw in columns:
            m.fields[canon], m.how[canon] = raw, "override"
            used.add(raw)

    by_key: dict[str, str] = {}
    for col in columns:
        by_key.setdefault(norm_key(col), col)

    # Pass 1: exact (normalised) alias match.
    for canon, aliases in COLUMN_ALIASES.items():
        if canon in m.fields:
            continue
        for alias in [canon, *aliases]:
            col = by_key.get(norm_key(alias))
            if col is not None and col not in used:
                m.fields[canon], m.how[canon] = col, "exact"
                used.add(col)
                break

    # Pass 2: fuzzy match for the remainder; best (field, column) pairs first.
    candidates: list[tuple[float, str, str]] = []
    for canon, aliases in COLUMN_ALIASES.items():
        if canon in m.fields:
            continue
        for col in columns:
            if col in used:
                continue
            ck = norm_key(col)
            if not ck:
                continue
            score = max(difflib.SequenceMatcher(None, ck, norm_key(a)).ratio() for a in [canon, *aliases])
            if score >= FUZZY_THRESHOLD:
                candidates.append((score, canon, col))
    for score, canon, col in sorted(candidates, key=lambda t: -t[0]):
        if canon in m.fields or col in used:
            continue
        m.fields[canon], m.how[canon] = col, f"fuzzy:{score:.2f}"
        used.add(col)

    m.unmapped_columns = [c for c in columns if c not in used]
    return m


def empty_record() -> dict[str, Any]:
    """A canonical record with every key present and empty values."""
    return {k: (list(v) if isinstance(v, list) else dict(v) if isinstance(v, dict) else v)
            for k, v in CANONICAL_FIELDS.items()}


def complete_record(rec: dict[str, Any]) -> dict[str, Any]:
    """Fill any missing canonical keys (e.g. hand-written eval cases)."""
    out = empty_record()
    out.update({k: v for k, v in rec.items()})
    for k in LIST_FIELDS:
        if out[k] is None:
            out[k] = []
    if out["extra"] is None:
        out["extra"] = {}
    return out
