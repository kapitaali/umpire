#!/usr/bin/env python3
"""Fill pins.json's post-download/build identity hashes (run once after fetching)."""

import hashlib
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).parent
pins_path = HERE / "pins.json"
pins = json.loads(pins_path.read_text())

gguf = HERE.parent / "models" / "Qwen3-1.7B-Q8_0.gguf"
bin_path = HERE.parent / "runners" / "src" / "build" / "bin" / "llama-server"


def sha256_of(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


filled = []

if gguf.exists():
    digest = "sha256:" + sha256_of(gguf)
    if pins["qwen3-1.7b"]["gguf"].get("sha256") != digest:
        pins["qwen3-1.7b"]["gguf"]["sha256"] = digest
        pins["qwen3-1.7b"]["gguf"]["bytes"] = gguf.stat().st_size
        # weights_hash = FULL gguf content digest — the pin's reproducibility
        # anchor (record schema allows sha256:<8..64> hex)
        pins["qwen3-1.7b"]["weights_hash"] = digest
        filled += ["gguf.sha256", "weights_hash"]
if bin_path.exists():
    digest = "sha256:" + sha256_of(bin_path)
    if pins["qwen3-1.7b"]["runner"].get("binary_sha256") != digest:
        pins["qwen3-1.7b"]["runner"]["binary_sha256"] = digest
        pins["qwen3-1.7b"]["decoding"] = f"json-label-enum-v0@llama.cpp-v0.7.0/{digest[:16]}"
        filled += ["runner.binary_sha256", "decoding"]
if filled:
    pins_path.write_text(json.dumps(pins, indent=2) + "\n")
    print("filled:", ", ".join(filled))
else:
    print("pins already filled / nothing present to fill")
    sys.exit(1)
