#!/usr/bin/env python3
"""umpire decision-ledger v0 validator (+ --replay roundtrip gate).

Validator-first discipline à la ctxvm RFC-0: its own PASS + exit code is the contract.

Usage:
  python3 spec/validate_ledger.py L1.ledger.jsonl L1.records.jsonl [L2 L2 ...]
      Validates each (ledger, records) pair — per-line schemas, profile rules,
      minting recomputation, join rules — then runs the built-in negative corpus
      (ten engineered bad cases, plus two M2 calibrated-record cases when a fitted
      artifact is present; each MUST be caught).

  python3 spec/validate_ledger.py --replay L.ledger.jsonl L.records.jsonl BASELINE
      Re-derives the page table (spec/ledger_tools.py, generator shared) and
      compares it byte-for-byte with the committed baseline. Byte-identical = pass.

Deps: jsonschema >= 4  (the vendored xCABI-0.1 + local decision-record schemas).
"""

import hashlib
import json
import pathlib
import sys

try:
    import jsonschema
except ImportError:  # pragma: no cover
    print("deps: pip install jsonschema>=4", file=sys.stderr)
    sys.exit(2)

sys.path.insert(0, str(pathlib.Path(__file__).parent))

from ledger_tools import (  # noqa: E402
    canonical_json,
    decide_ref,
    derive_page_table,
    render_page_table,
    response_hash,
)
import calibration_tools as cal  # noqa: E402

HERE = pathlib.Path(__file__).parent
XCABI = json.loads((HERE / "vendor" / "x-cabi-0.1.schema.json").read_text())
RECORD_SCHEMA = json.loads((HERE / "decision-record-v0.schema.json").read_text())
PROFILE_EVENTS = frozenset({"page.declare", "page.fault"})


def load_calibration_artifacts():
    """M2 artifacts indexed by data_version. Structurally invalid artifacts are not
    loadable, so a calibrated record that references one is rejected (no artifact)."""
    artifacts = {}
    cal_dir = HERE.parent / "calibration"
    for path in sorted(cal_dir.glob("umpire-cal-*.json")):
        try:
            artifact = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        if cal.validate_artifact(artifact):
            continue
        version = artifact.get("data_version")
        if isinstance(version, str):
            artifacts[version] = artifact
    return artifacts


CAL_ARTIFACTS = load_calibration_artifacts()


def failures_for_pair(basename, ledger_lines, record_lines):
    """All rule violations for one (ledger, records) pair — only fully empty for a
    clean pair. Also returns raw counters for the summary line."""
    failures = []
    checked = 0

    def fail(label):
        failures.append(f"{basename}: {label}")

    events = []
    for i, line in enumerate(ledger_lines):
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            fail(f"ledger line {i + 1} not JSON: {exc}")
            continue
        checked += 1
        for err in jsonschema.Draft202012Validator(XCABI).iter_errors(event):
            checked += 1
            fail(f"ledger line {i + 1} xCABI schema: {err.message}")
        events.append(event)

    if (not failures or all("xCABI schema" not in f for f in failures)) and events:
        if events[0]["type"] != "session.open":
            fail("L3: first ledger line is not session.open")
        for i, event in enumerate(events[1:], start=2):
            checked += 1
            if event["type"] not in PROFILE_EVENTS:
                fail(f"L2: ledger line {i} out of profile ({event['type']})")
            elif event["type"] == "page.fault" and event["source"] != "tool_recall":
                fail(f"L4: fault source '{event['source']}' not allowed in the v0 profile")

    # recompute identity over declares/faults (minting gate)
    slots: dict[str, list[dict]] = {}
    for i, line in enumerate(ledger_lines):
        if not line:
            continue
        event = json.loads(line)
        if event["type"] == "page.declare":
            page = event["page"]
            slots.setdefault(page["id"]["logicalRef"], []).append(
                {"contentHash": page["id"]["contentHash"], "tokens": page["tokens"], "line": i + 1}
            )

    records = []
    for i, line in enumerate(record_lines):
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            fail(f"records line {i + 1} not JSON: {exc}")
            continue
        records.append(record)
        checked += 1
        for err in jsonschema.Draft202012Validator(RECORD_SCHEMA).iter_errors(record):
            checked += 1
            fail(f"records line {i + 1} schema: {err.message}")
        labels = record.get("labels") or []
        if len(set(labels)) != len(labels):
            fail(f"records line {i + 1}: duplicate labels")
            continue  # further checks need a sane label set
        label_set = set(labels)
        key = record.get("key")
        if key not in label_set:
            fail(f"records line {i + 1}: key '{key}' not in labels (contract §3.1)")
        probabilities = record.get("probabilities") or {}
        if set(probabilities) != label_set:
            fail(f"records line {i + 1}: probabilities keys != labels set")
        else:
            total = sum(probabilities.values())
            if abs(total - 1.0) > 1e-9:
                fail(f"records line {i + 1}: probabilities sum {total} != 1")
        # M2 calibration sidecar (optional): cross-check against the fitted artifact.
        # Raw probabilities and contentHash stay authoritative; calibration must not
        # change identity, labels, pin, or seed.
        if isinstance(record.get("calibrated"), dict):
            block = record["calibrated"]
            version = block.get("data_version")
            artifact = CAL_ARTIFACTS.get(version)
            if artifact is None:
                fail(f"records line {i + 1}: calibrated data_version {version!r} not found")
            else:
                artifact_failures = cal.validate_artifact(artifact)
                if artifact_failures:
                    fail(f"records line {i + 1}: calibration artifact invalid: {artifact_failures}")
                else:
                    if set(artifact.get("labels") or []) != label_set:
                        fail(f"records line {i + 1}: calibrated label set != artifact label set")
                    if block.get("method") != artifact.get("method"):
                        fail(f"records line {i + 1}: calibrated method != artifact method")
                    if record.get("seed") != artifact.get("seed"):
                        fail(f"records line {i + 1}: calibrated seed != artifact seed")
                    model_pin = record.get("model_pin") or {}
                    for field in ("model", "weights_hash", "decoding"):
                        if model_pin.get(field) != (artifact.get("pin") or {}).get(field):
                            fail(f"records line {i + 1}: calibrated model_pin.{field} != artifact pin")
                    try:
                        expected = cal.confidence_from_artifact(probabilities, key, artifact)
                    except Exception as exc:
                        fail(f"records line {i + 1}: calibrated confidence uncomputable: {exc}")
                    else:
                        checked += 1
                        stored = block.get("confidence")
                        if not isinstance(stored, (int, float)):
                            fail(f"records line {i + 1}: calibrated confidence is not numeric")
                        elif abs(expected - stored) > 1e-5:
                            fail(
                                f"records line {i + 1}: calibrated confidence {stored} "
                                f"!= recomputed {expected:.6f}"
                            )
        # minting recomputation — the stored echo is cross-checked, never trusted
        stored_ref = record.get("ref")
        stored_hash = record.get("contentHash")
        if isinstance(stored_ref, str):
            recomputed_ref = decide_ref(record.get("state"), labels)
            checked += 1
            if recomputed_ref != stored_ref:
                fail(f"records line {i + 1}: ref recompute mismatch (minting drift)")
        try:
            if isinstance(stored_hash, str):
                recomputed_hash = response_hash(record)
                checked += 1
                if recomputed_hash != stored_hash:
                    fail(f"records line {i + 1}: contentHash recompute mismatch")
        except (TypeError, KeyError):
            fail(f"records line {i + 1}: response fields unhashable")

    # join rules: fault rows need an earlier declare of their ref and must name the
    # resident (first) slot; every declared slot must be backed by >= 1 record
    declared_refs = set()
    backed = set()
    for i, line in enumerate(ledger_lines, start=1):
        if not line:
            continue
        event = json.loads(line)
        if event["type"] == "page.declare":
            page_id = event["page"]["id"]
            declared_refs.add(page_id["logicalRef"])
        elif event["type"] == "page.fault":
            checked += 1
            ref = event["page"]["logicalRef"]  # flat pageId on faults (xCABI asymmetry)
            if ref not in declared_refs:
                fail(f"ledger line {i}: fault before any declare of '{ref}'")
            else:
                resident = slots[ref][0]["contentHash"]
                if event["page"]["contentHash"] != resident:
                    fail(f"ledger line {i}: fault does not name the resident slot ({resident})")
    for record in records:
        if isinstance(record.get("ref"), str) and isinstance(record.get("contentHash"), str):
            backed.add((record["ref"], record["contentHash"]))
            if (record["ref"], record["contentHash"]) not in {
                (ref, s["contentHash"]) for ref, list_ in slots.items() for s in list_
            }:
                fail(f"record for '{record['ref']}' joins no declared slot")
    checked += 1
    orphans = [
        (ref, slot["contentHash"], slot["line"])
        for ref, list_ in slots.items()
        for slot in list_
        if (ref, slot["contentHash"]) not in backed
    ]
    if orphans:
        fail(f"declared slots without any record: {orphans}")

    return failures, checked, len(events), len(records)


PASS_MESSAGE = "LEDGER CONFORMANCE: PASS ({checked} checks)"


def run_positive(pairs):
    total_checked = 0
    total_events = 0
    total_records = 0
    clean = True
    for basename, ledger_path, records_path in pairs:
        ledger_lines = ledger_path.read_text().splitlines()
        record_lines = records_path.read_text().splitlines()
        failures, checked, nev, nrec = failures_for_pair(basename, ledger_lines, record_lines)
        total_checked += checked
        total_events += nev
        total_records += nrec
        for message in failures:
            print(f" - {message}")
            clean = False
        if failures:
            clean = False
    print(
        f"positive: {total_events} ledger events + {total_records} records validated "
        f"across {len(pairs)} fixtures"
    )
    return clean


def run_negative():
    caught = 0
    checked = 0
    for label, ledger_lines, record_lines in NEGATIVE_CASES:
        failures, n, _, _ = failures_for_pair(f"negative[{label}]", ledger_lines, record_lines)
        checked += n
        if failures:
            caught += 1
        else:
            print(f" - NEGATIVE GATE NOT CAUGHT ({label})")
    print(f"negative: {caught}/{len(NEGATIVE_CASES)} engineered bad cases correctly rejected")
    return caught == len(NEGATIVE_CASES)


def rec(  # shorthand for building a valid record inside the negative corpus
    state,
    labels,
    key,
    probabilities,
    confidence,
    ref=None,
    content_hash=None,
    seed=1,
    model_pin=None,
    calibrated=None,
):
    from ledger_tools import content_hash as ch

    record = {
        "schema": "decide-record/0.1",
        "state": state,
        "labels": labels,
        "ref": ref if ref is not None else decide_ref(state, labels),
        "contentHash": content_hash
        if content_hash is not None
        else ch(
            canonical_json(
                {"confidence": confidence, "key": key, "probabilities": probabilities}
            )
        ),
        "model_pin": model_pin
        or {"model": "fixtured-model", "weights_hash": "sha256:9a11b9c9", "decoding": "json-label-enum-v0"},
        "seed": seed,
        "key": key,
        "probabilities": probabilities,
        "confidence": confidence,
        "latency_ms": 350,
    }
    if calibrated is not None:
        record["calibrated"] = calibrated
    return json.dumps(record)


def ledger_of(*events):
    lines = []
    for ev in events:
        lines.append((json.dumps(ev) if isinstance(ev, dict) else ev) + "\n")
    return lines


OPEN = {
    "type": "session.open",
    "agentId": "umpire",
    "model": "m",
    "provider": "local",
    "budgetTokens": 0,
    "tokenizerId": "apx4-char",
}


def declare(ev_hash, tokens=10):
    return {
        "type": "page.declare",
        "page": {
            "id": {"logicalRef": REF_A, "contentHash": ev_hash },
            "kind": "tool_result",
            "tokens": tokens,
        },
    }


def fault(ev_hash, turn=1, source="tool_recall"):
    return {
        "type": "page.fault",
        "turn": turn,
        "page": {"logicalRef": REF_A, "contentHash": ev_hash},  # flat pageId
        "source": source,
    }


STATE_A = {"text": "hello world"}
LABELS_A = ["alpha", "beta"]
REF_A = decide_ref(STATE_A, LABELS_A)


NEGATIVE_CASES = [
    (
        "key-not-in-labels",
        ledger_of(OPEN, declare("sha256:00000001")),
        [rec(STATE_A, LABELS_A, "gamma", {"alpha": 0.5, "beta": 0.5}, 0.5)],
    ),
    (
        "probabilities-keys-mismatch",
        ledger_of(OPEN, declare("sha256:00000002")),
        [rec(STATE_A, LABELS_A, "alpha", {"alpha": 0.5, "beta": 0.4, "x": 0.1}, 0.5)],
    ),
    (
        "minting-drift",
        ledger_of(OPEN, declare("sha256:00000003")),
        [rec(STATE_A, LABELS_A, "alpha", {"alpha": 0.6, "beta": 0.4}, 0.5, ref="tool:decide({\"labels\":[\"alpha\",\"beta\"],\"state\":{\"text\":\"DIFFERENT\"}})")],
    ),
    (
        "probabilities-not-summing-1",
        ledger_of(OPEN, declare("sha256:00000004")),
        [rec(STATE_A, LABELS_A, "alpha", {"alpha": 0.5, "beta": 0.6}, 0.5)],
    ),
    (
        "event-out-of-profile",
        ledger_of(
            OPEN,
            declare("sha256:00000005"),
            {
                "type": "page.render",
                "turn": 1,
                "page": {"logicalRef": REF_A, "contentHash": "sha256:00000005"},
                "mode": "full",
            },
        ),
        [rec(STATE_A, LABELS_A, "alpha", {"alpha": 0.5, "beta": 0.5}, 0.5)],
    ),
    (
        "fault-source-semantic",
        ledger_of(OPEN, declare("sha256:00000006"), fault("sha256:11111111", source="semantic")),
        [rec(STATE_A, LABELS_A, "alpha", {"alpha": 0.5, "beta": 0.5}, 0.5)],
    ),
    (
        "float-in-ledger",
        ledger_of(OPEN, declare("sha256:00000007", tokens=12.5)),
        [rec(STATE_A, LABELS_A, "alpha", {"alpha": 0.5, "beta": 0.5}, 0.5)],
    ),
    (
        "confidence-out-of-range",
        ledger_of(OPEN, declare("sha256:00000008")),
        [rec(STATE_A, LABELS_A, "alpha", {"alpha": 0.5, "beta": 0.5}, 1.2)],
    ),
    (
        "orphan-declare",
        ledger_of(OPEN, declare("sha256:00000009")),
        [],
    ),
    (
        "duplicate-labels",
        ledger_of(OPEN, declare("sha256:0000000a")),
        [
            rec(
                STATE_A,
                ["alpha", "beta", "alpha"],
                "alpha",
                {"alpha": 0.5, "beta": 0.5},
                0.5,
                ref=decide_ref(STATE_A, ["alpha", "beta", "alpha"]),
            )
        ],
    ),
]


def _declare_for(ref, ev_hash, tokens=10):
    return {
        "type": "page.declare",
        "page": {
            "id": {"logicalRef": ref, "contentHash": ev_hash},
            "kind": "tool_result",
            "tokens": tokens,
        },
    }


def _calibrated_negative_cases():
    """Two M2 negatives, built only when a fitted artifact is present.

    A calibrated block must name a known artifact, match its pin/seed/labels/method,
    and carry the confidence that artifact recomputes. Both cases are otherwise
    schema-valid and join a valid declare.
    """
    if not CAL_ARTIFACTS:
        return []
    artifact = next(iter(CAL_ARTIFACTS.values()))
    labels = list(artifact["labels"])
    state = {"text": "calibration negative case"}
    ref = decide_ref(state, labels)
    key = labels[0]
    probabilities = {label: 1.0 / len(labels) for label in labels}
    model_pin = {
        field: artifact["pin"][field] for field in ("model", "weights_hash", "decoding")
    }
    cases = []
    for label, version, confidence in (
        ("calibrated-confidence-mismatch", artifact["data_version"], 0.123456),
        ("calibrated-unknown-version", "cal-v1:0000000000000000", probabilities[key]),
    ):
        block = {
            "method": artifact["method"],
            "data_version": version,
            "confidence": confidence,
        }
        record = json.loads(
            rec(
                state,
                labels,
                key,
                probabilities,
                0.0,
                seed=artifact["seed"],
                model_pin=model_pin,
                calibrated=block,
            )
        )
        cases.append(
            (
                label,
                ledger_of(OPEN, _declare_for(ref, record["contentHash"])),
                [json.dumps(record)],
            )
        )
    return cases


NEGATIVE_CASES.extend(_calibrated_negative_cases())


def main(argv):
    if "--replay" in argv:
        i = argv.index("--replay")
        ledger_path, records_path, baseline_path = (
            pathlib.Path(argv[i + 1]),
            pathlib.Path(argv[i + 2]),
            pathlib.Path(argv[i + 3]),
        )
        ledger_lines = ledger_path.read_text().splitlines()
        record_lines = records_path.read_text().splitlines()
        failures, checked, _, _ = failures_for_pair(ledger_path.name, ledger_lines, record_lines)
        if failures:
            print("roundtrip precondition failed — ledger itself is invalid:")
            for message in failures:
                print(" -", message)
            return 1
        derived = render_page_table(derive_page_table(ledger_lines, record_lines))
        baseline = baseline_path.read_bytes()
        digest = hashlib.sha256(derived).hexdigest()[:16]
        if derived == baseline:
            print(
                f"roundtrip: BYTE-IDENTICAL (page-table sha256:{digest}, "
                f"{checked} checks before derivation)"
            )
            return 0
        print(f"roundtrip: DIVERGENCE — re-derived sha256:{digest}, baseline does not match")
        print(" - tampered ledger, minting drift, or stale baseline")
        return 1

    usage = "usage: validate_ledger.py [--replay L R BASELINE] | [L1 R1 [L2 R2 ...]]"
    if not argv or any(a.startswith("--") for a in argv):
        print(usage)
        return 2
    if len(argv) % 2 != 0:
        print(usage)
        return 2
    pairs = [
        (pathlib.Path(a).name, pathlib.Path(a), pathlib.Path(b))
        for a, b in zip(argv[::2], argv[1::2])
    ]
    positive_ok = run_positive(pairs)
    negative_ok = run_negative()
    # rebuilding the tally from run_positive loses per-pair "checked" granularity;
    # re-run once to collect the total honestly.
    total_checked = 0
    for basename, ledger_path, records_path in pairs:
        _, checked, _, _ = failures_for_pair(
            basename, ledger_path.read_text().splitlines(), records_path.read_text().splitlines()
        )
        total_checked += checked
    for label, ledger_lines, record_lines in NEGATIVE_CASES:
        _, checked, _, _ = failures_for_pair(f"negative[{label}]", ledger_lines, record_lines)
        total_checked += checked
    if positive_ok and negative_ok:
        print(PASS_MESSAGE.format(checked=total_checked))
        return 0
    print("LEDGER CONFORMANCE: FAIL")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
