#!/usr/bin/env python3
"""Local stdio MCP server for the umpire typed-decision primitive.

This is intentionally dependency-free (Python stdlib only) so OpenCode and
Hermes can launch it without installing the MCP SDK.  It speaks the classic
newline-delimited JSON-RPC MCP handshake and exposes:

  decide             one typed decision (key + probabilities + confidence)
  pins               configured model pins and identities
  health             check the pinned llama-server
  calibration_info   metadata for a fitted calibration artifact
  validate           run the xCABI-0.1 validator on a ledger/records pair
  replay             run the deterministic page-table replay gate

The server does not start or stop llama-server.  Point it at an already-running
pinned runner with UMPIRE_SERVER_URL (default http://127.0.0.1:8081).

Environment:
  UMPIRE_SERVER_URL    llama-server base URL
  UMPIRE_PIN           default pin id (qwen3-1.7b)
  UMPIRE_SEED          default seed (7)
  UMPIRE_CALIBRATION   optional default calibration artifact path
  UMPIRE_LABELS        optional comma-separated default labels
  UMPIRE_LEDGER_DIR    ledger/records output directory
                       (default ~/.local/state/umpire/ledgers)
  UMPIRE_TIMEOUT       decision timeout seconds (default 180)

Security: local stdio server, no network listener.  State text can contain
secrets; ledgers are written under UMPIRE_LEDGER_DIR.  Calibration is opt-in and
must match pin, seed, and label set; the harness fails hard otherwise.
"""

import datetime as dt
import json
import os
import pathlib
import sys
import threading
import traceback
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "harness"))
sys.path.insert(0, str(ROOT / "spec"))

import calibration_tools as cal  # noqa: E402
import umpire  # noqa: E402

SERVER_NAME = "umpire"
SERVER_VERSION = "0.1.0"
SERVER_URL = os.environ.get("UMPIRE_SERVER_URL", "http://127.0.0.1:8081").rstrip("/")
DEFAULT_PIN = os.environ.get("UMPIRE_PIN", "qwen3-1.7b")
DEFAULT_SEED = int(os.environ.get("UMPIRE_SEED", "7"))
DEFAULT_CALIBRATION = os.environ.get("UMPIRE_CALIBRATION") or None
DEFAULT_LABELS = [
    label.strip()
    for label in os.environ.get("UMPIRE_LABELS", "").split(",")
    if label.strip()
]
DEFAULT_LEDGER_DIR = pathlib.Path(
    os.environ.get("UMPIRE_LEDGER_DIR", str(pathlib.Path.home() / ".local/state/umpire/ledgers"))
).expanduser()
DEFAULT_TIMEOUT = float(os.environ.get("UMPIRE_TIMEOUT", "180"))
DEBUG_LOG = os.environ.get("UMPIRE_MCP_DEBUG_LOG") or None
DECIDE_LOCK = threading.Lock()

INSTRUCTIONS = (
    "umpire is a local typed-decision primitive. Call the decide tool with a state "
    "text and a label set; the pinned local model returns exactly one key from the "
    "labels plus raw constrained probabilities and an entropy-complement confidence. "
    "Raw probabilities/identity are authoritative. If a calibration artifact is "
    "configured, the record also carries calibrated.confidence = fitted P(key is "
    "correct) for that pin/seed/label set/distribution; it is not a correctness "
    "guarantee and is opt-in. Use pins to inspect pin identities, "
    "health before a batch, and validate/replay on ledgers."
)

TOOLS = [
    {
        "name": "decide",
        "description": (
            "Make one typed decision with the pinned local model. Returns key, raw "
            "probabilities, raw entropy-complement confidence, optional calibrated "
            "P(key correct), ref/contentHash, and the ledger/records paths."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "minLength": 1,
                    "description": "Opaque state text to decide on.",
                },
                "labels": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 2,
                    "maxItems": 255,
                    "description": (
                        "Candidate label keys. Defaults to UMPIRE_LABELS if set; "
                        "calibration, when present, requires an exact label-set match."
                    ),
                },
                "pin": {"type": "string", "description": "Pin id (default UMPIRE_PIN)."},
                "seed": {"type": "integer", "description": "Decoding seed (default UMPIRE_SEED)."},
                "calibration": {
                    "type": "string",
                    "description": "Calibration artifact path (default UMPIRE_CALIBRATION).",
                },
                "calibrate": {
                    "type": "boolean",
                    "description": (
                        "Apply the default calibration artifact. Set false for a different "
                        "label set or when you want raw confidence only."
                    ),
                },
                "server_url": {
                    "type": "string",
                    "description": "llama-server base URL (default UMPIRE_SERVER_URL).",
                },
                "ledger_dir": {
                    "type": "string",
                    "description": "Directory for ledger/records files (default UMPIRE_LEDGER_DIR).",
                },
                "timeout": {
                    "type": "number",
                    "exclusiveMinimum": 0,
                    "description": "Model request timeout seconds (default UMPIRE_TIMEOUT).",
                },
            },
            "required": ["text"],
            "additionalProperties": False,
        },
    },
    {
        "name": "pins",
        "description": "List configured model pins with hashes and the default pin.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "health",
        "description": "Check that the pinned llama-server health endpoint is reachable.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "server_url": {"type": "string", "description": "Base URL to check."},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "calibration_info",
        "description": (
            "Read a fitted calibration artifact's identity and test metrics. "
            "Does not modify or apply it."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "artifact": {
                    "type": "string",
                    "description": "Artifact path (default UMPIRE_CALIBRATION).",
                },
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "validate",
        "description": (
            "Run the xCABI-0.1 validator on a ledger/records pair, including the "
            "built-in negative gates and calibrated-record cross-checks."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "ledger": {"type": "string", "description": "Ledger JSONL path."},
                "records": {"type": "string", "description": "Records JSONL path."},
            },
            "required": ["ledger", "records"],
            "additionalProperties": False,
        },
    },
    {
        "name": "replay",
        "description": "Re-derive the page table and compare it byte-for-byte to a baseline.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "ledger": {"type": "string", "description": "Ledger JSONL path."},
                "records": {"type": "string", "description": "Records JSONL path."},
                "baseline": {"type": "string", "description": "Page-table baseline path."},
            },
            "required": ["ledger", "records", "baseline"],
            "additionalProperties": False,
        },
    },
]


def log(message):
    print(f"[umpire-mcp] {message}", file=sys.stderr, flush=True)


def debug(event, **fields):
    if not DEBUG_LOG:
        return
    try:
        row = {"ts": dt.datetime.now(dt.timezone.utc).isoformat(), "event": event}
        row.update(fields)
        with open(DEBUG_LOG, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=True) + "\n")
    except Exception as exc:  # debug logging must never break the server
        log(f"debug log failed: {exc}")


def _tool_result(value, is_error=False):
    if isinstance(value, str):
        text = value
        structured = {"result": value}
    else:
        text = json.dumps(value, indent=2, sort_keys=True)
        structured = value if isinstance(value, dict) else {"result": value}
    return {
        "content": [{"type": "text", "text": text}],
        "structuredContent": structured,
        "isError": is_error,
    }


def _error(message):
    return _tool_result({"ok": False, "error": message}, is_error=True)


def _load_artifact(path):
    p = pathlib.Path(path).expanduser()
    if not p.exists():
        raise ValueError(f"calibration artifact not found: {p}")
    artifact = json.loads(p.read_text())
    failures = cal.validate_artifact(artifact)
    if failures:
        raise ValueError("calibration artifact invalid: " + "; ".join(failures))
    return artifact


def _resolve_labels(args):
    labels = args.get("labels")
    if labels is None:
        labels = DEFAULT_LABELS
    if not labels:
        raise ValueError("labels are required (pass labels or set UMPIRE_LABELS)")
    if not isinstance(labels, list) or not all(isinstance(x, str) for x in labels):
        raise ValueError("labels must be an array of strings")
    if len(set(labels)) != len(labels):
        raise ValueError("labels must be unique")
    return labels


def _ledger_paths(ledger_dir):
    directory = pathlib.Path(ledger_dir).expanduser()
    directory.mkdir(parents=True, exist_ok=True)
    day = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d")
    return directory / f"umpire-{day}.ledger.jsonl", directory / f"umpire-{day}.records.jsonl"


def _server_health(server_url):
    url = server_url.rstrip("/") + "/health"
    with urllib.request.urlopen(url, timeout=5) as resp:
        body = resp.read().decode("utf-8", errors="replace")
        return {"ok": resp.status == 200, "status": resp.status, "url": url, "body": body[:500]}


def tool_decide(args):
    text = args.get("text")
    if not isinstance(text, str) or not text.strip():
        raise ValueError("text must be a non-empty string")
    labels = _resolve_labels(args)
    pin = args.get("pin") or DEFAULT_PIN
    if pin not in umpire.PINS:
        raise ValueError(f"unknown pin {pin!r}; available: {sorted(umpire.PINS)}")
    seed = int(args.get("seed", DEFAULT_SEED))
    timeout = float(args.get("timeout", DEFAULT_TIMEOUT))
    server_url = args.get("server_url") or SERVER_URL
    calibrate = args.get("calibrate", True)
    if calibrate is False:
        calibration_path = None
    else:
        calibration_path = args.get("calibration") or DEFAULT_CALIBRATION
    artifact = _load_artifact(calibration_path) if calibration_path else None
    ledger, records = _ledger_paths(args.get("ledger_dir") or DEFAULT_LEDGER_DIR)

    with DECIDE_LOCK:
        response, record = umpire.decide(
            server_url,
            {"text": text},
            labels,
            pin,
            seed,
            ledger,
            records,
            timeout=timeout,
            calibration=artifact,
        )
    return {
        "ok": True,
        "key": response["key"],
        "probabilities": response["probabilities"],
        "confidence": response["confidence"],
        "calibrated": record.get("calibrated"),
        "ref": record["ref"],
        "contentHash": record["contentHash"],
        "model_pin": record["model_pin"],
        "labels": labels,
        "seed": seed,
        "latency_ms": record.get("latency_ms"),
        "ledger": str(ledger),
        "records": str(records),
    }


def tool_pins(_args):
    pins = []
    for pin_id, pin in umpire.PINS.items():
        pins.append(
            {
                "id": pin_id,
                "model": pin.get("model"),
                "weights_hash": pin.get("weights_hash"),
                "decoding": pin.get("decoding"),
                "prompt_template_sha256": pin.get("prompt_template_sha256"),
                "template": pin.get("template"),
                "default": pin_id == DEFAULT_PIN,
            }
        )
    return {"ok": True, "default_pin": DEFAULT_PIN, "pins": pins}


def tool_health(args):
    return _server_health(args.get("server_url") or SERVER_URL)


def tool_calibration_info(args):
    path = args.get("artifact") or DEFAULT_CALIBRATION
    if not path:
        raise ValueError("artifact required (pass artifact or set UMPIRE_CALIBRATION)")
    artifact = _load_artifact(path)
    test = (artifact.get("metrics") or {}).get("test") or {}
    return {
        "ok": True,
        "path": str(pathlib.Path(path).expanduser()),
        "schema": artifact.get("schema"),
        "data_version": artifact.get("data_version"),
        "created": artifact.get("created"),
        "method": artifact.get("method"),
        "confidence_statistic": artifact.get("confidence_statistic"),
        "labels": artifact.get("labels"),
        "seed": artifact.get("seed"),
        "pin": artifact.get("pin"),
        "corpus": artifact.get("corpus"),
        "test": {
            "n": test.get("n"),
            "accuracy": test.get("accuracy"),
            "calibrated": test.get("calibrated"),
            "raw_entropy": test.get("raw_entropy"),
            "raw_top_probability": test.get("raw_top_probability"),
            "bootstrap_delta_ece_vs_raw_top": test.get("bootstrap_delta_ece_vs_raw_top"),
            "bootstrap_delta_brier_vs_raw_top": test.get("bootstrap_delta_brier_vs_raw_top"),
        },
        "limitations": artifact.get("limitations"),
    }


def _run_validator(args, replay=False):
    ledger = args.get("ledger")
    records = args.get("records")
    if not ledger or not records:
        raise ValueError("ledger and records are required")
    cmd = [sys.executable, str(ROOT / "spec" / "validate_ledger.py")]
    if replay:
        baseline = args.get("baseline")
        if not baseline:
            raise ValueError("baseline is required for replay")
        cmd += ["--replay", str(ledger), str(records), str(baseline)]
    else:
        cmd += [str(ledger), str(records)]
    import subprocess

    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    return {
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
        "stdout": proc.stdout.strip(),
        "stderr": proc.stderr.strip(),
    }


def tool_validate(args):
    return _run_validator(args, replay=False)


def tool_replay(args):
    return _run_validator(args, replay=True)


HANDLERS = {
    "decide": tool_decide,
    "pins": tool_pins,
    "health": tool_health,
    "calibration_info": tool_calibration_info,
    "validate": tool_validate,
    "replay": tool_replay,
}


def _initialize(params):
    requested = (params or {}).get("protocolVersion") or "2024-11-05"
    supported = {
        "2024-11-05",
        "2025-03-26",
        "2025-06-18",
        "2025-11-25",
    }
    version = requested if requested in supported else "2024-11-05"
    return {
        "protocolVersion": version,
        "capabilities": {"tools": {"listChanged": False}},
        "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        "instructions": INSTRUCTIONS,
    }


def handle_request(req):
    method = req.get("method")
    params = req.get("params") or {}
    req_id = req.get("id")
    is_notification = "id" not in req

    if method == "initialize":
        return _initialize(params)
    if method in ("notifications/initialized", "notifications/cancelled"):
        return None
    if method == "ping":
        return {}
    if method == "tools/list":
        return {"tools": TOOLS}
    if method == "tools/call":
        name = params.get("name")
        args = params.get("arguments") or {}
        if name not in HANDLERS:
            raise ValueError(f"unknown tool {name!r}")
        if not isinstance(args, dict):
            raise ValueError("tool arguments must be an object")
        try:
            return _tool_result(HANDLERS[name](args))
        except Exception as exc:
            log(f"tool {name} failed: {type(exc).__name__}: {exc}")
            return _error(f"{type(exc).__name__}: {exc}")
    if method in ("resources/list", "resources/templates/list"):
        return {"resources": []} if method == "resources/list" else {"resourceTemplates": []}
    if method == "prompts/list":
        return {"prompts": []}
    if method == "logging/setLevel":
        return {}
    if is_notification:
        return None
    raise ValueError(f"method not found: {method}")


def _write(obj):
    sys.stdout.write(json.dumps(obj, separators=(",", ":"), ensure_ascii=True) + "\n")
    sys.stdout.flush()


def main():
    log(f"starting {SERVER_NAME} {SERVER_VERSION}; server={SERVER_URL} pin={DEFAULT_PIN}")
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError as exc:
            log(f"invalid JSON: {exc}")
            _write(
                {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {"code": -32700, "message": f"parse error: {exc}"},
                }
            )
            continue

        req_id = req.get("id")
        debug("request", method=req.get("method"), id=req_id)
        try:
            result = handle_request(req)
        except Exception as exc:  # keep the process alive; tool errors are content
            log(f"error handling {req.get('method')}: {exc}")
            if "id" in req:
                _write(
                    {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "error": {"code": -32603, "message": str(exc)},
                    }
                )
            continue

        if result is None or "id" not in req:
            continue
        debug("response", method=req.get("method"), id=req_id, result=result)
        _write({"jsonrpc": "2.0", "id": req_id, "result": result})


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
    except Exception:
        traceback.print_exc(file=sys.stderr)
        raise
