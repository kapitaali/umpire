# ODPS/ODCS packaging plan — `umpire-calcorpus-0.1` (spec-first)

**Status:** draft plan, 2026-10-23 (spec-first path picked by orchestrator; T14: local-only now, zero
provider spend, no model calls required). Nothing here is implemented yet.
**Inputs:**
- ctxvm validated precedent: `04-odps-dataproduct-corpus.yaml` (ODPS 4.1 — `valid: true`,
  `compliance_level: full`, `production_ready: true` against SDK 0.4.0), `contracts/corpus-0.1.odcs.yaml`
  (ODCS v3.0.2 — datacontract-cli 1.2.7 lint green; product audit fully passed), `06-odps-integration.md`
  (the complete empirically-derived gotcha list), `hub/catalog.yaml` (ODPC v1.0 catalog-as-code).
- Parent proposal `ctxvm/projects/01-open-decision-primitive.md` §5/§6: M2 full = calibration layer +
  labeled corpus packaged ODPS + ECE/Brier KPIs + hub leaderboard.
- Packaged payload: the v2 calibration drop family (9 files anchored by `calibration/checksums-v2.sha256`).

## 1. Product definition

**Decision (recorded): one `dataset` product.** The fitted calibration artifact rides inside the drop.
An Agentic `algorithm`-type product (proposal analog: "trained paging policies (v2)") is deferred — it
presupposes hosting/distribution of artifacts, which T14 gates.

| field | value |
|---|---|
| productID | `umpire-calcorpus-0.1` (mirrors ctxvm's `ctxvm-corpus-0.1`) |
| name | umpire Calibration Corpus |
| type | `dataset` (Reusable Data Product template) |
| productVersion | `0.1.0`, latest drop `calibration/v2` |
| UUID | `dd6c0a8a-8376-594e-a772-d8ccba457c1e` — deterministic `uuid5(NAMESPACE_URL, 'https://github.com/kapitaali/umpire/odps/umpire-calcorpus-0.1')`; shared by ODPS `contract.id` and ODCS `id` (ctxvm precedent) |
| visibility / status | `public` / `draft` — declared drop is created at publication; repo paths resolve locally until then |

Drop family (the packaged payload; `checksums-v2.sha256` is the sole integrity anchor):

| file | role |
|---|---|
| `calibration/corpus-v2.jsonl` | 480 examples (120/label, split 240/120/120; caller-owned routing guide in every state) |
| `calibration/raw-v2.jsonl` | 480 fit-input decision records |
| `calibration/corpus-v2.ledger.jsonl` | 481-event xCABI-0.1 decision ledger |
| `calibration/corpus-v2.records.jsonl` | 480 payload-sidecar records (raw view) |
| `calibration/corpus-v2.calibrated.records.jsonl` | calibrated sidecar on the 120 test rows |
| `calibration/corpus-v2.page-table.json` | deterministic replay baseline (`sha256:d75a1644…`) |
| `calibration/umpire-cal-v2.json` | fitted artifact `cal-v1:aceb881587b3b3a6`, method `isotonic/1` |
| `calibration/corpus-v2.capture.meta.json` | capture provenance (pin echo, seed, host, hashes) |
| `calibration/checksums-v2.sha256` | 9/9 integrity anchor |

## 2. Files to create

- `odps/umpire-calcorpus-0.1.odps.yaml` — ODPS 4.1 manifest
- `contracts/umpire-calcorpus-0.1.odcs.yaml` — ODCS v3.0.2 data contract
- `hub/catalog.yaml`, `hub/index.html`, `hub/hub.sha256` — Phase B only
- `spec/test_packaging.py` — offline unittest gates (stdlib-only; SDK checks env-gated)
- `reports/RR-ODPS-20261023.md` — validation report
- README / `00-SPEC.md` roadmap rows

## 3. ODPS manifest — field map

Every ctxvm-validated gotcha is pre-applied (sources: `06-odps-integration.md` tables):

| # | gotcha | applied here |
|---|---|---|
| 1 | `version: "4.1"` MUST be a quoted string (YAML float hazard) | quoted |
| 2 | SLA unit enum has **no `hours`** (`percent, milliseconds, seconds, minutes, days, weeks, months, years, never, date, null`) | days/weeks only |
| 3 | `dataHolder` is **flat** (no `.en` wrapper) | flat |
| 4 | DQ `description` is a plain **string** (not `{en:…}`) | string |
| 5 | DQ `executable` is a **named object** of DQDefinitions (not an array) | 3 named checks |
| 6 | DQ `spec` must be a YAML **object** even for `type: Custom` | object |
| 7 | `displayTitle` in DQDefinition = **array of language maps** | array |
| 8 | `contract.contractVersion` = the **standard's** version `"3.0.2"` (not document version) | `"3.0.2"` |
| 9 | `dataAccess.format` enum excludes JSONL | `format: JSON` + `x-format: jsonl` note |
| 10 | bundled core `odps.json` schema is the only truth; SDK validation is the gate | same SDK 0.4.0 |

### 3.1 contract block

```yaml
contract:
  id: dd6c0a8a-8376-594e-a772-d8ccba457c1e
  type: ODCS
  contractVersion: "3.0.2"
  $ref: contracts/umpire-calcorpus-0.1.odcs.yaml   # SDK cannot load remote contract URLs yet
```

### 3.2 `x-umpire` sidecar (composition over extension, à la x-cabi)

Values transcribed from `calibration/corpus-v2.capture.meta.json` — the manifest stays honest by echoing
pin identity verbatim:

- `recordProfile: xCABI-0.1-decision` (00-SPEC §2), `calibrationStatVersion: cal-v1:aceb881587b3b3a6`, `calibrationMethod: isotonic/1`
- `modelPin`: `qwen3-1.7b`, `weights_sha256 sha256:66a17017…`, `decoding json-label-enum-v0@llama.cpp-v0.7.0/sha256:0f60e0483`, `prompt_template_sha256 sha256:54852288…`, `runner_binary_sha256 sha256:0f60e048…`, `server_invocation` (verbatim)
- `seed: 7`; capture window, `host_sha256: sha256:1e769fcd99223f5a`, platform, python
- drop hashes: corpus/raw/ledger/records/page-table/artifact sha256 (+ `checksums_file_sha256`)
- `page_table_stats: {decisions: 480, declared_slots: 480, re_decides: 0}`
- `replayAudit`: M3 quiescent smoke 8/8 strict (`harness/replay_audit.py`) + explicit note that raw
  distributions are load-sensitive under concurrent capture (RR-M3), so replay determinism is claimed for
  the **quiescent page-table roundtrip**, not for model behavior under load

### 3.3 productStrategy — KPIs and release gates (MLG-lean: ≥1 KPI with unit+target+calculation; every gate carries `contributesToKPI.name`)

| KPI id | unit | target (direction) | calculation | v2 current |
|---|---|---|---|---:|
| `kpi-replay-determinism` | percentage | `at_least 100` | byte-identical page-table replay rounds ÷ declared slots | 100 (480/480) |
| `kpi-negative-conformance` | percentage | `at_least 100` | negative-corpus pass rate (validator) | 100 (12/12 raw + 12/12 calibrated) |
| `kpi-calibrated-ece` | percent | `at_most 5` | ECE on the untouched test split (n=120) using calibrated confidence | 2.67 |
| `kpi-calibrated-brier` | percent | `at_most 20` | Brier × 100 on the test split (calibrated confidence) | 17.23 |
| `kpi-decision-accuracy` | percent | `at_least 68` | correct ÷ n on the test split | 70.83 |

Targets are **floors/ceilings derived from the current v2 numbers and declared as release gates — not
model-performance promises.** The manifest description carries the T13 caveat verbatim: single
project-authored corpus, one pin/seed/machine, n=120 intervals exclude zero but remain single-corpus.

| gate | criterion | contributesToKPI |
|---|---|---|
| `replayDeterminismGate` | `spec/validate_ledger.py --replay` → roundtrip BYTE-IDENTICAL (`d75a1644…`) | kpi-replay-determinism |
| `negativeConformanceGate` | negatives 12/12 raw + 12/12 calibrated | kpi-negative-conformance |
| `calibrationFitGate` | artifact reproduces its recorded test metrics; ΔECE/ΔBrier CIs exclude 0 | kpi-calibrated-ece, kpi-calibrated-brier |
| `checksummedReleaseGate` | `sha256sum -c calibration/checksums-v2.sha256` → 9/9 | kpi-replay-determinism |
| `accuracyFloorGate` | test accuracy ≥ declared target on this corpus | kpi-decision-accuracy |

### 3.4 dataQuality

Declarative (`default`): weights mirror ctxvm — `validity` 60 (replay determinism proxy),
`completeness` 25 (calibrated sidecar on all test rows / no dangling pin refs), `uniqueness` 15
(corpus dedup + secret/PII scan). Objectives 100/100/100 — all three are machine-verifiable here.

Executable — named object; `spec` blocks are objects; `displayTitle` is the array form:

| check | command (real, local) | passCriterion | dimension |
|---|---|---|---|
| `replayDeterminism` | `python3 spec/validate_ledger.py --replay calibration/corpus-v2.ledger.jsonl calibration/corpus-v2.records.jsonl calibration/corpus-v2.page-table.json` | roundtrip BYTE-IDENTICAL; page-table sha256 unchanged | validity 60 |
| `calibrationParity` | `python3 spec/validate_ledger.py calibration/corpus-v2.ledger.jsonl calibration/corpus-v2.calibrated.records.jsonl` | PASS (2608 checks); negatives 12/12 | completeness 25 |
| `redactionScan` | full-corpus token/PII scan (project-owned pattern set) | zero matches | uniqueness 15 |

### 3.5 sla (unit enum only — no `hours`)

`timeToDetect: 1 day` (local gate run per change), `updateFrequency: 26 weeks` (declared ceiling —
calibration revisions are authored, not scheduled), `errorRate: 0 percent` (deterministic local gates).
Exact block shapes are transcribed from the ctxvm manifest at authoring time.

### 3.6 dataAccess

- `default`: `outputPortType: file`, `format: JSON` + `x-format: jsonl` (enum gotcha #1),
  `accessURL: https://github.com/kapitaali/umpire/releases/tag/calcorpus-0.1.0` (declared permalink;
  the release is created at drop publication — local path resolution via the `x-umpire` hashes until then),
  `hashType: SHA-256`, `checksum: TBD-at-release` (ctxvm precedent).
- `agent` port + llms.txt: **Phase C** (hybrid switch per T14).

### 3.7 license + dataHolder (both FLAT objects)

- `license.scope`: rights = Reproduction, Display, Distribution, Adaptation; `permanent: true`,
  `exclusive: false`. `governance.applicableLaws`: Apache-2.0 for code/artifacts, CC-BY-4.0 for corpus
  content (ctxvm mirror). Restriction: no redistribution of content failing the redaction scan;
  downstream must preserve checksum lineage.
- `dataHolder`: `legalName: "umpire Project"` (personal open-source project at
  https://github.com/kapitaali/umpire — **owner to confirm naming/email**), `businessDomain:
  decision-infra`, contact = GitHub noreply.

## 4. ODCS contract — field map

v3 shapes only (`06-odps-integration.md` contract track): `description` is an **object**
(`purpose/usage/limitations`); `quality` lives on schema elements; ODCS has **no `validity` dimension**
so replay determinism = `conformity`; custom checks require `engine` + `implementation` objects;
service levels = top-level `slaProperties` (not 2.x `servicelevels`).

```yaml
version: 1.0.0
kind: DataContract
id: dd6c0a8a-8376-594e-a772-d8ccba457c1e   # same UUID as the ODPS contract block
status: active
name: umpire_calibration_corpus
apiVersion: v3.0.2                         # the standard's version
```

Schemas — property sets transcribed from the real JSONL keys:

1. `umpireCalibrationExample` (corpus-v2.jsonl row): `id`, `split` {train|validation|test}, `labels`
   (array, unique), `message`, `gold`, `state.text`.
2. `umpireDecisionRecord` (records + calibrated.records rows): `schema` (const), `key` ∈ labels,
   `probabilities` (map; custom check Σ=1 across the requested labels), `confidence` [0..1],
   `ref` (pattern), `contentHash` (`sha256:[0-9a-f]{64}`), `labels`, `model_pin`, `seed`,
   `latency_ms`, `state.text`.
3. `umpireFitRecord` (raw-v2 row): `id`, `split`, `gold`, `correct` (boolean),
   `record` → umpireDecisionRecord.
4. `umpireCalibrationArtifact` (umpire-cal-v2.json): `data_version` const `cal-v1:aceb881587b3b3a6`,
   `method` (`isotonic/1`), bin mapping, fit/test metric blocks, pin echo (self-keys transcribed at
   authoring time).

Element-level `quality`:

- Custom (conformity): `replayDeterminism`, `calibrationParity`, `checksumVerify` — each with
  `engine` + `implementation` pointing at the real local commands above.
- Per-property library checks: `id` duplicateCount 0; `split` pattern/enum; `correct` notNull;
  `confidence` min 0 max 1; `ref`/`contentHash` pattern.

`slaProperties` (top level): checksum verification 100% of drop files; calibrated sidecar coverage
100% of test rows; negative conformance pass rate 100%.

**Explicitly not claimed by the contract:** gold-label accuracy, cross-machine determinism,
generalization beyond this corpus.

## 5. Validation + offline tests

Tooling restored from the ctxvm workaround — **rebuild venvs under a durable path**
(`~/.local/state/umpire/venvs/`) after any reboot; `/tmp`-hosted copies were lost
(2026-10-24) and the harness tmp reaps quickly:

- `~/.local/state/umpire/venvs/umpire-odps-venv` — Python 3.14.7, `open-data-products` 0.4.0 → manifest validation
  expected `valid: true`, `compliance_level: full`, `production_ready: true`.
- `~/.local/state/umpire/venvs/odcs-venv` — Python 3.12.15, `open-data-products[contracts]` (datacontract-cli) →
  product audit expected `passed: true` (`product_valid` + `contract_valid`, `$ref` resolved locally);
  ODCS lint green. Exact CLI invocations are pinned from `--help` at authoring.

`spec/test_packaging.py` (stdlib; no SDK import; always runnable):

- all nine drop files exist and match `checksums-v2.sha256`;
- both YAML documents parse; `version` key is the quoted string `"4.1"`;
- contract `$ref` target exists; ODPS `contract.id` == ODCS `id` == recomputed uuid5;
- KPI current values equal values recomputed from `calibration/umpire-cal-v2.json` + capture meta;
- DQ `executable` `spec` blocks are YAML objects; `license`/`dataHolder` are flat.

SDK-dependent checks are invoked via `UMPIRE_ODPS_VENV`/`UMPIRE_ODCS_VENV` env vars when set, skipped
otherwise (same pattern as the runner env-var gating in `harness/test_m2.py`).

## 6. Phase B — local hub (only if agreed)

- `hub/catalog.yaml`: ODPC v1.0 skeleton mirroring `ctxvm/hub/catalog.yaml` (`kind: Catalog`,
  `metadata`, `productReferences` → productModel `standard: ODPS`, `version: '4.1'`, `format: yaml`,
  `$ref: ../odps/umpire-calcorpus-0.1.odps.yaml`).
- `spec/build_hub.py`: regenerates `catalog.yaml` metadata + `index.html` leaderboard **from the
  manifest's `productStrategy.productKPIs`** (manifest = build input; rebuild must be byte-identical;
  `hub.sha256` covers all three). Static files only — no server, no domain (T14).
- `llms.txt`, agent `dataAccess` port, hosted domain: deferred to Phase C (hybrid switch).

## 7. Honest caveats (must survive into the manifest/contract verbatim)

- n=120 single test split, paired bootstrap on that sample: directional strength over n=40, still
  single-corpus/single-machine (T13 caveat retained; v1 result not retroactively upgraded).
- Per pin, label set, seed, machine, and runner build; artifact is not transferable without re-fitting.
- The artifact estimates P(key correct) on this input distribution — **not a correctness guarantee**;
  raw probabilities, `ref`, `contentHash` unchanged; calibration is an optional sidecar.
- Accuracy is not comparable across corpus revisions (v2 rows are deliberately harder).
- ODPS/ODCS packaging declares checksummed provenance + release gates — not model performance promises.

## 8. Out of scope / deferred

| item | reason |
|---|---|
| hosted runtime, per-decision pricing/payment | T14: local-only now; hybrid later |
| agent `dataAccess` port + llms.txt | Phase C on hybrid switch |
| `algorithm`-type Agentic product | presupposes artifact distribution |
| upstream ODPS issues (JSONL enum, SLA `hours`, doc drift) | already line-itemed in ctxvm 06 |
| hub hosted domain / CI | no server at present; static local files only |
| Hermes consumption | already served by `mcp_server.py` (separate surface) |

## 9. Execution order

1. **P0** author manifest + contract; run SDK validation + product audit + ODCS lint (venvs above);
   `spec/test_packaging.py` green.
2. **P1** `reports/RR-ODPS-20261023.md`, README/00-SPEC/LOG rows, commit.
3. **P2** hub trio (`hub/catalog.yaml`, `index.html`, `hub.sha256`) + byte-identical rebuild gate —
   only if agreed now.
4. **P3** agent port/llms.txt — on the T14 hybrid switch.

## 10. Corrections landed during P0 (2026-10-23, see RR-ODPS-20261023)

Empirical corrections produced by implementation; kept here so the plan stays truthful:

1. **KPI `unit` enum is `percentage`** (no `percent`) in ODPS `productStrategy` — all KPI units here use
   `percentage`; `percent` remains valid only inside SLA dimension units (§3.3 table read `percent`).
2. **`contract.$ref` resolves relative to the manifest's directory** — from `odps/…` the correct form is
   `../contracts/umpire-calcorpus-0.1.odcs.yaml` (§3.1 snippet said `contracts/…`).
3. **No `gates` block exists in ODPS `productStrategy`** (ctxvm-validated schema shapes): release gates =
   the declared KPI targets themselves, enforced by `spec/test_packaging.py` parity checks + the DQ
   executable commands (§3.3 gate table is therefore a documentation device, not a manifest block).
4. **Record `contentHash` is the short-form probe `sha256:<8 hex>`** and **`model_pin` is a nested object**
   — contract corrected accordingly (§4's item 2 sketch said string/64-hex).
5. `product audit` must run from the Python 3.12 venv (`open-data-products[contracts]`), confirmed again
   here; with the 3.14 venv it reports `DATACONTRACT_CLI_NOT_INSTALLED`.

## 11. Phase B landed (2026-10-23, see RR-HUB-20261023)

6. **hub-as-code built by their SDK end to end** (`spec/build_hub.sh`, mirroring ctxvm
   `tools/build-hub.sh`): manifest source-gate → basename-staged `odpc-build` →
   hub-relative `$ref` (`../odps/…`) → deterministic injection of the release-gate KPI
   section + `.odp-kpi` style into `index.html` → catalog re-validated (`valid: true`)
   → `hub.sha256` anchor → `check` mode `cmp`s a full rebuild against the committed
   files. The SDK's HTML output has no KPI rendering, so the leaderboard injection is
   the umpire addition (rows from `productStrategy`, currents from the committed
   artifact + capture meta; no timestamps anywhere → two runs byte-identical).

## 12. M3-full landed (2026-10-24, see RR-M3FULL-20261024)

The "full-corpus replay still pending" condition from RR-M3/§7 is resolved: full
480-row audit run 470/480 strict with identity 480/480; the 10 distribution-only
rows reproduce bit-for-bit across three audit samples on two server instances and
are attributed to capture-time concurrency (retracted: the earlier assumption that
the Oct-23 audit itself ran under concurrent decide traffic — no MCP decide traffic
was observed in either audit window). The full-run replay corpus is committed at
`tests/m3-replay-full` (validator 2488 checks + negatives 12/12; roundtrip
byte-identical `25b073cbc…`). Workspace decisions: v2 baseline frozen with the 10
rows annotated; **full quiescent v3 recapture queued before any refit**; KPI
currents and product 0.1.0 remain v2-based until v3 lands, so no manifest current
changed in this step (only the `x-umpire.replayAudit` wording).
