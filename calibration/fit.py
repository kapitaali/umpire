#!/usr/bin/env python3
"""Fit the M2 calibration artifact from raw corpus decisions (offline, stdlib).

Inputs:  calibration/corpus-v1.jsonl + calibration/raw-v1.jsonl
Outputs: calibration/umpire-cal-v1.json
         calibration/corpus.calibrated.records.jsonl  (calibrated block on TEST rows)
         calibration/checksums.sha256

Method selection is honest: temperature scaling and isotonic regression are both
fit on the train split, compared on the validation split by ECE (Brier tie-break),
then the winner is refit on train+validation. The test split is touched once for
the reported final metrics. Raw entropy-complement confidence and raw top
probability are reported as baselines so the calibration delta is visible.

The stored calibrated view is P(key is correct): temperature scaling uses q[key]
after fitting T by gold-label NLL; isotonic maps raw p[key] to empirical accuracy.
Raw probabilities and the decision identity (ref/contentHash) are unchanged.
"""

import argparse
import datetime as dt
import hashlib
import json
import math
import pathlib
import platform
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "spec"))

import calibration_tools as cal  # noqa: E402
from ledger_tools import decide_ref, response_hash  # noqa: E402

CORPUS = HERE / "corpus-v1.jsonl"
RAW = HERE / "raw-v1.jsonl"
ARTIFACT = HERE / "umpire-cal-v1.json"
CALIBRATED_RECORDS = HERE / "corpus.calibrated.records.jsonl"
CHECKSUMS = HERE / "checksums.sha256"


def sha256_file(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _rel(path: pathlib.Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def load_corpus(path: pathlib.Path) -> tuple[list[dict], dict]:
    rows = [json.loads(line) for line in path.read_text().splitlines() if line]
    meta_path = path.with_name(path.stem + ".meta.json")
    meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}
    if len(rows) != 160:
        raise SystemExit(f"corpus must have 160 examples, found {len(rows)}")
    return rows, meta


def load_raw(path: pathlib.Path, corpus_by_id: dict[str, dict]) -> list[dict]:
    rows = [json.loads(line) for line in path.read_text().splitlines() if line]
    if len(rows) != len(corpus_by_id):
        raise SystemExit(
            f"raw capture incomplete: {len(rows)}/{len(corpus_by_id)}; finish capture before fitting"
        )
    seen = set()
    entries = []
    for row in rows:
        eid = row.get("id")
        if eid in seen:
            raise SystemExit(f"duplicate raw id {eid!r}")
        seen.add(eid)
        if eid not in corpus_by_id:
            raise SystemExit(f"raw id {eid!r} not in corpus")
        expected = corpus_by_id[eid]
        rec = row.get("record") or {}
        if row.get("gold") != expected["gold"] or row.get("split") != expected["split"]:
            raise SystemExit(f"{eid}: raw gold/split differs from corpus")
        if rec.get("state") != expected["state"]:
            raise SystemExit(f"{eid}: record state differs from corpus")
        if list(rec.get("labels") or []) != list(expected["labels"]):
            raise SystemExit(f"{eid}: record labels differ from corpus")
        probs = rec.get("probabilities") or {}
        if set(probs) != set(expected["labels"]):
            raise SystemExit(f"{eid}: probability keys != labels")
        if abs(sum(probs.values()) - 1.0) > 1e-9:
            raise SystemExit(f"{eid}: probabilities do not sum to 1")
        if rec.get("ref") != decide_ref(rec.get("state"), rec.get("labels")):
            raise SystemExit(f"{eid}: ref drift")
        if rec.get("contentHash") != response_hash(rec):
            raise SystemExit(f"{eid}: contentHash drift")
        correct = rec.get("key") == expected["gold"]
        if bool(row.get("correct")) != correct:
            raise SystemExit(f"{eid}: correct flag disagrees with gold/key")
        entries.append(
            {
                "id": eid,
                "split": expected["split"],
                "gold": expected["gold"],
                "key": rec["key"],
                "probabilities": probs,
                "raw_confidence": rec["confidence"],
                "correct": correct,
                "record": rec,
            }
        )
    missing = set(corpus_by_id) - seen
    if missing:
        raise SystemExit(f"raw capture missing ids: {sorted(missing)[:5]}")
    pins = {json.dumps(e["record"].get("model_pin"), sort_keys=True) for e in entries}
    if len(pins) != 1:
        raise SystemExit("raw capture mixes model pins")
    return entries


def split(entries, name):
    subset = [e for e in entries if e["split"] == name]
    if not subset:
        raise SystemExit(f"empty split {name!r}")
    return subset


def evaluate(entries, artifact=None):
    raw_entropy = [e["raw_confidence"] for e in entries]
    raw_top = [e["probabilities"][e["key"]] for e in entries]
    correct = [1.0 if e["correct"] else 0.0 for e in entries]
    out = {
        "n": len(entries),
        "accuracy": round(sum(correct) / len(correct), 6),
        "raw_entropy": {
            "ece": round(cal.ece(raw_entropy, correct), 6),
            "brier": round(cal.brier(raw_entropy, correct), 6),
        },
        "raw_top_probability": {
            "ece": round(cal.ece(raw_top, correct), 6),
            "brier": round(cal.brier(raw_top, correct), 6),
            "nll": round(
                sum(-math.log(e["probabilities"][e["gold"]]) for e in entries) / len(entries), 6
            ),
        },
    }
    if artifact is None:
        return out
    calibrated = [cal.confidence_from_artifact(e["probabilities"], e["key"], artifact) for e in entries]
    out["calibrated"] = {
        "method": artifact["method"],
        "ece": round(cal.ece(calibrated, correct), 6),
        "brier": round(cal.brier(calibrated, correct), 6),
        "reliability": cal.reliability(calibrated, correct),
    }
    if artifact["method"] == cal.METHOD_TEMPERATURE:
        out["calibrated"]["nll"] = round(
            sum(
                -math.log(
                    cal.temperature_probs(e["probabilities"], artifact["temperature"])[e["gold"]]
                )
                for e in entries
            )
            / len(entries),
            6,
        )
    out["delta_ece_vs_raw_entropy"] = round(out["calibrated"]["ece"] - out["raw_entropy"]["ece"], 6)
    out["delta_ece_vs_raw_top"] = round(out["calibrated"]["ece"] - out["raw_top_probability"]["ece"], 6)
    out["delta_brier_vs_raw_top"] = round(out["calibrated"]["brier"] - out["raw_top_probability"]["brier"], 6)
    out["bootstrap_delta_ece_vs_raw_top"] = cal.bootstrap_delta(
        calibrated, correct, raw_top, correct, cal.ece
    )
    out["bootstrap_delta_brier_vs_raw_top"] = cal.bootstrap_delta(
        calibrated, correct, raw_top, correct, cal.brier
    )
    return out


def fit_candidates(train):
    temperature, train_nll = cal.fit_temperature(train)
    iso_points = cal.isotonic_fit(
        [(e["probabilities"][e["key"]], 1.0 if e["correct"] else 0.0) for e in train]
    )
    candidates = {
        "temperature": {
            "temperature": temperature,
            "fit_nll": round(train_nll, 6),
            "artifact": {"method": cal.METHOD_TEMPERATURE, "temperature": temperature},
        },
        "isotonic": {
            "points": iso_points,
            "artifact": {"method": cal.METHOD_ISOTONIC, "isotonic": {"points": iso_points}},
        },
    }
    return candidates


def select_candidate(candidates, validation):
    scored = {}
    for name, candidate in candidates.items():
        metrics = evaluate(validation, candidate["artifact"])
        scored[name] = {
            "ece": metrics["calibrated"]["ece"],
            "brier": metrics["calibrated"]["brier"],
        }
    order = {"temperature": 0, "isotonic": 1}
    selected = min(scored, key=lambda n: (scored[n]["ece"], scored[n]["brier"], order[n]))
    return selected, scored


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="fit umpire M2 calibration artifact")
    ap.add_argument("--corpus", type=pathlib.Path, default=CORPUS)
    ap.add_argument("--raw", type=pathlib.Path, default=RAW)
    ap.add_argument("--artifact", type=pathlib.Path, default=ARTIFACT)
    ap.add_argument("--calibrated-records", type=pathlib.Path, default=CALIBRATED_RECORDS)
    ap.add_argument("--checksums", type=pathlib.Path, default=CHECKSUMS)
    ap.add_argument("--pin", default="qwen3-1.7b")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--no-checksums", action="store_true")
    args = ap.parse_args(argv)

    corpus_rows, corpus_meta = load_corpus(args.corpus)
    corpus_by_id = {r["id"]: r for r in corpus_rows}
    entries = load_raw(args.raw, corpus_by_id)
    train, validation, test = split(entries, "train"), split(entries, "validation"), split(entries, "test")

    pins = json.loads((ROOT / "harness" / "pins.json").read_text())
    pin = pins[args.pin]
    for e in entries:
        mp = e["record"]["model_pin"]
        if (
            mp.get("model") != pin["model"]
            or mp.get("weights_hash") != pin["weights_hash"]
            or mp.get("decoding") != pin["decoding"]
        ):
            raise SystemExit(f"{e['id']}: model_pin does not match harness/pins.json[{args.pin!r}]")

    candidates = fit_candidates(train)
    selected, validation_scores = select_candidate(candidates, validation)

    # Refit the winning method on train+validation (test stays untouched).
    fit_entries = train + validation
    if selected == "temperature":
        temperature, fit_nll = cal.fit_temperature(fit_entries)
        final_candidate = {
            "method": cal.METHOD_TEMPERATURE,
            "temperature": round(temperature, 10),
            "isotonic": None,
            "fit_nll": round(fit_nll, 6),
        }
    else:
        points = cal.isotonic_fit(
            [(e["probabilities"][e["key"]], 1.0 if e["correct"] else 0.0) for e in fit_entries]
        )
        final_candidate = {
            "method": cal.METHOD_ISOTONIC,
            "temperature": None,
            "isotonic": {"points": points},
            "fit_nll": None,
        }

    pin_meta = {
        "model": pin["model"],
        "weights_hash": pin["weights_hash"],
        "decoding": pin["decoding"],
        "prompt_template_sha256": pin["prompt_template_sha256"],
        "runner_binary_sha256": pin["runner"]["binary_sha256"],
        "gguf_sha256": pin["gguf"]["sha256"],
        "server_invocation": pin["server"]["invocation"],
    }
    artifact = {
        "schema": cal.CAL_SCHEMA,
        "created": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
        "corpus": {
            "path": _rel(args.corpus),
            "sha256": "sha256:" + sha256_file(args.corpus),
            "examples": len(corpus_rows),
            "splits": {name: len(split(entries, name)) for name in ("train", "validation", "test")},
            "state_format": corpus_meta.get("state_format"),
            "routing_guide_sha256": corpus_meta.get("routing_guide_sha256"),
            "meta_sha256": "sha256:" + sha256_file(args.corpus.with_name(args.corpus.stem + ".meta.json"))
            if args.corpus.with_name(args.corpus.stem + ".meta.json").exists()
            else None,
        },
        "corpus_sha256": "sha256:" + sha256_file(args.corpus),
        "raw_sha256": "sha256:" + sha256_file(args.raw),
        "seed": args.seed,
        "labels": list(corpus_rows[0]["labels"]),
        "pin": pin_meta,
        "method": final_candidate["method"],
        "confidence_statistic": "top_probability",
        "input_statistic": "top_probability",
        "temperature": final_candidate["temperature"],
        "isotonic": final_candidate["isotonic"],
        "selection": {
            "fit_split": "train",
            "validation_split": "validation",
            "candidates": {
                name: {
                    **{k: v for k, v in candidate.items() if k != "artifact"},
                    "validation": validation_scores[name],
                }
                for name, candidate in candidates.items()
            },
            "selected": selected,
            "reason": "lowest validation ECE (Brier tie-break; temperature on exact tie)",
        },
        "metrics": {
            "test": evaluate(test, {"method": final_candidate["method"], **{k: final_candidate[k] for k in ("temperature", "isotonic")}}),
            "fit_in_sample": evaluate(
                fit_entries,
                {"method": final_candidate["method"], **{k: final_candidate[k] for k in ("temperature", "isotonic")}},
            ),
        },
        "provenance": {
            "git_head": _git(["rev-parse", "HEAD"]),
            "git_dirty_tracked": bool(_git(["status", "--porcelain", "--untracked-files=no"])),
            "python": platform.python_version(),
            "platform": platform.platform(),
            "fit_script": "calibration/fit.py",
            "capture_meta_sha256": "sha256:" + sha256_file(HERE / "corpus.meta.json")
            if (HERE / "corpus.meta.json").exists()
            else None,
        },
        "limitations": [
            "corpus-v1 is project-authored (160 synthetic support intents), not a production sample",
            "calibration is per pin + per label set (4 labels); do not apply it to other labels or pins",
            "single seed and single machine; cross-build determinism is not claimed",
            "test split is 40 examples; read the bootstrap intervals, not just point estimates",
            "calibrated.confidence estimates P(key correct) for this corpus distribution; it is not a correctness guarantee",
            "ODPS packaging and hub leaderboard are not part of this T13 card",
        ],
    }
    artifact["data_version"] = cal.compute_data_version(artifact)
    failures = cal.validate_artifact(artifact)
    if failures:
        raise SystemExit("artifact self-validation failed: " + "; ".join(failures))
    args.artifact.write_text(json.dumps(artifact, indent=2) + "\n")

    # Calibrated view on the untouched test rows only; raw records stay unchanged.
    records = []
    for e in entries:
        rec = dict(e["record"])
        if e["split"] == "test":
            rec["calibrated"] = cal.calibrated_block(
                e["probabilities"], e["key"], artifact
            )
            if response_hash(rec) != rec["contentHash"]:
                raise SystemExit(f"{e['id']}: calibrated view changed contentHash")
        records.append(rec)
    args.calibrated_records.write_text(
        "".join(json.dumps(r, separators=(",", ":"), ensure_ascii=True) + "\n" for r in records)
    )

    if not args.no_checksums:
        paths = [
            args.corpus,
            args.corpus.with_name(args.corpus.stem + ".meta.json"),
            args.raw,
            HERE / "corpus.ledger.jsonl",
            HERE / "corpus.records.jsonl",
            HERE / "corpus.page-table.json",
            HERE / "corpus.meta.json",
            args.artifact,
            args.calibrated_records,
        ]
        lines = []
        for path in paths:
            if path.exists():
                lines.append(f"{sha256_file(path)}  {_rel(path)}")
        args.checksums.write_text("\n".join(lines) + "\n")

    test_metrics = artifact["metrics"]["test"]
    print(
        f"selected: {selected} (validation {validation_scores})\n"
        f"data_version: {artifact['data_version']}\n"
        f"test n={test_metrics['n']} accuracy={test_metrics['accuracy']:.3f}\n"
        f"  ECE  raw-entropy={test_metrics['raw_entropy']['ece']:.4f} "
        f"raw-top={test_metrics['raw_top_probability']['ece']:.4f} "
        f"calibrated={test_metrics['calibrated']['ece']:.4f}\n"
        f"  Brier raw-top={test_metrics['raw_top_probability']['brier']:.4f} "
        f"calibrated={test_metrics['calibrated']['brier']:.4f}\n"
        f"wrote {_rel(args.artifact)} + {_rel(args.calibrated_records)}"
    )
    return 0


def _git(args):
    import subprocess

    try:
        return subprocess.run(
            ["git", *args], cwd=ROOT, capture_output=True, text=True, check=True
        ).stdout.strip() or None
    except Exception:
        return None


if __name__ == "__main__":
    raise SystemExit(main())
