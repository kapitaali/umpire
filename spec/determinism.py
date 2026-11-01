#!/usr/bin/env python3
"""Cross-machine and cross-build determinism check for the page-table derivation.

**What is hashed.** `sha256(render_page_table(derive_page_table(ledger_lines, records_lines)))`
— the same bytes the `--replay` gate compares against each committed baseline. Hashing the
derivation, not the validator's printed string, means the check is independent of message
formatting and of jsonschema (this script is standard-library only).

**Why it exists.** Three reports disclaim cross-machine and cross-build stability
(RR-M1SL §25, RR-M1 §87, RR-M3 §98) and nothing measured it. This turns the disclaimer into a
number: record the hashes in one environment, re-derive them in others, compare.

**Modes.**

    python3 spec/determinism.py record   write reports/determinism-hashes.txt
    python3 spec/determinism.py check    compare this environment against that file (default)
    python3 spec/determinism.py show     print environment + hashes, compare nothing

`check` exits 1 on any mismatch, missing row or extra row, so CI can fail on divergence.

**Scope, stated plainly.** A pass means the derivation produced identical bytes in every
environment actually compared — listed in `reports/RR-DET-*.md`. It is a measurement over a
finite set of interpreters and builds, not a proof, and it says nothing about model outputs
(which are not expected to be byte-identical; see RR-M3FULL's 470/480 result).
"""

import hashlib
import json
import platform
import pathlib
import struct
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from ledger_tools import derive_page_table, render_page_table  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
TABLE = ROOT / "reports" / "determinism-hashes.txt"

# name -> (ledger, records, optional committed baseline)
TARGETS = {
    "fixtures/ab-demo": ("fixtures/ab-demo.ledger.jsonl", "fixtures/ab-demo.records.jsonl",
                         "fixtures/ab-demo.page-table.json"),
    "fixtures/intent-router": ("fixtures/intent-router.ledger.jsonl",
                               "fixtures/intent-router.records.jsonl",
                               "fixtures/intent-router.page-table.json"),
    "fixtures/live-demo": ("fixtures/live-demo.ledger.jsonl", "fixtures/live-demo.records.jsonl",
                           "fixtures/live-demo.page-table.json"),
    "fixtures/support-triage": ("fixtures/support-triage.ledger.jsonl",
                                "fixtures/support-triage.records.jsonl",
                                "fixtures/support-triage.page-table.json"),
    "calibration/corpus": ("calibration/corpus.ledger.jsonl", "calibration/corpus.records.jsonl",
                           "calibration/corpus.page-table.json"),
    "calibration/corpus-calibrated": ("calibration/corpus.ledger.jsonl",
                                      "calibration/corpus.calibrated.records.jsonl", None),
    "calibration/corpus-v3": ("calibration/corpus-v3.ledger.jsonl",
                              "calibration/corpus-v3.records.jsonl",
                              "calibration/corpus-v3.page-table.json"),
    "calibration/corpus-v3-calibrated": ("calibration/corpus-v3.ledger.jsonl",
                                         "calibration/corpus-v3.calibrated.records.jsonl", None),
    "tests/m3-replay-full": ("tests/m3-replay-full/replay.ledger.jsonl",
                             "tests/m3-replay-full/replay.records.jsonl",
                             "tests/m3-replay-full/replay.page-table.json"),
}


def digest_for(ledger_rel, records_rel, baseline_rel):
    ledger = (ROOT / ledger_rel).read_text(encoding="utf-8").splitlines()
    records = (ROOT / records_rel).read_text(encoding="utf-8").splitlines()
    derived = render_page_table(derive_page_table(ledger, records))
    digest = "sha256:" + hashlib.sha256(derived).hexdigest()
    baseline_ok = None
    if baseline_rel:
        baseline_ok = derived == (ROOT / baseline_rel).read_bytes()
    return digest, baseline_ok


def compute():
    out = {}
    baselines = {}
    for name in sorted(TARGETS):
        digest, baseline_ok = digest_for(*TARGETS[name])
        out[name] = digest
        baselines[name] = baseline_ok
    return out, baselines


def fingerprint():
    py = sys.version_info
    seed = __import__("os").environ.get("PYTHONHASHSEED", "(unset)")
    return (f"python {platform.python_implementation()} {py.major}.{py.minor}.{py.micro} "
            f"| {platform.machine()} | {struct.calcsize('P') * 8}-bit | "
            f"json {json.__name__}/{getattr(json, 'JSONEncoder', object).__module__} | "
            f"PYTHONHASHSEED={seed}")


def read_table():
    rows = {}
    for line in TABLE.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        name, digest = line.split()
        rows[name] = digest
    return rows


def main(argv):
    mode = (argv[0] if argv else "check")
    computed, baselines = compute()

    if mode == "record":
        lines = [
            "# umpire page-table derivation hashes — regenerate with: python3 spec/determinism.py record",
            "# object: sha256(render_page_table(derive_page_table(ledger_lines, records_lines))) — full 64 hex",
            "# compared across environments in reports/RR-DET-20261101.md; a mismatch in `check` is a defect",
        ]
        lines += [f"{name} {digest}" for name, digest in sorted(computed.items())]
        TABLE.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"recorded {len(computed)} rows -> {TABLE.relative_to(ROOT)}")
        print("env:", fingerprint())
        return 0

    if mode == "show":
        print("env:", fingerprint())
        for name, digest in sorted(computed.items()):
            print(f"  {name:34s} {digest[:23]}…")
        return 0

    if mode != "check":
        print("usage: determinism.py [record|check|show]", file=sys.stderr)
        return 2

    if not TABLE.exists():
        print(f"determinism: no table at {TABLE.relative_to(ROOT)} — run `record` first", file=sys.stderr)
        return 2
    expected = read_table()
    print("env:", fingerprint())

    failures = []
    for name in sorted(set(expected) | set(computed)):
        want, got = expected.get(name), computed.get(name)
        if want is None:
            failures.append(f"extra row (not in table): {name}")
        elif got is None:
            failures.append(f"missing row (in table, not computed): {name}")
        elif want != got:
            failures.append(f"MISMATCH {name}: table {want[:23]}… vs here {got[:23]}…")
        else:
            print(f"  ok    {name:34s} {got[:23]}…")

    if failures:
        print("\ndeterminism: DIVERGENCE", file=sys.stderr)
        for line in failures:
            print("  -", line, file=sys.stderr)
        return 1

    drifted = [n for n, ok in baselines.items() if ok is False]
    if drifted:
        print("\ndeterminism: derivation is consistent here, but does not match the committed "
              "baseline for: " + ", ".join(sorted(drifted)), file=sys.stderr)
        return 1

    print(f"\ndeterminism: PASS — {len(computed)} derivations byte-identical across "
          f"this environment and the recorded table")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
