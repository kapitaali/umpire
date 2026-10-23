# umpire

**An open, JEV-style decision primitive** — one round trip maps an opaque `state` onto
caller-owned `labels` and returns one `key`, raw label probabilities, a confidence, an
optional corpus-fitted calibration view, and an auditable identity. Consumes the
[ctxvm]/xCABI trace standard; contributes nothing back into it.

**Status:** M0 + M1 + M2 calibration core complete · 2026-10-23 · **License:** Apache-2.0 · **Name:** `umpire` (working name)

**M1 pin structure (three pins, all live-exercised):**
- `qwen3-1.7b` — primary pin (Qwen3-1.7B Q8_0, apache-2.0, official GGUF)
- `qwen3-0.6b` — CI/speed fallback pin (Qwen3-0.6B Q8_0, apache-2.0)
- `qwen2.5-1.5b` — pin-B for A/B pricing demos (Qwen2.5-1.5B-Instruct Q8_0, apache-2.0)

Template routing is per-pin: `qwen3-hybrid` (Qwen3 think-block prefix) vs `chatml` (plain ChatML, Qwen2.5 has no think block). Template bytes are identity-stamped per pin (`prompt_template_sha256` in `harness/pins.json`) and verified fail-hard on every `decide` — a drifted or unstamped template is a hard error, not a silent behavior change (M1c lesson after a corrupted-template incident was caught by cross-fixture provenance probing, RR-M1C).

**M2 calibration core:** a fixed 160-example labeled corpus (`calibration/`, 40 per
label, 80/40/40 train/validation/test) with a caller-owned few-shot routing guide;
temperature-scaling and isotonic candidates fitted on train, selected on validation
ECE, refit on train+validation, and evaluated once on test. The fitted artifact
(`calibration/umpire-cal-v1.json`) is pinned by `data_version`
`cal-v1:caf6a04f9e94b9a2` (`isotonic/1`); calibrated records use the existing optional
`calibrated` block, and the validator recomputes it from the raw distribution. On the
40-example test split, accuracy is unchanged at 0.800, ECE is 0.0457 (raw
entropy-complement 0.1428 / raw top 0.1747), and Brier is 0.1361 (0.1522 / 0.1699);
the paired-bootstrap 95% intervals include zero at this n, so read the report before
quoting the delta. Raw probabilities and decision identity are unchanged. See
[`calibration/README.md`](calibration/README.md) for the corpus/labeling policy,
metrics, reproduction, and limitations.

## What it is / isn't

| it **is** | it **is not** |
|---|---|
| a typed `decide(state, labels) → key + probabilities + confidence` primitive | a chat model, an agent, or a context manager |
| an xCABI-0.1-profile decision ledger with re-decide (`page.fault`) waste semantics | a feature/plugin of ctxvm (§0 of the proposal: RFC-0 stays sealed) |
| deterministic under a pinned model + seed + grammar-constrained decoding (M1 gate) | a hosted decision service (per-decision pricing at ~70 ms is the JEV reference point) |

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
calibration/                    M2 corpus, raw capture, fitter, artifact, tests (see its README)
fixtures/<name>.{ledger.jsonl, records.jsonl, page-table.json}   3 synthetic triplets + 1 live A/B demo triplet
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
# Ran 12 tests … OK
python3 spec/validate_ledger.py \
  calibration/corpus.ledger.jsonl calibration/corpus.records.jsonl
# LEDGER CONFORMANCE: PASS (888 checks; negatives 12/12)
python3 spec/validate_ledger.py \
  calibration/corpus.ledger.jsonl calibration/corpus.calibrated.records.jsonl
# LEDGER CONFORMANCE: PASS (928 checks; negatives 12/12)
sha256sum -c calibration/checksums.sha256
# 9 data artifacts: OK

# M2 live gate (requires the pinned llama-server on 127.0.0.1:8081):
python3 harness/test_m2.py
# L1–L4 ok; M2 LIVE GATES: PASS

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
- **M2 — calibration core: done** — fixed 160-example labeled corpus, live raw
  capture, temperature-scaling/isotonic selection, fitted artifact, validator-enforced
  calibrated view (see `calibration/README.md`, `reports/RR-M2-20261023.md`). The full
  proposal M2 ODPS packaging + hub leaderboard are deferred.
- **M3** — replay-audit mode for pinned corpora; hosted/local-only decision pending
  (T14).

Provenance: parent proposal at `ctxvm/projects/01-open-decision-primitive.md` (M0 GO
2026-10-22, repo split); JEV reference: hosted decision primitive (≤255 labels, one key +
raw probabilities + optional calibrated confidence out, per-decision pricing,
confidence-gated routing).

[ctxvm]: https://github.com/kapitaali/ctxvm-xCABI
