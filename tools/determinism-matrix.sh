#!/usr/bin/env bash
# Determinism matrix — re-derive the page tables in several interpreters and compare.
#
# The committed table (reports/determinism-hashes.txt) is the reference. `check` compares one
# environment against it; this script runs `check` in each environment we can reach, so the
# evidence in reports/RR-DET-*.md can be reproduced with one command.
#
# Host + hash-seed variants always run. Container builds run when docker is available; each
# image is a different CPython build, which is the "cross-build" half of the check.
#
# Usage: bash tools/determinism-matrix.sh
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
PY="${UMPIRE_PY:-python3}"
fail=0

run() {  # label, command...
  local label="$1"; shift
  printf '%-42s ' "$label"
  if out=$("$@" 2>&1); then
    printf 'PASS  %s\n' "$(printf '%s' "$out" | tail -1)"
  else
    printf 'FAIL\n'
    printf '%s\n' "$out" | tail -4 | sed 's/^/    /'
    fail=1
  fi
}

run "host $( "$PY" -c 'import platform,sys;print(platform.python_implementation(), "%d.%d.%d" % sys.version_info[:3])' )" \
    "$PY" spec/determinism.py check
run "host PYTHONHASHSEED=0" env PYTHONHASHSEED=0 "$PY" spec/determinism.py check
run "host PYTHONHASHSEED=12345" env PYTHONHASHSEED=12345 "$PY" spec/determinism.py check

if command -v docker >/dev/null 2>&1; then
  if [ -n "${UMPIRE_MATRIX_IMAGES:-}" ]; then
    read -r -a images <<< "$UMPIRE_MATRIX_IMAGES"
  else
    images=(python:3.12-slim python:3.13-slim)
  fi
  for img in "${images[@]}"; do
    run "docker $img" docker run --rm -v "$PWD":/w:ro -w /w "$img" python spec/determinism.py check
  done
else
  printf '%-42s %s\n' "docker" "skipped (docker not installed)"
fi

if [ "$fail" -ne 0 ]; then
  echo "determinism-matrix: DIVERGENCE in at least one environment" >&2
  exit 1
fi
echo "determinism-matrix: all environments agree with the committed table"
