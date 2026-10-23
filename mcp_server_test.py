#!/usr/bin/env python3
"""Protocol-level tests for mcp_server.py (no model request)."""

import json
import os
import pathlib
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = pathlib.Path(__file__).resolve().parent


def call_sequence(messages, env_extra=None, timeout=60):
    env = dict(os.environ)
    env.setdefault("UMPIRE_LEDGER_DIR", tempfile.mkdtemp(prefix="umpire-mcp-test-"))
    if env_extra:
        env.update(env_extra)
    payload = "".join(json.dumps(message, separators=(",", ":")) + "\n" for message in messages)
    proc = subprocess.run(
        [sys.executable, str(ROOT / "mcp_server.py")],
        input=payload,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
    )
    replies = []
    for line in proc.stdout.splitlines():
        line = line.strip()
        if line:
            replies.append(json.loads(line))
    return proc, replies


class MCPServerTests(unittest.TestCase):
    def setUp(self):
        self.init = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "mcp-server-test", "version": "0"},
            },
        }
        self.initialized = {"jsonrpc": "2.0", "method": "notifications/initialized"}

    def test_tools_list_and_pins_result_shape(self):
        messages = [
            self.init,
            self.initialized,
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "pins", "arguments": {}},
            },
        ]
        proc, replies = call_sequence(messages)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        by_id = {reply.get("id"): reply for reply in replies}
        tools = by_id[2]["result"]["tools"]
        self.assertEqual(
            [tool["name"] for tool in tools],
            ["decide", "pins", "health", "calibration_info", "validate", "replay"],
        )
        result = by_id[3]["result"]
        self.assertIn("content", result)
        self.assertFalse(result["isError"])
        self.assertEqual(result["structuredContent"]["ok"], True)
        self.assertEqual(result["structuredContent"]["default_pin"], "qwen3-1.7b")
        self.assertTrue(result["structuredContent"]["pins"])

    def test_decide_input_error_is_tool_error(self):
        messages = [
            self.init,
            self.initialized,
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "decide", "arguments": {}},
            },
        ]
        proc, replies = call_sequence(messages)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        result = [r for r in replies if r.get("id") == 2][0]["result"]
        self.assertTrue(result["isError"])
        self.assertIn("text must be a non-empty string", result["content"][0]["text"])

    def test_validate_fixture(self):
        messages = [
            self.init,
            self.initialized,
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "validate",
                    "arguments": {
                        "ledger": str(ROOT / "fixtures/intent-router.ledger.jsonl"),
                        "records": str(ROOT / "fixtures/intent-router.records.jsonl"),
                    },
                },
            },
        ]
        proc, replies = call_sequence(messages)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        result = [r for r in replies if r.get("id") == 2][0]["result"]
        self.assertFalse(result["isError"])
        self.assertEqual(result["structuredContent"]["ok"], True)
        self.assertIn("PASS", result["structuredContent"]["stdout"])

    def test_decide_with_fake_runner_and_calibration(self):
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length", "0"))
                self.rfile.read(length)
                payload = {
                    "content": json.dumps({"key": "billing"}),
                    "completion_probabilities": [
                        {
                            "token": "b",
                            "top_logprobs": [
                                {"token": "billing", "logprob": -0.1},
                                {"token": "other", "logprob": -1.2},
                                {"token": "sales", "logprob": -1.6},
                                {"token": "technical", "logprob": -2.1},
                            ],
                        }
                    ],
                    "timings": {"prompt_ms": 11.0, "predicted_ms": 22.0},
                }
                data = json.dumps(payload).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):
                pass

        artifact = ROOT / "calibration/umpire-cal-v1.json"
        if not artifact.exists():
            self.skipTest("v1 artifact not present")
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            ledger_dir = tempfile.mkdtemp(prefix="umpire-mcp-fake-")
            label_list = ["billing", "other", "sales", "technical"]
            messages = [
                self.init,
                self.initialized,
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {
                        "name": "decide",
                        "arguments": {
                            "text": "Our invoice has a duplicate charge.",
                            "labels": label_list,
                        },
                    },
                },
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/call",
                    "params": {
                        "name": "decide",
                        "arguments": {
                            "text": "Our invoice has a duplicate charge.",
                            "labels": label_list,
                            "calibrate": False,
                        },
                    },
                },
            ]
            proc, replies = call_sequence(
                messages,
                env_extra={
                    "UMPIRE_SERVER_URL": f"http://127.0.0.1:{server.server_address[1]}",
                    "UMPIRE_CALIBRATION": str(artifact),
                    "UMPIRE_LEDGER_DIR": ledger_dir,
                    "UMPIRE_SEED": "7",
                },
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            by_id = {reply.get("id"): reply for reply in replies}
            calibrated = by_id[2]["result"]
            raw = by_id[3]["result"]
            self.assertFalse(calibrated["isError"])
            self.assertFalse(raw["isError"])
            c = calibrated["structuredContent"]
            r = raw["structuredContent"]
            self.assertEqual(c["key"], "billing")
            self.assertEqual(c["calibrated"]["data_version"], "cal-v1:caf6a04f9e94b9a2")
            self.assertIsNone(r["calibrated"])
            self.assertEqual(c["contentHash"], r["contentHash"])
            self.assertEqual(c["probabilities"], r["probabilities"])
            self.assertTrue(pathlib.Path(c["ledger"]).exists())
            self.assertTrue(pathlib.Path(c["records"]).exists())
        finally:
            server.shutdown()
            server.server_close()

    def test_calibration_info_v1(self):
        artifact = ROOT / "calibration/umpire-cal-v1.json"
        if not artifact.exists():
            self.skipTest("v1 artifact not present")
        messages = [
            self.init,
            self.initialized,
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "calibration_info",
                    "arguments": {"artifact": str(artifact)},
                },
            },
        ]
        proc, replies = call_sequence(messages)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        result = [r for r in replies if r.get("id") == 2][0]["result"]
        self.assertFalse(result["isError"])
        self.assertEqual(
            result["structuredContent"]["data_version"], "cal-v1:caf6a04f9e94b9a2"
        )

    def test_calibration_info_v2_if_present(self):
        artifact = ROOT / "calibration/umpire-cal-v2.json"
        if not artifact.exists():
            self.skipTest("v2 artifact not present")
        expected = json.loads(artifact.read_text())["data_version"]
        messages = [
            self.init,
            self.initialized,
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "calibration_info",
                    "arguments": {"artifact": str(artifact)},
                },
            },
        ]
        proc, replies = call_sequence(messages)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        result = [r for r in replies if r.get("id") == 2][0]["result"]
        self.assertFalse(result["isError"])
        self.assertEqual(result["structuredContent"]["data_version"], expected)
        self.assertEqual(result["structuredContent"]["test"]["n"], 120)

    def test_unknown_tool_is_jsonrpc_error(self):
        messages = [
            self.init,
            self.initialized,
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "not_a_tool", "arguments": {}},
            },
        ]
        proc, replies = call_sequence(messages)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        reply = [r for r in replies if r.get("id") == 2][0]
        self.assertEqual(reply["error"]["code"], -32603)
        self.assertIn("unknown tool", reply["error"]["message"])


if __name__ == "__main__":
    unittest.main()
