# umpire

**An open, JEV-style decision primitive** — one round trip maps an opaque `state` onto
caller-owned `labels` and returns one `key`, calibrated probabilities, a confidence, and
an auditable identity. Consumes the [ctxvm]/xCABI trace standard; contributes nothing
back into it.

**Status:** M0 + M1 complete · 2026-10-22 · **License:** Apache-2.0 · **Name:** `umpire` (working name)

**M1 pin structure (three pins, all live-exercised):**
- `qwen3-1.7b` — primary pin (Qwen3-1.7B Q8_0, apache-2.0, official GGUF)
- `qwen3-0.6b` — CI/speed fallback pin (Qwen3-0.6B Q8_0, apache-2.0)
- `qwen2.5-1.5b` — pin-B for A/B pricing demos (Qwen2.5-1.5B-Instruct Q8_0, apache-2.0)

Template routing is per-pin: `qwen3-hybrid` (Qwen3 think-block prefix) vs `chatml` (plain ChatML, Qwen2.5 has no think block). Template bytes are identity-stamped per pin (`prompt_template_sha256` in `harness/pins.json`) and verified fail-hard on every `decide` — a drifted or unstamped template is a hard error, not a silent behavior change (M1c lesson after a corrupted-template incident was caught by cross-fixture provenance probing, RR-M1C).

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
- **Confidence-gated escalation.** The pinned `confidence` (entropy-complement, 0–1) feeds a router: high confidence → auto-handle; low confidence → escalate to a human or a larger model. Confidence is a dispersion measurement, not a correctness promise — calibration arrives at M2.
- **Determinism audit.** Because `model_pin` and `seed` are excluded from the ref, re-deciding an identical `(state, labels)` under a different pin is a priced re-decide, not a new identity. The harness re-verifies itself end-to-end — byte-identical page-table roundtrip, cross-checked by `ctxvm replay` — and every pin's model, runner, and prompt template are hash-stamped with `decide` failing hard on template drift: "verify the pin yourself" means checkable bytes, not trust in a vendor's pin.
- **Duplicate-decision detection.** The ref `tool:decide({labels, state})` makes identical requests share one slot. A re-request that re-lands on the same hash appends only a fault row — redundant re-decisions are measurable, not silently free.

## File map

```
00-SPEC.md                      decision-ledger v0 — normative (profile, minting, joins, replay)
spec/vendor/…                   vendored xCABI-0.1 schema (pinned copy, do-not-edit-semantics)
spec/decision-record-v0.schema.json   payload sidecar schema
spec/ledger_tools.py            minting + derivation — single source of truth
spec/make_fixtures.py           deterministic golden-fixture generator
spec/validate_ledger.py         validator (+ --replay roundtrip gate)
fixtures/<name>.{ledger.jsonl, records.jsonl, page-table.json}   3 synthetic triplets + 1 live A/B demo triplet
reports/                        per-milestone reports + LOG
```

## Verification (this commit)

```sh
python3 spec/validate_ledger.py \
  fixtures/intent-router.ledger.jsonl  fixtures/intent-router.records.jsonl \
  fixtures/support-triage.ledger.jsonl fixtures/support-triage.records.jsonl \
  fixtures/ab-demo.ledger.jsonl        fixtures/ab-demo.records.jsonl
# LEDGER CONFORMANCE: PASS (98+ checks across all pairs)

python3 spec/validate_ledger.py --replay fixtures/ab-demo.ledger.jsonl \
  fixtures/ab-demo.records.jsonl fixtures/ab-demo.page-table.json
# roundtrip: BYTE-IDENTICAL

# consumption via ctxvm's own published tools (verbatim ledgers):
#   ctxvm validate_schema.py  → SCHEMA CONFORMANCE: PASS (61 checks)
#   ctxvm replay (rust)       → exit 0, divergences [],
#                               violations: 3× faultOfResidentPage (2 identical re-decides + 1 divergent pin-B)
#                               second-slot declare present (divergent-hash A/B path exercised live)
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

[ctxvm]: https://github.com/kapitaali/ctxvm-xCABI
