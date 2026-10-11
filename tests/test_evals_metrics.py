"""Unit tests for evals.metrics (pure functions)."""
import math

import pytest

from evals import metrics as M

Y_TRUE = ["high", "high", "high", "medium", "medium", "low", "low", "low"]
Y_PRED = ["high", "low", "medium", "medium", "high", "low", "low", None]


def test_confusion_matrix_counts_and_none_column():
    cm = M.confusion_matrix(Y_TRUE, Y_PRED)
    assert cm["high"] == {"high": 1, "medium": 1, "low": 1, "none": 0}
    assert cm["medium"] == {"high": 1, "medium": 1, "low": 0, "none": 0}
    assert cm["low"] == {"high": 0, "medium": 0, "low": 2, "none": 1}
    assert sum(sum(r.values()) for r in cm.values()) == len(Y_TRUE)


def test_per_class_prf():
    prf = M.per_class_prf(Y_TRUE, Y_PRED)
    assert prf["high"]["precision"] == pytest.approx(1 / 2)
    assert prf["high"]["recall"] == pytest.approx(1 / 3)
    assert prf["high"]["f1"] == pytest.approx(2 * 0.5 * (1 / 3) / (0.5 + 1 / 3))
    assert prf["low"]["precision"] == pytest.approx(2 / 3)
    assert prf["low"]["recall"] == pytest.approx(2 / 3)
    assert prf["medium"]["support"] == 2


def test_macro_f1_accuracy_perfect_and_worst():
    assert M.macro_f1(Y_TRUE, Y_TRUE) == 1.0
    assert M.accuracy(Y_TRUE, Y_TRUE) == 1.0
    assert M.accuracy(Y_TRUE, Y_PRED) == pytest.approx(4 / 8)
    expected = sum(M.per_class_prf(Y_TRUE, Y_PRED)[c]["f1"] for c in M.LABELS) / 3
    assert M.macro_f1(Y_TRUE, Y_PRED) == pytest.approx(expected)
    assert M.macro_f1(["high", "low"], ["low", "high"]) == 0.0


def test_precision_undefined_when_class_never_predicted():
    prf = M.per_class_prf(["high", "low"], ["low", "low"])
    assert prf["high"]["precision"] is None
    assert prf["high"]["f1"] == 0.0
    assert prf["medium"]["f1"] is None  # absent everywhere → excluded from macro
    assert M.macro_f1(["high", "low"], ["low", "low"]) == pytest.approx((0 + 2 / 3) / 2)


def test_business_metrics():
    assert M.high_precision(Y_TRUE, Y_PRED) == pytest.approx(0.5)
    assert M.under_call_rate(Y_TRUE, Y_PRED) == pytest.approx(1 / 3)
    assert M.under_call_rate(["low"], ["high"]) is None
    assert M.parse_error_rate(Y_PRED) == pytest.approx(1 / 8)


def test_high_precision_at_k_ranks_by_level_then_confidence():
    y_true = ["high", "low", "high", "medium"]
    y_pred = ["high", "high", "medium", "low"]
    conf = [0.6, 0.9, 0.99, 0.5]
    assert M.high_precision_at_k(y_true, y_pred, conf, 1) == 0.0  # low-truth "high" with 0.9 ranks first
    assert M.high_precision_at_k(y_true, y_pred, conf, 2) == 0.5
    assert M.high_precision_at_k(y_true, y_pred, conf, 3) == pytest.approx(2 / 3)
    assert M.high_precision_at_k(y_true, y_pred, conf, 0) is None


def test_wilson_ci_known_values():
    lo, hi = M.wilson_ci(8, 10)
    assert lo == pytest.approx(0.4902, abs=1e-3) and hi == pytest.approx(0.9433, abs=1e-3)
    lo, hi = M.wilson_ci(0, 5)
    assert lo == 0.0 and 0 < hi < 0.5
    assert M.wilson_ci(0, 0) is None
    with pytest.raises(ValueError):
        M.wilson_ci(3, 2)


def test_summarize_and_agreement():
    s = M.summarize([100, 200, None, 300, 400])
    assert s["n"] == 4 and s["mean"] == 250 and s["p50"] == 250 and s["total"] == 1000
    assert s["p95"] == pytest.approx(385)
    assert M.summarize([None])["mean"] is None
    a = M.agreement({"a": "high", "b": "low", "c": "low"}, {"a": "high", "b": "medium", "d": "low"})
    assert a["n"] == 2 and a["rate"] == 0.5 and a["disagreements"] == ["b"]
    perfect = M.agreement({"a": "high", "b": "low"}, {"a": "high", "b": "low"})
    assert perfect["kappa"] == pytest.approx(1.0)


def test_compute_metrics_shape():
    m = M.compute_metrics(Y_TRUE, Y_PRED, confidence=[0.9] * 8, cost_usd=[0.001] * 8, latency_ms=[500] * 8)
    assert m["n"] == 8 and m["high_predicted"] == 2
    assert m["accuracy_ci95"][0] < m["accuracy"] < m["accuracy_ci95"][1]
    assert m["cost_usd"]["mean"] == pytest.approx(0.001)
    assert m["latency_ms"]["p50"] == 500
    assert not any(isinstance(v, float) and math.isnan(v) for v in m.values())


def test_length_mismatch_raises():
    with pytest.raises(ValueError):
        M.accuracy(["high"], [])
