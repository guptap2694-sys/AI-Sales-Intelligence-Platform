"""Tests for the LLM layer. No network, no API key: fake provider + stubbed SDK only."""

from __future__ import annotations

import json
import sqlite3
from types import SimpleNamespace

import pytest

from llm.client import BudgetExceeded, LLMClient, LLMError, parse_json_output, ParseError
from llm.cost import PRICING, cost_usd, estimate
from llm.features import trim_company
from llm.features.account_brief import brief
from llm.features.need_classifier import classify
from llm.features.outreach import draft
from llm.registry import MissingVariableError, PromptError, list_versions, load_prompt, parse_prompt_text
from llm.tracing import REQUEST_KEYS, RESPONSE_KEYS, TRACE_KEYS, USAGE_KEYS, Tracer, read_traces, summarize

CONTRACT_TRACE_KEYS = [
    "trace_id", "run_id", "ts", "feature", "prompt_id", "prompt_version", "prompt_sha256",
    "provider", "model", "request", "response", "usage", "latency_ms", "cost_usd",
    "entity_id", "decision", "status", "error", "eval_case_id",
]

COMPANY = {
    "id": "c-1", "name": "Synthetic Health Pty Ltd", "domain": "synthetic-health.example",
    "description": "Synthetic test clinic group with a patient portal. " * 80,
    "industry": "Hospital & Health Care", "employee_count": 150, "size_band": "51-200",
    "country": "Australia", "technologies": [f"t{i}" for i in range(40)], "revenue_usd": None,
    "extra": {"huge_raw_blob": "x" * 50_000},
}
SIGNALS = [{"id": "regulated_industry", "label": "Regulated industry (healthcare)", "category": "regulatory",
            "weight": 15, "evidence": "industry = 'Hospital & Health Care'"}]


@pytest.fixture
def tracer(tmp_path):
    return Tracer(tmp_path / "llm_calls.jsonl", sqlite_path=tmp_path / "llm_calls.sqlite")


@pytest.fixture
def fake(tracer):
    return LLMClient(provider="fake", tracer=tracer, run_id="run-test")


# ---------------------------------------------------------------- registry

def test_registry_loads_all_contract_prompts():
    assert list_versions("need-classifier") == ["v1", "v2"]
    for pid, ver in [("need-classifier", "v1"), ("need-classifier", "v2"),
                     ("account-brief", "v1"), ("outreach-draft", "v1")]:
        p = load_prompt(pid, ver)
        assert (p.id, p.version) == (pid, ver)
        assert p.system and p.user_template and len(p.sha256) == 64
        assert p.output in ("json", "text") and p.max_tokens > 0
        assert p.meta.get("changelog")
    assert load_prompt("need-classifier").version == "v2"  # latest by default
    assert load_prompt("need-classifier", "v1").model == "claude-haiku-5-5"
    assert load_prompt("outreach-draft").model == "claude-sonnet-5-5"


def test_registry_parse_sections_and_sha(tmp_path):
    text = ("---\nid: demo\nversion: v3\nmodel: claude-haiku-5-5\nmax_tokens: 10\noutput: text\n---\n"
            "<!-- system -->\nSYS {{a}}\n<!-- user -->\nUSER {{ a }} and {{b}}\n")
    p = parse_prompt_text(text)
    assert p.system == "SYS {{a}}" and p.user_template.startswith("USER")
    assert p.variables == ["a", "b"]
    assert p.render(a=1, b="x") == "USER 1 and x"
    d = tmp_path / "demo"
    d.mkdir()
    (d / "v3.md").write_text(text)
    (d / "v10.md").write_text(text.replace("v3", "v10"))
    assert list_versions("demo", tmp_path) == ["v3", "v10"]
    assert load_prompt("demo", prompts_dir=tmp_path).version == "v10"
    assert load_prompt("demo", "v3", prompts_dir=tmp_path).sha256 == p.sha256


def test_registry_rendering_errors_on_missing_vars():
    p = load_prompt("need-classifier", "v2")
    with pytest.raises(MissingVariableError) as e:
        p.render(company_json="{}")
    assert "signals_json" in str(e.value)
    with pytest.raises(KeyError):  # also a KeyError for callers that catch that
        p.render()


@pytest.mark.parametrize("bad", [
    "no front matter\n<!-- system -->x<!-- user -->y",
    "---\nid: a\nversion: v1\nmodel: m\nmax_tokens: 1\noutput: json\n---\nno sections",
    "---\nid: a\nversion: v1\nmodel: m\nmax_tokens: 1\noutput: yaml\n---\n<!-- system -->x\n<!-- user -->y",
    "---\nid: a\nversion: v1\n---\n<!-- system -->x\n<!-- user -->y",
])
def test_registry_rejects_malformed(bad):
    with pytest.raises(PromptError):
        parse_prompt_text(bad)


def test_system_prompts_are_static_for_caching():
    # system prompts must not contain variables, so the cached prefix is byte-identical per company
    for pid in ("need-classifier", "account-brief", "outreach-draft"):
        assert "{{" not in load_prompt(pid).system


# ---------------------------------------------------------------- features + tracing

def test_trim_company_drops_extra_and_truncates():
    t = trim_company(COMPANY)
    assert "extra" not in t and "revenue_usd" not in t
    assert len(t["description"]) < 1300 and t["description"].endswith("[truncated]")
    assert len(t["technologies"]) == 15


def test_classify_fake_and_trace_schema(fake, tracer):
    out = classify(COMPANY, SIGNALS, client=fake)
    assert out["status"] == "ok" and out["provider"] == "fake"
    assert out["need_level"] in ("high", "medium", "low")
    assert 0 <= out["confidence"] <= 1 and isinstance(out["drivers"], list) and out["rationale"]
    assert out["trace_id"]
    # deterministic
    assert classify(COMPANY, SIGNALS, client=fake)["need_level"] == out["need_level"]

    recs = read_traces(tracer.path)
    assert len(recs) == 2
    r = recs[0]
    assert list(r.keys()) == CONTRACT_TRACE_KEYS == list(TRACE_KEYS)
    assert set(r["request"]) == set(REQUEST_KEYS)
    assert set(r["response"]) == set(RESPONSE_KEYS)
    assert set(r["usage"]) == set(USAGE_KEYS)
    assert r["provider"] == "fake" and r["run_id"] == "run-test" and r["entity_id"] == "c-1"
    assert r["feature"] == r["prompt_id"] == "need-classifier" and r["prompt_version"] == "v2"
    assert r["decision"] == out["need_level"] and r["trace_id"] == out["trace_id"]
    assert r["prompt_sha256"] == load_prompt("need-classifier", "v2").sha256
    assert "huge_raw_blob" not in json.dumps(r["request"])  # `extra` never sent

    con = sqlite3.connect(tracer.sqlite_path)
    cols = [c[1] for c in con.execute("PRAGMA table_info(llm_calls)")]
    assert cols == CONTRACT_TRACE_KEYS
    assert con.execute("SELECT count(*) FROM llm_calls").fetchone()[0] == 2


def test_classify_v1_and_eval_case_id(fake, tracer):
    out = classify(COMPANY, None, version="v1", client=fake, eval_case_id="nc-001")
    assert out["prompt_version"] == "v1"
    assert read_traces(tracer.path)[-1]["eval_case_id"] == "nc-001"


def test_brief_and_outreach_fake(fake, tracer):
    b = brief(COMPANY, SIGNALS, need={"need_level": "high", "confidence": 0.9, "drivers": [], "rationale": "x"},
              client=fake)
    assert b.startswith("- ")
    d = draft(COMPANY, SIGNALS, persona="CFO", client=fake)
    assert set(["subject", "body", "persona"]) <= set(d) and d["persona"] == "CFO" and d["subject"]
    d2 = draft(dict(COMPANY, persona="Practice Manager"), SIGNALS, client=fake)
    assert d2["persona"] == "Practice Manager"
    feats = [r["feature"] for r in read_traces(tracer.path)]
    assert feats == ["account-brief", "outreach-draft", "outreach-draft"]


def test_tracer_rejects_schema_drift(tmp_path):
    t = Tracer(tmp_path / "x.jsonl")
    with pytest.raises(ValueError):
        t.record({"trace_id": "1"})


def test_tracing_summary(fake, tracer):
    for _ in range(3):
        classify(COMPANY, SIGNALS, client=fake)
    bad = LLMClient(provider="fake", tracer=tracer, fake_responder=lambda *a: "not json")
    classify(COMPANY, SIGNALS, client=bad)
    rows = summarize(read_traces(tracer.path))
    assert len(rows) == 1
    row = rows[0]
    assert row["calls"] == 4 and row["parse_errors"] == 1 and row["error_rate"] == 0.25
    assert row["cost_usd"] > 0 and row["provider"] == "fake"


# ---------------------------------------------------------------- JSON parsing

def test_parse_json_output_variants():
    assert parse_json_output('```json\n{"a": 1}\n```', ["a"]) == {"a": 1}
    assert parse_json_output('Sure! Here it is: {"a": 1} hope that helps', ["a"]) == {"a": 1}
    assert parse_json_output('{"a": 1}') == {"a": 1}
    with pytest.raises(ParseError):
        parse_json_output('{"a": 1}', ["a", "b"])
    with pytest.raises(ParseError):
        parse_json_output("no json here")
    with pytest.raises(ParseError):
        parse_json_output("[1, 2]")
    with pytest.raises(ParseError):
        parse_json_output("")


@pytest.mark.parametrize("raw,why", [
    ("I cannot help with that.", "no valid JSON"),
    ('{"need_level": "high"}', "missing required keys"),
    ('{"need_level": "critical", "confidence": 0.9, "drivers": [], "rationale": "x"}', "invalid need_level"),
    ('{"need_level": "low", "confidence": "very", "drivers": [], "rationale": "x"}', "invalid confidence"),
])
def test_parse_failure_is_status_not_exception(tracer, raw, why):
    client = LLMClient(provider="fake", tracer=tracer, fake_responder=lambda *a: raw)
    out = classify(COMPANY, SIGNALS, client=client)
    assert out["status"] == "parse_error" and out["need_level"] is None and why in out["error"]
    rec = read_traces(tracer.path)[-1]
    assert rec["status"] == "parse_error" and rec["response"]["parsed"] is None
    assert rec["response"]["text"] == raw and why in rec["error"]


def test_fenced_json_from_model_is_accepted(tracer):
    raw = '```json\n{"need_level": "LOW", "confidence": 0.8, "drivers": ["bogus"], "rationale": "r"}\n```'
    client = LLMClient(provider="fake", tracer=tracer, fake_responder=lambda *a: raw)
    out = classify(COMPANY, SIGNALS, client=client)
    assert out["status"] == "ok" and out["need_level"] == "low" and out["drivers"] == []


# ---------------------------------------------------------------- cost + budget

def test_cost_math():
    assert PRICING["claude-haiku-5-5"]["input"] == 0.10
    assert PRICING["claude-sonnet-5-5"]["output"] == 10.00
    assert cost_usd("claude-haiku-5-5", 1_000_000, 0) == pytest.approx(0.10)
    assert cost_usd("claude-sonnet-5-5", 1000, 200) == pytest.approx((1000 * 2 + 200 * 10) / 1e6)
    assert cost_usd("claude-sonnet-5-5", 100, 100, cache_read_tokens=1000, cache_write_tokens=1000) == \
        pytest.approx((100 * 2 + 100 * 10 + 1000 * 0.2 + 1000 * 2.5) / 1e6)
    assert cost_usd("claude-haiku-5-5", 1000, 100, batch=True) == pytest.approx(cost_usd("claude-haiku-5-5", 1000, 100) / 2)
    assert cost_usd("anthropic.claude-haiku-5-5", 1_000_000, 0) == pytest.approx(0.10)
    with pytest.raises(KeyError):
        cost_usd("gpt-imaginary", 1, 1)


def test_cost_estimate_model_is_consistent():
    est = estimate()
    m = est["totals"]["monthly"]
    assert m["usd_cache_batch"] <= m["usd_cache"] <= m["usd_standard"]
    assert m["usd_cache_batch"] <= m["usd_batch"] <= m["usd_standard"]
    rec = est["recommendation"]
    assert rec["monthly_ceiling_usd"] >= rec["expected_monthly_usd"]
    models = {r["task"].split(" (")[0]: r["model"] for r in est["rows"]}
    assert models["Need classifier"] == "claude-haiku-5-5" and models["Outreach draft"] == "claude-sonnet-5-5"


def test_budget_guard_blocks_before_spend(tracer):
    p = load_prompt("need-classifier")
    client = LLMClient(provider="fake", tracer=tracer, budget_usd=0.001)
    vars_ = {"company_json": json.dumps(trim_company(COMPANY)), "signals_json": "[]"}
    est = client.estimate_call_cost(p, p.render_system(**vars_), p.render(**vars_))
    assert est > 0
    n_ok = 0
    with pytest.raises(BudgetExceeded):
        for _ in range(100):
            client.run(p, vars_)
            n_ok += 1
    assert 0 < n_ok < 100
    assert client.spent_usd <= 0.001
    assert len(read_traces(tracer.path)) == n_ok  # blocked call never executed or traced
    with pytest.raises(BudgetExceeded):
        classify(COMPANY, SIGNALS, client=LLMClient(provider="fake", tracer=tracer, budget_usd=0.0))


def test_provider_required_without_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(LLMError):
        LLMClient()
    with pytest.raises(LLMError):
        LLMClient(provider="openai")


# ---------------------------------------------------------------- anthropic path (stubbed SDK)

class _StubMessages:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        o = self.outcomes.pop(0)
        if isinstance(o, Exception):
            raise o
        return o


def _resp(text, stop="end_turn", model="claude-haiku-5-5"):
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)], stop_reason=stop, model=model,
        usage=SimpleNamespace(input_tokens=400, output_tokens=60, cache_read_input_tokens=1600,
                              cache_creation_input_tokens=0))


def _anthropic_client(tracer, outcomes, monkeypatch, **kw):
    import anthropic
    import httpx2 as httpx  # anthropic>=1.0 uses httpx2

    client = LLMClient(provider="anthropic", tracer=tracer, api_key="sk-test-not-real",
                       sleep=lambda s: None, **kw)
    stub = _StubMessages(outcomes)
    client._sdk = SimpleNamespace(messages=stub, beta=SimpleNamespace(messages=stub))

    def err(status):
        req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
        resp = httpx.Response(status, request=req, headers={"retry-after": "0"})
        cls = {429: anthropic.RateLimitError, 500: anthropic.InternalServerError,
               529: anthropic.APIStatusError, 400: anthropic.BadRequestError}[status]
        return cls("boom", response=resp, body=None)
    return client, stub, err


def test_anthropic_request_shape_caching_and_retry(tracer, monkeypatch):
    good = json.dumps({"need_level": "high", "confidence": 0.9, "drivers": ["regulatory"], "rationale": "r"})
    client, stub, err = _anthropic_client(tracer, [], monkeypatch)
    stub.outcomes = [err(429), err(529), err(500), _resp(good)]
    out = classify(COMPANY, SIGNALS, client=client)
    assert out["status"] == "ok" and out["need_level"] == "high"
    assert len(stub.calls) == 4
    req = stub.calls[-1]
    assert req["model"] == "claude-haiku-5-5"
    assert req["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert req["thinking"] == {"type": "disabled"}
    assert "temperature" not in req  # 5.5 models reject non-default sampling params
    rec = read_traces(tracer.path)[-1]
    assert rec["provider"] == "anthropic" and rec["usage"]["cache_read_tokens"] == 1600
    assert rec["cost_usd"] == pytest.approx(cost_usd("claude-haiku-5-5", 400, 60, 1600, 0))


def test_anthropic_non_retryable_and_exhausted(tracer, monkeypatch):
    client, stub, err = _anthropic_client(tracer, [], monkeypatch, max_retries=2)
    stub.outcomes = [err(400)]
    out = classify(COMPANY, SIGNALS, client=client)
    assert out["status"] == "error" and "BadRequestError" in out["error"] and len(stub.calls) == 1
    stub.outcomes = [err(429)] * 3
    stub.calls.clear()
    out = classify(COMPANY, SIGNALS, client=client)
    assert out["status"] == "error" and len(stub.calls) == 3


def test_anthropic_sonnet_fallback_and_refusal(tracer, monkeypatch):
    client, stub, err = _anthropic_client(tracer, [], monkeypatch)
    stub.outcomes = [_resp("", stop="refusal", model="claude-sonnet-5-5")]
    d = draft(COMPANY, SIGNALS, client=client)
    assert d["status"] == "error" and "refusal" in d["error"]
    req = stub.calls[-1]
    assert req["fallbacks"] == "default" and req["thinking"] == {"type": "between_tools"}
    assert req["output_config"] == {"effort": "low"}


def test_default_client_is_shared_and_budgeted(monkeypatch, tmp_path):
    from llm.features import get_client, reset_default_client

    monkeypatch.setenv("LLM_PROVIDER", "fake")
    monkeypatch.setenv("LLM_BUDGET_USD", "0.5")
    monkeypatch.setenv("LLM_TRACE_PATH", str(tmp_path / "t.jsonl"))
    reset_default_client()
    try:
        c = get_client()
        assert c is get_client() and c.provider == "fake" and c.budget_usd == 0.5
        classify(COMPANY, SIGNALS)
        assert len(read_traces(tmp_path / "t.jsonl")) == 1
    finally:
        reset_default_client()
