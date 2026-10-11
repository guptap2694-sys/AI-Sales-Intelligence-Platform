"""Pure metric functions for the need-classifier evals.

No I/O, no imports from the rest of the repo. Predictions may be ``None``
(parse error / failed call): a ``None`` prediction is always wrong — it counts
as a false negative for the true class and as a false positive for no class.
"""
from __future__ import annotations

import math
from statistics import mean
from typing import Iterable, Sequence

LABELS: tuple[str, ...] = ("high", "medium", "low")
NONE_LABEL = "none"  # confusion-matrix column for missing / unparseable predictions
_ORDINAL = {"low": 0, "medium": 1, "high": 2}


def _check(y_true: Sequence, y_pred: Sequence) -> None:
    if len(y_true) != len(y_pred):
        raise ValueError(f"length mismatch: {len(y_true)} labels vs {len(y_pred)} predictions")


def _safe_div(num: float, den: float) -> float | None:
    return None if den == 0 else num / den


def confusion_matrix(
    y_true: Sequence[str], y_pred: Sequence[str | None], labels: Sequence[str] = LABELS
) -> dict[str, dict[str, int]]:
    """``cm[true][pred]`` counts. Columns include ``"none"`` for missing predictions."""
    _check(y_true, y_pred)
    cols = list(labels) + [NONE_LABEL]
    cm = {t: {p: 0 for p in cols} for t in labels}
    for t, p in zip(y_true, y_pred):
        if t not in cm:
            raise ValueError(f"unknown true label {t!r}")
        cm[t][p if p in labels else NONE_LABEL] += 1
    return cm


def per_class_prf(
    y_true: Sequence[str], y_pred: Sequence[str | None], labels: Sequence[str] = LABELS
) -> dict[str, dict]:
    """Per-class precision / recall / F1 / support. Undefined values are ``None``."""
    _check(y_true, y_pred)
    out: dict[str, dict] = {}
    for c in labels:
        tp = sum(1 for t, p in zip(y_true, y_pred) if t == c and p == c)
        fp = sum(1 for t, p in zip(y_true, y_pred) if t != c and p == c)
        fn = sum(1 for t, p in zip(y_true, y_pred) if t == c and p != c)
        prec = _safe_div(tp, tp + fp)
        rec = _safe_div(tp, tp + fn)
        if prec is None or rec is None:
            f1 = None if (tp + fp + fn) == 0 else 0.0
        else:
            f1 = 0.0 if prec + rec == 0 else 2 * prec * rec / (prec + rec)
        out[c] = {"precision": prec, "recall": rec, "f1": f1, "support": tp + fn,
                  "tp": tp, "fp": fp, "fn": fn}
    return out


def macro_f1(y_true: Sequence[str], y_pred: Sequence[str | None], labels: Sequence[str] = LABELS) -> float | None:
    """Mean F1 over classes that appear in either labels or predictions (sklearn-style, zero for undefined)."""
    prf = per_class_prf(y_true, y_pred, labels)
    f1s = [v["f1"] for v in prf.values() if v["f1"] is not None]
    return mean(f1s) if f1s else None


def accuracy(y_true: Sequence[str], y_pred: Sequence[str | None]) -> float | None:
    _check(y_true, y_pred)
    return _safe_div(sum(1 for t, p in zip(y_true, y_pred) if t == p), len(y_true))


def high_precision(y_true: Sequence[str], y_pred: Sequence[str | None]) -> float | None:
    """Precision of the "high" class — the business metric: reps only call "high"."""
    return per_class_prf(y_true, y_pred)["high"]["precision"]


def under_call_rate(y_true: Sequence[str], y_pred: Sequence[str | None]) -> float | None:
    """Share of true-"high" accounts predicted "low" — the costly error (a buyer never gets called)."""
    _check(y_true, y_pred)
    highs = [p for t, p in zip(y_true, y_pred) if t == "high"]
    return _safe_div(sum(1 for p in highs if p == "low"), len(highs))


def high_precision_at_k(
    y_true: Sequence[str], y_pred: Sequence[str | None], confidence: Sequence[float | None], k: int
) -> float | None:
    """Precision of "high" among the top-k cases ranked by (predicted level, confidence).

    Mirrors how reps work a list: highest predicted need first, ties broken by model confidence.
    """
    _check(y_true, y_pred)
    _check(y_true, confidence)
    if k <= 0 or not y_true:
        return None
    ranked = sorted(
        range(len(y_true)),
        key=lambda i: (_ORDINAL.get(y_pred[i], -1), confidence[i] if confidence[i] is not None else 0.0),
        reverse=True,
    )[: min(k, len(y_true))]
    return sum(1 for i in ranked if y_true[i] == "high") / len(ranked)


def parse_error_rate(y_pred: Sequence[str | None], labels: Sequence[str] = LABELS) -> float | None:
    return _safe_div(sum(1 for p in y_pred if p not in labels), len(y_pred))


def wilson_ci(successes: int, n: int, z: float = 1.96) -> tuple[float, float] | None:
    """Wilson score interval for a binomial proportion (95% by default). ``None`` when n == 0."""
    if n <= 0:
        return None
    if not 0 <= successes <= n:
        raise ValueError("successes must be in [0, n]")
    p = successes / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def _percentile(xs: Sequence[float], q: float) -> float:
    s = sorted(xs)
    if len(s) == 1:
        return float(s[0])
    pos = (len(s) - 1) * q
    lo, hi = math.floor(pos), math.ceil(pos)
    return float(s[lo] + (s[hi] - s[lo]) * (pos - lo))


def summarize(values: Iterable[float | None]) -> dict:
    """mean / p50 / p95 / total / n over the non-None values."""
    xs = [float(v) for v in values if v is not None]
    if not xs:
        return {"mean": None, "p50": None, "p95": None, "total": None, "n": 0}
    return {"mean": mean(xs), "p50": _percentile(xs, 0.5), "p95": _percentile(xs, 0.95),
            "total": sum(xs), "n": len(xs)}


def agreement(preds_a: dict[str, str | None], preds_b: dict[str, str | None]) -> dict:
    """Agreement between two systems over shared case ids: raw rate + Cohen's kappa."""
    ids = sorted(set(preds_a) & set(preds_b))
    n = len(ids)
    if n == 0:
        return {"n": 0, "rate": None, "kappa": None, "disagreements": []}
    agree = sum(1 for i in ids if preds_a[i] == preds_b[i])
    po = agree / n
    cats = set(LABELS) | {preds_a[i] for i in ids} | {preds_b[i] for i in ids}
    pe = sum((sum(1 for i in ids if preds_a[i] == c) / n) * (sum(1 for i in ids if preds_b[i] == c) / n)
             for c in cats)
    kappa = None if pe == 1 else (po - pe) / (1 - pe)
    return {"n": n, "rate": po, "kappa": kappa,
            "disagreements": [i for i in ids if preds_a[i] != preds_b[i]]}


def compute_metrics(
    y_true: Sequence[str],
    y_pred: Sequence[str | None],
    confidence: Sequence[float | None] | None = None,
    cost_usd: Sequence[float | None] | None = None,
    latency_ms: Sequence[float | None] | None = None,
    k: int = 10,
) -> dict:
    """All headline metrics for one system run, JSON-serialisable."""
    _check(y_true, y_pred)
    n = len(y_true)
    confidence = list(confidence) if confidence is not None else [None] * n
    prf = per_class_prf(y_true, y_pred)
    correct = sum(1 for t, p in zip(y_true, y_pred) if t == p)
    hp = prf["high"]
    n_true_high = hp["support"]
    under = sum(1 for t, p in zip(y_true, y_pred) if t == "high" and p == "low")
    return {
        "n": n,
        "accuracy": accuracy(y_true, y_pred),
        "accuracy_ci95": wilson_ci(correct, n),
        "macro_f1": macro_f1(y_true, y_pred),
        "per_class": prf,
        "confusion_matrix": confusion_matrix(y_true, y_pred),
        "high_precision": hp["precision"],
        "high_precision_ci95": wilson_ci(hp["tp"], hp["tp"] + hp["fp"]),
        "high_predicted": hp["tp"] + hp["fp"],
        "high_precision_at_k": high_precision_at_k(y_true, y_pred, confidence, k),
        "k": min(k, n),
        "under_call_rate": under_call_rate(y_true, y_pred),
        "under_call_ci95": wilson_ci(under, n_true_high),
        "parse_error_rate": parse_error_rate(y_pred),
        "cost_usd": summarize(cost_usd or []),
        "latency_ms": summarize(latency_ms or []),
    }
