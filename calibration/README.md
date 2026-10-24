# umpire M2 calibration (v1 + v2 + v3)

This directory holds the **calibration layer**: fixed labeled corpora, raw decisions
captured from the pinned Qwen3-1.7B, fitted calibration artifacts, and the calibrated
records for the untouched test splits.

The narrow claim is:

> For this pinned model, four-label input distribution, and fixed seed, the raw
> constrained distribution can be mapped to a better-calibrated estimate of
> P(key is correct) than the raw confidence statistics.

The current artifact is **v3** (the 2026-10-24 quiescent recapture of the unchanged
v2 corpus — no concurrent decide traffic in the capture window).  On the v3
**120-example test split** the point estimates support the claim: accuracy is
unchanged at **0.7083**, while ECE falls from **0.2106** (raw entropy-complement) /
**0.2473** (raw top probability) to **0.0302**, and Brier falls from **0.2388**
(raw top) to **0.1726**.  The 95% paired-bootstrap intervals for the deltas vs raw
top exclude zero at this n (ΔECE `[-0.261838, -0.067204]`; ΔBrier
`[-0.115138, -0.017335]`).  This is a stronger local result than v1, but it is
still **one project-authored corpus, one pin, one seed, and one machine/build**;
it is not a production benchmark or a correctness guarantee.

v2 remains retained as the annotated concurrency-affected revision: its capture
overlapped a second decide client on the pinned server, which shifted the raw
distributions of exactly 10 rows (contentHash only; decision key/argmax unchanged),
and its artifact (`cal-v1:aceb881587b3b3a6`) was fit from those capture-time
distributions (test ECE 0.0267, Brier 0.1723 — optimistic-attribution honesty
noted in RR-M3FULL/RR-M3V3).  v1 remains historical and directional: its 40-example
test split had point-estimate improvements but 95% intervals that included zero.
Neither v1 nor v2 retroactively upgrades the n=40 result.

## Corpus

### v2/v3 (`corpus-v2.jsonl`, 480 examples, shared corpus file — v3 is the current drop)

- 120 examples per label (`billing`, `other`, `sales`, `technical`)
- deterministic stratified split: **240 train / 120 validation / 120 test**
  (60/30/30 per label), fixed seed `20261023`
- the v1 160 rows are preserved byte-for-byte and keep their original
  train/validation/test assignments; 320 new rows add 80 per label
  (40/20/20 per label)
- every state is a caller-owned few-shot routing guide plus the customer message
  (`state.text = ROUTING_GUIDE + message`); the original `message` is kept as a
  separate field for audit
- labeled by the policy below, independent of what the model predicted

The new rows are deliberately harder than v1.  Accuracy is therefore **not**
comparable across corpus revisions: v1 rows are 124/160 = 0.775 overall and 32/40 =
0.800 on test; the new v2 rows are 218/320 = 0.6813 overall and 53/80 = 0.6625 on
test.  The v2 corpus overall is 342/480 = 0.7125.

### v1 (`corpus-v1.jsonl`, 160 examples, historical)

40 examples per label, deterministic 80/40/40 split (20/10/10 per label), seed
`20261023`.  v1 artifact `umpire-cal-v1.json`, `data_version`
`cal-v1:caf6a04f9e94b9a2`, selected isotonic/1 on validation ECE (0.1094 vs 0.1547
for temperature scaling).  Its untouched 40-example test result was accuracy 0.800,
ECE 0.0457 (raw entropy 0.1428 / raw top 0.1747), Brier 0.1361 (0.1522 / 0.1699),
with intervals including zero.  See `reports/RR-M2-20261023.md`.

### Labeling policy

Gold is the **primary action the sender needs**; an explicit ask beats mentioned
context.  Examples deliberately contain cross-domain distractors.

| label | meaning |
|---|---|
| `billing` | money/account administration on an existing subscription: charges, invoices, refunds, payment methods, tax, cancellation/renewal, seat/plan changes on an existing paid account |
| `sales` | pre-purchase/commercial evaluation or new revenue: pricing, demos, quotes, plans, trials, contracts, partnerships/resellers/OEM, procurement for a new purchase |
| `technical` | product malfunction/configuration/usage: errors, outages, integrations, SDK/API, deployment, login, performance, data sync |
| `other` | non-product inbound: press, careers, legal/privacy/security disclosure, research, community, brand, sponsorship, vendor proposals |

A refund request caused by a bug is `billing`; a request to fix the bug is
`technical`.  An existing customer changing a plan is `billing`; a new purchase is
`sales`.

### Why the routing guide is part of the state

A 56-example bare-text pilot reached 46.4%; the 1.7B collapsed `sales` into
`billing`/`other`.  A fixed 12-example guide (three per label) inside the
caller-owned state raised a 10-case probe to 9/10.  The guide is therefore part of
the calibrated input distribution, not hidden harness behavior.  It is not drawn
verbatim from the corpus, but semantic overlap with few-shot examples is expected
and documented.

## Raw capture

`capture.py` sends each corpus example through the M1 harness once, with
`temperature=0`, seed `7`, and the pinned prompt template.  For v3 (current) it writes:

- a real xCABI-0.1 profile ledger (`corpus-v3.ledger.jsonl`) and record sidecar
  (`corpus-v3.records.jsonl`) — 480 fresh declares, no re-decides
- the fit input (`raw-v3.jsonl`): `id`, `split`, `gold`, `correct`, full raw record
- a deterministic page-table baseline (`corpus-v3.page-table.json`) — byte-identical
  to the committed full-run replay roundtrip (`sha256:25b073cbc…`)
- `corpus-v3.capture.meta.json`: pin/binary/GGUF hashes, latency stats, accuracy,
  hashes

For v2 the analogous v2-suffixed files exist unchanged (raw-v2, corpus-v2.ledger/
records/page-table/capture.meta).  The v2 capture overlapped a second decide client
on the pinned server; 10 of its rows carry capture-time distribution offsets (see
`reports/RR-M3FULL-20261024.md` for the audit, and RR-M3V3 for the confirmation via
this quiescent recapture).  The v1 capture used the analogous v1 files (`raw-v1.jsonl`,
`corpus.ledger.jsonl`, `corpus.records.jsonl`, `corpus.page-table.json`,
`corpus.meta.json`).

The capture verifies the runner binary and GGUF against `harness/pins.json` before
any call.  Raw probabilities are the model's; gold is used only to mark correctness
and to fit/evaluate.  Records carry **no** calibrated block.  The v1 capture used
the analogous v1 files (`raw-v1.jsonl`, `corpus.ledger.jsonl`,
`corpus.records.jsonl`, `corpus.page-table.json`, `corpus.meta.json`).

## Calibration artifacts

Two candidate methods are fit on **train only** and compared on **validation** by
ECE (Brier tie-break); the winner is refit on train+validation, then evaluated once
on **test**:

| method id | fit | stored calibrated confidence |
|---|---|---|
| `temperature-scaling/1` | T minimizes gold-label NLL: `q_i ∝ p_i ** (1/T)` | `q[key]` |
| `isotonic/1` | PAV from raw `p[key]` to correctness | `PAV(p[key])` |

Both target `P(key is correct)`; the raw entropy-complement confidence remains
untouched.  The artifact records the selected method, fitted T or PAV points, both
candidates' validation scores, test metrics (ECE/Brier/NLL where defined,
reliability bins, paired-bootstrap intervals), pin/corpus/raw hashes, and the
selection rationale.

The current v3 artifact is **`isotonic/1`**, `data_version`
**`cal-v1:057a5a35a9d4ec71`**.  Validation ECE selected isotonic (0.046561) over
temperature scaling (0.068825).  Test: n=120, accuracy 0.7083, ECE 0.0302, Brier
0.1726; raw top ECE 0.2473, Brier 0.2388.  The artifact's in-sample calibrated ECE
is 0 by construction (PAV fits the fit split); it is not evidence and is not used
for the claims.

`data_version` is `cal-v1:<16 hex>` over the stable artifact core (corpus/raw
hashes, labels, seed, pin, method, statistic, fitted parameters).  The `cal-v1:`
prefix is the artifact **format**; v2 is a new file with a unique version.  It is
referenced by every calibrated record.  Provenance fields (timestamps, metrics,
host) are outside the core; changing them does not change the version.

### Calibrated record view

The existing `decide-record/0.1` optional block is used with the same field set:

```json
"calibrated": {
  "method": "isotonic/1",
  "data_version": "cal-v1:057a5a35a9d4ec71",
  "confidence": 0.73
}
```

Raw `probabilities`, `confidence`, `ref`, and `contentHash` are unchanged; the
calibrated view is a sidecar.  `spec/validate_ledger.py` recomputes the calibrated
confidence from the raw probabilities and the artifact, and checks method,
`data_version`, labels, pin, and seed.  The built-in negatives include confidence
mismatch and unknown version; the v1, v2, and v3 artifacts are loaded when present

## Reproduce (v3, current)

```sh
# 1. offline corpus (unchanged since v2)
python3 calibration/make_corpus_v2.py

# 2. live capture (pinned 1.7B llama-server on :8081; keep the box + decide
#    traffic quiescent — capture-time concurrency perturbs distributions)
python3 calibration/capture.py \
  --server http://127.0.0.1:8081 \
  --corpus calibration/corpus-v2.jsonl \
  --raw calibration/raw-v3.jsonl \
  --ledger calibration/corpus-v3.ledger.jsonl \
  --records calibration/corpus-v3.records.jsonl \
  --page-table calibration/corpus-v3.page-table.json \
  --meta calibration/corpus-v3.capture.meta.json \
  --resume

# 3. fit + calibrated test records + checksums
python3 calibration/fit.py \
  --corpus calibration/corpus-v2.jsonl \
  --raw calibration/raw-v3.jsonl \
  --artifact calibration/umpire-cal-v3.json \
  --calibrated-records calibration/corpus-v3.calibrated.records.jsonl \
  --checksums calibration/checksums-v3.sha256 \
  --ledger calibration/corpus-v3.ledger.jsonl \
  --records calibration/corpus-v3.records.jsonl \
  --page-table calibration/corpus-v3.page-table.json \
  --capture-meta calibration/corpus-v3.capture.meta.json

# 4. offline gates
python3 -m unittest calibration/test_calibration.py -v
python3 spec/validate_ledger.py \
  calibration/corpus-v3.ledger.jsonl calibration/corpus-v3.records.jsonl
python3 spec/validate_ledger.py \
  calibration/corpus-v3.ledger.jsonl calibration/corpus-v3.calibrated.records.jsonl
sha256sum -c calibration/checksums-v3.sha256

# 5. live gate (server + artifact)
UMPIRE_CALIBRATION=calibration/umpire-cal-v3.json python3 harness/test_m2.py
```

For v1, use `make_corpus.py` / the v1 defaults and `checksums.sha256`; the v1
files remain unchanged.

## Limitations

- The corpora are project-authored (160 v1 synthetic support intents; 480 v2), not
  production samples.  Accuracy is a property of these corpora and the few-shot
  guide, not a general intent-router benchmark.  The v2 new rows are harder.
- Calibration is per pin, label set, seed, and machine/build.  Do not apply it to
  other pins or labels; the harness enforces pin/seed/label identity.
- The artifact is fitted to the corpus input distribution (`ROUTING_GUIDE +
  message`).  Applying it to a bare-text state without the guide is out of
  distribution and not covered by these metrics.
- v1's 40-example intervals include zero; the v1 caveat remains.  v2's 120-example
  intervals exclude zero for the ECE/Brier deltas vs raw top on this corpus, but
  that is still a single-corpus, single-machine measurement.
- Calibration cannot fix semantic label errors.  High-confidence errors remain.
- The calibrated view estimates P(key correct) on this distribution; it is not a
  correctness guarantee.
- Concurrent capture/replay can perturb raw probabilities and `contentHash` even
  when the decision key is stable; quiescent replay and quiescent capture are the
  supported strict checks (RR-M3, confirmed by the v3 recapture — its capture-time
  page-table equals the committed replay baseline byte-for-byte).
- ODPS packaging and the hub leaderboard from the proposal's full M2 definition are
  packaging land (v3 drop); this directory is the local calibration core.
