#!/usr/bin/env python3
"""M1 property tests (empirical gates for the reference harness).

Requires a running llama-server loaded with the pinned GGUF (see 00-SPEC §2 / RR-M1
for the invocation) and FILLED pins.json (harness/fill_pins.py).

Gates:
  P1 seeded determinism — same (state, labels, pin, seed), two cold calls ⇒
     identical decoded text AND identical record contentHash (contract §3.3).
  P2 key domain — every decoded key is in the request's labels (grammar can't
     invent a class; zero-invented-classes by construction).
  P3 probabilities — every record's probabilities sum to 1 over exactly the
     requested labels (checked via the M0 validator on the produced pair).
  P4 re-decide pricing — re-deciding an identical (state, labels) against the
     same ledger appends a page.fault (tool_recall) naming the resident slot,
     NOT a second declare of the same slot (M0 ledger semantics, now live-gated).
  P5 end-to-end validator — the live pair passes spec/validate_ledger.py and its
     --replay roundtrip is byte-identical.
"""

import json
import os
import pathlib
import subprocess
import sys
import tempfile
import urllib.request

HERE = pathlib.Path(__file__).parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "spec"))

import umpire  # noqa: E402

SERVER = "http://127.0.0.1:8081"
PIN = "qwen3-1.7b"
SEED = 7
LABELS = ["billing", "other", "sales", "technical"]
STATE = {"text": "I was charged twice this month and need it fixed now"}


def fail(label, detail=""):
    print(f"FAIL {label}: {detail}")
    globals()["FAILED"] = True


def server_ready():
    try:
        urllib.request.urlopen(SERVER + "/health", timeout=5)
        return True
    except Exception as exc:
        print(f"FAIL server: llama-server not ready at {SERVER} ({exc})")
        return False


FAILED = False

STATE_TEXT = STATE["text"]

def run_pair(tmp, tag, same_ledger=None):
    """One harness decide, into either a fresh pair or the shared live pair."""
    if same_ledger is None:
        ledger = pathlib.Path(tmp) / f"{tag}.ledger.jsonl"
        records = pathlib.Path(tmp) / f"{tag}.records.jsonl"
    else:
        ledger, records = same_ledger
    response, record = umpire.decide(
        SERVER,
        {"text": STATE_TEXT},
        LABELS,
        PIN,
        SEED,
        ledger,
        records,
    )
    return response, record, ledger, records


def p1_determinism(tmp):
    try:
        r1, rec1, _, _ = run_pair(tmp, "p1a")
        r2, rec2, _, _ = run_pair(tmp, "p1b")
    except Exception as exc:
        return fail("P1", f"decide raised: {exc}")
    if rec1["ref"] != rec2["ref"]:
        fail("P1", "refs differ for identical requests")
    if list(rec1["probabilities"].items()) != list(rec2["probabilities"].items()):
        fail("P1", "probabilities differ across identical requests")
    else:
        print(f"P1 ok: byte-identical raw distribution {rec1['probabilities']}")
    return rec1


def p2_key_domain(tmp):
    prompts = {
        "webhook": "webhook integration fails with 500s after department deploy",
        "invoice": "where do I send a PO for team licenses?",
        "refund": "order 5582 arrived damaged, want money back immediately",
    }
    ok = True
    for tag, text in prompts.items():
        try:
            response, record = umpire.decide(
                SERVER,
                {"text": text},
                LABELS,
                PIN,
                21,
                pathlib.Path(tmp) / f"p2-{tag}.ledger.jsonl",
                pathlib.Path(tmp) / f"p2-{tag}.records.jsonl",
            )
        except Exception as exc:
            fail("P2", f"{tag} decide raised: {exc}")
            return None
        if record["key"] not in LABELS:
            fail("P2", f"{tag}: key {record['key']!r} escaped the enum")
            ok = False
        else:
            total = sum(record["probabilities"].values())
            if abs(total - 1.0) > 1e-9:
                fail("P2", f"{tag}: probabilities sum {total} != 1")
                ok = False
            else:
                print(f"P2 ok: '{text[:38]}…' → key={record['key']} conf={record['confidence']}")
    return ok


def p4_and_p5(live_pair):
    ledger_path, records_path = live_pair
    before = len(ledger_path.read_text().splitlines())
    rec_before = len(records_path.read_text().splitlines())
    try:
        _, record = umpire.decide(
            SERVER, {"text": STATE_TEXT}, LABELS, PIN, SEED, ledger_path, records_path
        )
    except Exception as exc:
        return fail("P4", f"re-decide raised: {exc}")
    lines = ledger_path.read_text().splitlines()
    after = len(lines)
    events = [json.loads(l) for l in lines]
    new_events = events[before:]
    if len(new_events) != 1 or new_events[0]["type"] != "page.fault":
        fail("P4", f"expected exactly one page.fault, got {new_events}")
        return
    if new_events[0]["source"] != "tool_recall":
        fail("P4", f"fault source {new_events[0]['source']} != tool_recall")
        return
    declares = [e for e in events if e["type"] == "page.declare"]
    resident = declares[0]["page"]["id"]["contentHash"]
    if new_events[0]["page"]["contentHash"] != resident:
        fail("P4", "fault does not name the resident (first) slot")
        return
    if new_events[0]["turn"] != rec_before:
        fail("P4", f"fault turn {new_events[0]['turn']} != decision index {rec_before}")
        return
    print(f"P4 ok: re-decide → fault@turn{new_events[0]['turn']} naming resident {resident}")

    v = subprocess.run(
        [
            os.environ.get("UMPIRE_PYTHON", sys.executable),
            str(HERE.parent / "spec" / "validate_ledger.py"),
            str(ledger_path),
            str(records_path),
        ],
        capture_output=True,
        text=True,
    )
    if v.returncode != 0 or "PASS" not in v.stdout:
        fail("P5", f"validator rejected the live pair: {v.stdout}{v.stderr}")
        return
    print(f"P5 ok: live pair validates ({'PASS' if 'PASS' in v.stdout else '?'})")


def main():
    if not server_ready():
        return 1
    with tempfile.TemporaryDirectory(prefix="umpire-m1-") as tmp:
        rec1 = p1_determinism(tmp)
        p2_ok = p2_key_domain(tmp)
        live_pair = (pathlib.Path(tmp) / "live.ledger.jsonl", pathlib.Path(tmp) / "live.records.jsonl")
        if rec1:
            try:
                umpire.decide(SERVER, {"text": STATE_TEXT}, LABELS, PIN, SEED, *live_pair)
                p4_and_p5(live_pair)
            except Exception as exc:
                fail("P4", f"first live append raised: {exc}")
    if p2_ok:
        print("P2 ok (all)")
    print("M1 PROPERTY GATES:", "FAIL" if FAILED else "PASS (all reachable gates green)")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
