# `umpire` — decision-ledger v0 spec (M0 normative)

**Status:** M0 normative · M2 calibration layer landed 2026-10-23 · **License:**
Apache-2.0 · **Name:** `umpire` (working name, pending like `ctxvm`/`xCABI`).

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

### Calibrated view (M2, optional)

`calibrated` is an optional sidecar on a record; it never changes `ref`,
`contentHash`, raw `probabilities`, or raw `confidence`:

```yaml
calibrated:
  method: temperature-scaling/1 | isotonic/1
  data_version: cal-v1:<16 hex>   # stable artifact core hash
  confidence: <0..1>              # fitted P(key is correct) estimate
```

A fitted artifact (`calibration/umpire-cal-*.json`) pins the corpus hash, labels,
seed, model/decoding/template identity, the fitted temperature or PAV points, and
the selection/test metrics. `spec/validate_ledger.py` recomputes the calibrated
confidence from the raw probabilities and rejects a record whose method,
`data_version`, label set, `model_pin`, or seed does not match the artifact, or
whose calibrated confidence does not recompute. Raw fields remain authoritative:
calibration is a measurable layer with its own version, not a mutation of the
decision or its identity. M2 details and limitations: `calibration/README.md`.

## 4. Replay (roundtrip byte-identity) — the M0 audit primitive

`spec/validate_ledger.py --replay L R BASELINE` re-derives the ledger's **page table**
(pages → hash slots, fault rows, stats) deterministically and compares it byte-for-byte
with the committed `BASELINE`. Same derivation code as the generator
(`spec/ledger_tools.py`) imported by both sides — single source of truth, no duplication.
Divergence ⇒ tampering or a minting bug ⇒ audit fails. (M1 binds the same invariant
around the real harness: same request + pin + seed ⇒ byte-identical response, asserted
by re-run; M3 turns it into the model-level replay-audit mode for pinned corpora via
`harness/replay_audit.py`. T14 fixes the current path as **local-only**; a hybrid
deployment is a later phase and must preserve this replay contract.)

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
confidence > 1, orphan declare, duplicate labels) — plus two M2 calibrated-record cases
(confidence mismatch, unknown `data_version`) when a fitted artifact is present — each
must be caught for the run to PASS. Its own PASS + exit code is the contract.

## 6. Fixture provenance

The M0 fixture triplets are **synthetic** (reasoned examples, not model outputs; the
harness landed in M1). `weights_hash` values there are fixed placeholder strings shaped
`sha256:<8hex>`, not real model weights. Regenerate with `python3 spec/make_fixtures.py`
(deterministic: same bytes every run). The live M1/M2 ledgers are separate artifacts
with their own pin hashes and capture metadata.

## 7. Vendoring note

`spec/vendor/x-cabi-0.1.schema.json` is a pinned copy of ctxvm's published schema
(ctxvm @ `361043e`). Do not edit its semantics; if ctxvm bumps the schema, re-vendor and
re-run the gates (and note it in this file's status header). ODPS/ODCS packaging Phase A
landed 2026-10-23 (`odps/…odps.yaml` + `contracts/…odcs.yaml`, validated on the ctxvm
stack — see `reports/RR-ODPS-20261023.md`); the hub pipeline landed static-local
in Phase B (`hub/` ODPC catalog + KPI leaderboard + integrity anchor,
byte-identical regen — see `reports/RR-HUB-20261023.md`); publication/domain and
the agent port remain deferred to the T14 hybrid switch. The
edges-never-in-core rule still applies: packaging consumes the published
interfaces and adds no semantics to the xCABI core.

## 8. Milestone state

- **M0 (this repo, first commit):** spec + schemas + validator + 2 fixture ledgers +
  roundtrip baselines. DoD: validator green incl. negative gates; roundtrip
  byte-identical; plus the consumption demo — ctxvm's own validator and replay CLI accept
  the ledgers verbatim (recorded in `reports/RR-M0-20261022.md`).
- **M1 (done 2026-10-22, see `reports/RR-M1-20261022.md`):** reference harness over the
  pinned **Qwen3-1.7B** (apache-2.0, `Qwen/Qwen3-1.7B-GGUF` Q8_0,
  llama.cpp `v0.7.0` @ `620d2225` built from source) — GBNF `json-label-enum-v0`
  grammar-constrained greedy decode with fixed seed; harness CLI+lib
  (`harness/umpire.py`), property gates P1–P5 green (`harness/test_m1.py`), live
  ledger `fixtures/live-demo.*` (4 decisions, 1 priced re-decide) validates through
  the M0 gate (PASS 94 checks + byte-identical roundtrip) and through ctxvm's own
  validator + replay (exit 0, 1× `faultOfResidentPage`). Empirical honesty: ~10–13 s
  per decision on this 4-core CPU box (outside the proposal's 100–600 ms band —
  see RR for the latency plan); M1 model shortlist = `spec/m1-model-shortlist.md`.
- **M2 (calibration core, done 2026-10-23, see `reports/RR-M2-20261023.md`):**
  fixed 160-example labeled corpus over the four harness labels (80/40/40
  train/validation/test; caller-owned few-shot routing guide in every state),
  live raw capture from the pinned 1.7B as a real xCABI ledger/records pair,
  temperature-scaling and isotonic candidates selected on validation ECE, the
  winner refit on train+validation and evaluated once on test, and the fitted
  artifact (`calibration/umpire-cal-v1.json`) cross-checked by the validator.
  Calibrated records use the existing optional `calibrated` block; raw
  probabilities/identity are unchanged. A v2 expansion (480 examples, 120/label,
  240/120/120 split) preserves the v1 rows and has its own artifact
  (`cal-v1:aceb881587b3b3a6`, isotonic/1), `checksums-v2.sha256`, and report.  On
  its 120-example test split, ECE falls from 0.2483 raw-top / 0.2110 raw-entropy to
  0.0267 and Brier from 0.2380 to 0.1723 at unchanged 0.7083 accuracy; the v1 n=40
  result remains historical/directional. Full proposal M2's ODPS packaging and hub
  leaderboard are deferred.
- **T14 (decided 2026-10-23, see `reports/RR-T14-20261023.md`):** local-only now;
  hybrid later. The current M3 path is local replay-audit for pinned corpora, and the
  OpenCode/Hermes integration is a local stdio MCP server. No hosted endpoint,
  provider spend, ODPS/ODCS packaging, or hub leaderboard is claimed in this phase.
- **M3 (local replay-audit; smoke + full corpus done 2026-10-23/24, see
  `reports/RR-M3-20261023.md`, `reports/RR-M3FULL-20261024.md`):** `harness/replay_audit.py`
  re-runs selected pinned corpus rows and separately checks decision identity
  (`ref`/`key`/`model_pin`) and the raw distribution (`contentHash`/probabilities)
  against a committed baseline. Full 480-row audit: 470/480 strict, 10/480
  distribution-only (capture-side concurrency attribution; max Δp 0.1206 with
  unchanged argmax), identity 480/480; the 10 rows' replay distributions are
  bit-identical across three audit samples on two server instances. The
  deterministic page-table replay remains the fast gate; the full-run corpus is
  committed at `tests/m3-replay-full` (validator 2488 checks + negatives 12/12;
  roundtrip byte-identical `25b073cbc…`). Raw-probability bit-identity is claimed
  for quiescent same-build replay; rows captured under concurrent decide traffic
  may carry small offsets. A full quiescent v3 recapture is queued before any
  refit; cross-machine/cross-build determinism is not claimed.
- **ODPS/ODCS packaging — Phase A (done 2026-10-23, see `reports/RR-ODPS-20261023.md`):**
  the v2 calibration drop packaged as one `dataset` product `umpire-calcorpus-0.1`
  (0.1.0, drop `calibration/v2`): ODPS 4.1 manifest validated `compliance_level: full`
  (open-data-products SDK 0.4.0), `product audit` fully passed
  (`product_valid: true · contract_valid: true`, datacontract-cli 1.2.7, ODCS v3.0.2
  lint green), `x-umpire` sidecar echoing the pin identity verbatim and scoping
  replay-determinism claims to the quiescent page-table roundtrip, five KPI release
  gates derived from current v2 values (declared floors/ceilings, not guarantees), and
  stdlib-only packaging gates 21/21 (`spec/test_packaging.py`; DQ executables runnable
  via `UMPIRE_RUN_DQ_GATES=1`). Hub trio landed in Phase B (`reports/RR-HUB-20261023.md`):
  ODPC v1.0 catalog + KPI leaderboard HTML + `hub.sha256`, byte-identical regen via
  `spec/build_hub.sh check`; agent port/publication remain Phase C (T14 hybrid switch).
  Target KPIs are release gates — not model-performance promises.
