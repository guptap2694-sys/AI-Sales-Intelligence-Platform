"""LLMClient (contract §5) — the only place the platform talks to a model.

    from llm.client import LLMClient
    from llm.registry import load_prompt
    client = LLMClient(provider="anthropic", budget_usd=5.0)
    res = client.run(load_prompt("need-classifier"), {"company_json": ..., "signals_json": ...},
                     entity_id=company["id"], decision_key="need_level")

What every call gets, for free:
  * prompt caching   - the (static) system prompt is sent as a cached block
                       (``cache_control: ephemeral``); it is identical across thousands of
                       companies, so after the first call it bills at ~0.1x input price.
  * retries          - exponential backoff + jitter on 429 / 5xx / 529 / connection errors,
                       honouring ``retry-after``.
  * JSON handling    - for ``output: json`` prompts: strip code fences, extract the object,
                       validate ``required_keys``; failure -> ``status="parse_error"`` (no raise).
  * budget guard     - ``budget_usd``: before each call, worst-case cost (input estimate +
                       ``max_tokens`` output) is checked; ``BudgetExceeded`` is raised *before*
                       spending. Callers degrade to rules-only.
  * tracing          - one contract §6 record per call (ok, error or parse_error).

Model-specific request shaping (from the claude-api skill, Oct 2026):
  * Claude Haiku/Sonnet 5.5 reject non-default sampling params, so ``temperature`` from the
    prompt front-matter is recorded in the trace but not sent.
  * Thinking: prompts may set ``thinking: off`` (default for our short structured tasks).
    Haiku 5.5 -> ``{"type": "disabled"}``; Sonnet 5.5 -> ``{"type": "between_tools"}``
    (``disabled`` is a 400 there); ``effort`` front-matter -> ``output_config.effort``.
  * ``stop_reason == "refusal"`` (safety classifiers; a cybersecurity product can trip the
    ``cyber`` category) -> ``status="error"``; on Sonnet 5.5 we opt into the server-side
    refusal fallback (``fallbacks="default"``) unless ``refusal_fallback=False``.

``provider="fake"``: deterministic, schema-valid outputs derived from a hash of the input.
For unit tests and dry runs ONLY - traced with provider="fake"; never report as results.
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import re
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from llm.cost import cost_usd, estimate_tokens
from llm.registry import Prompt
from llm.tracing import Tracer

RETRYABLE_STATUS = {408, 409, 429, 500, 502, 503, 504, 529}
THINKING_OFF = {
    "claude-haiku-5-5": {"type": "disabled"},
    "claude-sonnet-5-5": {"type": "between_tools"},
}
REFUSAL_FALLBACK_MODELS = {"claude-sonnet-5-5"}
REFUSAL_FALLBACK_BETA = "server-side-fallback-2026-07-01"


class LLMError(RuntimeError):
    """Configuration / unrecoverable client error."""


class BudgetExceeded(LLMError):
    """Raised before a call whose worst-case cost would exceed the run budget."""

    def __init__(self, spent: float, estimate: float, budget: float):
        self.spent, self.estimate, self.budget = spent, estimate, budget
        super().__init__(
            f"LLM budget exceeded: spent ${spent:.4f} + next call worst-case ${estimate:.4f} "
            f"> budget ${budget:.2f}. Degrade to rules-only."
        )


@dataclass
class LLMResult:
    text: str
    parsed: dict | None
    model: str
    input_tokens: int
    output_tokens: int
    latency_ms: int
    cost_usd: float
    trace_id: str
    status: str = "ok"            # ok | error | parse_error
    error: str | None = None
    stop_reason: str | None = None
    provider: str = "anthropic"
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    prompt_id: str | None = None
    prompt_version: str | None = None

    @property
    def ok(self) -> bool:
        return self.status == "ok"


# ---------------------------------------------------------------------------
# JSON parsing
# ---------------------------------------------------------------------------

_FENCE_RE = re.compile(r"```(?:json|JSON)?\s*(.*?)```", re.S)


class ParseError(ValueError):
    pass


def parse_json_output(text: str, required_keys: list[str] | tuple[str, ...] = ()) -> dict:
    """Strip code fences / prose, parse the first JSON object, check required keys."""
    if text is None or not text.strip():
        raise ParseError("empty response")
    candidates = [m.group(1).strip() for m in _FENCE_RE.finditer(text)] + [text.strip()]
    last_err: Exception | None = None
    for cand in candidates:
        try:
            obj = json.loads(cand)
        except json.JSONDecodeError as e:
            last_err = e
            start, end = cand.find("{"), cand.rfind("}")
            if start == -1 or end <= start:
                continue
            try:
                obj = json.loads(cand[start:end + 1])
            except json.JSONDecodeError as e2:
                last_err = e2
                continue
        if not isinstance(obj, dict):
            last_err = ParseError(f"expected JSON object, got {type(obj).__name__}")
            continue
        missing = [k for k in required_keys if k not in obj]
        if missing:
            raise ParseError(f"missing required keys: {missing}")
        return obj
    raise ParseError(f"no valid JSON object found ({last_err})")


# ---------------------------------------------------------------------------
# Fake provider
# ---------------------------------------------------------------------------

def _fake_output(prompt: Prompt, system: str, user: str) -> str:
    """Deterministic, schema-valid output derived from sha256(prompt id + input)."""
    h = hashlib.sha256(f"{prompt.id}|{prompt.version}|{system}|{user}".encode()).digest()
    pick = lambda seq, i: seq[h[i] % len(seq)]  # noqa: E731
    if prompt.id == "need-classifier":
        level = pick(["high", "medium", "low"], 0)
        drivers = sorted({pick(["regulatory", "data_sensitivity", "attack_surface", "growth",
                                "maturity_gap", "timing"], i) for i in (1, 2)})
        return json.dumps({
            "need_level": level,
            "confidence": round(0.5 + (h[3] % 45) / 100, 2),
            "drivers": drivers if level != "low" else [],
            "rationale": f"[fake] deterministic placeholder ({level}); not a model judgement.",
            "evidence_fields": ["industry", "description"],
        })
    if prompt.id == "outreach-draft":
        return json.dumps({
            "subject": f"[fake] subject {h[0]:02x}{h[1]:02x}",
            "body": "[fake] deterministic placeholder outreach body. Not model output.",
            "cited_signals": [],
        })
    if prompt.output == "json":
        return json.dumps({k: f"[fake] {k}" for k in (prompt.required_keys or ["result"])})
    return "\n".join(f"- [fake] bullet {i + 1} ({h[i]:02x}); deterministic placeholder." for i in range(3))


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

class LLMClient:
    def __init__(
        self,
        provider: str | None = None,
        tracer: Tracer | None = None,
        run_id: str | None = None,
        budget_usd: float | None = None,
        max_retries: int = 4,
        timeout: float = 60.0,
        cache_system: bool = True,
        refusal_fallback: bool = True,
        api_key: str | None = None,
        fake_responder: Callable[[Prompt, str, str], str] | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ):
        if provider is None:
            if os.environ.get("ANTHROPIC_API_KEY") or api_key:
                provider = "anthropic"
            else:
                raise LLMError("no provider given and ANTHROPIC_API_KEY is not set "
                               "(use provider='fake' for tests/dry runs)")
        if provider not in ("anthropic", "fake"):
            raise LLMError(f"unknown provider {provider!r}")
        self.provider = provider
        self.tracer = tracer if tracer is not None else Tracer()
        self.run_id = run_id
        self.budget_usd = budget_usd
        self.max_retries = max_retries
        self.cache_system = cache_system
        self.refusal_fallback = refusal_fallback
        self.fake_responder = fake_responder
        self._sleep = sleep
        self._lock = threading.Lock()
        self.spent_usd = 0.0
        self.reserved_usd = 0.0
        self.calls = 0
        self._sdk = None
        if provider == "anthropic":
            import anthropic  # official SDK

            # SDK retries off: we retry ourselves so backoff is visible and bounded per call.
            self._sdk = anthropic.Anthropic(api_key=api_key, max_retries=0, timeout=timeout)
            self._anthropic = anthropic

    # -- budget -----------------------------------------------------------------
    def estimate_call_cost(self, prompt: Prompt, system: str, user: str) -> float:
        """Worst case: whole input uncached + max_tokens of output."""
        return cost_usd(prompt.model, estimate_tokens(system) + estimate_tokens(user), prompt.max_tokens)

    def _reserve(self, est: float) -> None:
        with self._lock:
            if self.budget_usd is not None and self.spent_usd + self.reserved_usd + est > self.budget_usd:
                raise BudgetExceeded(self.spent_usd + self.reserved_usd, est, self.budget_usd)
            self.reserved_usd += est

    def _settle(self, est: float, actual: float) -> None:
        with self._lock:
            self.reserved_usd = max(0.0, self.reserved_usd - est)
            self.spent_usd += actual
            self.calls += 1

    # -- main entry -------------------------------------------------------------
    def run(
        self,
        prompt: Prompt,
        variables: dict[str, Any],
        entity_id: str | None = None,
        decision_key: str | None = None,
        feature: str | None = None,
        eval_case_id: str | None = None,
        validate: Callable[[dict], str | None] | None = None,
    ) -> LLMResult:
        """Render, call, parse, trace. ``validate(parsed)`` may return an error string ->
        status=parse_error (schema checks beyond required keys, e.g. enum values)."""
        system = prompt.render_system(**variables)
        user = prompt.render(**variables)
        est = self.estimate_call_cost(prompt, system, user)
        self._reserve(est)  # raises BudgetExceeded before any spend

        trace_id = str(uuid.uuid4())
        t0 = time.perf_counter()
        text, stop_reason, model = "", None, prompt.model
        usage = {"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0, "cache_write_tokens": 0}
        status, error, parsed = "ok", None, None
        try:
            if self.provider == "fake":
                text, stop_reason, usage = self._call_fake(prompt, system, user)
            else:
                text, stop_reason, usage, model = self._call_anthropic(prompt, system, user)
                if stop_reason == "refusal":
                    status, error = "error", "refusal: model declined (safety classifier)"
        except Exception as e:  # noqa: BLE001 - traced, surfaced via status
            status, error = "error", f"{type(e).__name__}: {e}"
        latency_ms = int((time.perf_counter() - t0) * 1000)

        if status == "ok" and prompt.output == "json":
            try:
                parsed = parse_json_output(text, prompt.required_keys)
                verr = validate(parsed) if validate else None
                if verr:
                    raise ParseError(verr)
            except ParseError as e:
                parsed = None
                status, error = "parse_error", f"{e}" + (" (stop_reason=max_tokens)" if stop_reason == "max_tokens" else "")

        try:
            cost = cost_usd(model, usage["input_tokens"], usage["output_tokens"],
                            usage["cache_read_tokens"], usage["cache_write_tokens"])
        except KeyError:
            cost = cost_usd(prompt.model, usage["input_tokens"], usage["output_tokens"],
                            usage["cache_read_tokens"], usage["cache_write_tokens"])
        self._settle(est, cost)

        decision = None
        if parsed is not None and decision_key:
            decision = parsed.get(decision_key)
        self.tracer.record({
            "trace_id": trace_id,
            "run_id": self.run_id,
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "feature": feature or prompt.id,
            "prompt_id": prompt.id,
            "prompt_version": prompt.version,
            "prompt_sha256": prompt.sha256,
            "provider": self.provider,
            "model": model,
            "request": {
                "system": system,
                "messages": [{"role": "user", "content": user}],
                "max_tokens": prompt.max_tokens,
                "temperature": prompt.temperature,
            },
            "response": {"text": text, "parsed": parsed, "stop_reason": stop_reason},
            "usage": usage,
            "latency_ms": latency_ms,
            "cost_usd": cost,
            "entity_id": entity_id,
            "decision": decision,
            "status": status,
            "error": error,
            "eval_case_id": eval_case_id,
        })
        return LLMResult(
            text=text, parsed=parsed, model=model,
            input_tokens=usage["input_tokens"], output_tokens=usage["output_tokens"],
            latency_ms=latency_ms, cost_usd=cost, trace_id=trace_id, status=status, error=error,
            stop_reason=stop_reason, provider=self.provider,
            cache_read_tokens=usage["cache_read_tokens"], cache_write_tokens=usage["cache_write_tokens"],
            prompt_id=prompt.id, prompt_version=prompt.version,
        )

    # -- providers ----------------------------------------------------------------
    def _call_fake(self, prompt: Prompt, system: str, user: str):
        text = (self.fake_responder or _fake_output)(prompt, system, user)
        usage = {"input_tokens": estimate_tokens(system) + estimate_tokens(user),
                 "output_tokens": estimate_tokens(text), "cache_read_tokens": 0, "cache_write_tokens": 0}
        return text, "end_turn", usage

    def build_request(self, prompt: Prompt, system: str, user: str) -> dict[str, Any]:
        """Messages API params. Also usable as ``params`` in a Message Batches request."""
        sys_block: dict[str, Any] = {"type": "text", "text": system}
        if self.cache_system:
            sys_block["cache_control"] = {"type": "ephemeral"}
        params: dict[str, Any] = {
            "model": prompt.model,
            "max_tokens": prompt.max_tokens,
            "system": [sys_block],
            "messages": [{"role": "user", "content": user}],
        }
        if str(prompt.meta.get("thinking", "off")).lower() in ("off", "false", "disabled") and prompt.model in THINKING_OFF:
            params["thinking"] = THINKING_OFF[prompt.model]
        if prompt.meta.get("effort"):
            params["output_config"] = {"effort": str(prompt.meta["effort"])}
        return params

    def _call_anthropic(self, prompt: Prompt, system: str, user: str):
        a = self._anthropic
        params = self.build_request(prompt, system, user)
        use_fallback = self.refusal_fallback and prompt.model in REFUSAL_FALLBACK_MODELS
        attempt = 0
        while True:
            try:
                if use_fallback:
                    resp = self._sdk.beta.messages.create(
                        **params, betas=[REFUSAL_FALLBACK_BETA], fallbacks="default")
                else:
                    resp = self._sdk.messages.create(**params)
                break
            except (a.RateLimitError, a.InternalServerError, a.APIConnectionError) as e:
                err = e
            except a.APIStatusError as e:
                if e.status_code not in RETRYABLE_STATUS:
                    raise
                err = e
            attempt += 1
            if attempt > self.max_retries:
                raise err
            self._sleep(self._backoff(attempt, err))
        text = "".join(getattr(b, "text", "") for b in resp.content if getattr(b, "type", None) == "text")
        u = resp.usage
        usage = {
            "input_tokens": int(getattr(u, "input_tokens", 0) or 0),
            "output_tokens": int(getattr(u, "output_tokens", 0) or 0),
            "cache_read_tokens": int(getattr(u, "cache_read_input_tokens", 0) or 0),
            "cache_write_tokens": int(getattr(u, "cache_creation_input_tokens", 0) or 0),
        }
        return text, resp.stop_reason, usage, getattr(resp, "model", prompt.model) or prompt.model

    @staticmethod
    def _backoff(attempt: int, err: Exception | None = None) -> float:
        retry_after = None
        resp = getattr(err, "response", None)
        if resp is not None:
            try:
                retry_after = float(resp.headers.get("retry-after"))
            except (TypeError, ValueError, AttributeError):
                retry_after = None
        base = min(30.0, 1.0 * 2 ** (attempt - 1))
        return max(retry_after or 0.0, base + random.uniform(0, base / 2))
