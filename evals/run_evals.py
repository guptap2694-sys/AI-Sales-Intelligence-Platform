"""Need-classifier eval harness.

    python -m evals.run_evals [--dataset PATH] [--systems rules,llm:v1,llm:v2]
                              [--provider anthropic|fake] [--budget-usd X] [--concurrency N]

Writes evals/results/need-classifier/<system>_<UTCts>.json per system run and
regenerates evals/results/latest.md. Exits 1 if a regression gate fails.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from evals import adapters, metrics

DATASETS_DIR = Path(__file__).resolve().parent / "datasets"
DEFAULT_DATASET = DATASETS_DIR / "need_classifier.jsonl"
SMOKE_DATASET = DATASETS_DIR / "smoke_synthetic.jsonl"
DEFAULT_RESULTS = Path(__file__).resolve().parent / "results"
FEATURE = "need-classifier"
SMOKE_BANNER = ("HARNESS SMOKE TEST on SYNTHETIC cases — proves the pipeline runs; "
                "these numbers are NOT quality metrics.")
LOWER_IS_BETTER = {"under_call_rate", "parse_error_rate", "cost_per_case_usd", "latency_p50_ms"}
HEADLINE = ["macro_f1", "accuracy", "high_precision", "under_call_rate", "parse_error_rate",
            "cost_per_case_usd", "latency_p50_ms"]


def warn(msg: str) -> None:
    bar = "!" * 78
    print(f"\n{bar}\n!! {msg}\n{bar}\n", file=sys.stderr)


# --------------------------------------------------------------------------- dataset
def load_cases(path: Path) -> list[dict]:
    cases = []
    with open(path, encoding="utf-8") as f:
        for ln, line in enumerate(f, 1):
            line = line.strip()
            if not line or line.startswith("//"):
                continue
            try:
                cases.append(json.loads(line))
            except json.JSONDecodeError as e:
                raise SystemExit(f"{path}:{ln}: invalid JSON: {e}") from e
    return cases


def labelled(cases: list[dict]) -> list[dict]:
    return [c for c in cases if c.get("label") in metrics.LABELS]


def resolve_dataset(path: Path | None) -> tuple[Path, list[dict], bool]:
    """Returns (path, labelled cases, is_smoke). Falls back to the smoke set loudly."""
    path = Path(path) if path else DEFAULT_DATASET
    cases = labelled(load_cases(path)) if path.exists() else []
    if not cases:
        why = "does not exist" if not path.exists() else "has no labelled cases"
        warn(f"Dataset {path} {why} — FALLING BACK to {SMOKE_DATASET.name}. {SMOKE_BANNER}")
        path = SMOKE_DATASET
        cases = labelled(load_cases(path))
    ids = [c["case_id"] for c in cases]
    if len(ids) != len(set(ids)):
        raise SystemExit(f"{path}: duplicate case_id values")
    is_smoke = all(c.get("synthetic") for c in cases)
    if is_smoke:
        warn(SMOKE_BANNER)
    return path, cases, is_smoke


def dataset_hash(cases: list[dict]) -> str:
    canon = json.dumps(sorted(cases, key=lambda c: c["case_id"]), sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canon.encode()).hexdigest()


# --------------------------------------------------------------------------- systems
def default_systems() -> list[str]:
    return ["rules"] + [f"llm:{v}" for v in adapters.available_prompt_versions()]


def safe_name(system: str) -> str:
    return system.replace(":", "-").replace("/", "-")


def run_system(system: str, cases: list[dict], provider: str, budget_usd: float | None,
               concurrency: int, run_id: str, results_dir: Path | None = None) -> dict:
    """Run one system over the cases. Returns a status dict, with ``run`` when it ran."""
    meta: dict = {"system": system, "provider": None, "impl": None, "prompt": None, "model": None, "notes": []}
    if system == "rules":
        _, impl = adapters.rules_impl()
        meta["impl"] = impl
        predict = lambda c: adapters.predict_rules(c["company"])  # noqa: E731
        concurrency = 1
    elif system.startswith("llm:"):
        version = system.split(":", 1)[1]
        if provider == "anthropic" and not os.environ.get("ANTHROPIC_API_KEY"):
            return {"system": system, "status": "SKIPPED: no ANTHROPIC_API_KEY"}
        if version not in adapters.available_prompt_versions() and provider != "fake":
            return {"system": system, "status": f"SKIPPED: prompts/{FEATURE}/{version}.md not found"}
        try:
            # FAKE traces stay next to the FAKE results instead of polluting traces/llm_calls.jsonl
            trace_path = (results_dir / FEATURE / "fake_traces.jsonl") if provider == "fake" and results_dir else None
            llm_sys = adapters.LLMSystem.build(version, provider, run_id=run_id, budget_usd=budget_usd,
                                               trace_path=trace_path)
        except Exception as e:  # noqa: BLE001
            return {"system": system, "status": f"SKIPPED: {type(e).__name__}: {e}"}
        meta.update(provider=provider, impl=llm_sys.impl, prompt=adapters.prompt_meta(version), notes=llm_sys.notes)
        meta["model"] = meta["prompt"].get("model")

        def predict(c):
            company = c["company"]
            signals, _ = adapters.compute_signals(company)
            return llm_sys.predict(company, signals, case_id=c["case_id"])
    else:
        return {"system": system, "status": f"SKIPPED: unknown system {system!r}"}

    spent = [0.0]
    lock = threading.Lock()

    def one(c: dict) -> dict:
        with lock:
            over = budget_usd is not None and spent[0] >= budget_usd
        if over:
            row = {"pred": None, "status": "skipped_budget"}
        else:
            try:
                row = predict(c)
            except Exception as e:  # noqa: BLE001
                row = {"pred": None, "status": "error", "error": f"{type(e).__name__}: {e}"}
            with lock:
                spent[0] += row.get("cost_usd") or 0.0
        return {"case_id": c["case_id"], "label": c["label"], "tags": c.get("tags", []), **row}

    if concurrency > 1:
        with ThreadPoolExecutor(max_workers=concurrency) as ex:
            preds = list(ex.map(one, cases))
    else:
        preds = [one(c) for c in cases]

    skipped_budget = sum(1 for p in preds if p["status"] == "skipped_budget")
    scored = [p for p in preds if p["status"] != "skipped_budget"]
    if skipped_budget:
        meta["notes"].append(f"budget ${budget_usd} reached: {skipped_budget} cases not run (excluded from metrics)")
    if not meta["model"]:
        meta["model"] = next((p.get("model") for p in scored if p.get("model")), None)
    m = metrics.compute_metrics(
        [p["label"] for p in scored], [p["pred"] for p in scored],
        confidence=[p.get("confidence") for p in scored],
        cost_usd=[p.get("cost_usd") for p in scored], latency_ms=[p.get("latency_ms") for p in scored],
    ) if scored else None
    return {"system": system, "status": "ok", "run": {**meta, "metrics": m, "predictions": preds,
                                                       "spent_usd": spent[0]}}


# --------------------------------------------------------------------------- persistence
def utc_stamp(now: datetime) -> str:
    return now.strftime("%Y%m%dT%H%M%SZ")


def save_run(run: dict, results_dir: Path) -> Path:
    out_dir = results_dir / FEATURE
    out_dir.mkdir(parents=True, exist_ok=True)
    base = f"{safe_name(run['system'])}_{utc_stamp(datetime.fromisoformat(run['created_at']))}"
    if run.get("fake"):
        base += "_FAKE"
    path, i = out_dir / f"{base}.json", 1
    while path.exists():
        path, i = out_dir / f"{base}-{i}.json", i + 1
    path.write_text(json.dumps(run, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")
    return path


def load_runs(results_dir: Path) -> list[dict]:
    runs = []
    for p in sorted((results_dir / FEATURE).glob("*.json")):
        try:
            r = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        r["_file"] = p.name
        runs.append(r)
    runs.sort(key=lambda r: r.get("created_at", ""))
    return runs


def previous_baseline(runs: list[dict], run: dict) -> dict | None:
    """Most recent earlier non-FAKE run of the same system on the same dataset."""
    prior = [r for r in runs if r.get("system") == run["system"] and not r.get("fake")
             and r.get("dataset", {}).get("sha256") == run["dataset"]["sha256"]
             and r.get("created_at", "") < run["created_at"] and r.get("metrics")]
    return prior[-1] if prior else None


# --------------------------------------------------------------------------- report
def headline(run: dict) -> dict:
    m = run.get("metrics") or {}
    return {"macro_f1": m.get("macro_f1"), "accuracy": m.get("accuracy"),
            "high_precision": m.get("high_precision"), "under_call_rate": m.get("under_call_rate"),
            "parse_error_rate": m.get("parse_error_rate"),
            "cost_per_case_usd": (m.get("cost_usd") or {}).get("mean"),
            "latency_p50_ms": (m.get("latency_ms") or {}).get("p50")}


def _fmt(v, metric: str = "") -> str:
    if v is None:
        return "–"
    if metric == "cost_per_case_usd":
        return f"${v:.5f}"
    if metric == "latency_p50_ms":
        return f"{v:.0f} ms"
    return f"{v:.3f}"


def _ci(ci) -> str:
    return f" [{ci[0]:.2f}–{ci[1]:.2f}]" if ci else ""


def _delta(cur, prev, metric: str) -> str:
    if cur is None or prev is None:
        return "–"
    d = cur - prev
    places = 5 if metric == "cost_per_case_usd" else 0 if metric == "latency_p50_ms" else 3
    if round(d, places) == 0:
        return "= 0"
    arrow = "▲" if d > 0 else "▼"
    better = (d < 0) if metric in LOWER_IS_BETTER else (d > 0)
    num = f"{d:+.{places}f}"
    return f"{arrow} {num} ({'better' if better else 'worse'})"


def _label(run: dict) -> str:
    tags = []
    if run.get("smoke"):
        tags.append("SMOKE")
    if run.get("impl") and "shim" in run["impl"]:
        tags.append("SHIM")
    return run["system"] + (f" [{', '.join(tags)}]" if tags else "")


def _trunc(s, n: int = 140) -> str:
    s = (s or "").replace("|", "/").replace("\n", " ")
    return s if len(s) <= n else s[: n - 1] + "…"


def render_latest(runs: list[dict], invocation: list[dict] | None = None, gate: list[str] | None = None) -> str:
    real = [r for r in runs if not r.get("fake") and r.get("metrics")]
    fake = [r for r in runs if r.get("fake")]
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    L = [f"# Need-classifier eval — latest", "", f"_Regenerated {now} by `python -m evals.run_evals`._", ""]

    if invocation:
        L += ["## This invocation", "", "| system | status | file |", "|---|---|---|"]
        L += [f"| {i['system']} | {i['status']} | {i.get('file', '–')} |" for i in invocation]
        L.append("")
    if gate:
        L += ["**REGRESSION GATE FAILED:**", ""] + [f"- {g}" for g in gate] + [""]

    by_ds: dict[str, list[dict]] = {}
    for r in real:
        by_ds.setdefault(r["dataset"]["sha256"], []).append(r)
    if not by_ds:
        L += ["_No non-FAKE runs recorded yet._", ""]
    # most recently used dataset first
    for ds_hash, ds_runs in sorted(by_ds.items(), key=lambda kv: kv[1][-1]["created_at"], reverse=True):
        ds = ds_runs[-1]["dataset"]
        smoke = any(r.get("smoke") for r in ds_runs)
        L += [f"## Dataset `{ds.get('path')}` (n={ds.get('n')}, sha256 `{ds_hash[:12]}`)", ""]
        if smoke:
            L += [f"> **{SMOKE_BANNER}**", ""]
        latest: dict[str, dict] = {}
        for r in ds_runs:
            latest[r["system"]] = r
        if any("shim" in (r.get("impl") or "") for r in latest.values()):
            L += ["> Runs tagged **SHIM** used a stand-in implementation (the real pipeline/llm module "
                  "was not importable); they measure the shim, not the product.", ""]

        L += ["### Latest run per system", "",
              "| system | run (UTC) | model | macro-F1 | accuracy [95% Wilson] | high precision [95% Wilson] "
              "| P@k high | under-call rate | parse-err | cost/case | p50 latency |",
              "|---|---|---|---|---|---|---|---|---|---|---|"]
        for sys_name, r in sorted(latest.items()):
            m = r["metrics"]
            L.append(
                f"| {_label(r)} | {r['created_at'][:19]} | {r.get('model') or '–'} | {_fmt(m['macro_f1'])} "
                f"| {_fmt(m['accuracy'])}{_ci(m.get('accuracy_ci95'))} "
                f"| {_fmt(m['high_precision'])}{_ci(m.get('high_precision_ci95'))} (n={m.get('high_predicted')}) "
                f"| {_fmt(m.get('high_precision_at_k'))} (k={m.get('k')}) | {_fmt(m['under_call_rate'])} "
                f"| {_fmt(m['parse_error_rate'])} | {_fmt(headline(r)['cost_per_case_usd'], 'cost_per_case_usd')} "
                f"| {_fmt(headline(r)['latency_p50_ms'], 'latency_p50_ms')} |")
        L.append("")

        L += ["### Latest vs previous run (same system, same dataset)", ""]
        rows = []
        for sys_name, r in sorted(latest.items()):
            prev = previous_baseline(ds_runs, r)
            if not prev:
                rows.append(f"| {_label(r)} | _no previous run_ |" + " |" * len(HEADLINE))
                continue
            cur_h, prev_h = headline(r), headline(prev)
            rows.append(f"| {_label(r)} | {prev['created_at'][:19]} | "
                        + " | ".join(f"{_fmt(cur_h[k], k)} ({_delta(cur_h[k], prev_h[k], k)})" for k in HEADLINE)
                        + " |")
        L += ["| system | previous run | " + " | ".join(HEADLINE) + " |",
              "|---|---|" + "---|" * len(HEADLINE)] + rows + [""]

        llm_versions = sorted((s for s in latest if s.startswith("llm:")), key=lambda s: int(s.split(":v")[-1])
                              if s.split(":v")[-1].isdigit() else 0)
        if len(llm_versions) >= 2:
            cur, prev = latest[llm_versions[-1]], latest[llm_versions[-2]]
            ch, ph = headline(cur), headline(prev)
            L += [f"### Prompt version: {llm_versions[-1]} vs {llm_versions[-2]}", "",
                  f"Prompt sha256: `{(cur.get('prompt') or {}).get('sha256', '')[:12]}` vs "
                  f"`{(prev.get('prompt') or {}).get('sha256', '')[:12]}`", "",
                  f"| metric | {llm_versions[-2]} | {llm_versions[-1]} | delta |", "|---|---|---|---|"]
            L += [f"| {k} | {_fmt(ph[k], k)} | {_fmt(ch[k], k)} | {_delta(ch[k], ph[k], k)} |" for k in HEADLINE]
            L.append("")

        if "rules" in latest and llm_versions:
            L += ["### Rules vs LLM agreement", "", "| pair | n | agreement | Cohen's κ |", "|---|---|---|---|"]
            rp = {p["case_id"]: p["pred"] for p in latest["rules"]["predictions"]}
            for v in llm_versions:
                lp = {p["case_id"]: p["pred"] for p in latest[v]["predictions"] if p["status"] != "skipped_budget"}
                a = metrics.agreement(rp, lp)
                L.append(f"| rules vs {v} | {a['n']} | {_fmt(a['rate'])} | {_fmt(a['kappa'])} |")
            L.append("")

        L += ["### Error analysis", "", "Misclassified cases (latest run of each system):", "",
              "| system | case | label | pred | status | tags | rationale | trace_id |",
              "|---|---|---|---|---|---|---|---|"]
        any_err = False
        for sys_name, r in sorted(latest.items()):
            for p in r["predictions"]:
                if p["status"] == "skipped_budget" or p.get("pred") == p["label"]:
                    continue
                any_err = True
                costly = " **(under-call)**" if p["label"] == "high" and p.get("pred") == "low" else ""
                L.append(f"| {sys_name} | {p['case_id']} | {p['label']} | {p.get('pred') or '–'}{costly} "
                         f"| {p.get('status')} | {', '.join(p.get('tags') or [])} "
                         f"| {_trunc(p.get('rationale') or p.get('error'))} | {p.get('trace_id') or '–'} |")
        if not any_err:
            L.append("| – | none | | | | | | |")
        L.append("")
        if len(latest) >= 2:
            names = sorted(latest)
            pmap = {s: {p["case_id"]: p.get("pred") for p in latest[s]["predictions"]} for s in names}
            labels_ = {p["case_id"]: p["label"] for r in latest.values() for p in r["predictions"]}
            dis = [cid for cid in sorted(labels_) if len({pmap[s].get(cid) for s in names}) > 1]
            L += ["Disagreements between systems:", "",
                  "| case | label | " + " | ".join(names) + " |", "|---|---|" + "---|" * len(names)]
            L += [f"| {cid} | {labels_[cid]} | " + " | ".join(pmap[s].get(cid) or "–" for s in names) + " |"
                  for cid in dis] or ["| none |" + " |" * (len(names) + 1)]
            L.append("")

    if fake:
        L += ["## FAKE-provider runs (plumbing checks only — not eval results, never baselines)", ""]
        L += [f"- `{r['_file']}` — {r['system']} ({r.get('impl')})" for r in fake[-10:]]
        L.append("")
    L += ["---", "Small n ⇒ wide confidence intervals: treat deltas inside the Wilson interval as noise. "
          "See `evals/README.md`.", ""]
    return "\n".join(L)


# --------------------------------------------------------------------------- main
def parse_args(argv=None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(prog="python -m evals.run_evals", description=__doc__.split("\n")[0])
    ap.add_argument("--dataset", type=Path, default=None, help=f"default: {DEFAULT_DATASET.relative_to(adapters.REPO_ROOT)}")
    ap.add_argument("--systems", default=None, help="comma list, e.g. rules,llm:v1,llm:v2 (default: rules + every "
                                                     "prompts/need-classifier/vN.md)")
    ap.add_argument("--provider", choices=["anthropic", "fake"], default="anthropic")
    ap.add_argument("--budget-usd", type=float, default=None, help="stop issuing LLM calls once spend reaches this")
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--results-dir", type=Path, default=DEFAULT_RESULTS)
    ap.add_argument("--gate-macro-f1-drop", type=float, default=0.05,
                    help="fail (exit 1) if macro-F1 drops by more than this vs the previous run of the same system")
    ap.add_argument("--no-gate", action="store_true", help="report regressions but always exit 0")
    return ap.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    ds_path, cases, is_smoke = resolve_dataset(args.dataset)
    for c in cases:
        c["company"] = adapters.normalise_company(c.get("company") or {})
    ds_info = {"path": str(ds_path.relative_to(adapters.REPO_ROOT) if ds_path.is_relative_to(adapters.REPO_ROOT)
                           else ds_path), "n": len(cases), "sha256": dataset_hash(cases), "synthetic": is_smoke}
    systems = [s.strip() for s in args.systems.split(",") if s.strip()] if args.systems else default_systems()
    if args.provider == "fake":
        warn("--provider fake: LLM outputs are FAKE (deterministic stubs). Saved tagged FAKE, never used as baselines.")

    run_id = str(uuid.uuid4())
    prior_runs = load_runs(args.results_dir)
    invocation, gate_failures = [], []
    for system in systems:
        res = run_system(system, cases, args.provider, args.budget_usd, max(1, args.concurrency), run_id,
                         args.results_dir)
        if res["status"] != "ok":
            print(f"{system:<12} {res['status']}")
            invocation.append({"system": system, "status": res["status"]})
            continue
        run = res["run"]
        is_fake = system.startswith("llm:") and args.provider == "fake"
        run.update(feature=FEATURE, run_id=run_id, created_at=datetime.now(timezone.utc).isoformat(),
                   dataset=ds_info, smoke=is_smoke, fake=is_fake)
        path = save_run(run, args.results_dir)
        m = run["metrics"] or {}
        status = "FAKE (not a result)" if is_fake else ("ok — SMOKE TEST" if is_smoke else "ok")
        if "shim" in (run.get("impl") or ""):
            status += " — SHIM impl"
        prev = None if is_fake else previous_baseline(prior_runs, run)
        if prev and m.get("macro_f1") is not None and prev["metrics"].get("macro_f1") is not None:
            drop = prev["metrics"]["macro_f1"] - m["macro_f1"]
            if drop > args.gate_macro_f1_drop:
                gate_failures.append(f"{system}: macro-F1 {prev['metrics']['macro_f1']:.3f} → {m['macro_f1']:.3f} "
                                     f"(drop {drop:.3f} > {args.gate_macro_f1_drop}) vs {prev['_file']}")
        if is_fake:  # never print FAKE numbers as if they were results
            print(f"{system:<12} {status:<28} → {path}")
            invocation.append({"system": system, "status": status, "file": path.name})
            continue
        print(f"{system:<12} {status:<28} macro-F1={_fmt(m.get('macro_f1'))} acc={_fmt(m.get('accuracy'))} "
              f"highP={_fmt(m.get('high_precision'))} under-call={_fmt(m.get('under_call_rate'))} → {path}")
        invocation.append({"system": system, "status": status, "file": path.name})

    all_runs = load_runs(args.results_dir)
    latest_md = args.results_dir / "latest.md"
    latest_md.parent.mkdir(parents=True, exist_ok=True)
    latest_md.write_text(render_latest(all_runs, invocation, gate_failures), encoding="utf-8")
    print(f"report → {latest_md}")
    if gate_failures:
        warn("REGRESSION GATE FAILED:\n!! " + "\n!! ".join(gate_failures))
        return 0 if args.no_gate else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
