# umpire

**An open, JEV-style decision primitive** — one round trip maps an opaque `state` onto
caller-owned `labels` and returns one `key`, raw label probabilities, a confidence, an
optional corpus-fitted calibration view, and an auditable identity. Consumes the
[ctxvm]/xCABI trace standard; contributes nothing back into it.

**Status:** M0 + M1 + M2 calibration core (v1/v2) complete; M3 replay audit landed (smoke + full 480-row corpus); ODPS/ODCS packaging (Phase A) + hub (Phase B) + OpenCode MCP integration verified · 2026-10-24 · **License:** Apache-2.0 · **Name:** `umpire` (working name)

**M1 pin structure (three pins, all live-exercised):**
- `qwen3-1.7b` — primary pin (Qwen3-1.7B Q8_0, apache-2.0, official GGUF)
- `qwen3-0.6b` — CI/speed fallback pin (Qwen3-0.6B Q8_0, apache-2.0)
- `qwen2.5-1.5b` — pin-B for A/B pricing demos (Qwen2.5-1.5B-Instruct Q8_0, apache-2.0)

Template routing is per-pin: `qwen3-hybrid` (Qwen3 think-block prefix) vs `chatml` (plain ChatML, Qwen2.5 has no think block). Template bytes are identity-stamped per pin (`prompt_template_sha256` in `harness/pins.json`) and verified fail-hard on every `decide` — a drifted or unstamped template is a hard error, not a silent behavior change (M1c lesson after a corrupted-template incident was caught by cross-fixture provenance probing, RR-M1C).

**M2 calibration core (v1/v2):** v1 uses a fixed 160-example labeled corpus
(`calibration/`, 40 per label, 80/40/40 train/validation/test) with a caller-owned
few-shot routing guide; v2 preserves those rows and adds 320 harder examples for a
**480-example, 240/120/120** corpus with a **120-example test set**. Temperature
scaling and isotonic are fitted on train, selected on validation ECE, refit on
train+validation, and evaluated once on test. The current artifact
(`calibration/umpire-cal-v3.json`) is `cal-v1:057a5a35a9d4ec71` (`isotonic/1`) — the
2026-10-24 quiescent recapture of the unchanged v2 corpus (raw-v2 is retained as the
annotated concurrency-affected revision); the v1 artifact remains historical
(`cal-v1:caf6a04f9e94b9a2`) alongside the v2 artifact
(`cal-v1:aceb881587b3b3a6`). Calibrated records use
the existing optional `calibrated` block, and the validator recomputes it from the
raw distribution. On the v3 120-example test split, accuracy is 0.7083, ECE is
**0.0302** (raw entropy-complement 0.2106 / raw top 0.2473), and Brier is **0.1726**
(raw top 0.2388). The paired-bootstrap 95% intervals exclude zero for both deltas
vs raw top, but this is still one project-authored corpus, pin, seed, and machine;
the v1 n=40 result remains directional and is not retroactively upgraded. Raw
probabilities and decision identity are unchanged. See
[`calibration/README.md`](calibration/README.md) for the corpus/labeling policy,
metrics, reproduction, and limitations.

## What it is / isn't

| it **is** | it **is not** |
|---|---|
| a typed `decide(state, labels) → key + probabilities + confidence` primitive | a chat model, an agent, or a context manager |
| an xCABI-0.1-profile decision ledger with re-decide (`page.fault`) waste semantics | a feature/plugin of ctxvm (§0 of the proposal: RFC-0 stays sealed) |
| deterministic under a pinned model + seed + grammar-constrained decoding (M1 gate; quiescent same-build; full-corpus audit 470/480 strict with capture-side attribution, see the M3 caveat) | a hosted decision service (per-decision pricing at ~70 ms is the JEV reference point) |

## The decision ledger (v0)

Every decision is an **xCABI page**: `logicalRef` = `tool:decide(<canonical(state, labels)>`
(T6 minting: sorted keys, `sha256:8hex`, >120-char truncation + `#` tail), `contentHash`
nominal over the response. A **re-classification of an identical `(state, labels)`** is a
priced repeat call — xCABI `page.fault source:"tool_recall"` rows name the resident slot;
A/B pins and re-seeded runs count as exactly the waste being measured. Same hash re-land →
fault row only; divergent hash → fault row + second-slot declare. `model_pin`/`seed` are
deliberately **excluded** from the ref.

Normative: [`00-SPEC.md`](00-SPEC.md) · vendored schema:
[`spec/vendor/x-cabi-0.1.schema.json`](spec/vendor/x-cabi-0.1.schema.json) (ctxvm @ `361043e`).

## Use cases

`umpire` is a single-round-trip typed decision primitive — `decide(state, labels) → key + probabilities + confidence`. It fits anywhere an agent needs to pick one of N caller-owned options with an auditable identity, without inventing classes or trailing off into prose. Concrete uses:

- **Intent routing.** A coding agent maps an opaque user request onto labels like `{billing, technical, sales, other}` and routes to the right specialist sub-agent or tool. The `key` is guaranteed to be one of the requested labels (grammar constraint), so routing never breaks on an invented class.
- **Local agent integration.** OpenCode and Hermes launch the same dependency-free stdio MCP server (`mcp_server.py`) and discover `decide`, `pins`, `health`, `calibration_info`, `validate`, and `replay`. The server writes the same xCABI ledger/records as the CLI; no network listener, OAuth, or provider key is involved. See [`README-MCP.md`](README-MCP.md).
- **A/B prompt evaluation.** Run the same `(state, labels)` under two pins (e.g. `qwen3-1.7b` vs `qwen2.5-1.5b`, seed fixed). Identical response hash → a `page.fault` row naming the resident slot (priced re-decide, no waste beyond the fault); divergent hash → fault + second-slot declare. The ledger prices the waste of re-decisions automatically.
- **Confidence-gated escalation.** The raw `confidence` (entropy-complement, 0–1) and the M2 `calibrated.confidence` (corpus-fitted P(key correct) estimate) feed a router: high confidence → auto-handle; low confidence → escalate to a human or a larger model. Confidence is a measurement, not a correctness guarantee — calibration and its test-split error bars are in `calibration/`.
- **Determinism audit.** Because `model_pin` and `seed` are excluded from the ref, re-deciding an identical `(state, labels)` under a different pin is a priced re-decide, not a new identity. The harness re-verifies itself end-to-end — byte-identical page-table roundtrip, cross-checked by `ctxvm replay` — and every pin's model, runner, and prompt template are hash-stamped with `decide` failing hard on template drift: "verify the pin yourself" means checkable bytes, not trust in a vendor's pin.
- **Duplicate-decision detection.** The ref `tool:decide({labels, state})` makes identical requests share one slot. A re-request that re-lands on the same hash appends only a fault row — redundant re-decisions are measurable, not silently free.

## File map

```
00-SPEC.md                      decision-ledger v0 — normative (profile, minting, joins, replay, calibrated view)
spec/vendor/…                   vendored xCABI-0.1 schema (pinned copy, do-not-edit-semantics)
spec/decision-record-v0.schema.json   payload sidecar schema
spec/calibration_tools.py       shared temperature/isotonic/ECE/Brier math (fit + harness + validator)
spec/ledger_tools.py            minting + derivation — single source of truth
spec/make_fixtures.py           deterministic golden-fixture generator
spec/validate_ledger.py         validator (+ --replay roundtrip gate, + M2 calibrated checks)
calibration/                    M2 corpora (v1 + v2), raw capture, fitter, artifacts, tests (see its README)
harness/replay_audit.py         M3 model-level replay audit for a pinned corpus
harness/test_replay_audit.py    offline comparison-logic tests for the replay audit
odps/umpire-calcorpus-0.1.odps.yaml         ODPS 4.1 manifest for the calibration corpus drop
contracts/umpire-calcorpus-0.1.odcs.yaml    ODCS v3.0.2 data contract for the drop
spec/odps-odcs-plan.md          ODPS/ODCS packaging plan (spec-first, ctxvm-precedent)
spec/build_hub.sh               hub-as-code generator: ODPC catalog + KPI leaderboard (byte-identical)
spec/test_packaging.py          offline gates (packaging + hub: checksums, shapes, redaction, KPI parity)
hub/                            hub-as-code: ODPC catalog, KPI leaderboard HTML, hub.sha256 (Phase B)
mcp_server.py                   dependency-free stdio MCP server (OpenCode + Hermes)
mcp_server_test.py              protocol/fake-runner tests for the MCP server
README-MCP.md                   MCP setup, tool contract, calibration scope, security
fixtures/<name>.{ledger.jsonl, records.jsonl, page-table.json}   3 synthetic triplets + 1 live A/B demo triplet
tests/m3-replay-full/           preserved full-run M3 replay corpus (ledger 481 events + 480 records + page-table; roundtrip sha256:25b073cbc…; see reports/RR-M3FULL-20261024.md)
reports/                        per-milestone reports + LOG
```

## Verification (this commit)

```sh
python3 spec/validate_ledger.py \
  fixtures/intent-router.ledger.jsonl  fixtures/intent-router.records.jsonl \
  fixtures/support-triage.ledger.jsonl fixtures/support-triage.records.jsonl \
  fixtures/ab-demo.ledger.jsonl        fixtures/ab-demo.records.jsonl
# LEDGER CONFORMANCE: PASS (167 checks across all pairs with the M2 artifact;
# 12/12 negative cases, including the two calibrated-record gates)

python3 spec/validate_ledger.py --replay fixtures/ab-demo.ledger.jsonl \
  fixtures/ab-demo.records.jsonl fixtures/ab-demo.page-table.json
# roundtrip: BYTE-IDENTICAL

# M2 calibration offline gates (corpus, raw capture, artifact, calibrated test rows):
python3 -m unittest calibration/test_calibration.py -v
# Ran 16 tests … OK
python3 -m unittest mcp_server_test.py -v
# Ran 7 tests … OK
python3 -m unittest harness/test_replay_audit.py -v
# Ran 2 tests … OK
python3 spec/validate_ledger.py \
  calibration/corpus.ledger.jsonl calibration/corpus.records.jsonl
# LEDGER CONFORMANCE: PASS (888 checks; negatives 12/12)
python3 spec/validate_ledger.py \
  calibration/corpus.ledger.jsonl calibration/corpus.calibrated.records.jsonl
# LEDGER CONFORMANCE: PASS (928 checks; negatives 12/12)
sha256sum -c calibration/checksums.sha256
# 9 data artifacts: OK

# v3 calibration gates (current drop; quiescent recapture — corpus file unchanged since v2):
python3 spec/validate_ledger.py \
  calibration/corpus-v3.ledger.jsonl calibration/corpus-v3.records.jsonl
# PASS (2488 checks; negatives 12/12)
python3 spec/validate_ledger.py \
  calibration/corpus-v3.ledger.jsonl calibration/corpus-v3.calibrated.records.jsonl
# PASS (2608 checks; negatives 12/12)
python3 spec/validate_ledger.py --replay \
  calibration/corpus-v3.ledger.jsonl calibration/corpus-v3.records.jsonl \
  calibration/corpus-v3.page-table.json
# roundtrip: BYTE-IDENTICAL (same `25b073cbc…` as the committed full-run replay baseline)
sha256sum -c calibration/checksums-v3.sha256
# 9 data artifacts: OK (v2 family also verifies: sha256sum -c calibration/checksums-v2.sha256)

# ODPS/ODCS packaging gates (Phase A; see reports/RR-ODPS-20261023.md):
python3 spec/test_packaging.py
# Ran 21 tests … OK (skipped=3 opt-in)

UMPIRE_RUN_DQ_GATES=1 \
UMPIRE_ODPS_VENV=$HOME/.local/state/umpire/venvs/umpire-odps-venv/bin/open-data-products \
UMPIRE_ODCS_VENV=$HOME/.local/state/umpire/venvs/odcs-venv/bin/open-data-products \
python3 spec/test_packaging.py
# Ran 25 tests — OK (DQ gates + packaging + hub; venvs rebuilt under ~/.local/state after reboots)

~/.local/state/umpire/venvs/odcs-venv/bin/open-data-products product audit odps/umpire-calcorpus-0.1.odps.yaml
# Product valid; 1 Data Contract reference(s); contracts valid

# Hub-as-code gates (Phase B; see reports/RR-HUB-20261023.md):
python3 spec/test_packaging.py
# Ran 25 tests … OK (skipped=4 opt-in; hub sha/catalog/leaderboard + regen check)
spec/build_hub.sh check
# check OK: regenerated hub/ is byte-identical to the committed files

# M2 live gate (requires the pinned llama-server on 127.0.0.1:8081):
UMPIRE_CALIBRATION=calibration/umpire-cal-v3.json python3 harness/test_m2.py
# L1–L4 ok; M2 LIVE GATES: PASS

# M3 quiescent replay-audit smoke:
python3 harness/replay_audit.py \
  --corpus calibration/corpus-v2.jsonl --baseline-raw calibration/raw-v3.jsonl \
  --ids ex-012,ex-046,ex-008,ex-013,ex-016,ex-033,ex-001,ex-057
# 8/8 strict match

# M3 full 480-row audit (~104 min, pinned server required; durable work dir + output):
python3 harness/replay_audit.py --work-dir ~/.local/state/umpire/m3/<dir> --json \
  > ~/.local/state/umpire/m3/<dir>.json
# 470/480 strict; 10/480 distribution-only (capture-side attribution); identity 480/480
# — see reports/RR-M3FULL-20261024.md

# structural gates on the preserved full-run corpus:
python3 spec/validate_ledger.py tests/m3-replay-full/replay.ledger.jsonl \
  tests/m3-replay-full/replay.records.jsonl
# PASS (2488 checks); negatives 12/12
python3 spec/validate_ledger.py --replay tests/m3-replay-full/replay.ledger.jsonl \
  tests/m3-replay-full/replay.records.jsonl tests/m3-replay-full/replay.page-table.json
# roundtrip: BYTE-IDENTICAL (sha256:25b073cbc59a5e2c)

# consumption via ctxvm's own published tools (verbatim ledgers):
#   ctxvm validate_schema.py  → SCHEMA CONFORMANCE: PASS (61 checks)
#   ctxvm replay (rust)       → exit 0, divergences [],
#                               violations: 3× faultOfResidentPage (2 identical re-decides + 1 divergent pin-B)
#                               second-slot declare present (divergent-hash A/B path exercised live)
#   ctxvm validate_schema.py ../umpire/calibration/corpus.ledger.jsonl → PASS (216 checks)
#   ctxvm replay ../umpire/calibration/corpus.ledger.jsonl → events 161, divergences [], violations []
```

Deps: `pip install jsonschema>=4` (Python ≥3.10); else stdlib-only.

## Roadmap

- **M0 — decision-ledger spec + validator + fixtures: done** (see `reports/RR-M0-20261022.md`).
- **M1 — reference harness: done** — pinned Qwen3-1.7B, grammar-constrained
  decoding, seeded determinism, CLI parity, live ledger (see `reports/RR-M1-20261022.md`).
- **M2 — calibration core: done (v1/v2)** — 160-example v1 corpus plus the
  480-example v2 expansion, live raw capture, temperature-scaling/isotonic selection,
  fitted v2 artifact, validator-enforced calibrated view (see `calibration/README.md`,
  `reports/RR-M2V2-20261023.md`; v1 report `reports/RR-M2-20261023.md`). The full
  proposal M2 ODPS packaging + hub leaderboard are deferred.
- **ODPS/ODCS packaging — Phase A: done** — one `dataset` product
  `umpire-calcorpus-0.1` (drop `calibration/v2`) validated `compliance_level: full`
  with `product audit` fully passed (ODCS v3.0.2 lint green); offline packaging gates
  21/21 (`spec/test_packaging.py`); see `spec/odps-odcs-plan.md`,
  `reports/RR-ODPS-20261023.md`. Hub trio (Phase B) and agent port (Phase C) still
  pending; hosted runtime deferred per T14.
- **Hub-as-code — Phase B: done** — `hub/` = ODPC v1.0 catalog (`umpire.hub`) → the
  manifest via a hub-relative `$ref`; HTML release-gate KPI leaderboard injected
  from `productStrategy`; `hub.sha256` anchor; regenerated by `spec/build_hub.sh`
  (their SDK end to end) with a byte-identical `check` mode and 25/25 packaging
  gates (see `reports/RR-HUB-20261023.md`). Static + local per T14; llms.txt,
  agent port, and publication remain Phase C (hybrid switch).
- **M3 — local replay-audit: smoke + full 480-row corpus done** —
  `harness/replay_audit.py` re-runs selected pinned-corpus rows and compares `ref`,
  `contentHash`, probabilities, and `model_pin`; the page-table `--replay` gate
  remains the fast deterministic check. Full audit (2026-10-23): 470/480 strict,
  10/480 distribution-only with identity 480/480; the 10 rows' replay
  distributions reproduce bit-for-bit across three audit samples on two server
  instances, and the divergence is attributed to capture-time concurrency
  (capture ended before the shared-decide ledger went quiet; suspect rows are
  latency-elevated in the capture; same signature as RR-M3's v1 ex-001).
  Baseline raw-v2 stays frozen with the 10 rows annotated. The queued quiescent
  v3 recapture **landed 2026-10-24** (`reports/RR-M3V3-20261024.md`): 470/480
  content-identical to v2, 10/10 exactly the replay-path values, capture-time
  page-table byte-identical to the committed roundtrip; v3
  (`cal-v1:057a5a35a9d4ec71`) is now the calibration of record.
  T14 decided **local-only now; hybrid later** (see `reports/RR-T14-20261023.md`).
- **MCP integration (local): verified in OpenCode** — shared dependency-free stdio
  server (`mcp_server.py`); Hermes-side lifecycle/config is owned by the separate
  Hermes integration workstream (see `README-MCP.md`,
  `reports/RR-MCP-20261023.md`).

Provenance: parent proposal at `ctxvm/projects/01-open-decision-primitive.md` (M0 GO
2026-10-22, repo split); JEV reference: hosted decision primitive (≤255 labels, one key +
raw probabilities + optional calibrated confidence out, per-decision pricing,
confidence-gated routing).

[ctxvm]: https://github.com/kapitaali/ctxvm-xCABI
