# `umpire` — decision-ledger v0 spec (M0 normative)

**Status:** M0 normative · 2026-10-22 · **License:** Apache-2.0 · **Name:** `umpire`
(working name, pending like `ctxvm`/`xCABI`).

Parent proposal: `ctxvm/projects/01-open-decision-primitive.md` (repo moved here at M0 GO).
Consumes ctxvm artifacts (xCABI trace standard, replay semantics, validator-first
discipline) through their **published interfaces only**. Contributes nothing back into
the ctxvm framework's code, spec, or normative files (RFC-0 stays sealed).

## 1. The primitive (from the proposal, restated normatively)

One round trip: `POST /v1/decide` — or CLI/lib parity — maps an **opaque `state`** onto
**2..255 caller-owned `labels`** and returns **one `key` ∈ labels** (never free text,
never truncated mid-answer), **calibrated probabilities** summing to 1 across exactly the
requested labels, a **confidence** (a dispersion *measurement* calibrated on the labeled
corpus — not a correctness promise), a **`model_pin` echo**, and an **`ledger_ref`**
pointing at the decision's xCABI-page row. Stateless between requests (sessions live in
callers). No bespoke model: grammar/JSON-schema constrained decoding over a pinned OSS
small model (Qwen/Phi-class), post-hoc calibration (temperature scaling / isotonic),
pinned per version. Latency honesty: ~100–600 ms locally vs. hosted ~70 ms specialists —
conceded openly; confidence-gated routing composes rather than competes.

## 2. Decision-ledger v0 — an xCABI-0.1 profile

The ledger records every decision as an xCABI page (§5.1 of the proposal: identity
minting per the T6 shim contract). It is **byte-valid under the vendored xCABI-0.1
schema** (`spec/vendor/x-cabi-0.1.schema.json`) — ctxvm's own tools are consumers:

| xCABI event | Decision semantics (locked) |
|---|---|
| `session.open` | `{agentId:"umpire", model:<pin.model>, provider:"local", budgetTokens:0, tokenizerId:"apx4-char"}` — first line, required |
| `page.declare` | one decision recorded. `logicalRef` = `decideRef(request)`; `contentHash` = `contentHash(response)`; `kind:"tool_result"`; `tokens = approx4(canonicalJson(response))` |
| `page.fault` | a **re-decide** of an already-resident request: `source:"tool_recall"` (same key `(state, labels)` re-requested), `turn` = the new decision's index (0-based), and `page.contentHash` = the **resident** hash at request time (T8/T9-conformant: the fault names what was already resident). Schema asymmetry to respect: `page.declare` nests the identity under `page.id`, `page.fault` carries it **flat** (`page.logicalRef` / `page.contentHash`) |

Profile constraints (all enforced by the validator, each with an engineered negative):
**L1** — one decision = one turn; **L2** — allowed events are only the three above
(any other xCABI event = out-of-profile); **L3** — first line must be `session.open`;
**L4** — `faults` may only carry `source:"tool_recall"` (the auto same-key channel;
`semantic` is banned in v0, `explicit_api`/`identifier_scan` have no decision meaning);
**L5** — ledger lines carry no floats (xCABI rule; `tokens` integers only).

### Identity minting (mirrors `ctxvm/packages/node/src/ident.ts` @ 361043e, byte-compatible)

- `canonicalJson(v)` — recursively key-sorted compact JSON.
- `contentHash(s)` = `"sha256:" + first 8 hex of sha256(s)` over UTF-8.
- `decideRef(request)` = `decide(<canonicalArgs>)` where canonicalArgs =
  `{"labels":[sorted-unique uids],"state":<state>}` — and when the canonical string
  exceeds **120 chars**, the ref carries its first 120 chars + `#<hex8(canonical)>` tail
  (T6 truncation rule, identity stays stable and short).
- response contentHash is computed over `canonicalJson({"confidence":c,"key":k,"probabilities":{...}})`.

**Locked decision (waste-pricing rationale):** `model_pin` and `seed` are **excluded**
from the ref. A re-classification of an identical `(state, labels)` is a *repeat call*
per §5.2 of the proposal — including A/B comparisons across pins and reproducibility
re-runs under a new seed. They are measured re-decides (`page.fault` rows), never
normalized away. Same-ref re-lands resolve by response hash: identical hash → fault row
only, no new declare (re-landing on the resident page); divergent hash → fault row +
declare of a second slot (never scored as anything but the priced repeat).

**ASCII-only payload rule (v0):** the Python mirror of `canonicalJson`
(`json.dumps(sort_keys=True, separators=(",",":"), ensure_ascii=False)`) is byte-identical
to the TS reference for ASCII payloads; control characters / `\u2028\u2029` differ. v0
harness states are ASCII-only; non-ASCII canonicalization parity is M1 audit material.

## 3. Decision records (payload sidecar)

The ledger carries identity only; full payloads live in a per-ledger **records** sidecar
(`spec/decision-record-v0.schema.json`), strict at record level:

```yaml
schema: decide-record/0.1
state: <opaque, never mutated by umpire>
labels: [caller order preserved]          # 2..255 unique
ref, contentHash: <identity echo — validator recomputes and cross-checks>
model_pin: {model, weights_hash, decoding}
seed: <int>                               # REQUIRED (audit-mode determinism, §3.5 of the contract)
key: <uid>                                # ∈ labels
probabilities: {uid: p, ...}              # keys == set(labels), each p > 0, Σp = 1 (±1e-9)
confidence: <0..1>                        # raw dispersion; calibrated view ships in M2 (schema-ready)
latency_ms: <optional int>
```

Join rules (validator-enforced): every record's recomputed `(ref, contentHash)` must
equal its stored echo and be declared in its ledger; every declared slot must be backed
by ≥ 1 record; fault rows must reference a ref with an earlier declare.

## 4. Replay (roundtrip byte-identity) — the M0 audit primitive

`spec/validate_ledger.py --replay L R BASELINE` re-derives the ledger's **page table**
(pages → hash slots, fault rows, stats) deterministically and compares it byte-for-byte
with the committed `BASELINE`. Same derivation code as the generator
(`spec/ledger_tools.py`) imported by both sides — single source of truth, no duplication.
Divergence ⇒ tampering or a minting bug ⇒ audit fails. (M1 binds the same invariant
around the real harness: same request + pin + seed ⇒ byte-identical response, asserted
by re-run; M3 turns it into the replay-audit mode for pinned corpora.)

## 5. Validation gates (validator-first, à la RFC-0)

```sh
python3 spec/validate_ledger.py \
  fixtures/intent-router.ledger.jsonl fixtures/intent-router.records.jsonl \
  fixtures/support-triage.ledger.jsonl fixtures/support-triage.records.jsonl
```

Positive side: schema-validates every ledger line (against the vendored xCABI-0.1 schema)
and every record line, then runs the profile + join + minting cross-checks. Negative
side: ten engineered bad ledgers/records (invented key, probabilities ≠ label set,
minting drift, Σp ≠ 1, out-of-profile event, banned fault source, floats in the ledger,
confidence > 1, orphan declare, duplicate labels) — each must be caught for the run to
PASS. Its own PASS + exit code is the contract.

## 6. Fixture provenance

Both fixture triplets are **synthetic** (reasoned examples, not model outputs — there is
no harness yet; that is M1). `weights_hash` values are fixed placeholder strings shaped
`sha256:<8hex>`, not real model weights. Regenerate with `python3 spec/make_fixtures.py`
(deterministic: same bytes every run).

## 7. Vendoring note

`spec/vendor/x-cabi-0.1.schema.json` is a pinned copy of ctxvm's published schema
(ctxvm @ `361043e`). Do not edit its semantics; if ctxvm bumps the schema, re-vendor and
re-run the gates (and note it in this file's status header). M1+ adds the ODPS/ODCS+hub
pipeline consumption (proposal §5.4) with the same edges-never-in-core rule.

## 8. Milestone state

- **M0 (this repo, first commit):** spec + schemas + validator + 2 fixture ledgers +
  roundtrip baselines. DoD: validator green incl. negative gates; roundtrip
  byte-identical; plus the consumption demo — ctxvm's own validator and replay CLI accept
  the ledgers verbatim (recorded in `reports/RR-M0-20261022.md`).
- **M1 next:** reference harness (pinned OSS model, constrained decoding, seeded),
  CLI + library, determinism property tests.
- Decision points still open (proposal §9): M1 model shortlist (license-clean only),
  M3 hosted vs local-only (recommend local-only first).
