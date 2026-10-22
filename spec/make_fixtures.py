#!/usr/bin/env python3
"""Generate the two M0 fixture decision-ledgers (deterministic; same bytes every run).

Each fixture triplet: <name>.ledger.jsonl (xCABI 0.1 profile), <name>.records.jsonl
(payload sidecar), <name>.page-table.json (committed --replay baseline). Synthetic
provenance per 00-SPEC §6 — no model outputs exist yet (M1); weights_hash strings are
fixed placeholders shaped sha256:<8hex>.
"""

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))

from ledger_tools import (  # noqa: E402
    approx4,
    canonical_json,
    content_hash,
    decide_ref,
    derive_page_table,
    render_page_table,
    response_hash,
)

FIXTURES = pathlib.Path(__file__).parent.parent / "fixtures"

PIN_A = {
    "model": "qwen3-4b-instruct",
    "weights_hash": "sha256:9a11b9c9",
    "decoding": "json-label-enum-v0",
}
PIN_B = {
    "model": "qwen3-8b-instruct",
    "weights_hash": "sha256:4b3d2a1c",
    "decoding": "json-label-enum-v0",
}


def session_open() -> dict:
    return {
        "type": "session.open",
        "agentId": "umpire",
        "model": PIN_A["model"],
        "provider": "local",
        "budgetTokens": 0,
        "tokenizerId": "apx4-char",
    }


def decision(turn, state, labels, pin, seed, key, probabilities, confidence, latency_ms):
    """One decision record + its ledger contribution (declare/fault per rules)."""
    ref = decide_ref(state, labels)
    response = {"key": key, "probabilities": probabilities, "confidence": confidence}
    resp_hash = response_hash(response)
    record = {
        "schema": "decide-record/0.1",
        "state": state,
        "labels": labels,
        "ref": ref,
        "contentHash": resp_hash,
        "model_pin": pin,
        "seed": seed,
        "key": key,
        "probabilities": probabilities,
        "confidence": confidence,
        "latency_ms": latency_ms,
    }
    return {"turn": turn, "ref": ref, "hash": resp_hash, "record": record}


def build(name, decisions):
    """Assemble ledger lines from the decision sequence per the profile rules:
    fresh ref → declare; identical-hash re-decide → fault row only; divergent hash
    re-decide → fault row naming the resident (first) slot + declare the new slot."""
    ledger = [json.dumps(session_open())]
    first_slot: dict[str, str] = {}
    declared: set[tuple[str, str]] = set()
    for dec in decisions:
        turn, ref, resp_hash = dec["turn"], dec["ref"], dec["hash"]
        if ref not in first_slot:
            body = dec["record"]
            ledger.append(
                json.dumps(
                    {
                        "type": "page.declare",
                        "page": {
                            "id": {"logicalRef": ref, "contentHash": resp_hash},
                            "kind": "tool_result",
                            "tokens": approx4(
                                canonical_json(
                                    {
                                        "confidence": body["confidence"],
                                        "key": body["key"],
                                        "probabilities": body["probabilities"],
                                    }
                                )
                            ),
                        },
                    }
                )
            )
            first_slot[ref] = resp_hash
            declared.add((ref, resp_hash))
        else:
            ledger.append(
                json.dumps(
                    {
                        "type": "page.fault",
                        "turn": turn,
                        "page": {"logicalRef": ref, "contentHash": first_slot[ref]},
                        "source": "tool_recall",
                    }
                )
            )
            if (ref, resp_hash) not in declared:
                ledger.append(
                    json.dumps(
                        {
                            "type": "page.declare",
                            "page": {
                                "id": {"logicalRef": ref, "contentHash": resp_hash},
                                "kind": "tool_result",
                                "tokens": approx4(
                                    canonical_json(
                                        {
                                            "confidence": dec["record"]["confidence"],
                                            "key": dec["record"]["key"],
                                            "probabilities": dec["record"]["probabilities"],
                                        }
                                    )
                                ),
                            },
                        }
                    )
                )
                declared.add((ref, resp_hash))
    records = [json.dumps(dec["record"]) for dec in decisions]
    ledger_lines = [line + "\n" for line in ledger]
    record_lines = [line + "\n" for line in records]
    table = derive_page_table(ledger_lines, record_lines)
    written = {
        f"{name}.ledger.jsonl": "".join(ledger_lines),
        f"{name}.records.jsonl": "".join(record_lines),
        f"{name}.page-table.json": render_page_table(table).decode("utf-8"),
    }
    for fname, text in written.items():
        path = FIXTURES / fname
        path.write_text(text)
        print(f"wrote {path} ({len(text.encode('utf-8'))} bytes)")


# Fixture 1 — intent-router: 5 decisions over the same label set; exercises a fresh
# decision, an identical re-decide (fault row only), and an A/B pin comparison
# (fault row + second slot) — the exact rows §5.2 prices as waste.
INTENT_LABELS = ["sales", "billing", "other", "technical"]  # caller order (scrambled)
build(
    "intent-router",
    [
        decision(
            0,
            {"session_hint": "u-42", "text": "I was charged twice this month"},
            INTENT_LABELS, PIN_A, 7, "billing",
            {"billing": 0.87, "other": 0.01, "sales": 0.03, "technical": 0.09},
            0.86, 412,
        ),
        decision(
            1,
            {"session_hint": "u-43", "text": "webhook integration fails with 500s after deploy"},
            INTENT_LABELS, PIN_A, 7, "technical",
            {"billing": 0.08, "other": 0.04, "sales": 0.06, "technical": 0.82},
            0.74, 391,
        ),
        decision(  # identical re-decide of turn 0 — same pin, same seed
            2,
            {"session_hint": "u-42", "text": "I was charged twice this month"},
            INTENT_LABELS, PIN_A, 7, "billing",
            {"billing": 0.87, "other": 0.01, "sales": 0.03, "technical": 0.09},
            0.86, 397,
        ),
        decision(  # A/B: same state+labels, heavier pin — divergent hash, second slot
            3,
            {"session_hint": "u-42", "text": "I was charged twice this month"},
            INTENT_LABELS, PIN_B, 7, "billing",
            {"billing": 0.91, "other": 0.02, "sales": 0.02, "technical": 0.05},
            0.9, 587,
        ),
        decision(
            4,
            {"session_hint": "u-77", "text": "where do I send a PO for team licenses?"},
            INTENT_LABELS, PIN_A, 7, "sales",
            {"billing": 0.02, "other": 0.02, "sales": 0.93, "technical": 0.03},
            0.88, 378,
        ),
    ],
)

# Fixture 2 — support-triage: 4 decisions; exercises a same-seed reproducibility
# re-decide (identical hash → fault row only) and an escalation-tail decision.
TRIAGE_LABELS = ["info", "refund", "escalate"]
build(
    "support-triage",
    [
        decision(
            0,
            {"ticket": "t-901", "text": "order 5582 arrived damaged — want a refund"},
            TRIAGE_LABELS, PIN_A, 21, "refund",
            {"escalate": 0.05, "info": 0.08, "refund": 0.87},
            0.8, 366,
        ),
        decision(
            1,
            {"ticket": "t-902", "text": "how do I reset my 2FA device?"},
            TRIAGE_LABELS, PIN_A, 21, "info",
            {"escalate": 0.03, "info": 0.92, "refund": 0.05},
            0.91, 342,
        ),
        decision(  # identical re-decide of turn 0 — same seed, identical response
            2,
            {"ticket": "t-901", "text": "order 5582 arrived damaged — want a refund"},
            TRIAGE_LABELS, PIN_A, 21, "refund",
            {"escalate": 0.05, "info": 0.08, "refund": 0.87},
            0.8, 358,
        ),
        decision(  # escalation tail — honest low confidence on a brittle class
            3,
            {"ticket": "t-903", "text": "legal notice regarding API terms — needs human review"},
            TRIAGE_LABELS, PIN_A, 21, "escalate",
            {"escalate": 0.71, "info": 0.26, "refund": 0.03},
            0.19, 401,
        ),
    ],
)

print("content_hash spot check:", content_hash("umpire"))
