"""ICP fit + intent -> priority score, tier, segment, play, persona (contract §3).

fit_score    (0-100): is this the kind of company we sell to? size, industry risk tier, geography, data quality.
intent_score (0-100): does it need security *now*? sum of signal weights, capped per category.
priority     = w_fit * fit + w_intent * intent  (the app recomputes this when users move the sliders).

All weights live in SCORING_CONFIG.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from pipeline.schema import complete_record
from pipeline.signals import CATEGORIES, SIGNAL_CONFIG, classify_sector, compute_signals

SCORING_CONFIG: dict[str, Any] = {
    "version": "rules-v1",
    "weights": {"fit": 0.5, "intent": 0.5},
    "fit": {
        # Peaks at 51-500 staff: heavily attacked, material losses, rarely a security team.
        "size": {"1-10": 5, "11-50": 20, "51-200": 35, "201-500": 35, "501-1000": 26,
                 "1001-5000": 15, "5000+": 8, "unknown": 8},
        "industry_tiers": {
            1: (["healthcare", "finance", "insurance", "legal", "government", "critical_infrastructure"], 35),
            2: (["education", "technology", "retail_ecommerce", "professional_services", "manufacturing"], 22),
            3: (["hospitality", "nonprofit", "other"], 10),
        },
        "industry_unknown": 5,
        "geo": {"target_countries": ["australia"], "secondary_countries": ["new zealand"],
                "target": 15, "secondary": 8, "other": 3, "unknown": 5},
        # Data completeness / reachability (max 15).
        "data": {"domain": 5, "linkedin_url": 3, "description": 4, "employee_count": 3},
    },
    # Per-category caps so one category can't dominate intent (caps sum to 100).
    "intent_caps": {"regulatory": 25, "data_sensitivity": 20, "attack_surface": 15, "growth": 15,
                    "maturity_gap": 15, "timing": 10},
    "tiers": [("A", 75), ("B", 60), ("C", 40), ("D", 0)],
    # Play selection: first rule that matches wins (category points after caps).
    "plays": {
        "compliance": {"regulatory": 15},
        "scale_up": {"growth+timing": 18},
        "modernise_legacy": {"signal": "legacy_stack"},
        "exposed_surface": {"attack_surface": 12},
        "modernise": {"maturity_gap": 12},
    },
    "play_min_priority": 35,  # below this priority everything is "nurture"
    # need_level_from_rules: blend biased towards intent (need), thresholds on 0-100.
    "need": {"fit_weight": 0.35, "intent_weight": 0.65, "high": 58, "medium": 36},
}

COUNTRY_ALIASES = {"au": "australia", "aus": "australia", "nz": "new zealand", "nzl": "new zealand",
                   "us": "united states", "usa": "united states", "united states of america": "united states",
                   "uk": "united kingdom", "gb": "united kingdom", "gbr": "united kingdom"}


def norm_country(c: str | None) -> str | None:
    if not c:
        return None
    k = c.strip().lower()
    return COUNTRY_ALIASES.get(k, k)


def fit_breakdown(c: dict[str, Any], target_countries: list[str] | None = None) -> dict[str, int]:
    cfg = SCORING_CONFIG["fit"]
    size = cfg["size"].get(c.get("size_band") or "unknown", cfg["size"]["unknown"])

    sector = classify_sector(c)
    industry = cfg["industry_unknown"]
    if c.get("industry") or c.get("sub_industry") or c.get("keywords"):
        for sectors, pts in cfg["industry_tiers"].values():
            if sector in sectors:
                industry = pts
                break

    g = cfg["geo"]
    targets = [norm_country(t) for t in (target_countries or g["target_countries"])]
    country = norm_country(c.get("country"))
    geo = (g["unknown"] if country is None else g["target"] if country in targets
           else g["secondary"] if country in g["secondary_countries"] else g["other"])

    data = sum(pts for f, pts in cfg["data"].items() if c.get(f))
    return {"size": size, "industry": industry, "geo": geo, "data": data}


def intent_breakdown(signals: list[dict[str, Any]]) -> dict[str, int]:
    caps = SCORING_CONFIG["intent_caps"]
    raw = {cat: 0 for cat in CATEGORIES}
    for s in signals:
        raw[s["category"]] = raw.get(s["category"], 0) + int(s["weight"])
    return {cat: min(raw[cat], caps[cat]) for cat in CATEGORIES}


def tier_for(priority: float) -> str:
    return next(t for t, threshold in SCORING_CONFIG["tiers"] if priority >= threshold)


def size_segment(n: int | None) -> str:
    if n is None:
        return "Unknown-size"
    if n <= 50:
        return "Small business"
    if n <= 1000:
        return "Mid-market"
    return "Enterprise"


def segment_for(c: dict[str, Any]) -> str:
    return f"{size_segment(c.get('employee_count'))} {SIGNAL_CONFIG['sector_labels'][classify_sector(c)]}"


def persona_for(c: dict[str, Any]) -> str:
    """Buyer persona by size band (docs/RESEARCH.md §4), plus Risk & Compliance for finance/insurance."""
    n = c.get("employee_count")
    if n is None:
        p = "Head of IT / IT Manager"
    elif n <= 50:
        p = "Owner / CEO (often with an outsourced MSP)"
    elif n <= 200:
        p = "IT Manager (CFO/COO as economic buyer)"
    elif n <= 1000:
        p = "Head of IT / IT Director"
    else:
        p = "CISO / Head of Security"
    if classify_sector(c) in ("finance", "insurance"):
        p += " + Head of Risk & Compliance"
    return p


def play_for(intent: dict[str, int], priority: float, signal_ids: set[str] | None = None) -> str:
    """First matching play rule in SCORING_CONFIG['plays']; low-priority accounts are nurtured."""
    if priority < SCORING_CONFIG["play_min_priority"]:
        return "nurture"
    for name, rule in SCORING_CONFIG["plays"].items():
        play = name.split("_legacy")[0]
        for key, threshold in rule.items():
            if key == "signal":
                if threshold in (signal_ids or set()):
                    return play
            elif sum(intent.get(k, 0) for k in key.split("+")) >= threshold:
                return play
    return "nurture"


def score_company(c: dict[str, Any], signals: list[dict[str, Any]] | None = None, *,
                  weights: dict[str, float] | None = None, target_countries: list[str] | None = None,
                  as_of: dt.date | None = None) -> dict[str, Any]:
    """Return scoring fields for one canonical record (computes signals if not given)."""
    if signals is None:
        signals = compute_signals(c, as_of=as_of)
    w = weights or SCORING_CONFIG["weights"]
    fb = fit_breakdown(c, target_countries)
    ib = intent_breakdown(signals)
    fit = max(0, min(100, sum(fb.values())))
    intent = max(0, min(100, sum(ib.values())))
    priority = round(w["fit"] * fit + w["intent"] * intent)
    return {
        "fit_score": fit, "intent_score": intent, "priority_score": priority, "tier": tier_for(priority),
        "fit_breakdown": fb, "intent_breakdown": ib,
        "segment": segment_for(c), "play": play_for(ib, priority, {s["id"] for s in signals}),
        "persona": persona_for(c),
        "signals": sorted(signals, key=lambda s: -s["weight"]),
    }


def need_level_from_rules(account: dict[str, Any], as_of: dt.date | None = None) -> str:
    """Rules baseline for evals: map a canonical record or scored account to high/medium/low.

    Accepts a partial canonical record (missing keys are filled); computes signals and
    scores if the account isn't already scored.
    """
    if "fit_score" not in account or "intent_score" not in account:
        from pipeline.ingest import add_derived  # local import keeps scoring free of duckdb at import time

        rec = complete_record(account)
        if "size_band" not in account or "company_age_years" not in account:
            rec = add_derived(rec, (as_of or dt.date.today()).year)
        account = {**rec, **score_company(rec, as_of=as_of)}
    n = SCORING_CONFIG["need"]
    need = n["fit_weight"] * account["fit_score"] + n["intent_weight"] * account["intent_score"]
    return "high" if need >= n["high"] else "medium" if need >= n["medium"] else "low"
