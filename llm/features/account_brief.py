"""Account brief: 3–4 bullets (Why now / Risk / Angle / Ask) for a rep.

    brief(company, signals=None, need=None, version=None, client=None) -> str

Returns "" if the call failed (status in the trace). ``BudgetExceeded`` propagates.

CLI:
    python -m llm.features.account_brief --company-json file.json [--provider fake]
"""

from __future__ import annotations

import argparse
import sys
from typing import Any

from llm.features import get_client, load_company_json, to_json, trim_company, trim_signals
from llm.registry import load_prompt

PROMPT_ID = "account-brief"
_NEED_KEYS = ("need_level", "confidence", "drivers", "rationale")


def brief_result(company: dict[str, Any], signals: list[dict] | None = None, need: dict | None = None,
                 version: str | None = None, client=None):
    """Like ``brief`` but returns the full LLMResult (trace_id, cost, status)."""
    prompt = load_prompt(PROMPT_ID, version)
    client = get_client(client)
    need_trim = {k: need.get(k) for k in _NEED_KEYS} if need and need.get("need_level") else None
    variables = {
        "company_json": to_json(trim_company(company)),
        "signals_json": to_json(trim_signals(signals)),
        "need_json": to_json(need_trim),
    }
    return client.run(prompt, variables, entity_id=company.get("id"), feature=PROMPT_ID)


def brief(company: dict[str, Any], signals: list[dict] | None = None, need: dict | None = None,
          version: str | None = None, client=None) -> str:
    res = brief_result(company, signals, need, version, client)
    return res.text.strip() if res.ok else ""


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m llm.features.account_brief")
    ap.add_argument("--company-json", required=True)
    ap.add_argument("--version", default=None)
    ap.add_argument("--provider", default=None, choices=["anthropic", "fake"])
    ap.add_argument("--budget-usd", type=float, default=1.0)
    args = ap.parse_args(argv)
    from llm.client import LLMClient

    data = load_company_json(args.company_json)
    res = brief_result(data["company"], data.get("signals"), data.get("need"), args.version,
                       LLMClient(provider=args.provider, budget_usd=args.budget_usd))
    print(res.text if res.ok else f"[{res.status}] {res.error}")
    return 0 if res.ok else 1


if __name__ == "__main__":
    sys.exit(main())
