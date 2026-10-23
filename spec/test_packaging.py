"""Packaging gates for the umpire ODPS/ODCS drop (spec/odps-odcs-plan.md).

Stdlib-only offline checks, always runnable:
  python3 spec/test_packaging.py

Guards the manifest + contract invariants (the ctxvm-validated gotchas), the
checksummed drop integrity, corpus/record shape claims made by the ODCS contract,
token/PII redaction (the `redactionScan` DQ check), and KPI/current-value parity
against calibration/umpire-cal-v2.json + capture metadata (the `metric-parity`
custom check).

Optional live gates (skipped when unset):
  UMPIRE_RUN_DQ_GATES=1   -> run the three dataQuality executable commands
                             (validator raw, validator calibrated, page-table replay)
  UMPIRE_ODPS_VENV=<bin>  -> open-data-products CLI (manifest validate)
                             e.g. /tmp/opencode/umpire-odps-venv/bin/open-data-products
  UMPIRE_ODCS_VENV=<bin>  -> audit/align from a venv with open-data-products[contracts]
                             e.g. /tmp/opencode/odcs-venv/bin/open-data-products
"""

import hashlib
import json
import os
import re
import unittest
import uuid
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
MANIFEST = REPO / "odps" / "umpire-calcorpus-0.1.odps.yaml"
CONTRACT = REPO / "contracts" / "umpire-calcorpus-0.1.odcs.yaml"
CHECKSUMS = REPO / "calibration" / "checksums-v2.sha256"
ARTIFACT = REPO / "calibration" / "umpire-cal-v2.json"
CAPTURE_META = REPO / "calibration" / "corpus-v2.capture.meta.json"

PRODUCT_UUID_SOURCE = "https://github.com/kapitaali/umpire/odps/umpire-calcorpus-0.1"

DROP_FILES = [
    "calibration/corpus-v2.jsonl",
    "calibration/corpus-v2.calibrated.records.jsonl",
    "calibration/corpus-v2.capture.meta.json",
    "calibration/corpus-v2.ledger.jsonl",
    "calibration/corpus-v2.meta.json",
    "calibration/corpus-v2.page-table.json",
    "calibration/corpus-v2.records.jsonl",
    "calibration/raw-v2.jsonl",
    "calibration/umpire-cal-v2.json",
]

# PAT / secret / credential patterns; the redactionScan DQ check claims zero matches.
SECRET_PATTERNS = [
    r"github_pat_[A-Za-z0-9_]{20,}",
    r"ghp_[A-Za-z0-9]{20,}",
    r"gho_[A-Za-z0-9]{20,}",
    r"sk-[A-Za-z0-9-]{16,}",
    r"xox[bpars]-[A-Za-z0-9-]{10,}",
    r"AKIA[0-9A-Z]{16}",
    r"BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY",
]

RECORD_KEYS = {
    "confidence", "contentHash", "key", "labels", "latency_ms",
    "model_pin", "probabilities", "ref", "schema", "seed", "state",
}
EXAMPLE_KEYS = {"gold", "id", "labels", "message", "split", "state"}
FIT_KEYS = {"correct", "gold", "id", "record", "split"}


def manifest_text() -> str:
    return MANIFEST.read_text(encoding="utf-8")


def contract_text() -> str:
    return CONTRACT.read_text(encoding="utf-8")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def kpi_blocks(text: str) -> dict:
    """Extract id -> block text for productStrategy KPI/contribution entries
    (covers both the contributesToKPI: headline and the productKPIs: list)."""
    starts = [(m.start(), m.group(1)) for m in re.finditer(r"-? *id: (kpi-[a-z\-]+)", text)]
    end_markers = [text.index("\n  SLA:")]
    blocks = {}
    for i, (pos, kpi) in enumerate(starts):
        end = starts[i + 1][0] if i + 1 < len(starts) else min(end_markers[0], len(text))
        blocks[kpi] = text[pos:end]
    return blocks


def block_value(block: str, key: str):
    m = re.search(rf"\b{re.escape(key)}:\s*([0-9.]+)", block)
    return float(m.group(1)) if m else None


class DropIntegrity(unittest.TestCase):
    def test_all_nine_checksummed_files_match(self):
        lines = [l for l in CHECKSUMS.read_text().splitlines() if l.strip()]
        self.assertEqual(len(lines), 9, "checksums-v2 must list exactly 9 artifacts")
        seen = set()
        for line in lines:
            digest, rel = line.split()
            path = REPO / rel
            self.assertTrue(path.exists(), f"missing drop file {rel}")
            self.assertEqual(sha256_file(path), digest, f"sha mismatch for {rel}")
            seen.add(rel)
        self.assertEqual(seen, set(DROP_FILES))

    def test_capture_meta_complete_and_consistent(self):
        meta = json.loads(CAPTURE_META.read_text())
        self.assertTrue(meta["capture"]["complete"])
        self.assertEqual(meta["corpus"]["examples"], 480)
        self.assertEqual(meta["counts"]["total"], 480)
        self.assertEqual(meta["counts"]["per_split"], {
            "train": {"n": 240, "correct": 168, "accuracy": 0.7},
            "validation": {"n": 120, "correct": 89, "accuracy": 0.7417},
            "test": {"n": 120, "correct": 85, "accuracy": 0.7083},
        })
        self.assertEqual(meta["page_table_stats"]["re_decides"], 0)

    def test_jsonl_shape_claims(self):
        from collections import Counter
        splits = Counter()
        labels = Counter()
        ids = set()
        with (REPO / "calibration/corpus-v2.jsonl").open() as f:
            for line in f:
                row = json.loads(line)
                self.assertEqual(set(row), EXAMPLE_KEYS)
                self.assertIn(row["split"], {"train", "validation", "test"})
                self.assertIn(row["gold"], row["labels"])
                self.assertIn(row["state"]["text"], row["state"]["text"])  # nested shape
                splits[row["split"]] += 1
                labels[frozenset(row["labels"])] += 1
                ids.add(row["id"])
        self.assertEqual(dict(splits), {"train": 240, "validation": 120, "test": 120})
        self.assertEqual(len(labels), 1, "single label set across the corpus")
        self.assertEqual(sum(labels.values()), 480)
        self.assertEqual(len(ids), 480)

    def test_decision_records_shape_and_uniqueness(self):
        refs = set()
        rows = 0
        with (REPO / "calibration/corpus-v2.records.jsonl").open() as f:
            for line in f:
                row = json.loads(line)
                extra = set(row) - RECORD_KEYS
                self.assertEqual(extra, set(), f"unexpected record keys {extra}")
                self.assertEqual(row["schema"], "decide-record/0.1")
                self.assertIn(row["key"], row["labels"])
                self.assertRegex(row["contentHash"], r"^sha256:[0-9a-f]{8}$")
                self.assertIsInstance(row["model_pin"], dict)
                self.assertAlmostEqual(sum(row["probabilities"].values()), 1.0, places=6)
                self.assertLessEqual(row["confidence"], 1.0)
                self.assertGreaterEqual(row["confidence"], 0.0)
                refs.add(row["ref"])
                rows += 1
        self.assertEqual(rows, 480)
        self.assertEqual(len(refs), 480, "ref must be unique (one ref per fresh decision)")

    def test_calibrated_sidecar_only_semantics(self):
        raw = [json.loads(l) for l in (REPO / "calibration/corpus-v2.records.jsonl").read_text().splitlines()]
        with (REPO / "calibration/corpus-v2.calibrated.records.jsonl").open() as f:
            cal = [json.loads(l) for l in f]
        self.assertEqual(len(cal), 480)
        with_sidecar = [r for r in cal if "calibrated" in r]
        without = [r for r in cal if "calibrated" not in r]
        self.assertEqual(len(with_sidecar), 120, "calibrated block exists on test rows only")
        self.assertEqual(len(without), 360)
        for row in with_sidecar:
            self.assertEqual(row["calibrated"]["data_version"], "cal-v1:aceb881587b3b3a6")
            self.assertEqual(row["calibrated"]["method"], "isotonic/1")
        # every calibrated row matches its raw sibling field-for-field except the sidecar
        base = [{k: v for k, v in r.items() if k != "calibrated"} for r in raw]
        for r in cal:
            stripped = {k: v for k, v in r.items() if k != "calibrated"}
            self.assertIn(stripped, base, "calibrated record must carry its raw sibling")

    def test_ledger_and_fit_counts(self):
        with (REPO / "calibration/corpus-v2.ledger.jsonl").open() as f:
            ledger_lines = sum(1 for _ in f)
        self.assertEqual(ledger_lines, 481)
        fit_rows = 0
        fit_ids = set()
        with (REPO / "calibration/raw-v2.jsonl").open() as f:
            for line in f:
                row = json.loads(line)
                self.assertEqual(set(row), FIT_KEYS)
                self.assertIsInstance(row["correct"], bool)
                fit_ids.add(row["id"])
                fit_rows += 1
        self.assertEqual(fit_rows, 480)
        self.assertEqual(len(fit_ids), 480)


class ContractAndFixtures(unittest.TestCase):
    def test_redaction_scan_zero_matches(self):
        files = [REPO / rel for rel in DROP_FILES] + [MANIFEST, CONTRACT]
        for path in files:
            text = path.read_text(encoding="utf-8", errors="replace")
            for pat in SECRET_PATTERNS:
                self.assertIsNone(re.search(pat, text), f"secret pattern {pat} in {path.name}")

    def test_manifest_quotes_odps_version(self):
        self.assertIn('version: "4.1"', manifest_text())
        self.assertIn("schema: https://opendataproducts.org/v4.1/schema/odps.yaml", manifest_text())

    def test_contract_version_fields(self):
        m = manifest_text()
        c = contract_text()
        self.assertIn('contractVersion: "3.0.2"', m)  # standard's version, not document version
        self.assertIn("apiVersion: v3.0.2", c)
        self.assertIn("version: 1.0.0", c)
        self.assertIn("kind: DataContract", c)
        self.assertIn("status: active", c)

    def test_product_uuid_is_deterministic_and_shared(self):
        expected = str(uuid.uuid5(uuid.NAMESPACE_URL, PRODUCT_UUID_SOURCE))
        m = manifest_text()
        c = contract_text()
        ids_m = set(re.findall(r"id:\s*([0-9a-f\-]{36})", m))
        ids_c = set(re.findall(r"^id:\s*([0-9a-f\-]{36})", c, re.M))
        self.assertIn(expected, ids_m, "manifest contract id must be the deterministic uuid5")
        self.assertEqual(ids_c, {expected}, "contract id must equal the manifest contract id")

    def test_sla_unit_enum_has_no_hours(self):
        m = manifest_text()
        sla = m[m.index("\n  SLA:"): m.index("  dataQuality:")]
        self.assertNotIn("unit: hours", sla)
        for unit in ("unit: weeks", "unit: days", "unit: percent"):
            self.assertIn(unit, sla)

    def test_dq_executable_is_named_object_with_object_specs(self):
        m = manifest_text()
        dq = m[m.index("\n  dataQuality:"): m.index("  dataAccess:")]
        self.assertIn("  executable:", dq)
        for name in ("replayDeterminism", "calibrationParity", "redactionScan"):
            self.assertIn(f"    {name}:", dq, f"DQ executable must be named-object: {name}")
        self.assertEqual(dq.count("dimensions:"), 4)  # 1 declarative + 3 executable
        for fallback in ("tool: umpire",):
            self.assertIn(fallback, dq)
        # spec blocks are objects (tool/command/passCriterion indented under spec:)
        self.assertRegex(dq, r"spec:\n {10}tool: umpire")

    def test_flat_license_and_dataholder(self):
        m = manifest_text()
        self.assertIn("legalName: umpire Project", m)
        dh = m[m.index("  dataHolder:"):]
        self.assertRegex(dh, r"dataHolder:\n    legalName:")
        self.assertIn("applicableLaws:", m)
        self.assertIn("Apache-2.0", m)
        self.assertIn("CC-BY-4.0", m)

    def test_x_umpire_pin_identity_echoes_capture_meta(self):
        meta = json.loads(CAPTURE_META.read_text())
        m = manifest_text()
        pin = meta["pin"]
        for value in (
            pin["weights_hash"], pin["decoding"], pin["prompt_template_sha256"],
            pin["runner_binary_sha256"], meta["seed"], meta["hashes"]["page_table_sha256"],
            meta["hashes"]["corpus_sha256"] if "corpus_sha256" in meta["hashes"] else meta["corpus"]["sha256"],
            "cal-v1:aceb881587b3b3a6",
        ):
            self.assertIn(str(value), m, f"x-umpire must echo {value}")

    def test_contract_referenced_gates_exist(self):
        c = contract_text()
        self.assertIn("python3 spec/test_packaging.py", c)
        self.assertIn("sha256sum -c calibration/checksums-v2.sha256", c)
        self.assertIn("python3 spec/validate_ledger.py --replay", c)
        self.assertIn("negative corpus 12/12", c)

    def test_contract_declares_expected_properties(self):
        c = contract_text()
        for name in ("umpireCalibrationExample", "umpireDecisionRecord",
                     "umpireFitRecord", "umpireCalibrationArtifact"):
            self.assertIn(f"- name: {name}", c)
        self.assertIn("decide-record/0.1", c)
        self.assertIn("sha256:<8 hex>", c)
        self.assertIn("duplicateCount", c)
        self.assertIn("slaProperties:", c)


class KpiParity(unittest.TestCase):
    """The contract's `metric-parity` custom check: manifest KPI targets must match
    the values recomputed from the committed artifact."""

    def setUp(self):
        self.art = json.loads(ARTIFACT.read_text())
        self.test = self.art["metrics"]["test"]

    def test_artifact_metrics_match_reported_values(self):
        self.assertEqual(self.art["data_version"], "cal-v1:aceb881587b3b3a6")
        self.assertEqual(self.art["method"], "isotonic/1")
        self.assertEqual(self.test["n"], 120)
        self.assertAlmostEqual(self.test["calibrated"]["ece"], 0.026684, places=6)
        self.assertAlmostEqual(self.test["calibrated"]["brier"], 0.172292, places=6)
        self.assertAlmostEqual(self.test["accuracy"], 0.708333, places=6)
        self.assertAlmostEqual(self.test["raw_top_probability"]["ece"], 0.248294, places=6)
        self.assertAlmostEqual(self.test["raw_top_probability"]["brier"], 0.238044, places=6)

    def test_manifest_kpi_targets_hold_against_artifact(self):
        blocks = kpi_blocks(manifest_text())
        for kpi in ("kpi-calibrated-ece", "kpi-replay-determinism",
                    "kpi-negative-conformance", "kpi-calibrated-brier", "kpi-decision-accuracy"):
            self.assertIn(kpi, blocks, f"missing KPI {kpi}")
        ece = block_value(blocks["kpi-calibrated-ece"], "target")
        brier = block_value(blocks["kpi-calibrated-brier"], "target")
        acc = block_value(blocks["kpi-decision-accuracy"], "target")
        self.assertEqual((ece, brier, acc), (5.0, 20.0, 68.0))
        self.assertLessEqual(self.test["calibrated"]["ece"] * 100, ece)
        self.assertLessEqual(self.test["calibrated"]["brier"] * 100, brier)
        self.assertGreaterEqual(self.test["accuracy"] * 100, acc)
        self.assertIn("direction: at_most", blocks["kpi-calibrated-ece"])
        self.assertIn("direction: at_most", blocks["kpi-calibrated-brier"])
        self.assertIn("direction: at_least", blocks["kpi-replay-determinism"])
        self.assertIn("direction: at_least", blocks["kpi-decision-accuracy"])


class OptionalLiveGates(unittest.TestCase):
    """Skipped unless explicitly requested; runs the DQ executable commands verbatim."""

    def _run(self, argv):
        import subprocess
        p = subprocess.run(argv, cwd=REPO, capture_output=True, text=True, timeout=1800)
        return p

    def test_dq_executable_gates(self):
        if not os.environ.get("UMPIRE_RUN_DQ_GATES"):
            self.skipTest("set UMPIRE_RUN_DQ_GATES=1 to run the DQ executable gates")
        base = ["python3", "spec/validate_ledger.py"]
        p1 = self._run(base + ["calibration/corpus-v2.ledger.jsonl", "calibration/corpus-v2.records.jsonl"])
        self.assertEqual(p1.returncode, 0, p1.stdout[-400:])
        p2 = self._run(base + ["calibration/corpus-v2.ledger.jsonl", "calibration/corpus-v2.calibrated.records.jsonl"])
        self.assertEqual(p2.returncode, 0, p2.stdout[-400:])
        p3 = self._run(base + ["--replay", "calibration/corpus-v2.ledger.jsonl",
                               "calibration/corpus-v2.records.jsonl",
                               "calibration/corpus-v2.page-table.json"])
        self.assertEqual(p3.returncode, 0, p3.stdout[-400:])
        self.assertIn("BYTE-IDENTICAL", p3.stdout)
        outs = p1.stdout + p2.stdout
        self.assertIn("12/12", outs)

    def test_sdk_validate_manifest(self):
        bin_odps = os.environ.get("UMPIRE_ODPS_VENV")
        if not bin_odps:
            self.skipTest("set UMPIRE_ODPS_VENV to the open-data-products CLI")
        p = self._run([bin_odps, "validate", str(MANIFEST)])
        self.assertEqual(p.returncode, 0, p.stdout[-400:])

    def test_sdk_product_audit(self):
        bin_audit = os.environ.get("UMPIRE_ODCS_VENV")
        if not bin_audit:
            self.skipTest("set UMPIRE_ODCS_VENV to an open-data-products CLI with datacontract-cli")
        p = self._run([bin_audit, "product", "audit", str(MANIFEST)])
        self.assertEqual(p.returncode, 0, p.stdout[-400:])


if __name__ == "__main__":
    unittest.main()
