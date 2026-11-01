#!/usr/bin/env python3
"""Absolute calibration metrics with bootstrap confidence intervals, on the test split.

W9 asked for ECE with bootstrap intervals and for point estimates never to be presented as
precise. The reports carried paired-bootstrap intervals for the *deltas* (ECE and Brier,
calibrated minus raw) and nothing for the absolute values. This closes that half.

What it reads — committed artifacts only, no model, no network:
  calibration/raw-v3.jsonl                     rows with id/split/gold/correct + raw confidence
  calibration/corpus-v3.calibrated.records.jsonl  calibrated confidence per ref
  calibration/umpire-cal-v3.json               the artifact of record (data_version check)

Metrics reuse `spec/calibration_tools.py` (ece, brier) so the confidence intervals are
intervals around the same statistic the artifact reports — not a lookalike.

Modes:
  python3 spec/calibration_ci.py report [--split test]     print the table
  python3 spec/calibration_ci.py write  PATH               compute and write the JSON
  python3 spec/calibration_ci.py check  PATH               recompute and compare (exit 1 on drift)

Scope, stated: percentile bootstrap over the 120 test rows of ONE project-authored corpus,
one pin, one seed, one machine, one runner build. The interval covers resampling of these
rows; it does not cover corpus authorship, the pin, or a different decoder. ECE is additionally
bin-dependent (10 equal-width bins here, the definition the artifact uses).
"""

import json
import pathlib
import random
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from calibration_tools import brier, ece  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
RAW = ROOT / "calibration" / "raw-v3.jsonl"
CAL_RECORDS = ROOT / "calibration" / "corpus-v3.calibrated.records.jsonl"
ARTIFACT = ROOT / "calibration" / "umpire-cal-v3.json"

BOOTSTRAP_N = 2000
BOOTSTRAP_SEED = 42
DIGITS = 6


def load_rows(split):
    calibrated = {}
    for line in CAL_RECORDS.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        # the calibrated records file carries a `calibrated` block only on the rows the fit
        # was evaluated on (the held-out test split) — 120 of 480 in the v3 corpus
        if "calibrated" in rec:
            calibrated[rec["ref"]] = rec["calibrated"]["confidence"]

    rows = []
    for line in RAW.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row["split"] != split:
            continue
        ref = row["record"]["ref"]
        if ref not in calibrated:
            raise SystemExit(f"no calibrated record for {ref}")
        record = row["record"]
        probs = record["probabilities"]
        rows.append({
            "id": row["id"],
            # the statistic the calibration maps: top probability (`confidence_statistic`)
            "raw_top": float(max(probs.values())),
            # the record's own field is the entropy-complement confidence (≠ top probability)
            "raw_entropy": float(record["confidence"]),
            "cal": float(calibrated[ref]),
            "correct": bool(row["correct"]),
        })
    if not rows:
        raise SystemExit(f"no rows for split {split!r}")
    return rows


def metrics(rows):
    top = [r["raw_top"] for r in rows]
    ent = [r["raw_entropy"] for r in rows]
    cal = [r["cal"] for r in rows]
    correct = [r["correct"] for r in rows]
    return {
        "ece_raw_top": ece(top, correct),
        "ece_raw_entropy": ece(ent, correct),
        "ece_calibrated": ece(cal, correct),
        "brier_raw_top": brier(top, correct),
        "brier_raw_entropy": brier(ent, correct),
        "brier_calibrated": brier(cal, correct),
        "accuracy": sum(1 for c in correct if c) / len(correct),
    }


def bootstrap(rows, n=BOOTSTRAP_N, seed=BOOTSTRAP_SEED):
    rng = random.Random(seed)
    samples = {key: [] for key in ("ece_raw_top", "ece_raw_entropy", "ece_calibrated",
                                   "brier_raw_top", "brier_raw_entropy", "brier_calibrated",
                                   "accuracy")}
    for _ in range(n):
        pick = [rows[rng.randrange(len(rows))] for _ in range(len(rows))]
        for key, value in metrics(pick).items():
            samples[key].append(value)
    out = {}
    for key, values in samples.items():
        values.sort()
        out[key] = {
            "ci95": [round(values[int(0.025 * (n - 1))], DIGITS), round(values[int(0.975 * (n - 1))], DIGITS)],
        }
    return out


def compute(split="test"):
    rows = load_rows(split)
    point = metrics(rows)
    intervals = bootstrap(rows)
    artifact = json.loads(ARTIFACT.read_text(encoding="utf-8"))
    result = {
        "schema": "umpire-calibration-precision/0.1",
        "artifact": artifact.get("data_version"),
        "split": split,
        "n": len(rows),
        "bootstrap": {"n": BOOTSTRAP_N, "seed": BOOTSTRAP_SEED, "method": "percentile"},
        "metrics": {},
        "corpus_sha256": artifact.get("corpus_sha256"),
        "scope": ("percentile bootstrap over this corpus's rows; one project-authored corpus, "
                  "one pin/seed/machine/runner build; ECE uses 10 equal-width bins"),
    }
    for key, value in point.items():
        result["metrics"][key] = {"point": round(value, DIGITS), "ci95": intervals[key]["ci95"]}
    return result


def main(argv):
    mode = argv[0] if argv else "report"
    if mode == "report":
        split = "test"
        if "--split" in argv:
            split = argv[argv.index("--split") + 1]
        res = compute(split)
        print(f"artifact {res['artifact']} | split {res['split']} n={res['n']} | "
              f"bootstrap {res['bootstrap']['n']} seed {res['bootstrap']['seed']}")
        for key, row in res["metrics"].items():
            print(f"  {key:18s} {row['point']:.4f}   95% CI [{row['ci95'][0]:.4f}, {row['ci95'][1]:.4f}]")
        return 0

    if mode in ("write", "check") and len(argv) > 1:
        path = pathlib.Path(argv[1])
        res = compute("test")
        if mode == "write":
            path.write_text(json.dumps(res, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            print(f"wrote {path}")
            return 0
        if not path.exists():
            print(f"no committed precision file at {path}", file=sys.stderr)
            return 2
        committed = json.loads(path.read_text(encoding="utf-8"))
        problems = []
        if committed.get("artifact") != res["artifact"]:
            problems.append(f"artifact changed: {committed.get('artifact')} -> {res['artifact']}")
        if committed.get("n") != res["n"]:
            problems.append(f"n changed: {committed.get('n')} -> {res['n']}")
        for key in sorted(set(committed.get("metrics", {})) | set(res["metrics"])):
            want = committed.get("metrics", {}).get(key)
            got = res["metrics"].get(key)
            if want != got:
                problems.append(f"{key}: {want} -> {got}")
        if problems:
            print("calibration-ci: DRIFT", file=sys.stderr)
            for line in problems:
                print("  -", line, file=sys.stderr)
            return 1
        print(f"calibration-ci: OK — {len(res['metrics'])} metrics reproduce "
              f"(split {res['split']}, n={res['n']})")
        return 0

    print("usage: calibration_ci.py [report [--split S] | write PATH | check PATH]", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
