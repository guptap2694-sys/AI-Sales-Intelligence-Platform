"""Need classifier (contract §7) — core evaluated LLM feature.

    classify(company, signals=None, version=None, client=None) -> dict
    {"need_level": "high|medium|low"|None, "confidence": float|None, "drivers": [...],
     "rationale": str, "trace_id": str, "status": "ok|error|parse_error",
     "model": str, "provider": str, "prompt_version": str, "cost_usd": float, "error": str|None}

On LLM failure ``need_level`` is None and ``status`` says why; callers fall back to rules.
``BudgetExceeded`` propagates so a batch run can switch to rules-only.

CLI:
    python -m llm.features.need_classifier --company-json file.json [--version v1] [--provider fake]
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from llm.features import get_client, load_company_json, to_json, trim_company, trim_signals
from llm.registry import load_prompt

PROMPT_ID = "need-classifier"
LEVELS = ("high", "medium", "low")
DRIVERS = ("regulatory", "data_sensitivity", "attack_surface", "growth", "maturity_gap", "timing")


def _normalise(parsed: dict[str, Any]) -> tuple[dict[str, Any], str | None]:
    level = str(parsed.get("need_level", "")).strip().lower()
    if level not in LEVELS:
        return {}, f"invalid need_level {parsed.get('need_level')!r}"
    try:
        conf = float(parsed.get("confidence"))
    except (TypeError, ValueError):
        return {}, f"invalid confidence {parsed.get('confidence')!r}"
    conf = min(1.0, max(0.0, conf))
    drivers = [d for d in (parsed.get("drivers") or []) if d in DRIVERS]
    rationale = str(parsed.get("rationale") or "").strip()
    return {"need_level": level, "confidence": round(conf, 3), "drivers": drivers, "rationale": rationale}, None


def classify(company: dict[str, Any], signals: list[dict] | None = None, version: str | None = None,
             client=None, eval_case_id: str | None = None) -> dict[str, Any]:
    prompt = load_prompt(PROMPT_ID, version)
    client = get_client(client)
    variables = {
        "company_json": to_json(trim_company(company)),
        "signals_json": to_json(trim_signals(signals)),
    }
    res = client.run(prompt, variables, entity_id=company.get("id"), decision_key="need_level",
                     feature=PROMPT_ID, eval_case_id=eval_case_id,
                     validate=lambda parsed: _normalise(parsed)[1])
    out: dict[str, Any] = {
        "need_level": None, "confidence": None, "drivers": [], "rationale": "",
        "trace_id": res.trace_id, "status": res.status, "error": res.error,
        "model": res.model, "provider": res.provider, "prompt_version": prompt.version,
        "cost_usd": res.cost_usd, "latency_ms": res.latency_ms,
    }
    if res.ok and res.parsed is not None:
        norm, _ = _normalise(res.parsed)  # already validated inside client.run
        out.update(norm)
        if res.parsed.get("evidence_fields"):
            out["evidence_fields"] = list(res.parsed["evidence_fields"])
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m llm.features.need_classifier", description=__doc__.split("\n")[0])
    ap.add_argument("--company-json", required=True,
                    help="canonical company record JSON, or {'company': {...}, 'signals': [...]}")
    ap.add_argument("--version", default=None, help="prompt version (default: latest)")
    ap.add_argument("--provider", default=None, choices=["anthropic", "fake"])
    ap.add_argument("--trace-path", default=None)
    ap.add_argument("--budget-usd", type=float, default=1.0)
    args = ap.parse_args(argv)
    from llm.client import LLMClient
    from llm.tracing import Tracer

    data = load_company_json(args.company_json)
    client = LLMClient(provider=args.provider, tracer=Tracer(args.trace_path) if args.trace_path else None,
                       budget_usd=args.budget_usd)
    out = classify(data["company"], data.get("signals"), version=args.version, client=client)
    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0 if out["status"] == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
