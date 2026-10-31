---
name: umpire-hermes-integration
description: "Call umpire typed decisions through Hermes via MCP server."
version: 0.4.0
author: Hermes Agent
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [umpire, mcp, decision-primitive, integration, typed-decision]
    related_skills: [umpire-pinned-decision-primitive]
---

# umpire-hermes-integration — Hermes → umpire integration

Call umpire's typed-decision primitive (`decide(state, labels) → key + probabilities + confidence`)
from Hermes Agent through the umpire MCP server. The MCP server wraps `harness/umpire.py` and exposes
six tools: `decide`, `pins`, `health`, `calibration_info`, `validate`, `replay`.

This directory lives in the umpire repo at `hermes/`. The MCP server is `mcp_server.py` at the
repo root — a dependency-free stdio JSON-RPC server (standard library only, no MCP SDK), which
is what lets Hermes launch it with its own toolchain interpreter. It resolves the repo root
from its own location, so `harness/umpire.py`, `spec/validate_ledger.py` and
`harness/pins.json` need no configuration.

## When to Use

Use this skill when:

- You want Hermes to make a typed decision (pick one key from a closed label set) with
  grammar-constrained output and an auditable ledger.
- You need reproducible, seeded decisions: same `(state, labels, pin, seed)` ⇒ byte-identical
  response on the same runner/machine.
- You need priced re-decides: identical hash → `page.fault` naming the resident slot; divergent
  hash → fault + second-slot declare (A/B waste semantics).
- You need to validate a ledger or check byte-identical replay via ctxvm.

Do not use for:

- Managing umpire pins, templates, or GGUF downloads (use `umpire-pinned-decision-primitive`).
- Running calibration (M2 — separate workflow).
- Starting/stopping llama-server (umpire manages its own runner; the MCP server assumes it
  is already running).

## Prerequisites

1. **Hermes MCP server configured.** `mcp_servers.umpire` must be set in
   `~/.hermes/config.yaml` (see `hermes config get mcp_servers`). Example config at
   `hermes/config.yaml.example`. Run `hermes/setup.sh` to discover paths.
2. **llama-server running.** Start it before calling `decide` (`hermes/setup.sh` prints the
   resolved repo path; export it first, since a bare `$UMPIRE_REPO` in a copied command
   expands to nothing unless you set it):
   ```sh
   export UMPIRE_REPO=/path/to/umpire
   cd "$UMPIRE_REPO" && ./runners/src/build/bin/llama-server \
     -m models/Qwen3-1.7B-Q8_0.gguf -c 2048 --host 127.0.0.1 --port 8081 --threads 4
   ```
   Wait for `{"status":"ok"}` from `curl http://127.0.0.1:8081/health`.
3. **No MCP SDK needed.** The server is standard library only. (An older FastMCP-based copy
   under `hermes/` was removed — it needed `mcp.server.fastmcp`, could not import under
   Hermes's interpreter, and exposed four tools instead of six.)
4. **`jsonschema` reachable by the validator subprocess.** `validate` and `replay` shell out to
   `spec/validate_ledger.py`, which imports `jsonschema`. The server resolves an interpreter
   that can import it: `UMPIRE_PYTHON` if set, else its own, then `python3`/`python` on `PATH`.
   If none has it, the tool returns the validator's exit 2 with
   `stderr: deps: pip install jsonschema>=4` — an environment fact, not a validation failure.

## How to Run (through Hermes)

Once the MCP server is configured and Hermes is restarted, the six tools appear as
`mcp__umpire__decide`, `mcp__umpire__pins`, `mcp__umpire__health`,
`mcp__umpire__calibration_info`, `mcp__umpire__validate`, `mcp__umpire__replay`
(server-side names: `decide`, `pins`, `health`, `calibration_info`, `validate`, `replay`).
Call them naturally from any Hermes surface (CLI, TUI, desktop, web, gateway):

```
hermes chat -q "Use mcp__umpire__decide to decide whether this message is billing,
technical, sales, or other: 'I was charged twice this month and need it fixed now'"""
```

Or call individual tools directly via the MCP tool interface.

## Quick Reference

| Tool | Purpose | Required args |
|------|---------|---------------|
| `mcp__umpire__decide` | One typed decision | `text`, `labels` |
| `mcp__umpire__pins` | List pins with hash anchors | (none) |
| `mcp__umpire__health` | Check llama-server health | `server_url` (optional) |
| `mcp__umpire__calibration_info` | Read calibration artifact | `artifact` (optional) |
| `mcp__umpire__validate` | Validate ledger+records | `ledger`, `records` |
| `mcp__umpire__replay` | Byte-identical roundtrip check | `ledger`, `records`, `baseline` |

## Procedure

### Step 1 — Verify the MCP server is reachable

```
hermes chat -q "Call mcp__umpire__pins and report the default pin and available pins."
```

Expected: returns the three pins (`qwen3-1.7b`, `qwen3-0.6b`, `qwen2.5-1.5b`) with their
`weights_hash` and `decoding` anchors.

### Step 2 — Check llama-server health

```
hermes chat -q "Call mcp__umpire__health and report whether the server is reachable."
```

Expected: `{"ok": true, "status": 200, ...}` if llama-server is running on port 8081.

### Step 3 — Make a typed decision

```
hermes chat -q "Use mcp__umpire__decide with text='I was charged twice this month and need
it fixed now' and labels=['billing','other','sales','technical'] on the default pin."
```

Expected: returns `key` (one of the labels), `probabilities` (distribution over all labels),
`confidence` (entropy-complement), `ref`, `contentHash`, `model_pin`, `seed`,
`latency_ms`, `ledger`, `records`.

### Step 4 — Exercise priced re-decides (A/B waste semantics)

Re-decide the identical `(text, labels, pin, seed)`:
- Same hash re-land → `page.fault` naming the resident slot (fault only, no new declare).
- Divergent hash (different pin or model) → `page.fault` naming the resident slot +
  `page.declare` second-slot.

Since the xCABI RFC-0.1 adoption, `session.open` carries `schemaVersion: "0.1"` and those
fault rows are `source: "intentional_rerun"`, not `tool_recall` — umpire pages nothing out,
so a deliberate re-decide is a typed re-execution and the resident-fault rule no longer
flags it. A ledger without the version marker must still use `tool_recall`; its validator
branches on the marker, so do not hand-mix the two.

### Step 5 — Validate and replay

```
hermes chat -q "Call mcp__umpire__validate on <ledger-path> and <records-path>."
hermes chat -q "Call mcp__umpire__replay on <ledger-path>, <records-path>, and <baseline>."
```

Expected: `passed: true` / `LEDGER CONFORMANCE: PASS`; `roundtrip: BYTE-IDENTICAL`.

## Pitfalls

- **llama-server must be running.** `mcp__umpire__decide` fails if the pinned runner is
  not reachable at `UMPIRE_SERVER_URL` (default `http://127.0.0.1:8081`). Start it before
  calling `decide`.
- **Template identity is part of the decoding contract.** umpire enforces fail-hard on
  `prompt_template_sha256` mismatch. Do not bypass — template drift silently corrupts
  fixtures while every mechanical gate stays green.
- **Labels must be safe-ASCII single-line uids.** GBNF literals must stay exact; labels
  with control characters or non-ASCII are rejected.
- **One llama-server at a time.** RAM constraints — stop server A before starting server B;
  do not run both concurrently.
- **Calibration is opt-in and strict.** A calibration artifact must match the pin, seed,
  and label set exactly, or `decide()` fails before any model call. When the artifact is
  missing, call `decide` with `calibrate=false` to get raw confidence only.
- **MCP config interpolates `${VAR}` only — a bare `$VAR` stays literal.** Hermes resolves
  `${VAR}` and `${env:VAR}` in `mcp_servers` values; `$UMPIRE_REPO` in a `command`, `args`,
  `cwd` or `env` entry is passed through as the four-character string `$UMP…` and the
  launch fails with a confusing not-found. Write `${UMPIRE_REPO}/mcp_server.py` (with the
  variable actually exported) or an absolute `/path/to/umpire/mcp_server.py`. `~` is also
  literal to Hermes — it works only where the target program expands it itself
  (`mcp_server.py` calls `expanduser()` on `UMPIRE_LEDGER_DIR`, not on
  `UMPIRE_CALIBRATION`).
- **MCP server is stdio — Hermes spawns it.** Do not run `mcp_server.py` directly from the
  shell and expect tools to register. Use `manage_connections enable` for the `umpire` MCP
  server; Hermes launches it with the interpreter from `mcp_servers.umpire.command`.
- **jsonschema on the MCP server interpreter.** `validate` and `replay` fail with exit code
  2 if `jsonschema>=4` is missing from the interpreter configured as
  `mcp_servers.umpire.command`. Install it there, or run the validator directly:
  `hermes_python spec/validate_ledger.py <ledger> <records>` (or `--replay <ledger> <records>
  <baseline>`).
- **This skill has two homes, and they drift.** The authoritative copy is
  `hermes/skill/SKILL.md` in the umpire repo; `~/.hermes/skills/umpire-hermes-integration/`
  is the installed copy an agent actually loads. Edit the repo copy first, then re-install
  it and prove the result with `diff -q` — an installed copy dozens of lines behind still
  loads happily and still teaches the stale absolute paths.
- **3-minute hard interrupt per cron run.** Do not schedule long decision batches in cron;
  use `delegate_task` for longer work.

## Verification

- `mcp__umpire__pins` returns all three pins with correct `weights_hash` and `decoding`.
- `mcp__umpire__health` returns `ok: true`.
- `mcp__umpire__decide` returns `key ∈ labels`, `probabilities` summing to 1, `confidence ∈ [0,1]`.
- `mcp__umpire__validate` returns `passed: true` and `exit_code: 0` when `jsonschema>=4`
  is available on the MCP server interpreter.
- `mcp__umpire__replay` returns `byte_identical: true` and `exit_code: 0` under the same
  dependency condition. Direct run with Hermes's python is the fallback.
- Repeated identical decides return identical `contentHash` (determinism).
- `mcp__umpire__decide` ledger/records paths exist and contain valid JSONL.
