# umpire M2 calibration

This directory holds the **T13 / M2 calibration layer**: a fixed labeled corpus, the
raw decisions captured from the pinned Qwen3-1.7B, the fitted calibration artifact,
and the calibrated records for the untouched test split.

The narrow claim is:

> For this pinned model, four-label input distribution, and fixed seed, the raw
> constrained distribution can be mapped to a better-calibrated estimate of
> P(key is correct) than the raw entropy-complement confidence.

On the fixed 40-example test split the **point estimates** support this: accuracy is
unchanged at 0.800, while ECE falls from 0.1428 (raw entropy-complement) / 0.1747
(raw top probability) to 0.0457, and Brier falls from 0.1522 / 0.1699 to 0.1361.
The paired-bootstrap 95% intervals at n=40 still include zero (e.g. ΔECE vs raw top
`[-0.198, +0.065]`), so this is a directional result, not a decisive population
claim. It is **not** a claim that the model is accurate, that confidence is a
correctness guarantee, or that the calibration transfers to other labels, pins,
seeds, or machines.

## Corpus (`corpus-v1.jsonl`, 160 examples)

- 40 examples per label (`billing`, `other`, `sales`, `technical`)
- deterministic stratified split: 80 train / 40 validation / 40 test
  (20/10/10 per label), fixed seed `20261023`
- every state is a caller-owned few-shot routing guide plus the customer message
  (`state.text = ROUTING_GUIDE + message`); the original `message` is kept as a
  separate field for audit
- labeled by the policy below, independent of what the model predicted

### Labeling policy

Gold is the **primary action the sender needs**; an explicit ask beats mentioned
context. Examples deliberately contain cross-domain distractors.

| label | meaning |
|---|---|
| `billing` | money/account administration on an existing subscription: charges, invoices, refunds, payment methods, tax, cancellation/renewal, seat/plan changes on an existing paid account |
| `sales` | pre-purchase/commercial evaluation or new revenue: pricing, demos, quotes, plans, trials, contracts, partnerships/resellers/OEM, procurement for a new purchase |
| `technical` | product malfunction/configuration/usage: errors, outages, integrations, SDK/API, deployment, login, performance, data sync |
| `other` | non-product inbound: press, careers, legal/privacy/security disclosure, research, community, brand, sponsorship, vendor proposals |

A refund request caused by a bug is `billing`; a request to fix the bug is
`technical`. An existing customer changing a plan is `billing`; a new purchase is
`sales`.

### Why the routing guide is part of the state

A 56-example bare-text pilot (`/tmp` only, not committed) reached **46.4%**
accuracy; the 1.7B collapsed `sales` into `billing`/`other` (14 of 15 sales
examples wrong). Label uids alone were insufficient. A fixed 12-example guide
(three per label) inside the caller-owned state raised a 10-case probe to 9/10.
The guide is therefore part of the calibrated input distribution, not hidden
harness behavior. It is not drawn verbatim from the corpus, but semantic overlap
with few-shot examples is expected and documented.

## Raw capture (`raw-v1.jsonl`, `corpus.ledger.jsonl`, `corpus.records.jsonl`)

`capture.py` sends each corpus example through the M1 harness once, with
`temperature=0`, seed `7`, and the pinned prompt template. It writes:

- a real xCABI-0.1 profile ledger (`corpus.ledger.jsonl`) and record sidecar
  (`corpus.records.jsonl`) — one fresh declare per example, no re-decides
- the fit input (`raw-v1.jsonl`): `id`, `split`, `gold`, `correct`, full raw record
- a deterministic page-table baseline (`corpus.page-table.json`)
- `corpus.meta.json`: pin/binary/GGUF hashes, latency stats, accuracy, hashes

The capture verifies the runner binary and GGUF against `harness/pins.json` before
any call. Raw probabilities are the model's; gold is used only to mark correctness
and to fit/evaluate. Records carry **no** calibrated block.

## Calibration (`umpire-cal-v1.json`)

Two candidate methods are fit on **train only** and compared on **validation** by
ECE (Brier tie-break); the winner is refit on train+validation, then evaluated once
on **test**:

| method id | fit | stored calibrated confidence |
|---|---|---|
| `temperature-scaling/1` | T minimizes gold-label NLL: `q_i ∝ p_i ** (1/T)` | `q[key]` |
| `isotonic/1` | PAV from raw `p[key]` to correctness | `PAV(p[key])` |

Both target `P(key is correct)`; the raw entropy-complement confidence remains
untouched. The artifact records the selected method, fitted T or PAV points, both
candidates' validation scores, test metrics (ECE/Brier/NLL where defined,
reliability bins, paired-bootstrap intervals), pin/corpus/raw hashes, and the
selection rationale.

The committed artifact is **`isotonic/1`**, `data_version`
`cal-v1:caf6a04f9e94b9a2`. Validation ECE selected isotonic (0.1094) over
temperature scaling (0.1547); the refit on train+validation (120 examples) gives
test ECE 0.0457 and Brier 0.1361 at unchanged 0.800 accuracy. The artifact's
in-sample calibrated ECE is 0 by construction (PAV fits the fit split); it is not
evidence and is not used for the claims.

`data_version` is `cal-v1:<16 hex>` over the stable artifact core (corpus/raw
hashes, labels, seed, pin, method, statistic, fitted parameters). It is referenced
by every calibrated record. Provenance fields (timestamps, metrics, host) are
outside the core; changing them does not change the version.

### Calibrated record view

The existing `decide-record/0.1` optional block is used with the same field set:

```json
"calibrated": {
  "method": "isotonic/1",
  "data_version": "cal-v1:…",
  "confidence": 0.73
}
```

Raw `probabilities`, `confidence`, `ref`, and `contentHash` are unchanged; the
calibrated view is a sidecar. `spec/validate_ledger.py` recomputes the calibrated
confidence from the raw probabilities and the artifact, and checks method,
`data_version`, labels, pin, and seed. Two negative gates (confidence mismatch,
unknown version) are added to the built-in corpus.

## Reproduce

```sh
# 1. offline corpus (no server)
python3 calibration/make_corpus.py

# 2. live capture (pinned 1.7B llama-server on :8081)
python3 calibration/capture.py --server http://127.0.0.1:8081
#    -> raw-v1.jsonl, corpus.ledger.jsonl, corpus.records.jsonl,
#       corpus.page-table.json, corpus.meta.json

# 3. fit + calibrated test records + checksums
python3 calibration/fit.py

# 4. offline gates
python3 -m unittest calibration/test_calibration.py -v
python3 spec/validate_ledger.py \
  calibration/corpus.ledger.jsonl calibration/corpus.records.jsonl
python3 spec/validate_ledger.py \
  calibration/corpus.ledger.jsonl calibration/corpus.calibrated.records.jsonl
sha256sum -c calibration/checksums.sha256

# 5. live gates (server + artifact)
python3 harness/test_m2.py
```

The committed `corpus-v1.jsonl`, raw capture, artifact, calibrated records, and
`checksums.sha256` are the evidence. `sha256sum -c calibration/checksums.sha256`
from the repo root checks the data artifacts.

## Limitations

- The corpus is project-authored (160 synthetic support intents), not a production
  sample. Accuracy is a property of this corpus and the few-shot guide, not a
  general intent-router benchmark.
- Calibration is per pin, per label set, per seed, and per machine/build. Do not
  apply it to other pins or labels; the harness enforces pin/seed/label identity.
- The artifact is fitted to the corpus input distribution (`ROUTING_GUIDE + message`).
  The harness cannot verify the state text; applying it to a bare-text state (without
  the guide) is out of distribution and not covered by these metrics.
- The test split has 40 examples. Read the bootstrap intervals, not just point
  estimates: the observed ECE/Brier improvements have 95% paired-bootstrap intervals
  that include zero, so the delta is directional at this n, not decisive.
- Calibration cannot fix semantic label errors. One high-confidence test error
  (`other` routed to `billing` at raw top 1.0) remains at calibrated 1.0; the
  calibrated [0.9,1.0] bin is 15/16 correct at mean confidence 0.973.
- The calibrated view estimates P(key correct) on this distribution; it is not a
  correctness guarantee.
- ODPS packaging and the hub leaderboard from the proposal's full M2 definition are
  deferred; this card is the calibration core.
