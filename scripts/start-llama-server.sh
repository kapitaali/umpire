#!/usr/bin/env bash
# start-llama-server.sh — start (or inspect, or stop) the PINNED umpire model server.
#
# WHY THIS EXISTS: umpire's MCP server can be alive while every decide still fails with
# "connection refused" — the MCP is a thin proxy over a pinned llama-server
# (mcp_server.py: UMPIRE_SERVER_URL, default http://127.0.0.1:8081), and the model server is
# the owner's to run: it holds ~4.5 GB of RAM. After a reboot the process is gone and the
# weights are still on disk, so the restart is one command. This script is that command, and
# it is deliberately strict: it starts the PINNED binary with the PINNED weights, verified by
# hash against harness/pins.json, and it refuses to start anything else.
#
# THE PINNED INVOCATION (harness/pins.json -> qwen3-1.7b -> server.invocation) is used
# verbatim, so a change of pin propagates here instead of drifting in a shell history:
#
#   ./runners/src/build/bin/llama-server -m models/Qwen3-1.7B-Q8_0.gguf -c 2048 \
#       --host 127.0.0.1 --port 8081 --threads 4
#
# Usage:
#   scripts/start-llama-server.sh                 start it (verify the pins, wait for health)
#   scripts/start-llama-server.sh --status        report what is running, exit 0
#   scripts/start-llama-server.sh --stop          stop the server this script started
#   scripts/start-llama-server.sh --skip-hash     start without the hash check (prints UNVERIFIED)
#
# Environment: UMPIRE_READY_TIMEOUT (seconds to wait for health, default 180).
#
# The log and the pid file live under runners/ — which is gitignored by design, like models/
# (the bytes are pinned by hash, not committed).
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

PINS="harness/pins.json"
LOG="runners/llama-server.log"
PIDFILE="runners/llama-server.pid"
MODEL_ID="${UMPIRE_MODEL_ID:-qwen3-1.7b}"
READY_TIMEOUT="${UMPIRE_READY_TIMEOUT:-180}"
URL="${UMPIRE_SERVER_URL:-http://127.0.0.1:8081}"
SKIP_HASH=0

for arg in "$@"; do
  case "$arg" in
    --status)   MODE=status ;;
    --stop)     MODE=stop ;;
    --skip-hash) SKIP_HASH=1 ;;
    -h|--help)  sed -n '2,30p' "$0"; exit 0 ;;
    *) echo "unknown argument: $arg (try --help)" >&2; exit 2 ;;
  esac
done
MODE="${MODE:-start}"

# ---- the pins, read through a dotted path (python3: the repo's interpreter of record) ----
pin() {
  python3 - "$PINS" "$MODEL_ID" "$1" <<'PY'
import json, sys
pins_path, model_id, dotted = sys.argv[1], sys.argv[2], sys.argv[3]
with open(pins_path, encoding="utf-8") as fh:
    node = json.load(fh)[model_id]
for part in dotted.split("."):
    node = node[part]
print(node)
PY
}

sha256_of() { sha256sum "$1" | cut -d" " -f1; }

health() {
  python3 - "$URL" <<'PY'
import sys, urllib.request
try:
    with urllib.request.urlopen(sys.argv[1].rstrip("/") + "/health", timeout=3) as resp:
        sys.exit(0 if resp.status == 200 else 1)
except Exception:
    sys.exit(1)
PY
}

pid_of() { [ -f "$PIDFILE" ] && cat "$PIDFILE" || true; }

running_pid() {
  # Only a pid we recorded AND whose command line is the pinned binary counts as "ours".
  local pid; pid="$(pid_of)"
  [ -n "$pid" ] || return 1
  kill -0 "$pid" 2>/dev/null || return 1
  grep -qa 'llama-server' "/proc/$pid/cmdline" 2>/dev/null || return 1
  printf '%s' "$pid"
}

case "$MODE" in
  status)
    pid="$(running_pid)" || {
      echo "server: NOT running (no pinned llama-server process from $PIDFILE)"
      health && echo "note: $URL answers /health - something else is serving it" || true
      exit 0
    }
    echo "server: running (pid $pid)"
    echo "url:    $URL"
    echo "log:    $REPO_ROOT/$LOG"
    if health; then echo "health: ok"; else echo "health: NOT answering yet"; fi
    exit 0
    ;;
  stop)
    pid="$(running_pid)" || { echo "stop: nothing of ours is running"; exit 0; }
    echo "stop: sending TERM to pid $pid"
    kill "$pid" 2>/dev/null || true
    for _ in $(seq 1 20); do
      kill -0 "$pid" 2>/dev/null || break
      sleep 0.5
    done
    if kill -0 "$pid" 2>/dev/null; then
      echo "stop: pid $pid still alive - leaving it alone rather than escalating"
      exit 1
    fi
    rm -f "$PIDFILE"
    echo "stop: done (log kept at $LOG)"
    exit 0
    ;;
esac

# ---- start ----
INVOCATION="$(pin server.invocation)"
BINARY="$(pin runner.binary)"
WEIGHTS="models/$(pin gguf.file)"
WANT_BIN_SHA="$(pin runner.binary_sha256)"
WANT_W_SHA="$(pin gguf.sha256)"

if health; then
  echo "start: $URL already answers /health - not starting a second server"
  exit 0
fi

if [ "$SKIP_HASH" -eq 0 ]; then
  echo "verify: binary $BINARY"
  [ -x "$BINARY" ] || { echo "verify: FAIL - $BINARY is missing or not executable" >&2; exit 1; }
  got="$(sha256_of "$BINARY")"
  if [ "$got" != "${WANT_BIN_SHA#sha256:}" ]; then
    echo "verify: FAIL - the runner binary's sha256 is ${got:0:16}..., the pin says ${WANT_BIN_SHA#sha256:}" >&2
    echo "verify: a drifted runner is a hard error (AGENTS.md section 1) - rebuild it from the pinned tag" >&2
    exit 1
  fi
  echo "verify: binary sha256 ok (${got:0:16}...)"
  echo "verify: weights $WEIGHTS"
  [ -f "$WEIGHTS" ] || { echo "verify: FAIL - $WEIGHTS is missing (models/ is gitignored; the bytes are pinned by hash)" >&2; exit 1; }
  got="$(sha256_of "$WEIGHTS")"
  if [ "$got" != "${WANT_W_SHA#sha256:}" ]; then
    echo "verify: FAIL - the weights' sha256 is ${got:0:16}..., the pin says ${WANT_W_SHA#sha256:}" >&2
    exit 1
  fi
  echo "verify: weights sha256 ok (${got:0:16}...)"
else
  echo "verify: SKIPPED (--skip-hash) - the binary and the weights are UNVERIFIED against $PINS"
fi

mkdir -p "$(dirname "$LOG")"
echo "start: $INVOCATION"
nohup bash -c "$INVOCATION" >"$LOG" 2>&1 &
pid=$!
echo "$pid" >"$PIDFILE"

deadline=$(( $(date +%s) + READY_TIMEOUT ))
while [ "$(date +%s)" -lt "$deadline" ]; do
  if ! kill -0 "$pid" 2>/dev/null; then
    echo "start: FAIL - the server exited; last lines of $LOG:" >&2
    tail -n 20 "$LOG" >&2 || true
    rm -f "$PIDFILE"
    exit 1
  fi
  if health; then
    echo "start: ready (pid $pid, health ok after $(( READY_TIMEOUT - (deadline - $(date +%s)) ))s)"
    echo "start: url    $URL"
    echo "start: log    $REPO_ROOT/$LOG"
    echo "start: the MCP proxies to it (UMPIRE_SERVER_URL, default $URL)"
    exit 0
  fi
  sleep 1
done

echo "start: FAIL - no health after ${READY_TIMEOUT}s; last lines of $LOG:" >&2
tail -n 20 "$LOG" >&2 || true
exit 1
