"""End-to-end harness tests on the SYNTHETIC smoke set (harness checks, not quality metrics)."""
import json

import pytest

from evals import adapters, make_labelling_set as mls, run_evals
from evals.run_evals import SMOKE_DATASET


def _runs(results_dir):
    return sorted((results_dir / "need-classifier").glob("*.json"))


def test_smoke_dataset_is_marked_synthetic():
    cases = run_evals.load_cases(SMOKE_DATASET)
    assert 8 <= len(cases) <= 15
    for c in cases:
        assert c["synthetic"] is True and c["case_id"].startswith("syn-")
        assert c["label"] in ("high", "medium", "low")
        assert set(adapters.CANONICAL_FIELDS) <= set(c["company"])


def test_end_to_end_rules_and_fake_llm(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    rc = run_evals.main(["--dataset", str(SMOKE_DATASET), "--systems", "rules,llm:v1",
                         "--provider", "fake", "--results-dir", str(tmp_path), "--concurrency", "2"])
    assert rc == 0
    files = _runs(tmp_path)
    assert len(files) == 2
    rules = json.loads(next(f for f in files if f.name.startswith("rules_")).read_text())
    fake = json.loads(next(f for f in files if f.name.startswith("llm-v1_")).read_text())
    assert fake["fake"] is True and fake["system"] == "llm:v1"
    assert "_FAKE" in next(f for f in files if f.name.startswith("llm-v1_")).name
    assert rules["fake"] is False and rules["smoke"] is True
    assert rules["dataset"]["synthetic"] is True and len(rules["dataset"]["sha256"]) == 64
    assert len(rules["predictions"]) == rules["dataset"]["n"]
    for p in rules["predictions"]:
        assert {"case_id", "label", "pred", "rationale", "trace_id", "status"} <= set(p)
    m = rules["metrics"]
    for key in ("macro_f1", "accuracy", "accuracy_ci95", "per_class", "confusion_matrix", "high_precision",
                "under_call_rate", "parse_error_rate", "cost_usd", "latency_ms"):
        assert key in m
    assert "prompt" in fake and "trace_id" in fake["predictions"][0]

    md = (tmp_path / "latest.md").read_text()
    assert "HARNESS SMOKE TEST" in md
    assert "FAKE-provider runs" in md
    assert "Wilson" in md and "Error analysis" in md
    # FAKE metrics must not appear in the metric tables
    table = md.split("### Latest run per system")[1].split("###")[0]
    assert "llm:v1" not in table


def test_llm_skipped_without_key(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    rc = run_evals.main(["--dataset", str(SMOKE_DATASET), "--systems", "rules,llm:v2",
                         "--provider", "anthropic", "--results-dir", str(tmp_path)])
    assert rc == 0
    assert [f.name.split("_")[0] for f in _runs(tmp_path)] == ["rules"]
    assert "SKIPPED: no ANTHROPIC_API_KEY" in (tmp_path / "latest.md").read_text()


def test_fallback_to_smoke_when_dataset_missing(tmp_path, capsys):
    path, cases, is_smoke = run_evals.resolve_dataset(tmp_path / "nope.jsonl")
    assert path == SMOKE_DATASET and is_smoke and cases
    assert "FALLING BACK" in capsys.readouterr().err
    unlabelled = tmp_path / "u.jsonl"
    unlabelled.write_text(json.dumps({"case_id": "nc-001", "company": {}, "label": None}) + "\n")
    assert run_evals.resolve_dataset(unlabelled)[0] == SMOKE_DATASET


def test_regression_gate_and_deltas(tmp_path, monkeypatch):
    labels = {c["case_id"]: c["label"] for c in run_evals.load_cases(SMOKE_DATASET)}
    by_name = {c["company"]["name"]: c["case_id"] for c in run_evals.load_cases(SMOKE_DATASET)}
    mode = {"perfect": True}

    def fake_rules(company):
        lab = labels[by_name[company["name"]]]
        return {"pred": lab if mode["perfect"] else "low", "confidence": None, "rationale": "stub",
                "drivers": [], "trace_id": None, "model": None, "cost_usd": 0.0, "latency_ms": 1.0,
                "status": "ok", "impl": "test-stub"}

    monkeypatch.setattr(adapters, "predict_rules", fake_rules)
    args = ["--dataset", str(SMOKE_DATASET), "--systems", "rules", "--results-dir", str(tmp_path)]
    assert run_evals.main(args) == 0
    assert run_evals.main(args) == 0  # same score → no regression
    mode["perfect"] = False
    assert run_evals.main(args) == 1  # macro-F1 collapses → gate fails
    md = (tmp_path / "latest.md").read_text()
    assert "REGRESSION GATE FAILED" in md and "▼" in md and "(worse)" in md
    mode["perfect"] = True
    assert run_evals.main(args) == 0  # recovery is an improvement, not a regression
    mode["perfect"] = False
    assert run_evals.main(args + ["--no-gate"]) == 0  # gate fails but --no-gate keeps exit 0
    assert "REGRESSION GATE FAILED" in (tmp_path / "latest.md").read_text()
    assert len(_runs(tmp_path)) == 5


def test_render_prompt_version_comparison_and_agreement():
    def run(system, preds, ts):
        y = [p[1] for p in preds]
        from evals import metrics
        return {"system": system, "created_at": ts, "fake": False, "smoke": False, "impl": "real",
                "dataset": {"path": "d.jsonl", "n": len(preds), "sha256": "h" * 64},
                "prompt": {"sha256": system * 4}, "model": "m",
                "metrics": metrics.compute_metrics([p[1] for p in preds], [p[2] for p in preds]),
                "predictions": [{"case_id": c, "label": l, "pred": p, "status": "ok", "rationale": "r",
                                 "trace_id": "t", "tags": []} for c, l, p in preds], "_file": f"{system}.json"}
    base = [("a", "high", "high"), ("b", "low", "low"), ("c", "medium", "medium")]
    runs = [run("rules", base, "2026-01-01T00:00:00"),
            run("llm:v1", [("a", "high", "low"), ("b", "low", "low"), ("c", "medium", "medium")], "2026-01-01T00:00:01"),
            run("llm:v2", base, "2026-01-01T00:00:02")]
    md = run_evals.render_latest(runs)
    assert "Prompt version: llm:v2 vs llm:v1" in md
    assert "rules vs llm:v1" in md and "(under-call)" in md
    assert "Disagreements between systems" in md


def test_labelling_sampler_edges_and_strata(tmp_path):
    duckdb = pytest.importorskip("duckdb")
    p = tmp_path / "rows.parquet"
    con = duckdb.connect()
    con.execute("CREATE TABLE t AS SELECT 'Co' || i AS company_name, "
                "CASE i % 5 WHEN 0 THEN 'Legal Services' WHEN 1 THEN 'Construction' WHEN 2 THEN "
                "'Government Administration' WHEN 3 THEN 'Computer & Network Security' ELSE NULL END AS industry, "
                "CASE i % 3 WHEN 0 THEN 3 WHEN 1 THEN 2000 ELSE NULL END AS employees, 'AU' AS country, "
                "'x' AS misc FROM range(200) r(i)")
    con.execute(f"COPY t TO '{p}' (FORMAT parquet)")
    recs = [adapters.normalise_company(r) for r in mls._iter_fallback(p, 1000, 1)]
    assert len(recs) == 200 and recs[0]["extra"].get("misc") == "x"
    picked = mls.stratified_sample(recs, 20, seed=3)
    assert len(picked) == 20 and len({r["id"] for r, _, _ in picked}) == 20
    tags = {t for _, ts, _ in picked for t in ts}
    assert {"edge:regulated-micro", "edge:large-low-risk", "edge:government", "edge:security-vendor"} <= tags
    assert len({s.split("|")[0] for _, _, s in picked}) >= 4
