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

---

## Amendment 1 result — arms extended to the validation split (n=180)

`design/W9-TRANSFER.md` Amendment 1, decoded 11:00:23Z → 11:32:43Z (32 min 20 s), same pin, seed and
map. Both validation arms are sound: `PASS (555 checks)`, `BYTE-IDENTICAL` replay
(`a1v sha256:a02ffb2d…`, `a2v sha256:2ae9232b…`). Evaluation set = test ∪ validation, n=180 per arm.

```
A1 — drop 'other', n=180
  ECE     A0 0.0750   arm 0.1223   Δ +0.0473 [-0.0048, +0.0923]
  Brier   A0 0.1360   arm 0.1289   Δ -0.0072 [-0.0248, +0.0104]
  acc     A0 0.7722   arm 0.8278   Δ +0.0556 [+0.0056, +0.1056]
  verdict: no detectable degradation

A2 — drop 'billing', n=180
  ECE     A0 0.0493   arm 0.0681   Δ +0.0188 [-0.0488, +0.0723]
  Brier   A0 0.1932   arm 0.2045   Δ +0.0113 [-0.0171, +0.0379]
  acc     A0 0.6556   arm 0.6611   Δ +0.0056 [-0.0611, +0.0722]
  verdict: no detectable degradation
```

**The powered run changes the story in two ways, and both matter.**

1. **The n=90 point estimates were partly sampling luck.** A1's ΔECE fell from +0.068 to +0.047 and
   A2's from +0.058 to **+0.019 — below the +0.02 materiality threshold**. The interval tightened as
   predicted (±0.049 against ±0.07), and the effect got smaller, not sharper.
2. **The rule still does not fire.** A1 comes within a hair: its interval lower bound is −0.0048, so
   "no effect" is excluded by 0.5 percentage points of ECE — not by enough to call. A2 is not close.

So the experiment now says something narrower and more useful than either previous reading: **removing
a label raises calibrated ECE consistently (both arms positive, Brier roughly flat), by an amount
between roughly 2 and 5 percentage points, and at n≤180 that is not separable from zero.** The
earlier "ECE doubles" headline was an artefact of the 90-row subsets; with 180 rows the honest
phrasing is "ECE rises from 7.5% to 12.2% in A1 (a 63% relative increase) and from 4.9% to 6.8% in A2
(38%), with intervals that include zero".

**I recommend stopping here rather than extending to train.** Three reasons: the interval is already
at the ±0.05 the amendment promised; the effect is at or below the threshold the rule was frozen
against, so more rows would buy precision about a quantity smaller than the materiality cut-off; and
the train split is in-sample for the map, so adding it would bias A0 downward and manufacture the
appearance of degradation. Resolving a 2-point effect would need a **larger held-out corpus**
(a new capture, new checksums), not more arms on this one — that is a corpus-growth decision, which
belongs to the owner.

A1's accuracy CI still excludes zero (+0.056 [+0.006, +0.106]): the arm without the catch-all class
is genuinely easier, which is the confound already named above, not a better system.
