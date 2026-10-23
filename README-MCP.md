# umpire MCP server (OpenCode + Hermes)

`mcp_server.py` is a dependency-free local stdio MCP server that exposes the
umpire typed-decision primitive to agent harnesses.  It wraps the same
`harness/umpire.py` library used by the CLI; it is not a second implementation.

The server is the common integration surface for **OpenCode V2** and
**Hermes Agent**.  Both launch it over stdio and discover the same tools.

## Tools

| tool | purpose |
|---|---|
| `decide` | one decision: `key`, raw `probabilities`, raw `confidence`, optional `calibrated.confidence`, `ref`, `contentHash`, ledger paths |
| `pins` | configured pins with model/weights/runner/template identities |
| `health` | check the pinned `llama-server` health endpoint |
| `calibration_info` | identity and test metrics of a fitted artifact (read-only) |
| `validate` | run the xCABI-0.1 validator, including negative and calibrated-record gates |
| `replay` | re-derive the page table and compare it byte-for-byte to a baseline |

OpenCode names tools `<server>_<tool>` (for example `umpire_decide`); Hermes
names them `mcp_<server>_<tool>` (for example `mcp_umpire_decide`).

## Prerequisites

1. A pinned runner is already running.  The default is
   `http://127.0.0.1:8081` (Qwen3-1.7B, pinned in `harness/pins.json`).
2. A fitted calibration artifact is optional.  The support-domain corpus
   artifact is `calibration/umpire-cal-v2.json` (the v1 artifact remains for
   history).  Calibration is **per pin, seed, label set, and distribution**;
   the harness refuses a mismatched artifact before calling the model.
3. Ledger output directory (default `~/.local/state/umpire/ledgers`).  The
   server creates it; ledgers are append-only.

The MCP server never starts or stops `llama-server`.  Start the pinned runner
separately (see `README.md`), or point `UMPIRE_SERVER_URL` at another compatible
pinned endpoint.

## Run standalone

```sh
cd /home/theb/Documents/umpire
UMPIRE_SERVER_URL=http://127.0.0.1:8081 \
UMPIRE_CALIBRATION=calibration/umpire-cal-v2.json \
UMPIRE_LABELS=billing,other,sales,technical \
python3 mcp_server.py
```

The process speaks newline-delimited JSON-RPC on stdin/stdout and logs to
stderr.  A minimal smoke test:

```sh
printf '%s\n' \
  '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"smoke","version":"0"}}}' \
  '{"jsonrpc":"2.0","method":"notifications/initialized"}' \
  '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' |
  python3 mcp_server.py
```

Do not call `decide` while a calibration capture is running; the pinned
llama-server serves one request at a time.

## Tests

```sh
python3 -m unittest mcp_server_test.py -v
# 7 tests ... OK
python3 -m unittest harness/test_replay_audit.py -v
# 2 tests ... OK (offline comparison logic)
```

`mcp_server_test.py` includes a fake-runner `decide` test: it starts a tiny local
HTTP server that returns a valid constrained response, exercises the MCP
`tools/call` envelope, checks the calibrated sidecar and `calibrate:false`, and
verifies the ledger/records files.  No model request is made.

## OpenCode V2

Add the server under `mcp.servers` (V2 does **not** put server names directly
under `mcp`).  Use the CLI or edit configuration.

### CLI

```sh
opencode mcp add umpire --global \
  --env UMPIRE_SERVER_URL=http://127.0.0.1:8081 \
  --env UMPIRE_CALIBRATION=/home/theb/Documents/umpire/calibration/umpire-cal-v2.json \
  --env UMPIRE_LABELS=billing,other,sales,technical \
  --env UMPIRE_LEDGER_DIR=/home/theb/.local/state/umpire/ledgers \
  -- python3 /home/theb/Documents/umpire/mcp_server.py
opencode mcp list
```

### Manual global config

`~/.config/opencode/opencode.jsonc`:

```jsonc
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "servers": {
      "umpire": {
        "type": "local",
        "command": ["python3", "/home/theb/Documents/umpire/mcp_server.py"],
        "cwd": "/home/theb/Documents/umpire",
        "environment": {
          "UMPIRE_SERVER_URL": "http://127.0.0.1:8081",
          "UMPIRE_CALIBRATION": "/home/theb/Documents/umpire/calibration/umpire-cal-v2.json",
          "UMPIRE_LABELS": "billing,other,sales,technical",
          "UMPIRE_LEDGER_DIR": "/home/theb/.local/state/umpire/ledgers",
          "UMPIRE_PIN": "qwen3-1.7b",
          "UMPIRE_SEED": "7",
          "UMPIRE_TIMEOUT": "300"
        },
        "timeout": {
          "startup": 30000,
          "catalog": 30000,
          "execution": 360000
        }
      }
    }
  }
}
```

The repository also ships a project-scoped `opencode.jsonc` using relative
paths; it applies when OpenCode opens this repository.  A higher-precedence
project entry replaces the same server object, so keep the fields complete if
you override the global entry.

Verify in a running OpenCode session (or via `/mcps`).  Under the default Code
Mode, call it as `tools.umpire.decide(...)`; with `codemode: false`, the tool is
`umpire_decide`.

In this build `opencode mcp list` can start a short-lived server, close stdio,
and report `Connection closed`; that is a CLI quirk, not proof the configured
server is broken.  The live session discovery and a Code Mode call are the
authoritative checks.

The same build has also been observed to drop an idle local server after roughly
one to two minutes; touching the config (or restarting the session) reconnects it.
The server itself is long-lived and exits cleanly on stdin EOF.

## Hermes Agent

Hermes reads `mcp_servers` from `~/.hermes/config.yaml`.  Add the server with the
CLI (it performs discovery) or edit YAML.

### CLI

```sh
hermes mcp add umpire \
  --command python3 \
  --env UMPIRE_SERVER_URL=http://127.0.0.1:8081 \
        UMPIRE_CALIBRATION=/home/theb/Documents/umpire/calibration/umpire-cal-v2.json \
        UMPIRE_LABELS=billing,other,sales,technical \
        UMPIRE_LEDGER_DIR=/home/theb/.local/state/umpire/ledgers \
        UMPIRE_PIN=qwen3-1.7b UMPIRE_SEED=7 UMPIRE_TIMEOUT=300 \
  --args /home/theb/Documents/umpire/mcp_server.py
hermes mcp test umpire
hermes mcp list
```

### YAML

```yaml
mcp_servers:
  umpire:
    command: "python3"
    args: ["/home/theb/Documents/umpire/mcp_server.py"]
    env:
      UMPIRE_SERVER_URL: "http://127.0.0.1:8081"
      UMPIRE_CALIBRATION: "/home/theb/Documents/umpire/calibration/umpire-cal-v2.json"
      UMPIRE_LABELS: "billing,other,sales,technical"
      UMPIRE_LEDGER_DIR: "/home/theb/.local/state/umpire/ledgers"
      UMPIRE_PIN: "qwen3-1.7b"
      UMPIRE_SEED: "7"
      UMPIRE_TIMEOUT: "300"
    timeout: 300
```

After adding, restart Hermes (or the gateway) so it discovers the tools.  The
tool names are `mcp_umpire_decide`, `mcp_umpire_pins`, `mcp_umpire_health`,
`mcp_umpire_calibration_info`, `mcp_umpire_validate`, and
`mcp_umpire_replay`.

## Example decision

```text
Use umpire_decide with labels ["billing","other","sales","technical"] and text:
"Our webhook stopped delivering events after the deploy."
```

The result is a typed key in the label set, the raw constrained distribution,
the raw entropy-complement confidence, and (if configured) the calibrated
P(key correct) sidecar.  The ledger/records paths are returned for audit.

Calibration is per call and opt-in: pass `calibrate: false` (or omit the
`calibration` argument and leave `UMPIRE_CALIBRATION` unset) when using a
different label set.  Do not let a default artifact silently apply to a new
domain; the harness would reject the label mismatch anyway.

## Calibration scope

The checked-in v2 artifact is fitted to the four support labels on the
project-authored support corpus.  Passing a different label set while that
artifact is configured fails hard; this is intentional.  For an agent-specific
label set, fit a separate artifact with `calibration/capture.py` and
`calibration/fit.py`, then point `UMPIRE_CALIBRATION` at it.  Do not reuse the
support artifact across domains.

Raw `probabilities`, `confidence`, `ref`, and `contentHash` are never changed by
calibration; the calibrated view is a sidecar estimate, not a correctness
guarantee.

## Security / lifecycle

- Stdio only: no listening port, no OAuth, no network credentials.
- The server inherits only the environment explicitly passed by the harness.
- State text can contain secrets; ledgers are written under
  `UMPIRE_LEDGER_DIR` and are append-only.  Choose a private location.
- The server assumes a trusted local user and a trusted local runner.  Do not
  expose it through a remote transport without authentication and a retention
  policy.
- One MCP process serializes `decide` calls with a lock.  Multiple server
  processes against the same ledger file are not coordinated; use one process
  per ledger directory or separate directories.
