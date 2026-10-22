# LOG

| id | type | summary |
|---|---|---|
| RR-M0-20261022 | milestone | umpire M0: decision-ledger v0 as an xCABI-0.1 profile (spec + vendored schema @ ctxvm 361043e + record sidecar schema + minting engine mirroring ident.ts + validator w/ 10-case negative corpus + 2 synthetic golden fixture triplets + committed page-table baselines); gates: PASS (125 checks), negatives 10/10, roundtrip BYTE-IDENTICAL ×2; consumption demo — ctxvm's own validator PASS (67 checks) and rust replay exit 0 ×2 w/ divergences [] + 2×/1× faultOfResidentPage waste rows; found via vendored schema: page.fault carries FLAT pageId vs declare's nested page.id; project repo split from ctxvm/projects/01 (M0 GO by orchestrator) |
