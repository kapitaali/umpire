# Research: umpire as a Jev/ArchiveExplorer-style typed decision primitive

**Source investigated.** `https://x.com/ArchiveExplorer/status/2103590048230695408`
(Archive, posted 2026-09-25): "Three builds on Jev: a requirements report, diagnostic
selection, a bounded repair loop. Working code, real runs, 28 correct decisions out of
28 across two labelled sets." Jev is TypeSafe AI's hosted typed-decision primitive
(≤255 labels, one key + calibrated probabilities + confidence out, per-decision pricing,
pinned model version). Umpute is umpire's open, local-only equivalent — a pinned
llama.cpp runner + GBNF grammar-constrained `decide(state, labels) → key + probabilities
+ confidence`, with an xCABI-0.1 decision ledger, priced re-decides, A/B pin comparison,
and determinism audit via ctxvm replay (M0+M1+M1b+M2 all green; see reports/).

**Purpose of this doc.** Map the Jev post's concepts onto umpire's current capabilities,
show how umpire reproduces Jev's three builds (requirements report → check selector →
bounded repair loop) and its nine example products, state what umpire adds beyond Jev,
and list what umpire would still need to add to be a drop-in Jev equivalent.

All Jev claims below are taken from the X post (verified by read). Umpire claims are
backed by committed repo state (pins.json, fixtures/ab-demo.*, reports/RR-*.md,
spec/decision-record-v0.schema.json, calibration/). Claims marked UNVERIFIED are not
presented as fact.

---

## 1. What Jev does (from the post)

Jev takes `state` (the material being judged), `questions` (what to know), and `model`
(a pinned version), and returns a decision in a shape the program sets in advance — no
free text, no parsing, the application branches on the value directly.

Request limits from the post: 64 k tokens per request, 32 k for state + longest
question, ≤255 options in a Choice, 2–10 levels in a Score. One key insight: "The
program builds the options; Jev picks from a closed set you assembled." Another: "A
typed answer is still an opinion. A check decides readiness."

### Three answer primitives (post §3)

| Primitive | Meaning | What it returns |
|-----------|---------|-----------------|
| Choice | pick one of several unordered options | label (= most likely) + probabilities |
| Score | position on a described scale (0,1,2…) | probability-weighted index into a legend |
| Noul | judge whether one statement holds | single probability the statement holds |

All three go out in one request, evaluated independently. Choice includes an "other"
exit so the model isn't forced to pick a bad fit. Score levels are observable situations
("bad / very bad / catastrophically bad" is too vague — the post's example levels are
concrete states). Noul has no distribution, just one probability; it's the cheapest and
most dangerous to phrase.

### Confidence (post §confidence)

Confidence = concentration of the distribution; the post's measured formula is
`(n × p_max − 1) / (n − 1)` over n options (matched against 161 saved answers, max
deviation 0.015). Confidence is a dispersion measurement, not a correctness promise.
A threshold of 0.60 on three options means p_max ≥ 0.73 — stricter than it looks.

### Three builds (post §Three builds)

1. **Requirements report** — each requirement → judgement on how evidence relates to it.
2. **Diagnostic selection (check selector)** — given a requirement + results, choose which
   diagnostic check to run next.
3. **Bounded repair loop** — choose a repair action, check whether the fix is verified,
   loop with a bound, stop on low confidence.

### Nine example products (post §The program builds the options)

Support routing (message → Choice category + Score impact → prioritized queue), document
search (query + fragment → Noul "does this answer the condition" → reorder), browser
actions (DOM as numbered table → Choice index), model/tool picking (task + worker
descriptions → Choice → call chosen worker), checking a coding agent (requirement +
results → judgement on evidence, then diagnostic choice), context compaction (two Nouls
per tool call: is the call needed, is output needed verbatim), knowledge-graph traversal
(probabilities become hypothesis scores in a beam search; argmax never taken), code
review triage (file × dimension matrix as five Nouls in one request), completion
verification (Noul "does the message claim the work is done" — not "is the work done").

### Operational rules the post learned the hard way

- Pin the question phrasing together with the threshold and version them as one thing.
- Low confidence = absence of opinion, not agreement; an unanswered question must be a
  distinct state from "asked and answered" and "asked and unavailable."
- Advice needs two thresholds: confidence above 0.55 AND probability of the chosen
  category above 0.65 (one threshold misses "confidently picked between two near-equals";
  the other misses "sharp distribution but the model does not trust itself").
- The label is not the answer: the probabilities matter more than the p_max label.
  `{export: 1.0}` and `{export: 0.4, account: 0.35, payment: 0.25}` both give label
  `export` but mean different things.

---

## 2. Primitive mapping: Jev → umpire

| Jev concept | Jev behavior | Umpire equivalent | Status |
|-------------|--------------|-------------------|--------|
| Choice | closed set, unordered, label + probabilities | `decide(state, labels)` — GBNF json-label-enum-v0 constrains output to `key ∈ labels`; `harvest_probs` returns raw distribution over all labels | ✓ implemented (M1) |
| Noul | single statement, probability it holds | `decide(state, {"holds", "not_holds"})` → read `p(holds)`; two-label Choice is structurally a Noul | ✓ implementable today, no code change |
| Score | position on scale 0..N, probability-weighted index | `decide(state, {"level_0", …, "level_N"})` → compute Σ p(i)·i as post-processing | ✓ implementable today, no code change |
| "other" exit | legitimate non-pick | caller adds an `"other"` / `"none_suitable"` label to the label set | ✓ implementable today, no code change |
| Multiple questions in one request | Choice+Score+Noul sent together, evaluated independently | umpire `decide()` is single-question per call; repeat `decide()` with same `state`, different label sets (or prefix-joined labels) | ⚠ sequential calls, not atomic |
| Confidence | `(n × p_max − 1)/(n − 1)`, concentration measurement | entropy-complement `1 − H/log₂n` (M1); isotonic calibration → `P(key is correct)` (M2) | ⚠ different formula; Jev's formula not implemented |
| Confidence thresholds | 0.60 on 3 options ⇒ p_max ≥ 0.73; two-threshold advice | umpire `confidence` + `p(key)` both in record; caller applies any thresholds | ✓ implementable today |
| Pinned model version | `model` field, pin `jev-1.13.0` | `model_pin` + `weights_hash` + `decoding` (runner hash) in `pins.json`; `model_pin`/`seed` excluded from ref | ✓ implemented (M1) |
| Program builds options | caller assembles closed label set; model can't invent outside it | labels are caller-built; GBNF grammar physically constrains output to the enum | ✓ implemented (M1) |
| Decision ledger / priced re-decides | not present in Jev | xCABI-0.1 `page.declare`/`page.fault`, identical-hash → fault only, divergent hash → fault + second-slot declare | ✓ umpire adds this (M0/M1b) |
| Determinism audit | not present in Jev | `ctxvm replay` byte-identical; `model_pin`/`seed` excluded from ref so cross-pin re-decides are priced | ✓ umpire adds this (M1b) |
| A/B pin comparison | not present in Jev | three pins (qwen3-1.7b primary, qwen3-0.6b CI fallback, qwen2.5-1.5b pin-B), divergent-hash second-slot demo live | ✓ umpire adds this (M1b) |
| Calibration → P(key correct) | not present in Jev | isotonic/1 calibration, ECE 0.1428→0.0457 on test split; `calibrated` sidecar on records | ✓ umpire adds this (M2) |

**Bottom line on primitives:** umpire's `decide()` is a superset of Jev's Choice and a
structural match for Noul and Score (both reducible to a closed label set read through
the probabilities). The parts umpire does NOT have natively are: a single atomic
multi-question request, a native Score index output type, a native Noul type, and Jev's
exact confidence formula. All four are additive, not architectural.

---

## 3. Reproducing Jev's three builds in umpire

### Build 1 — Requirements report

For each requirement R with evidence E and criteria C:

```
decide(state = {requirement: R, evidence: E, criteria: C},
       labels = {"met", "partially_met", "not_met", "cannot_determine"})
```

Read `key` + `probabilities` + `confidence`. If `confidence < 0.60` (or p(key) < 0.65),
flag for human review instead of recording the judgement. Record each as an xCABI
`page.declare`; re-running the same requirement produces a priced `page.fault` (re-decide)
instead of a silent duplicate. The ledger makes the report auditable: every judgement is
a pinned page with a contentHash.

This is Jev's "checking a coding agent: requirement + results → judgement on how the
evidence relates to the requirement."

### Build 2 — Diagnostic selection (check selector)

Given a requirement + prior results, choose the next diagnostic check:

```
decide(state = {requirement: R, prior_results: [...]},
       labels = {"check_logs", "replay_session", "inspect_diff", "ask_user", "none_suitable"})
```

`none_suitable` is the "other" exit. The program then runs the chosen check. Re-running
against identical input re-lands on the same hash → a `page.fault` naming the resident
slot, so redundant diagnostic selection is priced and visible in the ledger.

This is Jev's "diagnostic selection" build: the router organises diagnostics and ties
them to a worker, recording why it stopped.

### Build 3 — Bounded repair loop

```
for attempt in 1..bound:
    action = decide(state = {failure: F, history: attempts},
                    labels = {"patch_A", "patch_B", "rollback", "escalate", "no_action"})
    if confidence(action) < 0.60:  # absence of opinion → stop
        break
    verified = decide(state = {attempt: action, result: test_outcome},
                      labels = {"verified", "not_verified", "uncertain"})
    if p(verified) > 0.65:
        break  # fix confirmed
# loop bound reached → escalate to human
```

Two thresholds per the post's advice rule: confidence above 0.55 AND p(chosen) above
0.65. The bounded loop stops on low confidence (absence of opinion) rather than
auto-finishing. Every attempt is a ledger page, so the repair history is replayable and
the waste (repeated failed patches) is visible as priced re-decides.

This is Jev's "bounded repair loop": ties the router to a worker and records why it
stopped, with a successful repair, a stop caused by an unconfident answer, and a tested
way to carry on.

---

## 4. Reproducing Jev's nine example products in umpire

| Jev product | Umpire `decide()` call | Umpire adds |
|-------------|------------------------|-------------|
| Support routing | message → Choice `{billing, other, sales, technical}` (+ Score impact) | priced re-decides on duplicate tickets; A/B pins compare models on the same ticket |
| Document search | query + fragment → Noul `{answers_condition, does_not_answer}` → reorder results | Noul via two-label Choice; ledger stores each judgement for audit |
| Browser actions | DOM as numbered table → Choice of action index | GBNF constrains to valid indices; replay audits which action was chosen |
| Model/tool picking | task + worker descriptions → Choice worker → call worker | cross-pin determinism: same task, different pin → priced re-decide, not a new identity |
| Checking a coding agent | requirement + results → Choice judgement + diagnostic Choice | two-stage decide with priced re-decides at each stage |
| Context compaction | per tool call: Noul "is call needed" + Noul "is output needed verbatim" | two decides per call; each page.declare'd; verbatim output preserved in ledger |
| Knowledge-graph traversal | probabilities as hypothesis scores in beam search (no argmax) | full `probabilities` distribution is available, not just the label |
| Code review triage | file × dimension matrix → 5 Nouls in one request | 5 sequential decides (or prefix-joined labels); each recorded as a page |
| Completion verification | Noul "does message claim work is done" (not "is work done") | two-label Choice; narrow fact about text, recorded + auditable |

Umpire's distinguishing addition across all nine: every decision is an xCABI page, so
the full decision history is a replayable trace. Jev returns a decision and the
application owns the memory; umpire records the decision, prices re-decides, and lets
`ctxvm replay` prove byte-identical behaviour under a pinned model+seed.

---

## 5. What umpire adds beyond Jev (open axes Jev doesn't address)

1. **Priced re-decides / waste measurement.** Jev charges per-decision but doesn't
   measure redundant decisions. Umpire's ledger prices every re-decide: identical hash
   → `page.fault` naming the resident slot; divergent hash → fault + second-slot declare.
   The A/B demo (fixtures/ab-demo.*) exercised this live: Qwen3-1.7B → `technical`,
   Qwen2.5-1.5B → `billing` (divergent hash → fault naming resident + second-slot declare).
   This is the waste the Jev post hints at ("advice needs two thresholds") but doesn't
   instrument.

2. **Determinism audit / "verify the pin yourself."** Jev pins a model version (`jev-1.13.0`)
   but the user trusts TypeSafe's pin. Umpire excludes `model_pin`/`seed` from the ref
   and `ctxvm replay` proves byte-identical behaviour under a pinned model+seed+runner.
   Cross-pin re-decides are priced, not silently treated as new identities.

3. **Calibration → P(key is correct).** Jev's confidence is a dispersion measurement
   (not correctness). Umpire M2 adds isotonic calibration (ECE 0.1428→0.0457 on a 40-example
   test split), producing a calibrated `P(key is correct)` sidecar on records without
   touching raw probabilities or hashes. This directly addresses the post's warning that
   "you still cannot read confidence as a measured probability of being right on your
   data."

4. **Local-only, open weights, no provider credits.** Jev is hosted (per-decision pricing
   at ~70 ms is the reference point). Umpire runs pinned open GGUFs (Qwen3-1.7B/0.6B,
   Qwen2.5-1.5B) locally through llama.cpp; M2 capture ran 160 decisions with zero
   provider spend.

---

## 6. What umpire would still need to add (to be a drop-in Jev equivalent)

These are additive features, none requiring architectural change:

1. **Native Score output type.** Today a Score is post-processed (Σ p(i)·i over ordinal
   labels). Jev returns the probability-weighted index as a first-class answer type with
   its own legend semantics. Umpire could add a `score` view on records.

2. **Native Noul type.** Today a Noul is a two-label Choice; the caller reads `p(holds)`.
   Jev treats Noul as its own primitive with no distribution. Umpire could add a `noul`
   view returning a single probability.

3. **Atomic multi-question request.** Jev sends Choice+Score+Noul together in one request,
   evaluated independently. Umpire `decide()` is one question per call; reproducing Jev's
   atomic request requires N sequential decides or prefix-joined labels. A batch mode
   (`decide_many(state, [label_set_1, …])`) would close this.

4. **Jev's exact confidence formula.** Umpire uses entropy-complement; Jev uses
   `(n × p_max − 1)/(n − 1)`. Umpire could offer Jev's formula as an alternative
   `confidence` view alongside its own.

5. **"other" as a first-class exit.** Today the caller adds an `"other"` label manually.
   Jev treats it as a primitive feature. A `allow_other=true` flag that auto-appends the
   exit label would match Jev's UX.

6. **Bounded-repair harness pattern.** The loop in §3 is a manual recipe today. A
   reusable `repair_loop(state, labels, bound, thresholds)` harness would make Jev's
   third build one line.

7. **Question-phrasing versioning.** The post's key operational lesson: pin the phrasing
   with the threshold and version them as one thing. Umpire pins the template
   (`prompt_template_sha256` per pin, enforced fail-hard after the M1C review) but does
   not yet version the question text + threshold as a single unit.

---

## 7. Honest limits

- Jev claims (28/28 on two labelled sets, confidence formula, three builds, nine
  products, operational rules) come from the ArchiveExplorer X post and are taken as the
  user's reference, not independently re-verified by me. The post itself notes its
  labelled-set result "mostly proves the plumbing" and that "the honest check is a third
  set it never saw" (top comment by @kartikb753).
- Umpire's calibration metrics come from a project-authored 160-example corpus (M2
  report), not a production sample; accuracy is a property of that corpus, not a general
  benchmark.
- Umpire's determinism and byte-identity claims hold same-build/same-machine
  (M1 P1 gate); cross-build byte-stability is not claimed (per spec).
- Umpire's labels must be safe-ASCII single-line uids (GBNF literals must stay exact);
  Jev's label constraints may differ.

---

## 8. Bottom line

umpire's `decide(state, labels)` is Jev's Choice primitive with a stronger enforcement
mechanism (GBNF grammar makes "key ∈ labels" physically impossible to violate, not just
hoped-for). Jev's Noul and Score reduce to closed label sets readable through
`probabilities`, so they're implementable today with no code change. The three Jev builds
(requirements report, diagnostic selection, bounded repair loop) and all nine example
products map cleanly onto umpire `decide()` calls.

Where umpire diverges from Jev is deliberately additive: a priced decision ledger (waste
measurement), a determinism audit via `ctxvm replay` ("verify the pin yourself"), A/B pin
comparisons, and calibration to `P(key is correct)`. The gaps to a drop-in Jev equivalent
are small and additive: a native Score index, a native Noul type, an atomic multi-question
request, Jev's exact confidence formula, first-class "other" exit, a bounded-repair
harness, and question+threshold versioning. None of these block reproducing Jev's three
builds in umpire today.
