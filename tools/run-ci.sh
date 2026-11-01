#!/usr/bin/env bash
# tools/run-ci.sh — local mirror of .github/workflows/ci.yml (same steps, same order).
#
# umpire's gates are all offline: no model, no llama-server, no network beyond the
# package installs the CI would do. The LIVE gates (harness/test_m1.py against the pinned
# server, the M2 live gate) are deliberately NOT here — they need a running llama-server and
# belong to a capture session, not to CI.
#
# Usage: bash tools/run-ci.sh
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
PY="${UMPIRE_PY:-python3}"
log() { printf '[umpire-ci] %s\n' "$*"; }

log "gate 1/3: ledger conformance — fixtures"
"$PY" spec/validate_ledger.py \
  fixtures/intent-router.ledger.jsonl fixtures/intent-router.records.jsonl \
  fixtures/support-triage.ledger.jsonl fixtures/support-triage.records.jsonl \
  fixtures/ab-demo.ledger.jsonl fixtures/ab-demo.records.jsonl \
  fixtures/live-demo.ledger.jsonl fixtures/live-demo.records.jsonl

log "gate 1/3: page-table roundtrip — every fixture baseline"
for n in ab-demo intent-router live-demo support-triage; do
  "$PY" spec/validate_ledger.py --replay "fixtures/$n.ledger.jsonl" \
    "fixtures/$n.records.jsonl" "fixtures/$n.page-table.json" >/dev/null
  printf '  roundtrip %-15s BYTE-IDENTICAL\n' "$n"
done

log "gate 1/3: calibration corpora (v2 + v3, raw and calibrated records)"
"$PY" spec/validate_ledger.py calibration/corpus.ledger.jsonl calibration/corpus.records.jsonl >/dev/null
"$PY" spec/validate_ledger.py calibration/corpus.ledger.jsonl calibration/corpus.calibrated.records.jsonl >/dev/null
"$PY" spec/validate_ledger.py calibration/corpus-v3.ledger.jsonl calibration/corpus-v3.records.jsonl >/dev/null
"$PY" spec/validate_ledger.py calibration/corpus-v3.ledger.jsonl calibration/corpus-v3.calibrated.records.jsonl >/dev/null
printf '  corpora: v2 raw+calibrated, v3 raw+calibrated — PASS\n'

log "gate 1/3: calibration checksums"
# the checksum files list repo-root-relative paths — verify from the root
sha256sum -c calibration/checksums.sha256 >/dev/null
sha256sum -c calibration/checksums-v2.sha256 >/dev/null
sha256sum -c calibration/checksums-v3.sha256 >/dev/null
printf '  checksums: v1 + v2 + v3 families OK\n'

log "gate 2/3: unit suites"
"$PY" -m unittest mcp_server_test.py 2>&1 | tail -3
"$PY" -m unittest calibration/test_calibration.py 2>&1 | tail -3
"$PY" -m unittest harness/test_replay_audit.py 2>&1 | tail -3

log "gate 2/3: page-table determinism (committed hash table, this environment)"
"$PY" spec/determinism.py check 2>&1 | tail -2

log "gate 2/3: calibration precision (absolute ECE/Brier with bootstrap CIs)"
"$PY" spec/calibration_ci.py check calibration/cal-precision-v3.json 2>&1 | tail -1

log "gate 2/3: packaging invariants (stdlib-only offline gates)"
"$PY" spec/test_packaging.py 2>&1 | tail -3

log "gate 3/3: hub regen byte-identity"
bash spec/build_hub.sh check 2>&1 | tail -1

log "ALL GATES GREEN"
