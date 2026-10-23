# hub/ — the umpire hub catalog (Phase B proof)

Hub-as-code, per the hub-as-code claim proven in ctxvm (`hub/` + `06-odps-integration.md`
§5: "public Git repo, `*.yaml` per product, CI schema validation, static generation,
no bespoke hub server"). Everything here is generated from the single ODPS manifest
under it; two regenerations are byte-identical.

| File | What it is |
|---|---|
| `catalog.yaml` | ODPC v1.0 `kind: Catalog`, metadata `umpire.hub`, one `productReferences` entry (umpire-calcorpus-0.1) → ODPS 4.1 |
| `index.html` | Standalone browser-viewable HTML catalog + the release-gate KPI leaderboard section injected from the manifest's `productStrategy` |
| `hub.sha256` | Integrity anchor over the three files |

## Single-source-of-truth rule

The catalog references the manifest directly:

```yaml
productModel:
  standard: ODPS
  version: '4.1'
  $ref: ../odps/umpire-calcorpus-0.1.odps.yaml   # hub-relative
```

The manifest is NOT duplicated into `hub/` — the pointer is the authoritative link and
`odps/umpire-calcorpus-0.1.odps.yaml` stays the single source of truth (it carries the
SDK validation proof in its header comments).

## Regenerate (idempotent, byte-identical)

```sh
spec/build_hub.sh
# or with an explicit venv:
UMPIRE_ODPS_SDK=/tmp/opencode/umpire-odps-venv/bin/open-data-products spec/build_hub.sh
```

The script: re-validates the manifest (source gate) → stages a basename-stable copy →
`odpc-build` (catalog + HTML) → rewrites the `$ref` hub-relative → injects the
release-gate KPI section (rows parsed from `productStrategy`, current values derived
from `calibration/umpire-cal-v2.json` + capture metadata) → re-validates the committed
catalog (`validate --json` → `valid:true`, `errors:[]`) → `odpc-summary`. The SDK emits
no timestamps and every number comes from committed files, so two runs are
byte-identical; `UMPIRE_RO_RUN_CHECK=1 spec/build_hub.sh check` rebuilds to a temp dir
and `cmp`s against the committed files.

## Status

Static, local-only (T14): no server, no domain. When the umpire hub goes live (T14
hybrid switch), publish `hub/` as static pages; the manifest's `accessURL` claims
(`calcorpus-0.1.0` release tag) then resolve, and this catalog keeps pointing at the
same manifest (repo root = hub root).
