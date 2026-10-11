"""End-to-end build tests (pipeline/build.py). Never touches the repo's traces/ or app/data/."""

import json
import sys
import types
from pathlib import Path

import pytest

from pipeline.build import build, main

FIX = Path(__file__).parent / "fixtures"
CSV = str(FIX / "synthetic_companies.csv")
JSONL = str(FIX / "alt_schema_companies.jsonl")
ACCOUNT_KEYS = {"id", "name", "domain", "description", "industry", "country", "state", "city", "employee_count",
                "size_band", "revenue_usd", "founded_year", "technologies", "linkedin_url", "website", "fit_score",
                "intent_score", "priority_score", "tier", "fit_breakdown", "intent_breakdown", "segment", "play",
                "persona", "signals", "llm"}


@pytest.fixture(autouse=True)
def isolate_llm(monkeypatch, tmp_path):
    """No real API key and a throwaway trace file for every test in this module."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("LLM_TRACE_PATH", str(tmp_path / "llm_calls.jsonl"))


def test_build_end_to_end(tmp_path, capsys):
    out = tmp_path / "accounts.json"
    res = build(CSV, str(out), top=50)
    data = json.loads(out.read_text())
    assert data == json.loads(json.dumps(res, default=str))
    meta = data["meta"]
    assert meta["synthetic"] is True and meta["rows_total"] == 150 and meta["rows_included"] == 50
    assert meta["scoring_version"] == "rules-v1" and meta["weights"] == {"fit": 0.5, "intent": 0.5}
    assert meta["llm"]["enabled"] is False
    accs = data["accounts"]
    assert len(accs) == 50
    pri = [a["priority_score"] for a in accs]
    assert pri == sorted(pri, reverse=True)
    for a in accs:
        assert ACCOUNT_KEYS <= set(a)
        assert a["llm"] is None
        assert a["tier"] == ("A" if a["priority_score"] >= 75 else "B" if a["priority_score"] >= 60
                             else "C" if a["priority_score"] >= 40 else "D")
    assert set(data["facets"]) >= {"industries", "countries", "states", "size_bands", "signal_ids", "segments"}
    assert set(data["facets"]["signal_ids"]) == {s["id"] for a in accs for s in a["signals"]}
    printed = capsys.readouterr().out
    assert "tiers (all rows)" in printed and "Mapping coverage" in printed


def test_top_n_matches_full_ranking(tmp_path):
    full = build(CSV, str(tmp_path / "a.json"), top=1000, quiet=True)["accounts"]
    top = build(CSV, str(tmp_path / "b.json"), top=10, quiet=True)["accounts"]
    assert [a["id"] for a in top] == [a["id"] for a in full[:10]]


def test_build_alt_schema_and_cli(tmp_path):
    out = tmp_path / "alt.json"
    assert main(["--input", JSONL, "--out", str(out), "--as-of", "2026-10-11", "--fit-weight", "0.3"]) == 0
    data = json.loads(out.read_text())
    assert data["meta"]["rows_total"] == 6 and data["meta"]["weights"] == {"fit": 0.3, "intent": 0.7}
    names = [a["name"] for a in data["accounts"]]
    assert names[-1] == "Wattle Cafe"  # micro cafe with no data ranks last


def test_llm_top_skips_without_key(tmp_path, capsys):
    data = build(CSV, str(tmp_path / "x.json"), top=5, llm_top=3, quiet=True)
    assert data["meta"]["llm"]["enabled"] is False
    assert all(a["llm"] is None for a in data["accounts"])
    assert "ANTHROPIC_API_KEY" in capsys.readouterr().err


def test_llm_top_with_stubbed_features(tmp_path, monkeypatch):
    """Stub llm.features.* in sys.modules: exercises the wiring without any LLM client or trace writes."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-not-a-real-key")
    calls = []

    def classify(company, signals=None, version=None):
        calls.append(("classify", company["id"], version))
        return {"need_level": "high", "confidence": 0.9, "drivers": ["regulatory"], "rationale": "r",
                "model": "stub-model"}

    def brief(company, signals=None, need=None, version=None):
        return f"brief for {company['name']}"

    def draft(company, signals, persona=None, version=None):
        return {"subject": "s", "body": "b", "persona": persona}

    for mod, attr, fn in (("need_classifier", "classify", classify), ("account_brief", "brief", brief),
                          ("outreach", "draft", draft)):
        m = types.ModuleType(f"llm.features.{mod}")
        setattr(m, attr, fn)
        monkeypatch.setitem(sys.modules, f"llm.features.{mod}", m)

    data = build(CSV, str(tmp_path / "x.json"), top=5, llm_top=2, quiet=True)
    accs = data["accounts"]
    assert data["meta"]["llm"]["enabled"] is True
    assert accs[0]["llm"]["need_level"] == "high" and accs[0]["llm"]["brief"].startswith("brief for")
    assert accs[0]["llm"]["outreach"]["persona"] == accs[0]["persona"]
    assert accs[0]["llm"]["prompt_versions"]["need-classifier"] == "v2"
    assert accs[2]["llm"] is None and len(calls) == 2
    assert not (tmp_path / "llm_calls.jsonl").exists()
