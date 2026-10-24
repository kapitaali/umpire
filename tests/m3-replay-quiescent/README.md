# tests/m3-replay-quiescent — the Oct-24 10-row quiescent replay round (M3)

Committed record of the second M3 audit round (the "other test round" beside
`tests/m3-replay-full`): the 2026-10-24 quiescent re-decides of exactly the 10
distribution-only rows found by the Oct-23 full 480-row audit (`reports/RR-M3FULL-20261024.md`).

Contents (copied verbatim from the durable work dir `~/.local/state/umpire/m3/`):

| artifact | round | what it is |
|---|---|---|
| `first-quiescent.json` | 2026-10-24 ~08:40Z | complete JSON output of the first dedicated quiescent 10-row replay pass |
| `replay-10b/replay.{ledger,records}.jsonl` | 2026-10-24 ~08:37Z (partial, interrupted) | 3 rows / 4 ledger events — the interrupted second sample |
| `replay-10c/replay.{ledger,records}.jsonl` | 2026-10-24 ~08:38Z | full second sample: 10 rows / 11 ledger events |

Why this round mattered: re-deciding the 10 suspect rows on a freshly restarted
pinned server produced distributions **bit-identical 10/10** to the Oct-23 audit
actuals (3/3 among the interrupted partial), across two server instances —
which excluded audit-run load and pinned the attribution on **capture-time
concurrency** of the v2 capture. The 2026-10-24 quiescent v3 recapture
(`reports/RR-M3V3-20261024.md`) then reproduced all 10 of these values again in a
full 480-row run, and its capture-time page-table hashes byte-identical
(`sha256:25b073cbc…`) to the committed `tests/m3-replay-full` roundtrip.

Format is the same as every replay sample: `replay.ledger.jsonl` = xCABI-0.1
profile lines, `replay.records.jsonl` = `decide-record/0.1` payload lines. The
first attempt (`replay-10/`, emptied before this copy) is not retained; its
superseded partial-parsing mystery is moot per RR-M3FULL.
