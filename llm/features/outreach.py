"""Outreach draft: short, signal-grounded first-touch email.

    draft(company, signals, persona=None, version=None, client=None) -> {"subject", "body", "persona"}
    (plus "trace_id", "status", "cited_signals", "word_count" for observability)

``persona`` defaults to the account's ``persona`` field (set by pipeline scoring), else
"Head of IT / IT Manager". ``SENDER_CONTEXT`` env var overrides the seller description.
On failure subject/body are "" and status says why. ``BudgetExceeded`` propagates.

CLI:
    python -m llm.features.outreach --company-json file.json [--persona CFO] [--provider fake]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

from llm.features import get_client, load_company_json, to_json, trim_company, trim_signals
from llm.registry import load_prompt

PROMPT_ID = "outreach-draft"
DEFAULT_PERSONA = "Head of IT / IT Manager"
MAX_BODY_WORDS = 120
DEFAULT_SENDER_CONTEXT = (
    "We sell cybersecurity software for small and mid-sized organisations: endpoint and identity "
    "protection, email security, cloud and data security, plus compliance reporting that produces "
    "audit evidence. Deployed in days, managed from one console, priced per user."
)


def draft(company: dict[str, Any], signals: list[dict] | None, persona: str | None = None,
          version: str | None = None, client=None) -> dict[str, Any]:
    prompt = load_prompt(PROMPT_ID, version)
    client = get_client(client)
    persona = persona or company.get("persona") or DEFAULT_PERSONA
    variables = {
        "persona": persona,
        "sender_context": os.environ.get("SENDER_CONTEXT", DEFAULT_SENDER_CONTEXT),
        "company_json": to_json(trim_company(company)),
        "signals_json": to_json(trim_signals(signals)),
    }
    res = client.run(prompt, variables, entity_id=company.get("id"), decision_key="subject", feature=PROMPT_ID)
    out: dict[str, Any] = {"subject": "", "body": "", "persona": persona,
                           "trace_id": res.trace_id, "status": res.status, "error": res.error}
    if res.ok and res.parsed:
        body = str(res.parsed.get("body", "")).strip()
        out.update(
            subject=str(res.parsed.get("subject", "")).strip(),
            body=body,
            cited_signals=list(res.parsed.get("cited_signals") or []),
            word_count=len(body.split()),
        )
        if out["word_count"] > MAX_BODY_WORDS:
            out["warning"] = f"body is {out['word_count']} words (> {MAX_BODY_WORDS}); rep should trim"
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m llm.features.outreach")
    ap.add_argument("--company-json", required=True)
    ap.add_argument("--persona", default=None)
    ap.add_argument("--version", default=None)
    ap.add_argument("--provider", default=None, choices=["anthropic", "fake"])
    ap.add_argument("--budget-usd", type=float, default=1.0)
    args = ap.parse_args(argv)
    from llm.client import LLMClient

    data = load_company_json(args.company_json)
    out = draft(data["company"], data.get("signals"), args.persona, args.version,
                LLMClient(provider=args.provider, budget_usd=args.budget_usd))
    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0 if out["status"] == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
