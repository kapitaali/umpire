# RR-W9INSTALL-20261102 — install realism (the in-repo half): a fresh clone can reproduce the pinned decision primitive with two tools

**Card:** the W9 day index (reports/RR-DAY-20261101.md §5/§7) queued "installation
realism: pinned-model download script + quickstart are in-repo work; the release
half is gated by D2/T14". This card does the in-repo half. The release half
(published artifact, versioning, npm/PyPI identity) stays owner-gated — untouched.

## 1. The gap, stated locally

A fresh `git clone` has neither weights nor a runner: `.gitignore` rules are
explicit — "local inference artifacts are hashes-pinned in harness/pins.json,
not committed" (`models/`, `runners/`). The pins registry carries the full
identity (weights sha256 + byte size; runner source, tag, cmake recipe, binary
stamp; prompt-template stamp), and nothing before this card turned that registry
into an installer: a new machine had to reconstruct 4.5 GB of pinned bytes and a
pinned llama.cpp build from prose in the books.

## 2. What landed

**`tools/install-pins.py`** (stdlib-only: urllib, hashlib, argparse):
- resolves `gguf.repo`/`gguf.file` stanzas → `https://huggingface.co/<repo>/resolve/main/<file>` (all three resolve URLs live-verified, HTTP 200, 2026-11-02);
- streams to `<file>.part`, **resumes via HTTP Range** on the next run (2 GB artifacts must not restart from zero on a flaky link), final rename only after **sha256 + byte size verify against the pin stamp** — fail hard with got/want digests shown, never a partial install (same law the harness applies to templates at decide time);
- idempotent: an existing file that verifies is never re-fetched; `--force` re-downloads only files that fail; `--check` verifies existing weights offline with zero network (took this box's three real models, ≈4.5 GB, all PASS);
- writes a `<file>.sha256` sidecar (sha256sum -c format) as a local convenience artifact; **pins.json stays the committed hash carrier** (the .gitignore law is respected — no sidecar commits).

**`tools/build-runner.sh`** (the recipe half): clones llama.cpp at the pinned
tag (`runner.source`/`runner.tag`, shallow) or reconciles an existing tree
(fetch + checkout on deviate), applies the cmake flags **parsed from the pinned
`runner.build` string** (single source of truth — no recipe fork in a second
place), builds, then reports the binary sha256 against `runner.binary_sha256`
fail-truthfully: MATCH or DEVIATION-named — a foreign gcc/cmake legitimately
re-hashes, and the decide path stamps weights/template/decoding identity, never
the compiler output. Prints the pin's `server.invocation` on success.

**`tools/install_pins_test.py`** (18 offline tests; run-ci gate 2/3 line added):
URL construction + filename quoting, selection order + unknown-pin errors,
verify happy/mismatch/size-drift/stamp-format paths on tiny temp files,
`.part`/sidecar name laws, completed-part check, and the **registry contract
gate** — every pin in `harness/pins.json` must carry model/weights_hash/
decoding/prompt_template_sha256, a complete gguf stanza, a complete runner
recipe, and `weights_hash == gguf.sha256` (the decide-path stamping law,
enforced offline before any server exists).

**README Quickstart** (new §; file-map lines) — clone → offline gates →
weights → runner → health → first decide → validate, all copy-pasteable;
points to README-MCP.md and calibration/README.md for the integration and
calibration-scope surfaces. `.gitignore` gains a commented `ledgers/` rule
(runtime demo output, not books).

## 3. Verification (all real, on this box)

- `python3 tools/install-pins.py --check` — all three pinned models verify against the stamps offline (1,894,532,128 + 639,446,688 + 1,953,857,184 bytes); exit 0.
- Full download path exercised offline via a `file://` transport stub: install → verified bytes + sidecar; re-run → `already` (no refetch); tampered file → sha mismatch hard error with both digests, `--check` exit 1; unknown pin → exit 2 with the configured list.
- `bash tools/build-runner.sh` — real cmake reconfigure + targeted rebuild; resulting `runners/src/build/bin/llama-server` re-hashed **exactly to the pin stamp** `sha256:0f60e0483ee8aa394f2fadf487dcdcc7799abdd5941127952f17277ebe4ec61c` → stamp MATCH (this box is the stamp's build environment: gcc 14.2.0 / cmake 4.4.3).
- End-to-end quickstart run as written: server up with the pinned invocation → `/health {"status":"ok"}` → `harness/umpire.py --pin qwen3-1.7b --seed 7 --text "I was charged twice on my last invoice" --labels billing,technical,sales` → key `billing`, raw probabilities {billing: 0.9980753896…}, confidence 0.9864, ledger `ref tool:decide({"labels":["billing","sales","technical"],"state":{"text":"…"}})` → `spec/validate_ledger.py` PASS (110 checks; 14/14 negatives). Server killed after; demo ledger files (gitignored) remain in `ledgers/`.
- `bash tools/run-ci.sh` → ALL GATES GREEN (with the new 18-test suite at gate 2/3).

## 4. Limits, stated

- `binary_sha256` reproduce is only *expected* on the stamp's own build environment (gcc 14.2.0, cmake 4.4.3 — pinned in `runner.build`); elsewhere it legitimately deviates and the script says so. Nothing in the decide path depends on the binary hash; the enforced identity is weights + template + decoding stamps.
- The HF `resolve/main` endpoint is a vendor endpoint; a future HF URL mechanic change would break the fetcher loudly (fail with the URL shown), not silently.
- The quickstart first-decide numbers are this-box/this-pin/this-seed evidence, not a benchmark; determinism and calibration caveats live in their own books (RR-DET, calibration/README.md).
- The release half (D2/T14-gated), the four baselines, and the domain-transfer corpus remain the owner waits recorded in RR-DAY-20261101.

## 5. Deviations

None. One card, one change-family (fresh-install self-sufficiency). No schema,
no fixture, no calibration corpus, no harness bytes touched; the validator's
negative set and every committed baseline unchanged.
