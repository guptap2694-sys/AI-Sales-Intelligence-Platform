"""LLM features. Shared input-trimming helpers live here to control tokens.

Inputs to every prompt are a *trimmed* company dict: only fields that carry
signal for a cybersecurity-need judgement, ``extra`` (all unmapped raw columns)
dropped, empty/None fields dropped (the prompts treat an absent field as
"unknown", not "no"), long text truncated, lists capped.
"""

from __future__ import annotations

import json
import os
from typing import Any

KEEP_FIELDS = (
    "id", "name", "domain", "description", "industry", "sub_industry", "industry_code",
    "employee_count", "employee_range", "size_band", "revenue_usd", "revenue_range",
    "founded_year", "company_age_years", "country", "state", "city", "website",
    "technologies", "keywords", "employee_growth_pct", "funding_total_usd",
    "last_funding_date", "job_postings",
)
MAX_DESCRIPTION_CHARS = 1200
MAX_LIST_ITEMS = 15
SIGNAL_KEEP = ("id", "label", "category", "evidence")


def trim_company(company: dict[str, Any], max_description_chars: int = MAX_DESCRIPTION_CHARS) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k in KEEP_FIELDS:
        v = company.get(k)
        if v is None or v == "" or v == []:
            continue
        if k == "description" and isinstance(v, str):
            v = " ".join(v.split())
            if len(v) > max_description_chars:
                v = v[:max_description_chars].rsplit(" ", 1)[0] + " …[truncated]"
        elif isinstance(v, list):
            v = [str(x) for x in v[:MAX_LIST_ITEMS]]
        elif isinstance(v, float) and v.is_integer():
            v = int(v)
        out[k] = v
    return out


def trim_signals(signals: list[dict[str, Any]] | None, limit: int = 12) -> list[dict[str, Any]]:
    if not signals:
        return []
    return [{k: s.get(k) for k in SIGNAL_KEEP if s.get(k) is not None} for s in signals[:limit]]


def to_json(obj: Any) -> str:
    """Compact, key-sorted JSON: deterministic bytes (cache- and trace-friendly)."""
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


_DEFAULT_CLIENT = None


def get_client(client=None):
    """Return ``client``, else a process-wide default LLMClient (created once, so callers that
    don't pass a client still share one SDK connection pool and one run budget).

    Env: ``LLM_PROVIDER`` (anthropic|fake; default anthropic if ANTHROPIC_API_KEY set),
    ``LLM_BUDGET_USD`` (default 5.0), ``LLM_RUN_ID``, ``LLM_TRACE_PATH``.
    """
    global _DEFAULT_CLIENT
    if client is not None:
        return client
    if _DEFAULT_CLIENT is None:
        from llm.client import LLMClient
        from llm.tracing import Tracer

        _DEFAULT_CLIENT = LLMClient(
            provider=os.environ.get("LLM_PROVIDER") or None,
            tracer=Tracer(os.environ.get("LLM_TRACE_PATH") or None),
            run_id=os.environ.get("LLM_RUN_ID") or None,
            budget_usd=float(os.environ.get("LLM_BUDGET_USD", "5.0")),
        )
    return _DEFAULT_CLIENT


def reset_default_client() -> None:
    global _DEFAULT_CLIENT
    _DEFAULT_CLIENT = None


def load_company_json(path: str) -> dict[str, Any]:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict) and "company" in data and isinstance(data["company"], dict):
        return data  # eval-case shaped: {"company": {...}, "signals": [...]}
    return {"company": data}
