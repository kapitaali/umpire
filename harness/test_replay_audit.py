#!/usr/bin/env python3
"""Offline tests for harness/replay_audit.py (no model request)."""

import contextlib
import io
import json
import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "harness"))

import replay_audit as ra  # noqa: E402


def load_first(path):
    with path.open() as fh:
        for line in fh:
            if line.strip():
                return json.loads(line)
    raise AssertionError(f"empty {path}")


class ReplayAuditTests(unittest.TestCase):
    def setUp(self):
        self.corpus_row = load_first(ROOT / "calibration/corpus-v1.jsonl")
        self.baseline_row = load_first(ROOT / "calibration/raw-v1.jsonl")
        self.tmp = tempfile.TemporaryDirectory(prefix="umpire-replay-test-")
        self.dir = pathlib.Path(self.tmp.name)
        self.corpus = self.dir / "corpus.jsonl"
        self.baseline = self.dir / "baseline.jsonl"
        self.corpus.write_text(json.dumps(self.corpus_row) + "\n")
        self.baseline.write_text(json.dumps(self.baseline_row) + "\n")
        self.work = self.dir / "work"
        self.work.mkdir()
        self._old_decide = ra.umpire.decide

    def tearDown(self):
        ra.umpire.decide = self._old_decide
        self.tmp.cleanup()

    def run_audit(self):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = ra.main(
                [
                    "--corpus",
                    str(self.corpus),
                    "--baseline-raw",
                    str(self.baseline),
                    "--work-dir",
                    str(self.work),
                    "--limit",
                    "1",
                    "--json",
                ]
            )
        return code, json.loads(out.getvalue())

    def test_match(self):
        expected = self.baseline_row["record"]

        def fake_decide(*args, **kwargs):
            return {"key": expected["key"]}, expected

        ra.umpire.decide = fake_decide
        code, summary = self.run_audit()
        self.assertEqual(code, 0)
        self.assertTrue(summary["ok"])
        self.assertTrue(summary["identity_ok"])
        self.assertTrue(summary["distribution_ok"])
        self.assertEqual(summary["mismatches"], 0)
        self.assertEqual(summary["match_counts"]["contentHash"], 1)

    def test_mismatch(self):
        expected = dict(self.baseline_row["record"])
        expected["key"] = "technical" if expected["key"] != "technical" else "billing"
        expected["contentHash"] = "sha256:" + "0" * 64

        def fake_decide(*args, **kwargs):
            return {"key": expected["key"]}, expected

        ra.umpire.decide = fake_decide
        code, summary = self.run_audit()
        self.assertEqual(code, 1)
        self.assertFalse(summary["ok"])
        self.assertFalse(summary["identity_ok"])
        self.assertFalse(summary["distribution_ok"])
        self.assertEqual(summary["mismatches"], 1)
        self.assertFalse(summary["results"][0]["checks"]["key"])
        self.assertFalse(summary["results"][0]["checks"]["contentHash"])


if __name__ == "__main__":
    unittest.main()
