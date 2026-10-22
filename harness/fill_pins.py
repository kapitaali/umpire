#!/usr/bin/env python3
"""Fill pins.json's post-download/build identity hashes (run once after fetching).

Idempotent: re-running only updates entries whose on-disk file differs from the
recorded hash. Verified against the double-hash corruption lesson (2026-10-22):
never fill hashes from a partial file — each GGUF is hashed fully before any pin
field is written.
"""

import hashlib
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).parent
pins_path = HERE / "pins.json"
pins = json.loads(pins_path.read_text())

bin_path = HERE.parent / "runners" / "src" / "build" / "bin" / "llama-server"


def sha256_of(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


filled = []

# Runner hash (shared by all pins)
if bin_path.exists():
    digest = "sha256:" + sha256_of(bin_path)
    for pin_id, pin in pins.items():
        if pin["runner"].get("binary_sha256") != digest:
            pin["runner"]["binary_sha256"] = digest
            pin["decoding"] = f"json-label-enum-v0@llama.cpp-v0.7.0/{digest[:16]}"
            filled.append(f"{pin_id}.runner.binary_sha256")
            filled.append(f"{pin_id}.decoding")

# Per-pin GGUF hashes
for pin_id, pin in pins.items():
    gguf = HERE.parent / "models" / pin["gguf"]["file"]
    if gguf.exists():
        digest = "sha256:" + sha256_of(gguf)
        if pin["gguf"].get("sha256") != digest:
            pin["gguf"]["sha256"] = digest
            pin["gguf"]["bytes"] = gguf.stat().st_size
            pin["weights_hash"] = digest
            filled.append(f"{pin_id}.gguf.sha256")
            filled.append(f"{pin_id}.weights_hash")

if filled:
    pins_path.write_text(json.dumps(pins, indent=2) + "\n")
    print("filled:", ", ".join(filled))
else:
    print("pins already filled / nothing present to fill")
    sys.exit(1)
