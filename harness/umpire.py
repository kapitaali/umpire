#!/usr/bin/env python3
"""umpire reference harness (M1): CLI + library over a pinned llama.cpp runner.

`decide(state, labels, pin, seed)` performs one typed decision against a llama-server
endpoint using the pinned `json-label-enum-v0` decoding (GBNF grammar-constrained,
temperature 0, fixed seed — harness/gbnf.py), harvests the model's constrained
token-level distribution as the raw (pre-calibration) probabilities, derives the
pinned confidence, and records the decision as an xCABI-0.1 ledger page + record row
via spec/ledger_tools.py (single source of truth with the M0 validator).

Determinism contract (§3.3 of the proposal, empirical M1 gate): same
(state, labels, pin, seed) on the same runner build + machine ⇒ byte-identical
response. Cross-build/cross-hardware byte-stability is NOT claimed in v0.
"""

import argparse
import hashlib
import json
import math
import pathlib
import sys
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent / "spec"))

from ledger_tools import (  # noqa: E402
    approx4,
    canonical_json,
    response_hash,
    decide_ref,
)

from gbnf import label_enum_grammar, qwen3_prompt, chatml_prompt, decision_system, safe_labels  # noqa: E402

SPEC = pathlib.Path(__file__).parent.parent / "spec"
PINS = json.loads((pathlib.Path(__file__).parent / "pins.json").read_text())


# ---------------------------------------------------------------- llama-server
def llama_complete(server_url, prompt, grammar, seed, n_predict=96, n_probs=32, timeout=120):
    body = {
        "prompt": prompt,
        "grammar": grammar,
        "temperature": 0.0,
        "seed": seed,
        "n_predict": n_predict,
        "n_probs": n_probs,
        "cache_prompt": False,
    }
    req = urllib.request.Request(
        server_url.rstrip("/") + "/completion",
        data=canonical_json(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def harvest_probs(probs_block, labels, key):
    """Raw constrained distribution from the runner's per-token probability readout.

    Anchor: the FIRST block whose sampled token begins the CHOSEN key — that is the
    key choice point (the probabilities object comes later in the response and its
    literal positions are ignored). Token masses at that position are attributed to
    labels by first-character prefix. Emitted M1 boundaries (warnings, not errors):
    labels sharing a first character, or a label whose first char collides with a
    template literal char, make attribution approximate. A tiny floor keeps the
    record schema's exclusiveMinimum-0 rule satisfiable for zero-mass labels (they
    were unreachable under the grammar, so a raw 0 is *reported*, just re-scaled).

    Returns {label: p} over ALL labels, renormalized to 1.
    """
    starts = {l[:1] for l in labels}
    if len(starts) < len(labels):
        print("warn: labels share a first-character run — first choice point attribution is approximate", file=sys.stderr)
    anchor = None
    for entry in probs_block:
        first_char = (entry.get("token") or "")[:1]
        if first_char and first_char == key[:1] and first_char in starts:
            anchor = entry
            break
    if anchor is None:
        for entry in probs_block:
            opts = entry.get("top_logprobs") or []
            if opts and (opts[0].get("token") or "")[:1] in starts:
                anchor = entry
                break
    if anchor is None:
        raise RuntimeError(
            "no key choice point found in completion_probabilities — "
            "check the runner's logprobs facility (M1 gate, see RR)"
        )
    masses = {l: 0.0 for l in labels}
    options = anchor.get("top_logprobs") or []
    for opt in options[: min(16, len(options))]:
        tok = opt.get("token", "")
        first = tok[:1]
        if first in starts:
            # attribute to the label(s) beginning with this char (1 label typically;
            # shared-first-char labels split evenly — documented M1 boundary);
            # logprobs are natural-log: exp() recovers probability mass
            group = [l for l in labels if l[:1] == first]
            mass = math.exp(float(opt.get("logprob", -1e30)))
            for label in group:
                masses[label] += mass
    total = sum(masses.values())
    if total <= 0:
        raise RuntimeError("no label mass observed at the key choice point")
    EPS = 1e-9
    raw = {l: m / total if m > 0 else EPS for l, m in masses.items()}
    norm = sum(raw.values())
    return {l: p / norm for l, p in raw.items()}


def confidence(probs) -> float:
    """Pinned M1 dispersion measurement: entropy-complement, normalized to [0,1].

    1 - H(p)/log2(n) over the requested labels. This is a DISPERSION measurement,
    not a correctness promise (contract §2); calibration replaces it in M2.
    """
    n = len(probs)
    h = -sum(p * math.log2(p) for p in probs.values() if p > 0)
    return 1.0 - min(1.0, h / math.log2(n))


# ---------------------------------------------------------------- ledger writer
class LedgerWriter:
    """Append-only writer implementing the M0 profile rules (fresh declare /
    identical-fault / divergent fault+second-slot), reading existing state from
    disk so the harness works across restarts with no server-side memory. Owns the
    L3 session.open envelope: a brand-new ledger starts with it, derived from the
    pin."""

    def __init__(self, ledger_path, records_path, session_open=None):
        self.ledger_path = pathlib.Path(ledger_path)
        self.records_path = pathlib.Path(records_path)
        self.slots: dict[str, list[dict]] = {}
        self.decisions = 0
        self.line = 0
        has_open = False
        if self.ledger_path.exists() and self.ledger_path.read_text().strip():
            for line in self.ledger_path.read_text().splitlines():
                if not line:
                    continue
                ev = json.loads(line)
                if self.line == 0:
                    has_open = ev.get("type") == "session.open"
                self.line += 1
                if ev["type"] == "page.declare":
                    pid = ev["page"]["id"]
                    self.slots.setdefault(pid["logicalRef"], []).append(pid["contentHash"])
                elif ev["type"] == "page.fault":
                    self.decisions += 1
        if self.records_path.exists():
            self.decisions = max(
                self.decisions,
                len([l for l in self.records_path.read_text().splitlines() if l]),
            )
        if not has_open:
            open_ev = session_open or {
                "type": "session.open",
                "agentId": "umpire",
                "model": "unknown",
                "provider": "local",
                "budgetTokens": 0,
                "tokenizerId": "apx4-char",
            }
            with self.ledger_path.open("w" if self.line == 0 else "a") as f:
                f.write(json.dumps(open_ev) + "\n")
            self.line += 1

    def append(self, record):
        ref = record["ref"]
        resp_hash = record["contentHash"]
        turns: list[str] = []
        if ref not in self.slots:
            turns.append(
                json.dumps(
                    {
                        "type": "page.declare",
                        "page": {
                            "id": {"logicalRef": ref, "contentHash": resp_hash},
                            "kind": "tool_result",
                            "tokens": approx4(
                                canonical_json(
                                    {
                                        "confidence": record["confidence"],
                                        "key": record["key"],
                                        "probabilities": record["probabilities"],
                                    }
                                )
                            ),
                        },
                    }
                )
                + "\n"
            )
            self.slots[ref] = [resp_hash]
        else:
            resident = self.slots[ref][0]
            turns.append(
                json.dumps(
                    {
                        "type": "page.fault",
                        "turn": self.decisions,
                        "page": {"logicalRef": ref, "contentHash": resident},
                        "source": "tool_recall",
                    }
                )
                + "\n"
            )
            if resp_hash not in self.slots[ref]:
                turns.append(
                    json.dumps(
                        {
                            "type": "page.declare",
                            "page": {
                                "id": {"logicalRef": ref, "contentHash": resp_hash},
                                "kind": "tool_result",
                                "tokens": approx4(
                                    canonical_json(
                                        {
                                            "confidence": record["confidence"],
                                            "key": record["key"],
                                            "probabilities": record["probabilities"],
                                        }
                                    )
                                ),
                            },
                        }
                    )
                    + "\n"
                )
                self.slots[ref].append(resp_hash)
        with self.ledger_path.open("a") as f:
            f.writelines(turns)
        with self.records_path.open("a") as f:
            f.write(json.dumps(record) + "\n")
        self.decisions += 1
        return record


# ---------------------------------------------------------------- decide
def decide(server_url, state, labels, pin_id, seed, ledger_path, records_path, timeout=120):
    labels = safe_labels(labels)
    if len(set(labels)) != len(labels):
        raise ValueError("labels must be unique")
    if not (2 <= len(set(labels)) <= 255):
        raise ValueError("labels count out of range 2..255")
    pin = PINS[pin_id]
    grammar = label_enum_grammar(labels)
    template = pin.get("template", "qwen3-hybrid")
    try:
        prompt_fn = {"qwen3-hybrid": qwen3_prompt, "chatml": chatml_prompt}[template]
    except KeyError:
        raise RuntimeError(f"unknown template id {template!r} for pin {pin_id!r}") from None
    want = pin.get("prompt_template_sha256")
    if not want:
        raise RuntimeError(
            f"pin {pin_id!r} has no prompt_template_sha256 — template identity "
            "is mandatory (da8ff80 template-corruption incident)"
        )
    got = "sha256:" + hashlib.sha256(prompt_fn("[SYSTEM]", "[TEXT]").encode("utf-8")).hexdigest()
    if got != want:
        raise RuntimeError(
            f"prompt template identity mismatch for pin {pin_id!r} ({template}): "
            f"current {got} != pinned {want} — restore the pinned template or "
            "update the pin deliberately"
        )
    prompt = prompt_fn(decision_system(labels), state.get("text", ""))
    n_predict = max(96, 32 + 28 * len(sorted(set(labels))))
    raw = llama_complete(server_url, prompt, grammar, seed, n_predict=n_predict, timeout=timeout)
    text = raw.get("content", "")
    try:
        keyed = json.loads(text)
        key = keyed["key"]
    except (json.JSONDecodeError, KeyError) as exc:
        raise RuntimeError(f"grammar-constrained decode did not yield a key object: {text!r}") from exc
    if key not in labels:
        raise RuntimeError(f"decoded key {key!r} escaped the label enum (grammar bug?)")
    probs = harvest_probs(raw.get("completion_probabilities") or [], labels, key)
    response = {"key": key, "probabilities": probs, "confidence": round(confidence(probs), 4)}
    ref = decide_ref(state, labels)
    record = {
        "schema": "decide-record/0.1",
        "state": state,
        "labels": labels,
        "ref": ref,
        "contentHash": response_hash(response),
        "model_pin": {
            "model": pin["model"],
            "weights_hash": pin["weights_hash"],
            "decoding": pin["decoding"],
        },
        "seed": seed,
        "key": key,
        "probabilities": response["probabilities"],
        "confidence": response["confidence"],
        "latency_ms": int(raw.get("timings", {}).get("prompt_ms", 0) + raw.get("timings", {}).get("predicted_ms", 0))
        or None,
    }
    record = {k: v for k, v in record.items() if v is not None}
    session_open = {
        "type": "session.open",
        "agentId": "umpire",
        "model": pin["model"],
        "provider": "local",
        "budgetTokens": 0,
        "tokenizerId": "apx4-char",
    }
    writer = LedgerWriter(ledger_path, records_path, session_open)
    writer.append(record)
    return response, record


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "decide":
        argv = argv[1:]
    ap = argparse.ArgumentParser(description="umpire decide (M1 reference harness)")
    ap.add_argument("--server", default="http://127.0.0.1:8081")
    ap.add_argument("--pin", default=next(iter(PINS)))
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--text", required=True, help="state text (opaque payload)")
    ap.add_argument("--labels", required=True, help="comma-separated labels")
    ap.add_argument("--ledger", required=True)
    ap.add_argument("--records", required=True)
    args = ap.parse_args(argv)
    labels = [l.strip() for l in args.labels.split(",") if l.strip()]
    response, record = decide(
        args.server, {"text": args.text}, labels, args.pin, args.seed, args.ledger, args.records
    )
    print(canonical_json(response))
    print(f"# ref {record['ref']} ledger: {args.ledger}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
