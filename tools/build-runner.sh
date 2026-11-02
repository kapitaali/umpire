#!/usr/bin/env bash
# tools/build-runner.sh — reproduce the pinned llama.cpp runner from the recipe
# stamped in harness/pins.json (runner.source/tag + runner.build).
#
# Fail-truthful contract: the resulting binary sha256 is printed and compared
# against the pin's runner.binary_sha256 stamp, and a DEVIATION is reported,
# never silenced. A foreign build environment (gcc/cmake versions) legitimately
# re-hashes; the decide path enforces weights/template/decoding identity, not the
# compiler output. On the machine that owns the stamp (gcc 14.2.0, cmake 4.4.3)
# the reproduce is exact.
#
# Usage: bash tools/build-runner.sh [pin-id]        (default qwen3-1.7b)
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
PIN_ID="${1:-qwen3-1.7b}"

eval "$(python3 - "$PIN_ID" <<'PY'
import json, shlex, sys

pins = json.load(open("harness/pins.json"))
pid = sys.argv[1]
if pid not in pins:
    raise SystemExit(f"unknown pin {pid!r} (configured: {', '.join(sorted(pins))})")
r = pins[pid]["runner"]
build = r["build"].split(";")[0].strip()      # 'cmake Release -D…; gcc …; cmake …'
words = build.split()
if not words or words[0] != "cmake":
    raise SystemExit(f"pins.json runner.build for {pid!r} starts with {words[0] if words else '<empty>'!r} — cannot parse")
flags = ["-DCMAKE_BUILD_TYPE=Release" if w == "Release" else w for w in words[1:]]
joined = ",".join(flags)  # comma-joined scalar; bash splits it into an array (flags have no commas)
source_url = "https://" + r["source"]
for f in flags:
    if "," in f:
        raise SystemExit(f"runner.build flag contains a comma — carrier collision: {f!r}")
print(f"PIN_TAG={shlex.quote(r['tag'])}")
print(f"PIN_SRC={shlex.quote(source_url)}")
print(f"PIN_BINARY={shlex.quote(r['binary'])}")
print(f"PIN_STAMP={shlex.quote(r.get('binary_sha256', ''))}")
print(f"PIN_FLAGS={shlex.quote(joined)}")
print(f"PIN_SERVER={shlex.quote(pins[pid]['server']['invocation'])}")
print(f"PIN_MODEL={shlex.quote(pins[pid]['gguf']['file'])}")
PY
)"

SRC="$ROOT/runners/src"
if [ ! -d "$SRC/.git" ]; then
  echo "[runner] cloning $PIN_SRC at $PIN_TAG (shallow)"
  git clone --depth 1 -b "$PIN_TAG" "$PIN_SRC" "$SRC"
else
  have="$(git -C "$SRC" describe --tags 2>/dev/null || echo unknown)"
  if [ "$have" != "$PIN_TAG" ]; then
    echo "[runner] existing tree is at $have, pin wants $PIN_TAG — fetching and checking out"
    git -C "$SRC" fetch --tags origin "$PIN_TAG"
    git -C "$SRC" checkout "$PIN_TAG"
  else
    echo "[runner] tree already at $PIN_TAG"
  fi
fi

echo "[runner] cmake flags (from harness/pins.json runner.build): $PIN_FLAGS"
IFS=',' read -r -a CFLAGS <<< "$PIN_FLAGS"
cmake -S "$SRC" -B "$SRC/build" "${CFLAGS[@]}"
cmake --build "$SRC/build" --config Release -j "$(nproc)"

BIN="$ROOT/$PIN_BINARY"
if [ ! -x "$BIN" ]; then
  echo "[runner] NOT FOUND: $PIN_BINARY after a successful cmake — build target drift?" >&2
  exit 1
fi
sha="$(sha256sum "$BIN" | cut -d' ' -f1)"
echo "[runner] binary: $PIN_BINARY"
echo "[runner] sha256:  sha256:$sha"
if [ -n "$PIN_STAMP" ] && [ "sha256:$sha" = "$PIN_STAMP" ]; then
  echo "[runner] stamp MATCH — the exact byte identity the pin recorded"
else
  echo "[runner] stamp DEVIATION (stamp: ${PIN_STAMP:-<none>}) — different build "
  echo "         environment; reported, not silenced. decide still stamps "
  echo "         weights/template/decoding identity; the compiler output is not enforced."
fi
echo "[runner] start it (the pinned server invocation):"
echo "         cd $ROOT && $PIN_SERVER"
