# umpire

**An open, JEV-style decision primitive** — one round trip maps an opaque `state` onto
caller-owned `labels` and returns one `key`, calibrated probabilities, a confidence, and
an auditable identity. Consumes the [ctxvm]/xCABI trace standard; contributes nothing
back into it.

**Status:** M0 (spec + schemas + validator + golden fixtures + roundtrip baselines) ·
2026-10-22 · **License:** Apache-2.0 · **Name:** `umpire` (working name)

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

## File map

```
00-SPEC.md                      decision-ledger v0 — normative (profile, minting, joins, replay)
spec/vendor/…                   vendored xCABI-0.1 schema (pinned copy, do-not-edit-semantics)
spec/decision-record-v0.schema.json   payload sidecar schema
spec/ledger_tools.py            minting + derivation — single source of truth
spec/make_fixtures.py           deterministic golden-fixture generator
spec/validate_ledger.py         validator (+ --replay roundtrip gate)
fixtures/<name>.{ledger.jsonl, records.jsonl, page-table.json}   2 synthetic triplets
reports/                        per-milestone reports + LOG
```

## Verification (this commit)

```sh
python3 spec/validate_ledger.py \
  fixtures/intent-router.ledger.jsonl  fixtures/intent-router.records.jsonl \
  fixtures/support-triage.ledger.jsonl fixtures/support-triage.records.jsonl
# LEDGER CONFORMANCE: PASS (125 checks)   ← incl. 10/10 engineered negative cases rejected

python3 spec/validate_ledger.py --replay fixtures/intent-router.ledger.jsonl \
  fixtures/intent-router.records.jsonl fixtures/intent-router.page-table.json
# roundtrip: BYTE-IDENTICAL (page-table sha256:78d916291349d169)

# consumption via ctxvm's own published tools (verbatim ledgers):
#   ctxvm validate_schema.py  → SCHEMA CONFORMANCE: PASS (67 checks)
#   ctxvm replay (rust)       → exit 0, divergences [],
#                               violations: 2×/1× faultOfResidentPage (the priced re-decides)
```

Deps: `pip install jsonschema>=4` (Python ≥3.10); else stdlib-only.

## Roadmap

- **M1** — reference harness: pinned OSS model (license-clean shortlist pending),
  grammar-constrained decoding, seeded determinism property tests, CLI parity.
- **M2** — calibration layer (temperature scaling / isotonic, schema-ready in v0).
- **M3** — replay-audit mode for pinned corpora; hosted/local-only decision pending.

Provenance: parent proposal at `ctxvm/projects/01-open-decision-primitive.md` (M0 GO
2026-10-22, repo split); JEV reference: hosted decision primitive (≤255 labels, one key +
calibrated probabilities + confidence out, per-decision pricing, confidence-gated
routing).
