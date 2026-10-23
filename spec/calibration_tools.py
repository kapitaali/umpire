"""Shared calibration math for umpire M2 (single source of truth).

Used by the offline fitter (`calibration/fit.py`), the reference harness
(`harness/umpire.py`), and the validator (`spec/validate_ledger.py`). Stdlib only.

Calibration semantics (M2):
  - Raw probabilities are the M1 harvested constrained distribution; unchanged.
  - `raw confidence` remains the M1 entropy-complement dispersion measurement.
  - `calibrated.confidence` is a corpus-fitted estimate of P(key is correct),
    stored in the existing decide-record/0.1 optional `calibrated` block.
  - Two fitted methods are supported:
      temperature-scaling/1  q_i proportional to p_i ** (1/T); confidence = q[key]
      isotonic/1             confidence = PAV(raw_top_probability) -> correctness
    The artifact records which method and input statistic produced the value.
  - Identity is unchanged: contentHash is over {confidence, key, probabilities}
    (raw), so adding a calibrated view never changes refs or slots.
"""

import bisect
import hashlib
import math

from ledger_tools import canonical_json

CAL_SCHEMA = "umpire-calibration/0.1"
METHOD_TEMPERATURE = "temperature-scaling/1"
METHOD_ISOTONIC = "isotonic/1"
METHODS = (METHOD_TEMPERATURE, METHOD_ISOTONIC)
DATA_VERSION_PREFIX = "cal-v1:"


# ---------------------------------------------------------------- distributions
def temperature_probs(probabilities, temperature):
    """Temperature-scale a probability distribution: q_i ∝ p_i ** (1/T).

    Temperature scaling in logit space is equivalent to raising probabilities to
    1/T and renormalizing. T=1 returns the input distribution; T>1 flattens it.
    """
    if temperature <= 0:
        raise ValueError(f"temperature must be > 0, got {temperature!r}")
    if not probabilities:
        raise ValueError("empty probability distribution")
    logs = {}
    for label, p in probabilities.items():
        if p <= 0:
            raise ValueError(f"probability for {label!r} must be > 0, got {p!r}")
        logs[label] = math.log(p) / temperature
    top = max(logs.values())
    exps = {label: math.exp(v - top) for label, v in logs.items()}
    total = sum(exps.values())
    return {label: v / total for label, v in exps.items()}


def nll_at_temperature(entries, temperature):
    """Mean negative log-likelihood of each entry's gold label under T scaling."""
    total = 0.0
    for entry in entries:
        q = temperature_probs(entry["probabilities"], temperature)
        total -= math.log(q[entry["gold"]])
    return total / len(entries)


def fit_temperature(entries, t_min=0.05, t_max=20.0, iterations=120):
    """Fit T by minimizing gold-label NLL. Convex in beta=1/T; ternary search."""
    if not entries:
        raise ValueError("fit_temperature needs at least one entry")

    def objective(beta):
        return nll_at_temperature(entries, 1.0 / beta)

    lo, hi = 1.0 / t_max, 1.0 / t_min
    for _ in range(iterations):
        m1 = lo + (hi - lo) / 3.0
        m2 = hi - (hi - lo) / 3.0
        if objective(m1) <= objective(m2):
            hi = m2
        else:
            lo = m1
    beta = (lo + hi) / 2.0
    temperature = 1.0 / beta
    return temperature, nll_at_temperature(entries, temperature)


# ---------------------------------------------------------------- isotonic PAV
def isotonic_fit(pairs):
    """Pool-adjacent-violators fit for (x, y∈{0,1}) pairs.

    Returns a sorted list of [x, y] points; prediction is a right-continuous step
    function (last point with x <= value, clipped to the endpoint values). Ties in
    x are averaged first, so the output x values are unique.
    """
    if not pairs:
        raise ValueError("isotonic_fit needs at least one pair")
    by_x = {}
    for x, y in pairs:
        by_x.setdefault(float(x), []).append(float(y))
    data = [(x, sum(ys) / len(ys)) for x, ys in sorted(by_x.items())]

    # blocks: [weight, weighted_sum, [x values]], preserving sorted x
    blocks = []
    for x, y in data:
        blocks.append([1.0, y, [x]])
        while len(blocks) >= 2:
            left, right = blocks[-2], blocks[-1]
            left_mean = left[1] / left[0]
            right_mean = right[1] / right[0]
            if left_mean <= right_mean:
                break
            blocks[-2:] = [[
                left[0] + right[0],
                left[1] + right[1],
                left[2] + right[2],
            ]]
    points = []
    for weight, weighted_sum, xs in blocks:
        mean = weighted_sum / weight
        for x in xs:
            points.append([x, mean])
    points.sort()
    return points


def isotonic_apply(points, x):
    """Apply a PAV step function, clipping outside the fitted x range."""
    if not points:
        raise ValueError("empty isotonic points")
    xs = [p[0] for p in points]
    idx = bisect.bisect_right(xs, float(x)) - 1
    if idx < 0:
        idx = 0
    return min(1.0, max(0.0, float(points[idx][1])))


# ---------------------------------------------------------------- artifact
def confidence_from_artifact(probabilities, key, artifact):
    """Calibrated P(key correct) for one decision under a fitted artifact."""
    if key not in probabilities:
        raise ValueError(f"key {key!r} not in probabilities")
    method = artifact["method"]
    if method == METHOD_TEMPERATURE:
        q = temperature_probs(probabilities, artifact["temperature"])
        return q[key]
    if method == METHOD_ISOTONIC:
        return isotonic_apply(artifact["isotonic"]["points"], probabilities[key])
    raise ValueError(f"unknown calibration method {method!r}")


def calibrated_block(probabilities, key, artifact, digits=6):
    value = confidence_from_artifact(probabilities, key, artifact)
    return {
        "method": artifact["method"],
        "data_version": artifact["data_version"],
        "confidence": round(min(1.0, max(0.0, value)), digits),
    }


def artifact_core(artifact):
    """Fields that define the calibration view; everything else is provenance."""
    return {
        "schema": artifact["schema"],
        "corpus_sha256": artifact["corpus_sha256"],
        "raw_sha256": artifact.get("raw_sha256"),
        "labels": sorted(artifact["labels"]),
        "method": artifact["method"],
        "confidence_statistic": artifact["confidence_statistic"],
        "input_statistic": artifact["input_statistic"],
        "seed": artifact.get("seed"),
        "temperature": artifact.get("temperature"),
        "isotonic": artifact.get("isotonic"),
        "pin": {
            k: artifact["pin"][k]
            for k in ("model", "weights_hash", "decoding", "prompt_template_sha256")
        },
    }


def compute_data_version(artifact):
    digest = hashlib.sha256(canonical_json(artifact_core(artifact)).encode("utf-8")).hexdigest()
    return DATA_VERSION_PREFIX + digest[:16]


def validate_artifact(artifact):
    failures = []
    if artifact.get("schema") != CAL_SCHEMA:
        failures.append(f"schema != {CAL_SCHEMA}")
    labels = artifact.get("labels") or []
    if not (2 <= len(labels) <= 255) or len(set(labels)) != len(labels):
        failures.append("labels must be 2..255 unique")
    if artifact.get("method") not in METHODS:
        failures.append(f"method {artifact.get('method')!r} not supported")
    if artifact.get("confidence_statistic") != "top_probability":
        failures.append("confidence_statistic must be top_probability in v1")
    if artifact.get("input_statistic") not in ("top_probability", None):
        failures.append("input_statistic must be top_probability or null")
    if not isinstance(artifact.get("seed"), int):
        failures.append("seed must be an integer")
    if artifact.get("method") == METHOD_TEMPERATURE:
        t = artifact.get("temperature")
        if not isinstance(t, (int, float)) or t <= 0:
            failures.append("temperature must be > 0")
    if artifact.get("method") == METHOD_ISOTONIC:
        points = (artifact.get("isotonic") or {}).get("points") or []
        if not points:
            failures.append("isotonic.points missing")
        else:
            xs = [p[0] for p in points]
            ys = [p[1] for p in points]
            if xs != sorted(set(xs)):
                failures.append("isotonic x points must be sorted and unique")
            if any(not (0 <= y <= 1) for y in ys):
                failures.append("isotonic y points must be in [0,1]")
            if any(ys[i] > ys[i + 1] + 1e-12 for i in range(len(ys) - 1)):
                failures.append("isotonic points must be non-decreasing")
    if artifact.get("data_version") != compute_data_version(artifact):
        failures.append("data_version does not match artifact core")
    return failures


# ---------------------------------------------------------------- metrics
def ece(confidences, correctness, bins=10):
    """Expected calibration error, equal-width bins over [0,1]."""
    if len(confidences) != len(correctness):
        raise ValueError("confidences/correctness length mismatch")
    if not confidences:
        raise ValueError("ECE needs at least one item")
    bucket = [[] for _ in range(bins)]
    for c, y in zip(confidences, correctness):
        idx = min(bins - 1, max(0, int(float(c) * bins)))
        bucket[idx].append((float(c), 1.0 if y else 0.0))
    total = 0.0
    for items in bucket:
        if not items:
            continue
        avg_conf = sum(c for c, _ in items) / len(items)
        avg_acc = sum(y for _, y in items) / len(items)
        total += (len(items) / len(confidences)) * abs(avg_conf - avg_acc)
    return total


def brier(confidences, correctness):
    if len(confidences) != len(correctness):
        raise ValueError("confidences/correctness length mismatch")
    if not confidences:
        raise ValueError("Brier needs at least one item")
    return sum((float(c) - (1.0 if y else 0.0)) ** 2 for c, y in zip(confidences, correctness)) / len(confidences)


def reliability(confidences, correctness, bins=10):
    bucket = [[] for _ in range(bins)]
    for c, y in zip(confidences, correctness):
        idx = min(bins - 1, max(0, int(float(c) * bins)))
        bucket[idx].append((float(c), 1.0 if y else 0.0))
    rows = []
    for idx, items in enumerate(bucket):
        if not items:
            continue
        rows.append(
            {
                "bin": f"[{idx / bins:.1f},{(idx + 1) / bins:.1f})" if idx < bins - 1 else f"[{(bins - 1) / bins:.1f},1.0]",
                "n": len(items),
                "avg_confidence": round(sum(c for c, _ in items) / len(items), 6),
                "accuracy": round(sum(y for _, y in items) / len(items), 6),
            }
        )
    return rows


def bootstrap_delta(conf_a, correct_a, conf_b, correct_b, metric, n=2000, seed=42):
    """Percentile CI for metric(a) - metric(b) by paired bootstrap."""
    import random

    if len(conf_a) != len(conf_b) or len(conf_a) != len(correct_a):
        raise ValueError("bootstrap inputs must be paired")
    rng = random.Random(seed)
    n_items = len(conf_a)
    deltas = []
    for _ in range(n):
        idx = [rng.randrange(n_items) for _ in range(n_items)]
        ca = [conf_a[i] for i in idx]
        ya = [correct_a[i] for i in idx]
        cb = [conf_b[i] for i in idx]
        yb = [correct_b[i] for i in idx]
        deltas.append(metric(ca, ya) - metric(cb, yb))
    deltas.sort()
    lo = deltas[int(0.025 * (n - 1))]
    hi = deltas[int(0.975 * (n - 1))]
    return {"n_bootstrap": n, "seed": seed, "ci95": [round(lo, 6), round(hi, 6)]}
