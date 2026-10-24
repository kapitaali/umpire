#!/usr/bin/env python3
"""M3 replay-audit for a pinned corpus (local-only).

Re-runs selected corpus rows through the same pinned harness and compares the
new decision record with the baseline raw capture.  This is the model-level
counterpart to the page-table `--replay` gate: the ledger can be re-derived
byte-for-byte, and a pinned corpus can be re-decided with the same identity.

A full corpus replay is intentionally not run by default; it costs one model
request per row (~10-15 s on the reference 4-core CPU).  Use --limit or --ids
for a smoke audit, or run the full corpus deliberately.

Usage:
    python3 harness/replay_audit.py --corpus calibration/corpus-v2.jsonl \
        --baseline-raw calibration/raw-v3.jsonl --limit 4
    python3 harness/replay_audit.py ... --split test --label technical
    python3 harness/replay_audit.py ... --ids ex-001,ex-200 --json

Exit code 0 means every selected row matched ref, contentHash, key,
probabilities, and model_pin.  Any mismatch exits 1.  A partial/incomplete
baseline is an error, not a pass.
"""

import argparse
import datetime as dt
import json
import os
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "harness"))

import umpire  # noqa: E402


def load_jsonl(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def select_rows(rows, ids_arg, split, label, limit):
    selected = rows
    if ids_arg:
        wanted = [x.strip() for x in ids_arg.split(",") if x.strip()]
        by_id = {r["id"]: r for r in selected}
        missing = [x for x in wanted if x not in by_id]
        if missing:
            raise SystemExit(f"ids not in corpus: {missing}")
        selected = [by_id[x] for x in wanted]
    if split:
        selected = [r for r in selected if r["split"] == split]
    if label:
        selected = [r for r in selected if r["gold"] == label]
    if limit is not None:
        selected = selected[:limit]
    if not selected:
        raise SystemExit("no rows selected")
    return selected


def main(argv=None):
    ap = argparse.ArgumentParser(description="replay-audit a pinned corpus")
    ap.add_argument("--corpus", type=pathlib.Path, default=ROOT / "calibration/corpus-v2.jsonl")
    ap.add_argument("--baseline-raw", type=pathlib.Path, default=ROOT / "calibration/raw-v3.jsonl")
    ap.add_argument("--server", default="http://127.0.0.1:8081")
    ap.add_argument("--pin", default="qwen3-1.7b")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--timeout", type=float, default=180)
    ap.add_argument("--ids", help="comma-separated corpus ids")
    ap.add_argument("--split", choices=("train", "validation", "test"))
    ap.add_argument("--label", choices=("billing", "other", "sales", "technical"))
    ap.add_argument("--limit", type=int, help="audit only the first N selected rows")
    ap.add_argument("--work-dir", type=pathlib.Path, help="fresh directory for replay ledgers")
    ap.add_argument("--json", action="store_true", help="emit one JSON summary line")
    args = ap.parse_args(argv)

    if args.limit is not None and args.limit < 1:
        raise SystemExit("--limit must be >= 1")
    corpus = load_jsonl(args.corpus)
    baseline = load_jsonl(args.baseline_raw)
    baseline_by_id = {row["id"]: row for row in baseline}
    selected = select_rows(corpus, args.ids, args.split, args.label, args.limit)
    missing = [row["id"] for row in selected if row["id"] not in baseline_by_id]
    if missing:
        raise SystemExit(
            f"baseline incomplete for selected ids: {missing[:5]} "
            f"({len(baseline)}/{len(corpus)} baseline rows)"
        )

    if args.work_dir:
        work = args.work_dir
        if work.exists() and any(work.iterdir()):
            raise SystemExit(f"work dir is not empty: {work}")
        work.mkdir(parents=True, exist_ok=True)
        cleanup = False
    else:
        work = pathlib.Path(
            tempfile.mkdtemp(
                prefix="umpire-replay-", dir=os.environ.get("UMPIRE_TMPDIR") or None
            )
        )
        cleanup = True
    ledger = work / "replay.ledger.jsonl"
    records = work / "replay.records.jsonl"

    started = dt.datetime.now(dt.timezone.utc)
    results = []
    mismatches = 0
    errors = 0
    match_counts = {name: 0 for name in ("ref", "contentHash", "key", "probabilities", "model_pin")}
    for index, row in enumerate(selected, start=1):
        expected = baseline_by_id[row["id"]]["record"]
        try:
            response, record = umpire.decide(
                args.server,
                row["state"],
                row["labels"],
                args.pin,
                args.seed,
                ledger,
                records,
                timeout=args.timeout,
            )
        except Exception as exc:
            mismatches += 1
            errors += 1
            results.append(
                {
                    "id": row["id"],
                    "ok": False,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
            print(f"[{index}/{len(selected)}] {row['id']} ERROR {exc}", file=sys.stderr)
            continue

        checks = {
            "ref": record.get("ref") == expected.get("ref"),
            "contentHash": record.get("contentHash") == expected.get("contentHash"),
            "key": record.get("key") == expected.get("key"),
            "probabilities": record.get("probabilities") == expected.get("probabilities"),
            "model_pin": record.get("model_pin") == expected.get("model_pin"),
        }
        for name, matched in checks.items():
            if matched:
                match_counts[name] += 1
        ok = all(checks.values())
        if not ok:
            mismatches += 1
        results.append(
            {
                "id": row["id"],
                "split": row["split"],
                "gold": row["gold"],
                "ok": ok,
                "checks": checks,
                "expected_key": expected.get("key"),
                "actual_key": record.get("key"),
                "expected_hash": expected.get("contentHash"),
                "actual_hash": record.get("contentHash"),
            }
        )
        status = "ok" if ok else "MISMATCH"
        print(
            f"[{index}/{len(selected)}] {row['id']} {row['split']:<10} "
            f"gold={row['gold']:<9} key={record.get('key')} {status}",
            file=sys.stderr,
        )

    elapsed = (dt.datetime.now(dt.timezone.utc) - started).total_seconds()
    n = len(selected)
    identity_ok = (
        errors == 0
        and match_counts["ref"] == n
        and match_counts["key"] == n
        and match_counts["model_pin"] == n
    )
    distribution_ok = (
        errors == 0
        and match_counts["contentHash"] == n
        and match_counts["probabilities"] == n
    )
    summary = {
        "ok": mismatches == 0,
        "identity_ok": identity_ok,
        "distribution_ok": distribution_ok,
        "match_counts": match_counts,
        "pin": args.pin,
        "seed": args.seed,
        "corpus": str(args.corpus),
        "baseline_raw": str(args.baseline_raw),
        "selected": n,
        "mismatches": mismatches,
        "errors": errors,
        "elapsed_s": round(elapsed, 3),
        "started": started.isoformat(),
        "work_dir": str(work),
        "results": results,
    }
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(
            f"REPLAY AUDIT: {'PASS' if summary['ok'] else 'FAIL'} "
            f"({n} rows, {mismatches} mismatches, {elapsed:.1f}s)"
        )
        print(
            "identity checks: "
            f"ref {match_counts['ref']}/{n}, key {match_counts['key']}/{n}, "
            f"model_pin {match_counts['model_pin']}/{n}; "
            "distribution checks: "
            f"contentHash {match_counts['contentHash']}/{n}, "
            f"probabilities {match_counts['probabilities']}/{n}"
        )
        if not summary["ok"]:
            for result in results:
                if not result.get("ok"):
                    print(json.dumps(result, indent=2, sort_keys=True))
    if cleanup and not args.json:
        print(f"replay artifacts: {work}")
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
