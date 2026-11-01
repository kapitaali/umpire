#!/usr/bin/env python3
"""W9 calibration transfer: does the artifact of record survive a label-set change?

Design: `design/W9-TRANSFER.md` (frozen before the run). Arms:

  A0  calibration/corpus-v3.*         four labels, existing capture (no new run)
  A1  calibration/transfer/a1.*       three labels (no `other`), 90 re-decoded test rows
  A2  calibration/transfer/a2.*       three labels (no `billing`), 90 re-decoded test rows

The calibrated confidence is the artifact's isotonic map applied UNCHANGED to each arm's top
probability — the question is transfer, so no refitting. A0 is restricted to the same row ids,
so the comparison is paired row for row.

Modes:
  python3 spec/transfer_eval.py report              print the table and the verdict
  python3 spec/transfer_eval.py write PATH          compute and write the verdict JSON
  python3 spec/transfer_eval.py check PATH          recompute and compare (exit 1 on drift)

Decision rule, fixed in the design before the run: material degradation iff ΔECE > +0.02 AND the
paired 95% interval excludes 0; no detectable degradation iff the interval includes 0; otherwise
"small, possibly real".
"""

import json
import pathlib
import random
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from calibration_tools import brier, ece, isotonic_apply  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
CAL = ROOT / "calibration"
TRANSFER = CAL / "transfer"
ARTIFACT = CAL / "umpire-cal-v3.json"

BOOTSTRAP_N = 2000
BOOTSTRAP_SEED = 42
DIGITS = 6
ARMS = {"a1": "other", "a2": "billing"}


def load_jsonl(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def top_confidence(record):
    return float(max(record["probabilities"].values()))


def load_pairs(arm):
    """(a0 rows, arm rows) aligned by id, restricted to the arm's ids."""
    provenance = json.loads((TRANSFER / "provenance.json").read_text(encoding="utf-8"))
    ids = set(provenance["arms"][arm]["ids"])
    a0_all = {row["id"]: row for row in load_jsonl(CAL / "raw-v3.jsonl")}
    arm_all = {row["id"]: row for row in load_jsonl(TRANSFER / f"{arm}.raw.jsonl")}
    missing = sorted(ids - set(arm_all))
    if missing:
        raise SystemExit(f"{arm}: {len(missing)} ids missing from the capture, e.g. {missing[:3]}")
    a0, cur = [], []
    for row_id in provenance["arms"][arm]["ids"]:
        a0.append(a0_all[row_id])
        cur.append(arm_all[row_id])
    return a0, cur


def calibrate(rows, points):
    return [isotonic_apply(points, top_confidence(r["record"])) for r in rows]


def summarise(rows, conf, correct):
    return {"ece": ece(conf, correct), "brier": brier(conf, correct),
            "accuracy": sum(1 for c in correct if c) / len(correct)}


def paired_bootstrap(a0_conf, a0_correct, arm_conf, arm_correct, n=BOOTSTRAP_N, seed=BOOTSTRAP_SEED):
    rng = random.Random(seed)
    ece_d, brier_d, acc_d = [], [], []
    size = len(a0_conf)
    for _ in range(n):
        idx = [rng.randrange(size) for _ in range(size)]
        a_c = [a0_conf[i] for i in idx]
        a_y = [a0_correct[i] for i in idx]
        b_c = [arm_conf[i] for i in idx]
        b_y = [arm_correct[i] for i in idx]
        ece_d.append(ece(b_c, b_y) - ece(a_c, a_y))
        brier_d.append(brier(b_c, b_y) - brier(a_c, a_y))
        acc_d.append(sum(1 for y in b_y if y) / size - sum(1 for y in a_y if y) / size)
    out = {}
    for name, values in (("ece", ece_d), ("brier", brier_d), ("accuracy", acc_d)):
        values.sort()
        out[name] = {"delta": None,  # filled by the caller from the point estimates
                     "ci95": [round(values[int(0.025 * (n - 1))], DIGITS),
                              round(values[int(0.975 * (n - 1))], DIGITS)]}
    return out


def verdict_for(delta, ci):
    if delta > 0.02 and ci[0] > 0:
        return "material degradation"
    if ci[0] <= 0 <= ci[1]:
        return "no detectable degradation"
    return "small, possibly real"


def compute():
    points = json.loads(ARTIFACT.read_text(encoding="utf-8"))["isotonic"]["points"]
    result = {"schema": "umpire-transfer-verdict/0.1",
              "design": "design/W9-TRANSFER.md",
              "artifact": json.loads(ARTIFACT.read_text(encoding="utf-8"))["data_version"],
              "a0": {"source": "calibration/corpus-v3.*"},
              "bootstrap": {"n": BOOTSTRAP_N, "seed": BOOTSTRAP_SEED, "method": "percentile, paired"},
              "arms": {}}
    for arm, dropped in ARMS.items():
        a0_rows, arm_rows = load_pairs(arm)
        a0_conf, arm_conf = calibrate(a0_rows, points), calibrate(arm_rows, points)
        a0_correct = [bool(r["correct"]) for r in a0_rows]
        arm_correct = [bool(r["correct"]) for r in arm_rows]
        a0_sum = summarise(a0_rows, a0_conf, a0_correct)
        arm_sum = summarise(arm_rows, arm_conf, arm_correct)
        boots = paired_bootstrap(a0_conf, a0_correct, arm_conf, arm_correct)
        deltas = {k: arm_sum[k] - a0_sum[k] for k in ("ece", "brier", "accuracy")}
        for k in deltas:
            boots[k]["delta"] = round(deltas[k], DIGITS)
        result["arms"][arm] = {
            "dropped_label": dropped,
            "n": len(arm_rows),
            "a0": {k: round(v, DIGITS) for k, v in a0_sum.items()},
            "arm": {k: round(v, DIGITS) for k, v in arm_sum.items()},
            "delta": boots,
            "verdict": verdict_for(deltas["ece"], boots["ece"]["ci95"]),
        }
    return result


def main(argv):
    mode = argv[0] if argv else "report"
    res = compute()

    if mode == "report":
        print(f"artifact {res['artifact']} | paired bootstrap n={res['bootstrap']['n']} seed={res['bootstrap']['seed']}")
        for arm, row in res["arms"].items():
            a0, cur, d = row["a0"], row["arm"], row["delta"]
            print(f"\n{arm.upper()} — drop {row['dropped_label']!r}, n={row['n']}")
            print(f"  ECE     A0 {a0['ece']:.4f}   arm {cur['ece']:.4f}   Δ {d['ece']['delta']:+.4f} "
                  f"[{d['ece']['ci95'][0]:+.4f}, {d['ece']['ci95'][1]:+.4f}]")
            print(f"  Brier   A0 {a0['brier']:.4f}   arm {cur['brier']:.4f}   Δ {d['brier']['delta']:+.4f} "
                  f"[{d['brier']['ci95'][0]:+.4f}, {d['brier']['ci95'][1]:+.4f}]")
            print(f"  acc     A0 {a0['accuracy']:.4f}   arm {cur['accuracy']:.4f}   Δ {d['accuracy']['delta']:+.4f} "
                  f"[{d['accuracy']['ci95'][0]:+.4f}, {d['accuracy']['ci95'][1]:+.4f}]")
            print(f"  verdict: {row['verdict']}")
        return 0

    if mode in ("write", "check") and len(argv) > 1:
        path = pathlib.Path(argv[1])
        if mode == "write":
            path.write_text(json.dumps(res, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            print(f"wrote {path}")
            return 0
        if not path.exists():
            print(f"no committed verdict at {path}", file=sys.stderr)
            return 2
        committed = json.loads(path.read_text(encoding="utf-8"))
        if committed.get("arms") != res["arms"]:
            print("transfer-eval: DRIFT", file=sys.stderr)
            for arm in res["arms"]:
                if committed.get("arms", {}).get(arm) != res["arms"][arm]:
                    print(f"  - {arm} differs", file=sys.stderr)
            return 1
        print(f"transfer-eval: OK — {len(res['arms'])} arms reproduce")
        return 0

    print("usage: transfer_eval.py [report | write PATH | check PATH]", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
