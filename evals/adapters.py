"""Thin adapters between the eval harness and pipeline/ + llm/ (CONTRACT.md §1, §4, §5, §7).

Every import of pipeline/ or llm/ is lazy and lives here. When a real module is
not importable, a clearly-labelled SHIM is used instead and the run records
``impl: "shim"`` so a shim result can never be mistaken for the real system.
"""
from __future__ import annotations

import hashlib
import importlib
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

REPO_ROOT = Path(__file__).resolve().parent.parent
PROMPTS_DIR = REPO_ROOT / "prompts" / "need-classifier"
LABELS = ("high", "medium", "low")

# CONTRACT.md §1 — canonical company record; unknown = None / [] / {}.
CANONICAL_FIELDS: dict[str, Any] = {
    "id": None, "name": None, "domain": None, "description": None, "industry": None,
    "sub_industry": None, "industry_code": None, "employee_count": None, "employee_range": None,
    "revenue_usd": None, "revenue_range": None, "founded_year": None, "country": None,
    "state": None, "city": None, "postcode": None, "linkedin_url": None, "website": None,
    "technologies": list, "keywords": list, "employee_growth_pct": None, "funding_total_usd": None,
    "last_funding_date": None, "job_postings": None, "extra": dict,
}


def normalise_company(company: dict) -> dict:
    """Ensure every canonical key is present (contract: all keys present)."""
    out = dict(company)
    for k, default in CANONICAL_FIELDS.items():
        if k not in out or out[k] is None and callable(default):
            out[k] = default() if callable(default) else default
    return out


def _try_import(name: str):
    try:
        return importlib.import_module(name)
    except ImportError:
        return None


# --------------------------------------------------------------------------- signals
def compute_signals(company: dict) -> tuple[list[dict] | None, str]:
    """Rule signals via ``pipeline.signals`` if available. Returns (signals, impl)."""
    mod = _try_import("pipeline.signals")
    if mod is None:
        return None, "missing"
    for fn_name in ("compute_signals", "signals_for", "detect_signals", "extract_signals", "compute"):
        fn = getattr(mod, fn_name, None)
        if callable(fn):
            return list(fn(company)), f"pipeline.signals.{fn_name}"
    return None, "missing"


# --------------------------------------------------------------------------- rules system
_REGULATED = re.compile(r"health|hospital|medical|pharma|bank|financ|insur|credit|legal|law|account|"
                        r"government|public sector|defen[cs]e|utilit|energy|education|school|universit", re.I)
_SENSITIVE = re.compile(r"patient|payment|fintech|payroll|records|personal data|pii|e-?commerce|retail|saas|"
                        r"cloud|data", re.I)
_LOW_RISK = re.compile(r"farm|agricult|restaurant|cafe|landscap|construction|plumb|cleaning|hospitality|"
                       r"arts|craft|fitness", re.I)


def shim_need_level_from_rules(account: dict) -> str:
    """SHIM for ``pipeline.scoring.need_level_from_rules`` — used only until pipeline/ exists.

    Crude, deterministic heuristic over canonical fields so the harness can run end to end.
    Its numbers are not a measurement of the real rules system.
    """
    text = " ".join(str(account.get(k) or "") for k in ("industry", "sub_industry", "description"))
    text += " " + " ".join(account.get("keywords") or [])
    score = 0
    if _REGULATED.search(text):
        score += 2
    if _SENSITIVE.search(text):
        score += 1
    if _LOW_RISK.search(text):
        score -= 2
    emp = account.get("employee_count")
    if isinstance(emp, (int, float)):
        score += 2 if emp >= 200 else 1 if emp >= 20 else 0
    growth = account.get("employee_growth_pct")
    if isinstance(growth, (int, float)) and growth >= 20:
        score += 1
    return "high" if score >= 4 else "medium" if score >= 2 else "low"


def rules_impl() -> tuple[Callable[[dict], str], str]:
    mod = _try_import("pipeline.scoring")
    fn = getattr(mod, "need_level_from_rules", None) if mod else None
    if callable(fn):
        return fn, "pipeline.scoring.need_level_from_rules"
    return shim_need_level_from_rules, "shim"


def predict_rules(company: dict) -> dict:
    fn, impl = rules_impl()
    signals, _ = compute_signals(company)  # for the rationale only; the scorer computes its own
    t0 = time.perf_counter()
    level = fn(dict(company))
    if isinstance(level, dict):  # tolerate {"need_level": ...}
        level = level.get("need_level")
    level = str(level).lower() if level is not None else None
    return {"pred": level if level in LABELS else None, "confidence": None,
            "rationale": _signals_rationale(signals), "drivers": sorted({s.get("category") for s in signals or []
                                                                          if s.get("category")}),
            "trace_id": None, "model": None, "cost_usd": 0.0,
            "latency_ms": (time.perf_counter() - t0) * 1000, "status": "ok" if level in LABELS else "parse_error",
            "impl": impl}


def _signals_rationale(signals: list[dict] | None) -> str | None:
    if not signals:
        return None
    return "; ".join(f"{s.get('id')}: {s.get('evidence')}" for s in signals[:5])


# --------------------------------------------------------------------------- prompts
def available_prompt_versions() -> list[str]:
    """Versions (``v1``, ``v2`` …) under prompts/need-classifier/, sorted numerically."""
    if not PROMPTS_DIR.is_dir():
        return []
    vs = [p.stem for p in PROMPTS_DIR.glob("v*.md") if re.fullmatch(r"v\d+", p.stem)]
    return sorted(vs, key=lambda v: int(v[1:]))


def prompt_meta(version: str) -> dict:
    """{version, sha256, model, source} via llm.registry, else parsed from the file."""
    reg = _try_import("llm.registry")
    if reg is not None and hasattr(reg, "load_prompt"):
        try:
            p = reg.load_prompt("need-classifier", version)
            return {"version": getattr(p, "version", version), "sha256": getattr(p, "sha256", None),
                    "model": getattr(p, "model", None), "source": "llm.registry"}
        except Exception:  # noqa: BLE001 — fall back to reading the file
            pass
    path = PROMPTS_DIR / f"{version}.md"
    if not path.exists():
        return {"version": version, "sha256": None, "model": None, "source": "missing"}
    raw = path.read_bytes()
    model = None
    m = re.match(rb"---\s*\n(.*?)\n---", raw, re.S)
    if m:
        try:
            import yaml
            model = (yaml.safe_load(m.group(1)) or {}).get("model")
        except Exception:  # noqa: BLE001
            pass
    return {"version": version, "sha256": hashlib.sha256(raw).hexdigest(), "model": model, "source": "file"}


# --------------------------------------------------------------------------- LLM system
@dataclass
class LLMSystem:
    """Wraps llm.features.need_classifier.classify (or a FAKE shim) for one prompt version."""
    version: str
    provider: str  # "anthropic" | "fake"
    impl: str = "missing"
    _classify: Callable | None = None
    _client: Any = None
    notes: list[str] = field(default_factory=list)

    @classmethod
    def build(cls, version: str, provider: str, run_id: str | None = None, budget_usd: float | None = None,
              trace_path: Path | None = None) -> "LLMSystem":
        """``trace_path``: where to write traces (None = the client's default, traces/llm_calls.jsonl)."""
        sys_ = cls(version=version, provider=provider)
        mod = _try_import("llm.features.need_classifier")
        fn = getattr(mod, "classify", None) if mod else None
        if callable(fn):
            sys_._classify, sys_.impl = fn, "llm.features.need_classifier.classify"
            client_mod = _try_import("llm.client")
            if client_mod is not None and hasattr(client_mod, "LLMClient"):
                kwargs: dict[str, Any] = {"provider": provider, "run_id": run_id, "budget_usd": budget_usd}
                tracing = _try_import("llm.tracing")
                if trace_path is not None and tracing is not None:
                    kwargs["tracer"] = tracing.Tracer(trace_path)
                sys_._client = client_mod.LLMClient(**kwargs)
            elif provider == "fake":
                sys_._classify, sys_.impl = _fake_classify_shim, "shim:fake"
                sys_.notes.append("llm.client missing — using FAKE classifier shim")
        elif provider == "fake":
            sys_._classify, sys_.impl = _fake_classify_shim, "shim:fake"
            sys_.notes.append("llm.features.need_classifier missing — using FAKE classifier shim")
        else:
            raise RuntimeError("llm.features.need_classifier is not available")
        return sys_

    def predict(self, company: dict, signals: list[dict] | None, case_id: str | None = None) -> dict:
        t0 = time.perf_counter()
        try:
            try:
                out = self._classify(company, signals=signals, version=self.version, client=self._client,
                                     eval_case_id=case_id)
            except TypeError as e:
                if "eval_case_id" not in str(e):
                    raise
                out = self._classify(company, signals=signals, version=self.version, client=self._client)
        except Exception as e:  # noqa: BLE001 — one bad case must not kill the run
            if type(e).__name__ == "BudgetExceeded":
                return {"pred": None, "status": "skipped_budget", "error": str(e), "impl": self.impl}
            status = "parse_error" if "pars" in type(e).__name__.lower() or "json" in str(e).lower() else "error"
            return {"pred": None, "confidence": None, "rationale": None, "drivers": [], "trace_id": None,
                    "model": None, "cost_usd": None, "latency_ms": (time.perf_counter() - t0) * 1000,
                    "status": status, "error": f"{type(e).__name__}: {e}", "impl": self.impl}
        wall = (time.perf_counter() - t0) * 1000
        return _normalise_assessment(out, wall, self.impl)


def _get(obj: Any, key: str) -> Any:
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj.get(key)
    return getattr(obj, key, None)


def _normalise_assessment(out: Any, wall_ms: float, impl: str) -> dict:
    """Map a NeedAssessment (dict or object; may nest an LLMResult) to a prediction row."""
    nested = [out] + [_get(out, k) for k in ("result", "llm_result", "llm", "meta", "trace")]
    nested = [n for n in nested if n is not None]

    def first(key):
        for n in nested:
            v = _get(n, key)
            if v is not None:
                return v
        return None

    level = first("need_level")
    level = str(level).lower() if level is not None else None
    status = first("status") or ("ok" if level in LABELS else "parse_error")
    if first("parse_error") is True:
        status = "parse_error"
    if level not in LABELS:
        level, status = None, ("parse_error" if status == "ok" else status)
    latency = first("latency_ms")
    return {"pred": level, "confidence": first("confidence"), "rationale": first("rationale"),
            "drivers": list(first("drivers") or []), "trace_id": first("trace_id"), "model": first("model"),
            "cost_usd": first("cost_usd"), "latency_ms": latency if latency is not None else wall_ms,
            "input_tokens": first("input_tokens"), "output_tokens": first("output_tokens"),
            "status": status, "impl": impl}


def _fake_classify_shim(company: dict, signals=None, version=None, client=None) -> dict:
    """SHIM: deterministic FAKE classifier (hash of id+version). Plumbing only — never a result."""
    h = int(hashlib.sha256(f"{company.get('id')}|{version}".encode()).hexdigest(), 16)
    level = LABELS[h % 3]
    return {"need_level": level, "confidence": round(0.5 + (h % 50) / 100, 2), "drivers": [],
            "rationale": f"FAKE shim output ({version}).", "model": "fake", "cost_usd": 0.0,
            "trace_id": f"fake-{h % 10**12:012d}", "status": "ok"}
