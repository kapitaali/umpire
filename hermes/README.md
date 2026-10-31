# Hermes Integration — umpire typed-decision primitive

This directory holds the Hermes Agent side of the umpire decision primitive: the skill, the
setup script, and an example config block. It lets Hermes call umpire's typed decision
(`decide(text, labels) → key + probabilities + confidence`) as native MCP tools.

## Layout

```
hermes/
├── config.yaml.example  # example ~/.hermes/config.yaml mcp_servers.umpire block
├── setup.sh             # discovers paths, writes an env file, prints the config commands
├── skill/
│   └── SKILL.md         # Hermes skill: when to use it, procedure, pitfalls
└── README.md            # this file
```

The MCP server itself is **`mcp_server.py` at the repo root**, not a copy under `hermes/`.
It is a dependency-free stdio JSON-RPC server (standard library only — no MCP SDK, no
`FastMCP`), which is what lets Hermes launch it with its own toolchain interpreter. It
resolves the repo root from its own location, so it finds `harness/umpire.py`,
`spec/validate_ledger.py` and `harness/pins.json` without configuration.

## Tools

The server registers six tools under these names:

| Server name | Hermes-facing name | Purpose |
|---|---|---|
| `decide` | `mcp__umpire__decide` | One typed decision (key + probabilities + confidence) |
| `pins` | `mcp__umpire__pins` | List model pins with hash anchors |
| `health` | `mcp__umpire__health` | Check llama-server health |
| `calibration_info` | `mcp__umpire__calibration_info` | Read calibration artifact metadata |
| `validate` | `mcp__umpire__validate` | Validate a ledger+records pair (xCABI-0.1 validator) |
| `replay` | `mcp__umpire__replay` | Byte-identical roundtrip check (page-table replay) |

`decide` takes `text` (non-empty) and optional `labels`. It does **not** start or stop
llama-server; point it at an already-running pinned runner via `UMPIRE_SERVER_URL`.

## Interpreter requirements

`pins`, `calibration_info` and the server itself need only the standard library. `validate`
and `replay` shell out to `spec/validate_ledger.py`, which imports `jsonschema` — so the
interpreter used for that subprocess needs it. The server resolves one that can import
`jsonschema`: `UMPIRE_PYTHON` if set, else its own interpreter, then `python3`/`python` on
`PATH`. If none has it, the tool returns the validator's exit 2 with
`stderr: deps: pip install jsonschema>=4` rather than pretending to pass.

## Hermes configuration

Add to `~/.hermes/config.yaml` (see `hermes/config.yaml.example`):

```yaml
mcp_servers:
  umpire:
    command: "/path/to/hermes/python"
    args: ["/path/to/umpire/mcp_server.py"]
    env:
      UMPIRE_SERVER_URL: "http://127.0.0.1:8081"
      UMPIRE_LEDGER_DIR: "~/.local/state/umpire/ledgers"
      UMPIRE_PIN: "qwen3-1.7b"
      UMPIRE_SEED: "7"
```

Paths are literal: the Hermes loader interpolates only `${VAR}` and `${env:VAR}`, so a bare
`$VAR` would ship a config that fails at launch. `~` works for `UMPIRE_LEDGER_DIR` because
the server expands it.

After editing `config.yaml`, restart Hermes so it re-spawns the server:
`hermes gateway restart`

## Testing without Hermes

Speak MCP over stdin directly:

```sh
"/path/to/hermes/python" mcp_server.py
# then: initialize → notifications/initialized → tools/list → tools/call
```

A full probe (initialize, tools/list, and a call per tool) is what verified this integration:
`pins`, `calibration_info`, `validate` and `replay` return `ok: true`; `health` and `decide`
return a clean `URLError` when no llama-server is running, which is the correct answer rather
than a crash.

## Related

- `mcp_server.py` — the server (repo root)
- `harness/umpire.py` — the underlying decision library + CLI
- `harness/pins.json` — hash-anchored model pins
- `spec/validate_ledger.py` — xCABI-0.1 validator
- `spec/ledger_tools.py` — minting + page-table derivation
- `projects/hermes-integration.md` — integration plan
- `research/jev-mapping.md` — Jev/ArchiveExplorer → umpire mapping
