#!/usr/bin/env python3
"""Offline M2 calibration tests (stdlib unittest; no model server required).

Run:
    python3 -m unittest calibration/test_calibration.py -v

The corpus/raw/artifact/calibrated-record tests skip until the corresponding
artifacts exist; the math tests always run.
"""

import hashlib
import json
import pathlib
import sys
import unittest

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "spec"))

import calibration_tools as cal  # noqa: E402
from ledger_tools import decide_ref, response_hash  # noqa: E402


def sha256_file(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_jsonl(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def make_artifact(temperature=2.0, **overrides):
    artifact = {
        "schema": cal.CAL_SCHEMA,
        "corpus_sha256": "sha256:" + "1" * 64,
        "labels": ["billing", "other", "sales", "technical"],
        "method": cal.METHOD_TEMPERATURE,
        "confidence_statistic": "top_probability",
        "input_statistic": "top_probability",
        "seed": 7,
        "temperature": temperature,
        "isotonic": None,
        "pin": {
            "model": "qwen3-1.7b",
            "weights_hash": "sha256:" + "2" * 64,
            "decoding": "json-label-enum-v0@llama.cpp-v0.7.0/sha256:0f60e0483",
            "prompt_template_sha256": "sha256:" + "3" * 64,
        },
    }
    artifact.update(overrides)
    artifact["data_version"] = cal.compute_data_version(artifact)
    return artifact


class MathTests(unittest.TestCase):
    def test_temperature_identity_and_direction(self):
        p = {"a": 0.9, "b": 0.1}
        q1 = cal.temperature_probs(p, 1.0)
        self.assertAlmostEqual(q1["a"], 0.9, places=12)
        self.assertAlmostEqual(sum(q1.values()), 1.0, places=12)
        q2 = cal.temperature_probs(p, 2.0)
        self.assertLess(q2["a"], q1["a"])  # T>1 flattens
        self.assertGreater(q2["b"], q1["b"])
        q05 = cal.temperature_probs(p, 0.5)
        self.assertGreater(q05["a"], q1["a"])  # T<1 sharpens
        self.assertAlmostEqual(sum(q05.values()), 1.0, places=12)
        with self.assertRaises(ValueError):
            cal.temperature_probs(p, 0)

    def test_temperature_fit_improves_overconfident_nll(self):
        entries = []
        for i in range(100):
            gold = "a" if i % 4 else "b"  # 25% wrong at p(a)=0.95
            entries.append({"probabilities": {"a": 0.95, "b": 0.05}, "gold": gold})
        t, nll = cal.fit_temperature(entries)
        self.assertGreater(t, 1.0)
        self.assertLess(nll, cal.nll_at_temperature(entries, 1.0))

    def test_isotonic_pav(self):
        points = cal.isotonic_fit(
            [(0.6, 0), (0.6, 1), (0.7, 0), (0.8, 1), (0.9, 1)]
        )
        xs = [p[0] for p in points]
        ys = [p[1] for p in points]
        self.assertEqual(xs, sorted(set(xs)))
        self.assertTrue(all(ys[i] <= ys[i + 1] + 1e-12 for i in range(len(ys) - 1)))
        self.assertAlmostEqual(cal.isotonic_apply(points, 0.5), 0.25)
        self.assertAlmostEqual(cal.isotonic_apply(points, 0.7), 0.25)
        self.assertAlmostEqual(cal.isotonic_apply(points, 0.85), 1.0)
        self.assertAlmostEqual(cal.isotonic_apply(points, 1.0), 1.0)

    def test_ece_brier_known(self):
        self.assertAlmostEqual(cal.ece([0.9, 0.9], [1, 0]), 0.4)
        self.assertAlmostEqual(cal.brier([0.9, 0.9], [1, 0]), 0.41)
        self.assertAlmostEqual(cal.ece([1.0], [1]), 0.0)
        self.assertAlmostEqual(cal.brier([1.0], [1]), 0.0)

    def test_bootstrap_identical_is_zero(self):
        c = [0.5, 0.8, 0.2]
        y = [0, 1, 0]
        a = cal.bootstrap_delta(c, y, c, y, cal.ece, n=200)
        b = cal.bootstrap_delta(c, y, c, y, cal.ece, n=200)
        self.assertEqual(a, b)
        self.assertEqual(a["ci95"], [0.0, 0.0])


class ArtifactTests(unittest.TestCase):
    def test_version_depends_on_core_not_metrics(self):
        a = make_artifact(2.0)
        b = make_artifact(2.0)
        b["metrics"] = {"anything": 1}
        b["created"] = "2026-10-23T00:00:00Z"
        self.assertEqual(a["data_version"], cal.compute_data_version(b))
        c = make_artifact(3.0)
        self.assertNotEqual(a["data_version"], c["data_version"])
        d = make_artifact(2.0, seed=8)
        self.assertNotEqual(a["data_version"], cal.compute_data_version(d))

    def test_validate_artifact(self):
        self.assertEqual(cal.validate_artifact(make_artifact()), [])
        bad = make_artifact()
        bad["data_version"] = "cal-v1:deadbeefdeadbeef"
        self.assertTrue(cal.validate_artifact(bad))
        iso = make_artifact(
            method=cal.METHOD_ISOTONIC,
            temperature=None,
            isotonic={"points": [[0.5, 0.2], [0.9, 0.2]]},
        )
        self.assertEqual(cal.validate_artifact(iso), [])
        iso["isotonic"]["points"] = [[0.5, 0.9], [0.9, 0.2]]
        self.assertTrue(cal.validate_artifact(iso))

    def test_calibrated_block(self):
        artifact = make_artifact(2.0)
        p = {"billing": 0.7, "other": 0.1, "sales": 0.1, "technical": 0.1}
        block = cal.calibrated_block(p, "billing", artifact)
        self.assertEqual(block["method"], artifact["method"])
        self.assertEqual(block["data_version"], artifact["data_version"])
        expected = cal.temperature_probs(p, 2.0)["billing"]
        self.assertAlmostEqual(block["confidence"], expected, places=6)


class CorpusTests(unittest.TestCase):
    def test_corpus_integrity_if_present(self):
        path = HERE / "corpus-v1.jsonl"
        if not path.exists():
            self.skipTest("corpus-v1.jsonl not generated yet")
        rows = load_jsonl(path)
        self.assertEqual(len(rows), 160)
        self.assertEqual(len({r["id"] for r in rows}), 160)
        self.assertEqual(len({r["state"]["text"] for r in rows}), 160)
        self.assertTrue(all(r["state"]["text"].isascii() for r in rows))
        self.assertTrue(all(r["state"]["text"].endswith(r["message"]) for r in rows))
        self.assertTrue(all("Route the customer message" in r["state"]["text"] for r in rows))
        self.assertEqual({r["gold"] for r in rows}, {"billing", "other", "sales", "technical"})
        self.assertEqual(sum(1 for r in rows if r["split"] == "train"), 80)
        self.assertEqual(sum(1 for r in rows if r["split"] == "validation"), 40)
        self.assertEqual(sum(1 for r in rows if r["split"] == "test"), 40)
        for label in ("billing", "other", "sales", "technical"):
            self.assertEqual(sum(1 for r in rows if r["gold"] == label), 40)
        meta = json.loads((HERE / "corpus-v1.meta.json").read_text())
        self.assertEqual(meta["sha256"], "sha256:" + sha256_file(path))

    def test_raw_capture_integrity_if_present(self):
        raw_path = HERE / "raw-v1.jsonl"
        corpus_path = HERE / "corpus-v1.jsonl"
        if not raw_path.exists() or not corpus_path.exists():
            self.skipTest("raw capture not present yet")
        raw = load_jsonl(raw_path)
        corpus = {r["id"]: r for r in load_jsonl(corpus_path)}
        if len(raw) != 160:
            self.skipTest(f"raw capture incomplete ({len(raw)}/160)")
        self.assertEqual(len(raw), 160)
        self.assertEqual({r["id"] for r in raw}, set(corpus))
        pins = set()
        for row in raw:
            rec = row["record"]
            expected = corpus[row["id"]]
            self.assertEqual(row["gold"], expected["gold"])
            self.assertEqual(row["split"], expected["split"])
            self.assertEqual(rec["state"], expected["state"])
            self.assertTrue(row["correct"] == (rec["key"] == expected["gold"]))
            self.assertEqual(rec["ref"], decide_ref(rec["state"], rec["labels"]))
            self.assertEqual(rec["contentHash"], response_hash(rec))
            self.assertAlmostEqual(sum(rec["probabilities"].values()), 1.0, places=9)
            pins.add(json.dumps(rec["model_pin"], sort_keys=True))
        self.assertEqual(len(pins), 1)

    def test_artifact_and_calibrated_records_if_present(self):
        artifact_path = HERE / "umpire-cal-v1.json"
        records_path = HERE / "corpus.calibrated.records.jsonl"
        raw_path = HERE / "raw-v1.jsonl"
        if not (artifact_path.exists() and records_path.exists()):
            self.skipTest("fit artifacts not present yet")
        artifact = json.loads(artifact_path.read_text())
        self.assertEqual(cal.validate_artifact(artifact), [])
        self.assertEqual(artifact["metrics"]["test"]["n"], 40)
        raw = load_jsonl(raw_path)
        records = load_jsonl(records_path)
        self.assertEqual(len(records), 160)
        self.assertEqual(
            [r["record"]["contentHash"] for r in raw],
            [r["contentHash"] for r in records],
        )
        calibrated = 0
        for row, record in zip(raw, records):
            if row["split"] == "test":
                self.assertIn("calibrated", record)
                expected = cal.confidence_from_artifact(
                    record["probabilities"], record["key"], artifact
                )
                self.assertAlmostEqual(record["calibrated"]["confidence"], expected, places=5)
                self.assertEqual(record["calibrated"]["data_version"], artifact["data_version"])
                calibrated += 1
            else:
                self.assertNotIn("calibrated", record)
        self.assertEqual(calibrated, 40)


    def test_corpus_v2_integrity_if_present(self):
        path = HERE / "corpus-v2.jsonl"
        if not path.exists():
            self.skipTest("corpus-v2.jsonl not generated yet")
        rows = load_jsonl(path)
        self.assertEqual(len(rows), 480)
        self.assertEqual(len({r["id"] for r in rows}), 480)
        self.assertEqual(len({r["state"]["text"] for r in rows}), 480)
        self.assertTrue(all(r["state"]["text"].isascii() for r in rows))
        self.assertTrue(all(r["state"]["text"].endswith(r["message"]) for r in rows))
        self.assertEqual(
            {r["gold"] for r in rows}, {"billing", "other", "sales", "technical"}
        )
        self.assertEqual(sum(1 for r in rows if r["split"] == "train"), 240)
        self.assertEqual(sum(1 for r in rows if r["split"] == "validation"), 120)
        self.assertEqual(sum(1 for r in rows if r["split"] == "test"), 120)
        for label in ("billing", "other", "sales", "technical"):
            self.assertEqual(sum(1 for r in rows if r["gold"] == label), 120)
            for split, expected in (("train", 60), ("validation", 30), ("test", 30)):
                self.assertEqual(
                    sum(1 for r in rows if r["gold"] == label and r["split"] == split),
                    expected,
                )
        v1 = load_jsonl(HERE / "corpus-v1.jsonl")
        self.assertEqual(
            [(r["id"], r["message"], r["gold"], r["split"]) for r in rows[:160]],
            [(r["id"], r["message"], r["gold"], r["split"]) for r in v1],
        )
        meta = json.loads((HERE / "corpus-v2.meta.json").read_text())
        self.assertEqual(meta["sha256"], "sha256:" + sha256_file(path))
        self.assertEqual(meta["examples"], 480)
        self.assertEqual(meta["base_corpus"]["examples"], 160)

    def test_raw_v2_capture_integrity_if_present(self):
        raw_path = HERE / "raw-v2.jsonl"
        corpus_path = HERE / "corpus-v2.jsonl"
        if not raw_path.exists() or not corpus_path.exists():
            self.skipTest("raw-v2 capture not present yet")
        raw = load_jsonl(raw_path)
        corpus = {r["id"]: r for r in load_jsonl(corpus_path)}
        if len(raw) != 480:
            self.skipTest(f"raw-v2 capture incomplete ({len(raw)}/480)")
        self.assertEqual({r["id"] for r in raw}, set(corpus))
        pins = set()
        for row in raw:
            rec = row["record"]
            expected = corpus[row["id"]]
            self.assertEqual(row["gold"], expected["gold"])
            self.assertEqual(row["split"], expected["split"])
            self.assertEqual(rec["state"], expected["state"])
            self.assertTrue(row["correct"] == (rec["key"] == expected["gold"]))
            self.assertEqual(rec["ref"], decide_ref(rec["state"], rec["labels"]))
            self.assertEqual(rec["contentHash"], response_hash(rec))
            self.assertAlmostEqual(sum(rec["probabilities"].values()), 1.0, places=9)
            pins.add(json.dumps(rec["model_pin"], sort_keys=True))
        self.assertEqual(len(pins), 1)

    def test_artifact_v2_and_calibrated_records_if_present(self):
        artifact_path = HERE / "umpire-cal-v2.json"
        records_path = HERE / "corpus-v2.calibrated.records.jsonl"
        raw_path = HERE / "raw-v2.jsonl"
        if not (artifact_path.exists() and records_path.exists() and raw_path.exists()):
            self.skipTest("v2 fit artifacts not present yet")
        artifact = json.loads(artifact_path.read_text())
        self.assertEqual(cal.validate_artifact(artifact), [])
        self.assertEqual(artifact["metrics"]["test"]["n"], 120)
        raw = load_jsonl(raw_path)
        records = load_jsonl(records_path)
        self.assertEqual(len(raw), 480)
        self.assertEqual(len(records), 480)
        self.assertEqual(
            [r["record"]["contentHash"] for r in raw],
            [r["contentHash"] for r in records],
        )
        calibrated = 0
        for row, record in zip(raw, records):
            if row["split"] == "test":
                self.assertIn("calibrated", record)
                expected = cal.confidence_from_artifact(
                    record["probabilities"], record["key"], artifact
                )
                self.assertAlmostEqual(record["calibrated"]["confidence"], expected, places=5)
                self.assertEqual(record["calibrated"]["data_version"], artifact["data_version"])
                calibrated += 1
            else:
                self.assertNotIn("calibrated", record)
        self.assertEqual(calibrated, 120)

    def test_checksums_v2_if_present(self):
        path = HERE / "checksums-v2.sha256"
        if not path.exists():
            self.skipTest("v2 checksums not written yet")
        for line in path.read_text().splitlines():
            if not line.strip():
                continue
            digest, rel = line.split("  ", 1)
            target = ROOT / rel
            self.assertTrue(target.exists(), rel)
            self.assertEqual(sha256_file(target), digest, rel)

    def test_checksums_if_present(self):
        path = HERE / "checksums.sha256"
        if not path.exists():
            self.skipTest("checksums not written yet")
        for line in path.read_text().splitlines():
            if not line.strip():
                continue
            digest, rel = line.split("  ", 1)
            target = ROOT / rel
            self.assertTrue(target.exists(), rel)
            self.assertEqual(sha256_file(target), digest, rel)


if __name__ == "__main__":
    unittest.main()
