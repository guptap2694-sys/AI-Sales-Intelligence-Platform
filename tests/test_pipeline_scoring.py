"""Signals + scoring tests (pipeline/signals.py, pipeline/scoring.py)."""

import datetime as dt

import pytest

from pipeline.ingest import add_derived
from pipeline.schema import complete_record
from pipeline.scoring import (SCORING_CONFIG, need_level_from_rules, persona_for, score_company, segment_for,
                              tier_for)
from pipeline.signals import CATEGORIES, classify_sector, compute_signals

AS_OF = dt.date(2026, 10, 11)


def rec(**kw):
    return add_derived(complete_record(kw), AS_OF.year)


def ids(signals):
    return {s["id"] for s in signals}


CLINIC = rec(name="Synthetic Clinic", industry="Hospital & Health Care", country="Australia", employee_count=120,
             description="Medical clinics storing patient medical records; telehealth and online booking.",
             technologies=["WordPress", "Windows Server 2012", "Microsoft 365"], domain="clinic.example.com.au")
CAFE = rec(name="Synthetic Cafe", industry="Restaurants", country="Australia", employee_count=3)


# ---- signals -------------------------------------------------------------------
def test_signal_shape_and_evidence():
    sigs = compute_signals(CLINIC, as_of=AS_OF)
    assert sigs
    for s in sigs:
        assert set(s) == {"id", "label", "category", "weight", "evidence"}
        assert s["category"] in CATEGORIES
        assert isinstance(s["weight"], int) and s["weight"] > 0
        assert s["evidence"] and isinstance(s["evidence"], str)


def test_clinic_signals():
    got = ids(compute_signals(CLINIC, as_of=AS_OF))
    assert {"regulated_industry", "handles_health_records", "privacy_act_threshold", "exposed_cms", "legacy_stack",
            "target_without_soc", "maturity_gap_no_security", "insurance_control_gap", "remote_services"} <= got
    reg = next(s for s in compute_signals(CLINIC, as_of=AS_OF) if s["id"] == "regulated_industry")
    assert "Hospital & Health Care" in reg["evidence"] and "NDB" in reg["label"]


@pytest.mark.parametrize("record", [{}, {"name": None}, {"technologies": None, "keywords": None},
                                    {"employee_count": None, "description": ""}, {"extra": None}])
def test_missing_fields_never_crash(record):
    assert compute_signals(record, as_of=AS_OF) == [] or isinstance(compute_signals(record, as_of=AS_OF), list)
    assert compute_signals(complete_record(record), as_of=AS_OF) == []


def test_micro_cafe_has_no_signals():
    assert compute_signals(CAFE, as_of=AS_OF) == []


def test_apra_and_fin_supply_chain():
    bank = rec(industry="Banking", country="AU", employee_count=300, revenue_usd=5e7)
    got = ids(compute_signals(bank, as_of=AS_OF))
    assert {"regulated_industry", "apra_cps234", "privacy_act_threshold"} <= got
    supplier = rec(industry="Computer Software", country="Australia", employee_count=80,
                   description="SaaS platform for banks and super funds.")
    got = ids(compute_signals(supplier, as_of=AS_OF))
    assert {"financial_supply_chain", "saas_vendor_soc2", "saas_platform"} <= got
    assert "apra_cps234" not in got


def test_privacy_act_threshold_uses_employee_proxy_when_no_revenue():
    s = next(s for s in compute_signals(rec(industry="Construction", country="Australia", employee_count=40),
                                        as_of=AS_OF) if s["id"] == "privacy_act_threshold")
    assert "proxy" in s["evidence"]
    small_rev = rec(industry="Construction", country="Australia", employee_count=40, revenue_usd=1e6)
    assert "privacy_act_threshold" not in ids(compute_signals(small_rev, as_of=AS_OF))
    us = rec(industry="Construction", country="United States", employee_count=40)
    assert "privacy_act_threshold" not in ids(compute_signals(us, as_of=AS_OF))


def test_critical_infrastructure_soci():
    util = rec(industry="Utilities", sub_industry="Electricity", country="Australia", employee_count=600)
    s = next(s for s in compute_signals(util, as_of=AS_OF) if s["id"] == "critical_infrastructure")
    assert "SOCI" in s["label"] and s["weight"] == 8
    school = rec(industry="Primary/Secondary Education", country="Australia", employee_count=80)
    assert "critical_infrastructure" not in ids(compute_signals(school, as_of=AS_OF))
    uni = rec(industry="Higher Education", description="University with three campuses.", country="Australia",
              employee_count=2000)
    assert "critical_infrastructure" in ids(compute_signals(uni, as_of=AS_OF))


def test_growth_and_timing_signals():
    r = rec(industry="Computer Software", country="Australia", employee_count=100, employee_growth_pct=40,
            job_postings=12, funding_total_usd=2e7, last_funding_date="2026-05-01", founded_year=2021)
    got = ids(compute_signals(r, as_of=AS_OF))
    assert {"rapid_growth", "hiring_signal", "venture_funded", "young_scaling", "recent_funding", "rapid_hiring",
            "hypergrowth"} <= got
    old = rec(last_funding_date="2019-01-01")
    assert "recent_funding" not in ids(compute_signals(old, as_of=AS_OF))


def test_hiring_security_roles_from_extra_jobs_column():
    r = rec(employee_count=200, extra={"Open Job Titles": "Senior Security Engineer; Accountant"})
    s = next(s for s in compute_signals(r, as_of=AS_OF) if s["id"] == "hiring_security_roles")
    assert s["category"] == "timing" and "Open Job Titles" in s["evidence"]


def test_security_stack_suppresses_gap_signals():
    r = rec(employee_count=200, technologies=["Okta", "CrowdStrike", "Veeam", "Microsoft 365"],
            description="Accounting firm.")
    got = ids(compute_signals(r, as_of=AS_OF))
    assert "maturity_gap_no_security" not in got and "insurance_control_gap" not in got
    # No tech data -> unknown, so no tech-gap signals either.
    assert not ids(compute_signals(rec(employee_count=200), as_of=AS_OF)) & {"maturity_gap_no_security",
                                                                              "insurance_control_gap"}


def test_keyword_matching_uses_word_boundaries():
    r = rec(description="We sell posters and apiary supplies")  # 'pos' / 'api' must not match inside words
    got = ids(compute_signals(r, as_of=AS_OF))
    assert "handles_payments" not in got and "saas_platform" not in got


def test_sector_classification():
    assert classify_sector({"industry": "Insurance"}) == "insurance"
    assert classify_sector({"industry": "Hospital & Health Care"}) == "healthcare"
    assert classify_sector({"industry": None, "keywords": ["law firm"]}) == "legal"
    assert classify_sector({}) == "other"


def test_deterministic():
    assert compute_signals(CLINIC, as_of=AS_OF) == compute_signals(CLINIC, as_of=AS_OF)


# ---- scoring -------------------------------------------------------------------
@pytest.mark.parametrize("r", [CLINIC, CAFE, rec(), rec(employee_count=50000, industry="Banking", country="AU")])
def test_score_bounds_and_shape(r):
    s = score_company(r, as_of=AS_OF)
    for k in ("fit_score", "intent_score", "priority_score"):
        assert 0 <= s[k] <= 100 and isinstance(s[k], int)
    assert set(s["fit_breakdown"]) == {"size", "industry", "geo", "data"}
    assert sum(s["fit_breakdown"].values()) == s["fit_score"]
    assert set(s["intent_breakdown"]) == set(CATEGORIES)
    assert s["tier"] in "ABCD"
    assert s["play"] in ("compliance", "scale_up", "exposed_surface", "modernise", "nurture")
    for cat, pts in s["intent_breakdown"].items():
        assert pts <= SCORING_CONFIG["intent_caps"][cat]


def test_intent_category_cap():
    many = [{"id": f"x{i}", "label": "x", "category": "growth", "weight": 10, "evidence": "e"} for i in range(10)]
    s = score_company(CAFE, many)
    assert s["intent_breakdown"]["growth"] == SCORING_CONFIG["intent_caps"]["growth"]
    assert s["intent_score"] == SCORING_CONFIG["intent_caps"]["growth"]


def test_priority_weights():
    s = score_company(CLINIC, weights={"fit": 1.0, "intent": 0.0}, as_of=AS_OF)
    assert s["priority_score"] == s["fit_score"]


def test_ranking_sanity():
    assert score_company(CLINIC, as_of=AS_OF)["priority_score"] > score_company(CAFE, as_of=AS_OF)["priority_score"]
    assert score_company(CAFE, as_of=AS_OF)["play"] == "nurture"


def test_size_fit_peaks_51_to_500():
    size = {b: score_company(rec(employee_count=n), [])["fit_breakdown"]["size"]
            for b, n in (("micro", 5), ("small", 30), ("mid", 120), ("upper", 400), ("large", 800), ("ent", 9000))}
    assert size["mid"] == size["upper"] == max(size.values())
    assert size["micro"] < size["small"] < size["mid"] and size["ent"] < size["large"] < size["upper"]


def test_geo_target_countries_configurable():
    au = score_company(rec(country="Australia"), [])["fit_breakdown"]["geo"]
    us = score_company(rec(country="United States"), [])["fit_breakdown"]["geo"]
    assert au > us
    us_target = score_company(rec(country="USA"), [], target_countries=["United States"])["fit_breakdown"]["geo"]
    assert us_target == au


@pytest.mark.parametrize("p,t", [(100, "A"), (75, "A"), (74.9, "B"), (60, "B"), (40, "C"), (39, "D"), (0, "D")])
def test_tiers(p, t):
    assert tier_for(p) == t


def test_persona_and_segment():
    assert persona_for(rec(employee_count=20)).startswith("Owner / CEO")
    assert persona_for(rec(employee_count=150)).startswith("IT Manager")
    assert persona_for(rec(employee_count=600)).startswith("Head of IT")
    assert persona_for(rec(employee_count=6000)).startswith("CISO")
    assert "Risk & Compliance" in persona_for(rec(employee_count=150, industry="Financial Services"))
    assert segment_for(CLINIC) == "Mid-market Healthcare"
    assert segment_for(CAFE) == "Small business Hospitality"


def test_plays():
    assert score_company(CLINIC, as_of=AS_OF)["play"] == "compliance"
    legacy = rec(industry="Construction", country="Australia", employee_count=150,
                 technologies=["Exchange Server", "Windows Server 2012", "MYOB AccountRight"])
    assert score_company(legacy, as_of=AS_OF)["play"] == "modernise"
    scale = rec(industry="Marketing & Advertising", country="Australia", employee_count=120, employee_growth_pct=45,
                last_funding_date="2026-06-01", job_postings=15, domain="x.example.com")
    assert score_company(scale, as_of=AS_OF)["play"] == "scale_up"


def test_need_level_from_rules_accepts_partial_records_and_scored_accounts():
    assert need_level_from_rules({"name": "x"}) == "low"
    assert need_level_from_rules({**CLINIC}, as_of=AS_OF) == "high"
    assert need_level_from_rules({"fit_score": 20, "intent_score": 10}) == "low"
    assert need_level_from_rules({"fit_score": 60, "intent_score": 40}) in ("medium", "high")
