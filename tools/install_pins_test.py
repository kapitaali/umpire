#!/usr/bin/env python3
"""Offline tests for tools/install-pins.py (W9 install-realism card, 2026-11-02).

No network, no real model files: the verification logic is exercised on tiny
temp files with dynamically computed digests, the registry contract of
harness/pins.json is checked structurally (the stamps the harness fail-hards on
at decide time are the same stamps the downloader enforces at install time),
and the sidecar/resume-name laws are pinned as strings.

Run: python3 -m unittest tools/install_pins_test.py
"""

import importlib.util
import hashlib
import json
import pathlib
import sys
import tempfile
import unittest

_TOOLS = pathlib.Path(__file__).resolve().parent
_ROOT = _TOOLS.parent

_spec = importlib.util.spec_from_file_location("install_pins", _TOOLS / "install-pins.py")
if _spec is None or _spec.loader is None:  # pragma: no cover - import machinery failure
    raise ImportError("cannot import tools/install-pins.py")
ip = importlib.util.module_from_spec(_spec)
sys.modules["install_pins_module"] = ip
_spec.loader.exec_module(ip)


class UrlTest(unittest.TestCase):
    def test_url_for_builds_resolve_url_from_the_stanza(self):
        pin = {"gguf": {"repo": "Qwen/Qwen3-1.7B-GGUF", "file": "Qwen3-1.7B-Q8_0.gguf"}}
        self.assertEqual(ip.url_for(pin),
                         "https://huggingface.co/Qwen/Qwen3-1.7B-GGUF/resolve/main/Qwen3-1.7B-Q8_0.gguf")

    def test_url_for_quotes_spaces_in_filenames(self):
        pin = {"gguf": {"repo": "org/rep-o", "file": "model name v2.gguf"}}
        self.assertEqual(ip.url_for(pin),
                         "https://huggingface.co/org/rep-o/resolve/main/model%20name%20v2.gguf")


class ResolveTest(unittest.TestCase):
    def test_all_pins_selected_when_no_ids(self):
        self.assertEqual(ip.resolve_pins({"b": {}, "a": {}}, None), ["a", "b"])

    def test_caller_order_preserved_and_duplicates_dropped(self):
        self.assertEqual(ip.resolve_pins({"a": 1, "b": 2, "c": 3}, ["c", "a", "c"]), ["c", "a"])

    def test_unknown_pin_is_an_explicit_error_listing_valid_ids(self):
        with self.assertRaisesRegex(ValueError, "unknown pin 'x' .*configured: a, b"):
            ip.resolve_pins({"a": 1, "b": 2}, ["x"])

    def test_dash_style_cli_maps_to_underscores_module(self):
        # the module file name is tools/install-pins.py (dash) while the test
        # file uses install_pins (underscore); the module loads under its own name
        self.assertEqual(ip.__name__, "install_pins")


class VerifyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.payload = b"tiny pinned bytes, not a model"
        self.path = pathlib.Path(self.tmp.name) / "fake.gguf"
        self.path.write_bytes(self.payload)
        self.sha = "sha256:" + hashlib.sha256(self.payload).hexdigest()

    def test_verify_accepts_exact_stamp(self):
        ip.verify(self.path, self.sha, len(self.payload))

    def test_verify_rejects_mismatched_digest_with_both_values_shown(self):
        bad = "sha256:" + hashlib.sha256(b"tampered").hexdigest()
        with self.assertRaisesRegex(RuntimeError, r"sha256 mismatch.*got \b.*stamp wants"):
            ip.verify(self.path, bad, len(self.payload))

    def test_verify_rejects_non_sha256_stamp(self):
        with self.assertRaisesRegex(RuntimeError, "not a sha256: digest"):
            ip.verify(self.path, "md5:abcdef", len(self.payload))

    def test_verify_rejects_size_drift_even_with_matching_digest(self):
        with self.assertRaisesRegex(RuntimeError, "byte size .*stamp .*refusing"):
            ip.verify(self.path, self.sha, len(self.payload) + 7)

    def test_verify_rejects_tampered_file_of_exact_size(self):
        self.path.write_bytes(self.payload[:-1] + b"x")
        with self.assertRaisesRegex(RuntimeError, "sha256 mismatch"):
            ip.verify(self.path, self.sha, len(self.payload))


class NameLawsTest(unittest.TestCase):
    def test_part_staging_name(self):
        p = ip.part_for(pathlib.Path("/x/models/Qwen3-1.7B-Q8_0.gguf"))
        self.assertEqual(p.name, "Qwen3-1.7B-Q8_0.gguf.part")

    def test_sidecar_name_and_sha256sum_format(self):
        self.assertEqual(ip.sidecar_path(pathlib.Path("models/m.gguf")).name, "m.gguf.sha256")
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        final = pathlib.Path(tmp.name) / "m.gguf"
        final.write_bytes(b"irrelevant; the sidecar carries the STAMP digest")
        import io
        contextlib_null = io.StringIO()
        old = sys.stdout
        sys.stdout = contextlib_null
        try:
            ip.write_sidecar(final, "sha256:" + "0" * 64)
        finally:
            sys.stdout = old
        self.assertEqual(ip.sidecar_path(final).read_text(), f"{'0' * 64}  m.gguf\n")

    def test_completed_part_check_requires_both_size_and_digest(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        part = pathlib.Path(tmp.name) / "m.gguf.part"
        small = part  # missing
        self.assertFalse(ip.check_finished_part(small, ("sha256:" + hashlib.sha256(b"x").hexdigest()), 5))
        part.write_bytes(b"01234")
        good = "sha256:" + hashlib.sha256(b"01234").hexdigest()
        self.assertTrue(ip.check_finished_part(part, good, 5))
        self.assertFalse(ip.check_finished_part(part, good, 6))          # right sha, wrong size
        self.assertFalse(ip.check_finished_part(part, "sha256:" + hashlib.sha256(b"z").hexdigest(), 5))


class PinsRegistryContractTest(unittest.TestCase):
    """The committed registry itself: every stamp decide fail-hards on must exist."""

    @classmethod
    def setUpClass(cls):
        cls.pins = json.loads((_ROOT / "harness" / "pins.json").read_text())
        assert len(cls.pins) >= 3, "pins.json must keep at least the three live pins"

    def test_every_pin_carries_model_weights_decoding_template(self):
        for pid, pin in self.pins.items():
            for field in ("model", "weights_hash", "decoding", "prompt_template_sha256"):
                self.assertIn(field, pin, f"{pid}.{field}")
                self.assertTrue(str(pin[field]).strip(), f"{pid}.{field} empty")

    def test_every_pin_carries_complete_gguf_stanza(self):
        for pid, pin in self.pins.items():
            for field in ("repo", "file", "sha256", "bytes"):
                self.assertIn(field, pin["gguf"], f"{pid}.gguf.{field}")
            self.assertTrue(str(pin["gguf"]["sha256"]).startswith("sha256:"))
            self.assertGreater(int(pin["gguf"]["bytes"]), 0)

    def test_every_pin_carries_runner_recipe_and_binary_in_repo_relative_form(self):
        for pid, pin in self.pins.items():
            r = pin["runner"]
            for field in ("source", "tag", "build", "binary", "binary_sha256"):
                self.assertIn(field, r, f"{pid}.runner.{field}")
            self.assertNotIn("..", r["binary"], f"{pid}.runner.binary must stay relative")
            if "server" in pin:
                self.assertIn("--port", pin["server"]["invocation"], f"{pid}.server.invocation")

    def test_weights_hash_and_gguf_sha256_agree_per_pin(self):
        for pid, pin in self.pins.items():
            self.assertEqual(pin["weights_hash"], pin["gguf"]["sha256"],
                             f"{pid}: weights_hash must track gguf.sha256")


if __name__ == "__main__":
    unittest.main()
