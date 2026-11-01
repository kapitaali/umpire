# W9 — calibration transfer across a label-set change (preregistration)

**Status:** frozen design, **no run yet**. Master-prompt W9: "Test calibration transfer: fit on one
label set, evaluate on a held-out label set and a held-out domain. The calibrated confidence is only
useful if it transfers; quantify how much it does not."

**Date frozen:** 2026-11-01. Nothing below may change after the first paid or unpinned decision; a
change is recorded as a deviation with its reason.

## Question

The artifact of record (`cal-v1:057a5a35a9d4ec71`, isotonic/1) maps a top probability to a
calibrated confidence. It was fit on a four-label routing corpus. **If the label set shrinks, does
that map still produce usable confidences, and by how much does it degrade?**

This is the part of calibration that matters for reuse: a demo that only works on the label set it
was fit on is a demo about one vocabulary, not about a decision primitive.

## Why the obvious cheap answer is not the answer

The cheap approximation — drop a label, renormalise the remaining probabilities, reuse the map —
was run on 2026-11-01 as an **exploratory probe** (`reports/RR-W9DESIGN-20261101.md`). It found no
detectable transfer penalty: transfer ECE landed within ±0.074 of an in-set refit, with the sign
flipping across arms, on 90-row test subsets. That question is *not* settled by it, because
renormalisation rescales the model's four-label distribution; it does not re-run the model under a
three-label grammar. The constrained decode is a different distribution, so the confirmatory
experiment must re-decode.

## Arms

All arms use the pinned runner and seed: `qwen3-1.7b`, `json-label-enum-v0@llama.cpp-v0.7.0`,
seed 7, temperature as pinned, one machine, one build.

| Arm | Label set | Rows decoded | Purpose |
|---|---|---|---|
| **A0** (baseline, no run) | `billing, other, sales, technical` | existing v3 capture | the 4-label reference already in `calibration/corpus-v3.*` |
| **A1** | `billing, sales, technical` (drop `other`) | the v3 **test** rows whose gold ≠ `other` | the hardest boundary — `other` overlaps every class |
| **A2** | `other, sales, technical` (drop `billing`) | the v3 **test** rows whose gold ≠ `billing` | the largest class — the map saw most of its mass here |

Re-decoding only the test split is deliberate: the question is whether the *existing* map transfers,
so no refit is required, and a refit would double the run for a different question ("is a refit
cheap?"). If the answer matters later, it is a follow-up card on the same captures.

Estimated cost: ~90 decisions per arm at the observed ~12 s/decision ≈ 18 min per arm, ~36 min for
both, CPU-only, **no monetary spend**.

## Metrics (fixed before the run)

For each arm, on the rows the arm actually decoded, compared against A0's rows **restricted to the
same texts**:

- ECE (10 equal-width bins, the artifact's definition), Brier, accuracy.
- Calibrated confidence = the v3 isotonic map applied to the arm's top probability, unchanged.
- **ΔECE = ECE(arm) − ECE(A0 subset)**, with a paired bootstrap 95% interval, n=2000, seed 42.
- Secondary: ΔBrier, and the accuracy change (a label-set change should not move accuracy much; if
  it does, the comparison is confounded and must be reported as such).

## Decision rule (frozen)

- **Material degradation:** ΔECE > +0.02 **and** the paired 95% interval excludes 0.
- **No detectable degradation:** the paired 95% interval includes 0.
- Anything else (interval excludes 0 but |ΔECE| ≤ 0.02) is reported as "small, possibly real".
- **A negative result is a result.** If the map transfers cleanly, that is the finding, and it
  strengthens the reuse argument; if it degrades, the finding is the size of the degradation, and
  the recommendation is to refit per label set.

## Guardrails

- Pin/seed/model identical across arms; a different build or machine voids the comparison (record,
  do not silently compare).
- No mid-run label-set edits, no post-hoc subsetting beyond the declared restriction.
- The corpus drop is checksummed like every other drop, with a provenance file naming this design
  document and the exploratory probe.
- Decoded rows keep their own ledgers/records; they do not overwrite the v3 capture.

## What this cannot show

- **Domain transfer.** No second-domain corpus exists; authoring one ourselves would make the
  corpus even more project-authored than the existing one. Domain shift stays open and is flagged as
  such in the report.
- **Above the sampling limit of the corpus.** Per-arm n is ~90 test rows; the absolute ECE interval
  on 120 rows was already ±0.04 (`RR-CALCI-20261101`), so only a large degradation is detectable.
  This is stated in the arm table in the report, not fixed after the fact.
- Anything about a stronger model with structured output and logprobs — a different arm family,
  still owner-gated on spend (D2/T14).

## Deliverables

1. `calibration/transfer/` — ledger, records, meta and checksums per arm, provenance naming this file.
2. `reports/RR-W9TRANSFER-<date>.md` — the table, the deltas with intervals, the verdict against the
   frozen rule, and the limits above.
3. A one-line status in `README.md` and `00-SPEC.md` §8 whichever way it comes out.

---

## Amendment 1 (2026-11-01, after the first run, before any new decision is decoded)

**What the first run showed.** Both arms returned "no detectable degradation" because their paired
95% intervals ([-0.019, +0.123] and [-0.054, +0.122]) include zero — while the point estimates
(+0.068 and +0.058) exceed the +0.02 materiality threshold by roughly three times. The rule could not
fire at n=90. That is a power failure in this design, not a property of the data.

**Amendment.** Each arm is extended with the **validation** split: A1v and A2v decode the v3
validation rows whose gold is not the dropped label (90 rows per arm, on top of the 90 test rows
already decoded). The evaluation set becomes test ∪ validation, n=180 per arm, and the paired
interval should tighten to roughly ±0.05 — enough for the frozen +0.02 rule to resolve a +0.06 point
estimate.

**Why not the train split.** The isotonic map of record was fit on the train split, so A0's train
confidences are in-sample: including them would make A0 look better than it is and inflate the
degradation delta. Validation rows were used only to choose between two candidate methods
(isotonic vs temperature), not to fit the map, so their bias is second-order and is stated as a
limit rather than corrected for.

**What does not change.** The decision rule, the metric definitions, the seed, the pin, the map
(everything is scored with the map unchanged — no refit), the artifacts of the first run, and the
A0 restriction to matched row ids. The first run's numbers stay in the record as the n=90 result.

**Cost.** 90 additional decisions per arm at the observed ~10.6 s/decision ≈ 16 min per arm,
~32 min total, CPU only, no monetary spend.
