# W9 transfer arms — frozen inputs

Two arm corpora for the calibration-transfer experiment. **Design:** `design/W9-TRANSFER.md`
(frozen 2026-11-01, no run at the time of writing this file).

| File | Contents |
|---|---|
| `a1.corpus.jsonl` | the 90 v3 **test** rows whose gold ≠ `other`, decoded under `billing, sales, technical` |
| `a2.corpus.jsonl` | the 90 v3 test rows whose gold ≠ `billing`, decoded under `other, sales, technical` |
| `provenance.json` | source corpus + sha256, per-arm labels, row ids, per-arm corpus sha256 |
| `checksums.sha256` | the three files above |

Decisions land here as `a1.{ledger,records,raw,page-table,meta}` and the same for `a2`. The v3
capture is never overwritten: A0 is `calibration/corpus-v3.*`, unchanged, and the comparison
restricts A0 to the ids listed in `provenance.json`.
