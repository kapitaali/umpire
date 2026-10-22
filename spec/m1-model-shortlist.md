# M1 model shortlist (license-clean) — decision artifact for §9.2

**Status:** researched + web-verified 2026-10-22 · feeds umpire proposal §9.2
("model shortlist for the reference harness — license-clean candidates only").
Verification method: HuggingFace model API (`https://huggingface.co/api/models/<id>`)
license tags + card license cross-checked same day; sizes from each repo's
`safetensors.total`; no claim sourced from memory.

## 1. The verified candidates

| # | model (locator) | license (HF tag + card, verified 2026-10-22) | params (safetensors) | monthly DLs | notes for constrained-decode harness |
|---|---|---|---|---|---|
| 1 | **Qwen/Qwen3-1.7B** (`huggingface.co/Qwen/Qwen3-1.7B`) | **apache-2.0** (tag = card ✓) | 2.03 B | 4.19 M | official GGUF exists: `Qwen/Qwen3-1.7B-GGUF` (apache-2.0 ✓); hybrid thinking mode → pin non-thinking for latency honesty |
| 2 | Qwen/Qwen3-0.6B (`…/Qwen3-0.6B`) | **apache-2.0** ✓ | 752 M | 26.7 M | fastest; CI/property-test fallback pin; lower decision quality |
| 3 | Qwen/Qwen2.5-1.5B-Instruct (`…/Qwen2.5-1.5B-Instruct`) | **apache-2.0** ✓ | 1.54 B | 4.94 M | mature baseline; natural pin-B for A/B pricing demos |
| 4 | HuggingFaceTB/SmolLM3-3B (`…/SmolLM3-3B`) | **apache-2.0** ✓ | 3.08 B | 0.73 M | strongest Apache small; ~2× RAM of #1 |
| 5 | microsoft/Phi-4-mini-instruct (`…/Phi-4-mini-instruct`) | **mit** ✓ | 3.84 B | 0.35 M | license-diversity backup; heaviest fit on this machine |
| 6 | ibm-granite/granite-3.3-2b-instruct (`…/granite-3.3-2b-instruct`) | **apache-2.0** ✓ | 2.53 B | 0.34 M | IBM lineage; enterprise-audience optics |
| 7 | allenai/OLMo-2-0425-1B (`…/OLMo-2-0425-1B`) | **apache-2.0** ✓ | 1.48 B | 0.19 M | fully-open lineage (weights+data+code) — best governance story, quality a likely notch below #1 |

**Excluded (verified, locator-backed):** `google/gemma-2-2b-it` → `license:gemma`
(custom, not OSI license-clean); `meta-llama/Llama-3.2-1B-Instruct` →
`license:llama3.2` (community license with use restrictions);
`LiquidAI/LFM2-1.2B` → `license:other` (custom terms). The shortlist rule is
proposal §9.2's explicit constraint: license-clean candidates **only**.

## 2. Recommendation

**Primary pin: Qwen3-1.7B (apache-2.0).** Rationale: license-clean like every candidate,
but it uniquely combines (a) an **official GGUF** signal of first-class llama.cpp-runner
support, (b) the largest inference ecosystem of the shortlist (4.2 M DLs → runtime
diversity risk of proposal §8.3 de-risked by community tooling), (c) size that fits this
machine with margin, (d) recent refresh (2025-07 repo update) → better instruction
following for short-JSON classification. **Fallback pin for CI/speed: Qwen3-0.6B** —
same family, same license, same harness path; and **pin-B: Qwen2.5-1.5B-Instruct** for
A/B pricing demos (the exact `page.fault`-with-second-slot row the ledger prices).

**A/B/pin structure baked into M1 tests:** primary (Qwen3-1.7B) vs fallback (0.6B) vs
pin-B (2.5-1.5B) — three pins exercise identical-hash re-lands, divergent-hash second
slots, and honest waste rows without any fixture hand-editing.

## 3. Reference runtime + determinism plan (for M1b)

- **Runner:** llama.cpp-class, **pinned release binary** (no ollama version drift): a
  release zip's runner hash enters the pin via `model_pin.decoding`
  (`json-label-enum-v0@llama.cpp-<release>`). Upstream support verified:
  llama.cpp README lists **GBNF grammars** +
  (`github.com/ggml-org/llama.cpp`, README features, README.md L105 @ master 2026-10-22).
- **Constrained decoding:** grammar-constrained label-enum JSON (`json-label-enum-v0`):
  the "no invented class" guarantee (contract §3.1/3.2) is a *decoding constraint*,
  exactly as the proposal §4 describes.
- **Seeded determinism:** `temperature: 0` (greedy) + fixed `--seed` on the llama.cpp
  sampler. M1 property test empirically asserts contract §3.3: same
  `(state, labels, pin, seed)` ⇒ byte-identical response — **on the same runner build
  and machine**. Cross-build/cross-hardware byte-stability is *not* claimed by v0 (the
  honest boundary; cross-machine equivalence is replay-audit territory M3 decides).
- **Probabilities:** read out from the runner's token-logprob facility over the
  grammar-constrained choice points; reported raw (pre-calibration) per contract §3.4;
  calibration layer arrives M2 (schema already exposes `calibrated`).
- **Machine fit (verified 2026-10-22):** 4 CPU cores · RAM 15 GiB total/~4.7 GiB
  available · disk 50 GiB free, no dGPU → q4_K_M Qwen3-1.7B ≈ 1.1–1.4 GB weights,
  comfortably inside available RAM; ~100–600 ms/decision sits inside the proposal's
  conceded latency band (§1, §4).
- **No provider credits are spent by M1** — this is local computation only (weights
  download ≈ 1.4 GB from HF once, then fully offline).

## 4. What changes if the orchestrator picks differently

Nothing structural: the harness treats the model as a swappable implementation detail
behind the pin (proposal §4). Same grammar, same property tests, same waste semantics —
only the GGUF and the pin strings change. Every shortlisted candidate above is verified
license-clean today, so this document remains the decision menu regardless of pick.
