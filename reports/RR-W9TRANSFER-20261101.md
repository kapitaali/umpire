# RR — W9TRANSFER-20261101 — calibration transfer across a label-set change

**Experiment:** `design/W9-TRANSFER.md`, frozen before the run. **Run:** 2026-11-01,
10:17:44Z → 10:50:04Z (32 min 20 s), pinned `qwen3-1.7b`, `json-label-enum-v0@llama.cpp-v0.7.0`,
seed 7, one machine, CPU only, **zero monetary spend**.

## What ran

| Arm | Label set | Rows | Accuracy | Median latency | Artifacts |
|---|---|---|---|---|---|
| A0 (no run) | `billing, other, sales, technical` | 120 test | 0.7083 | — | `calibration/corpus-v3.*` |
| A1 | `billing, sales, technical` (no `other`) | 90 | 0.8222 | 10,750 ms | `calibration/transfer/a1.*` |
| A2 | `other, sales, technical` (no `billing`) | 90 | 0.6778 | 10,595 ms | `calibration/transfer/a2.*` |

Both arms validate and replay clean: `LEDGER CONFORMANCE: PASS (555 checks)` each,
`roundtrip: BYTE-IDENTICAL` (`a1 sha256:b1bd67fa…`, `a2 sha256:8239a6c6…`, 452 checks each).
A0 was restricted to each arm's 90 row ids, so the comparison is paired row for row.

## Result

```
A1 — drop 'other', n=90
  ECE     A0 0.0558   arm 0.1239   Δ +0.0682 [-0.0190, +0.1230]
  Brier   A0 0.1517   arm 0.1454   Δ -0.0063 [-0.0362, +0.0224]
  acc     A0 0.7333   arm 0.8222   Δ +0.0889 [+0.0222, +0.1556]
  verdict: no detectable degradation

A2 — drop 'billing', n=90
  ECE     A0 0.0546   arm 0.1125   Δ +0.0579 [-0.0542, +0.1224]
  Brier   A0 0.1994   arm 0.1898   Δ -0.0096 [-0.0505, +0.0298]
  acc     A0 0.6333   arm 0.6778   Δ +0.0444 [-0.0556, +0.1444]
  verdict: no detectable degradation
```

Against the rule fixed in the design, both arms return **"no detectable degradation"**, because
both paired intervals include zero.

## The problem with that verdict, stated first

**The decision rule was underpowered for the n it was frozen against, and that is my error in the
design, not a property of the data.** The pre-run probe already measured the noise floor (±0.04 on
120 rows); at n=90 the paired ECE interval comes out roughly ±0.07, so a "+0.02 and CI excludes 0"
rule could only fire on an enormous effect. The point estimates are three times that threshold
(+0.068, +0.058) and the rule still cannot call them material.

What the data actually says, without the rule doing the talking:

- **ECE roughly doubles in both arms** — 5.6% → 12.4% (A1), 5.5% → 11.3% (A2). The direction is
  consistent across arms, and it is the direction that matters for reuse.
- **Brier slightly improves** (−0.006, −0.010) and its intervals comfortably include zero: the
  squared-error view does not see the degradation the binned view does, which is a reminder that
  ECE and Brier are not the same question.
- **Accuracy rises** (+0.089 with a CI excluding zero, +0.044 without) — but the arm's task is
  *easier*: one fewer class, and the class removed is the catch-all that absorbed mistakes. Rising
  accuracy is a confound of the label-set change, not evidence of a better system.
- **The restriction itself moves the baseline.** A0's full-test ECE is 0.0302; restricted to the 90
  rows of each arm it is 0.0558/0.0546. The excluded `other` rows were among the well-calibrated
  ones. Any future reading of "A0 ECE" must say which row set it means.

The honest headline: **the map's confidences degrade by roughly doubling ECE when the label set
shrinks, and this experiment could not prove that at its own threshold.** Both statements are true
and neither cancels the other.

## What to do about the power problem (owner call, not mine)

Three routes, in increasing cost:

1. **Raise the materiality threshold** to something the n can resolve (e.g. +0.08) and re-state the
   rule — cheap, but it is fitting the rule to the data after the fact, which is what a
   preregistration exists to prevent.
2. **Extend the arms to the train split** (~180 more rows per arm, ~36 min per arm) so the paired
   interval tightens to roughly ±0.04 and a +0.02 rule can fire. Costs an hour of CPU, no money.
   This is the route the design's own logic points to.
3. **Accept "cannot resolve at n=90" as the finding** and record the point-estimate direction as the
   working belief, with the interval as its caveat.

I would take (2) if the machine has an hour, and (3) otherwise. (1) is the one I would not do.

## Secondary observations worth keeping

- Calibration transfer is **not** the failure mode the absolute-interval card warned about: the
  uncertainty here is ordinary paired noise, not a badly scaled map. A refit arm (not run, per the
  design) would tell us whether refitting recovers the 6 points, which is the natural follow-up.
- The 3-label enum still decodes at the same speed (median 10.7 s vs 10.6 s on four labels): the
  grammar size is not the cost driver.
- `meta.json` records `git.dirty: true` on both arms because the capture computes it at the *end* of
  the run, when its own untracked outputs exist. `git.head` (`cb9ee8e`) is the meaningful pin — the
  arm corpora were committed before the run precisely so the inputs are identified.

## Verified

`spec/transfer_eval.py` recomputes the verdict deterministically (paired bootstrap, seed 42) and
`check` mode runs in `tools/run-ci.sh` and in the CI gates job: `transfer-eval: OK — 2 arms
reproduce`. `ALL GATES GREEN`. Both arms' ledger conformance and replay re-verified above.

## Files

`calibration/transfer/{a1,a2}.{corpus,ledger,records,raw,page-table,meta}` ·
`calibration/transfer/verdict.json` · `calibration/transfer/provenance.json` ·
`spec/transfer_eval.py` · this report.
