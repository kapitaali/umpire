#!/usr/bin/env python3
"""M2 live gates: one calibrated decision, determinism, guards, validator.

Requires:
  - llama-server on 127.0.0.1:8081 loaded with the pinned 1.7B GGUF
  - calibration/umpire-cal-v1.json from calibration/fit.py

Gates:
  L1 calibrated decide — record carries a calibrated block whose confidence
     recomputes from the raw distribution and the artifact; raw contentHash is
     unchanged by adding the view.
  L2 determinism — identical (state, labels, pin, seed) in two fresh ledgers gives
     the same contentHash and calibrated confidence.
  L3 guards — wrong seed, label set, pin, or data_version is rejected before any
     model call.
  L4 validator — the live pair passes spec/validate_ledger.py, including the two
     M2 negative cases (12/12).
"""

import copy
import json
import pathlib
import subprocess
import sys
import tempfile
import urllib.request

HERE = pathlib.Path(__file__).parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "spec"))

import calibration_tools as cal  # noqa: E402
import umpire  # noqa: E402

SERVER = "http://127.0.0.1:8081"
PIN = "qwen3-1.7b"
SEED = 7
LABELS = ["billing", "other", "sales", "technical"]
STATE = {"text": "The export job has been stuck for two hours and I need the CSV today"}
ARTIFACT = ROOT / "calibration" / "umpire-cal-v1.json"


def fail(label, detail=""):
    print(f"FAIL {label}: {detail}")
    globals()["FAILED"] = True


FAILED = False


def server_ready():
    try:
        urllib.request.urlopen(SERVER + "/health", timeout=5)
        return True
    except Exception as exc:
        print(f"FAIL server: llama-server not ready at {SERVER} ({exc})")
        return False


def one_decide(tmp, tag, artifact):
    ledger = pathlib.Path(tmp) / f"{tag}.ledger.jsonl"
    records = pathlib.Path(tmp) / f"{tag}.records.jsonl"
    response, record = umpire.decide(
        SERVER, dict(STATE), LABELS, PIN, SEED, ledger, records, calibration=artifact
    )
    return response, record, ledger, records


def l1_l2(tmp, artifact):
    r1, rec1, ledger, records = one_decide(tmp, "live-a", artifact)
    r2, rec2, _, _ = one_decide(tmp, "live-b", artifact)
    if "calibrated" not in rec1:
        fail("L1", "record has no calibrated block")
        return ledger, records
    block = rec1["calibrated"]
    expected = cal.confidence_from_artifact(rec1["probabilities"], rec1["key"], artifact)
    if abs(block["confidence"] - expected) > 1e-5:
        fail("L1", f"calibrated {block['confidence']} != recomputed {expected}")
    if block["data_version"] != artifact["data_version"]:
        fail("L1", "data_version mismatch")
    if block["method"] != artifact["method"]:
        fail("L1", "method mismatch")
    if not (0 <= block["confidence"] <= 1):
        fail("L1", f"calibrated confidence out of range: {block['confidence']}")
    raw_hash = cal.calibrated_block(rec1["probabilities"], rec1["key"], artifact)
    if raw_hash["confidence"] != block["confidence"]:
        fail("L1", "calibrated block is not deterministic")
    print(
        f"L1 ok: key={rec1['key']} raw_conf={rec1['confidence']} "
        f"calibrated={block['confidence']} ({block['method']})"
    )

    if rec1["contentHash"] != rec2["contentHash"]:
        fail("L2", "raw contentHash differs across identical decisions")
    if rec1["calibrated"] != rec2["calibrated"]:
        fail("L2", "calibrated view differs across identical decisions")
    if list(rec1["probabilities"].items()) != list(rec2["probabilities"].items()):
        fail("L2", "raw probabilities differ across identical decisions")
    if not FAILED:
        print(f"L2 ok: identical contentHash {rec1['contentHash']} and calibrated view")
    return ledger, records


def l3_guards(artifact):
    cases = []

    bad_seed = copy.deepcopy(artifact)
    bad_seed["seed"] = SEED + 1
    bad_seed["data_version"] = cal.compute_data_version(bad_seed)
    cases.append(("seed", bad_seed))

    bad_labels = copy.deepcopy(artifact)
    bad_labels["labels"] = ["billing", "sales", "technical"]
    bad_labels["data_version"] = cal.compute_data_version(bad_labels)
    cases.append(("labels", bad_labels))

    bad_pin = copy.deepcopy(artifact)
    bad_pin["pin"]["weights_hash"] = "sha256:" + "0" * 64
    bad_pin["data_version"] = cal.compute_data_version(bad_pin)
    cases.append(("pin", bad_pin))

    bad_version = copy.deepcopy(artifact)
    bad_version["data_version"] = "cal-v1:0000000000000000"
    cases.append(("data_version", bad_version))

    for label, bad in cases:
        try:
            umpire.decide(
                SERVER,
                dict(STATE),
                LABELS,
                PIN,
                SEED,
                pathlib.Path("/tmp/opencode/should-not-exist.ledger.jsonl"),
                pathlib.Path("/tmp/opencode/should-not-exist.records.jsonl"),
                calibration=bad,
            )
        except RuntimeError:
            print(f"L3 ok: rejected {label} mismatch before the model call")
        except Exception as exc:
            fail("L3", f"{label}: wrong exception {type(exc).__name__}: {exc}")
        else:
            fail("L3", f"{label}: invalid calibration was accepted")


def l4_validator(ledger, records):
    proc = subprocess.run(
        [
            sys.executable,
            str(ROOT / "spec" / "validate_ledger.py"),
            str(ledger),
            str(records),
        ],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0 or "PASS" not in proc.stdout:
        fail("L4", f"validator rejected live pair:\n{proc.stdout}{proc.stderr}")
    elif "negative: 12/12" not in proc.stdout:
        fail("L4", f"expected 12/12 negative cases:\n{proc.stdout}")
    else:
        print("L4 ok: live pair validates; negative cases 12/12")


def main():
    if not server_ready():
        return 1
    if not ARTIFACT.exists():
        print(f"FAIL artifact: {ARTIFACT} missing (run calibration/fit.py)")
        return 1
    artifact = json.loads(ARTIFACT.read_text())
    failures = cal.validate_artifact(artifact)
    if failures:
        print(f"FAIL artifact: {failures}")
        return 1
    with tempfile.TemporaryDirectory(prefix="umpire-m2-") as tmp:
        ledger, records = l1_l2(tmp, artifact)
        l3_guards(artifact)
        l4_validator(ledger, records)
    print("M2 LIVE GATES:", "FAIL" if FAILED else "PASS")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
