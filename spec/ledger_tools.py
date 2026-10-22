"""Shared identity minting + ledger derivation for umpire (single source of truth).

Python mirror of `ctxvm/packages/node/src/ident.ts` @ 361043e — byte-compatible for
ASCII-only payloads (00-SPEC.md §2 "ASCII-only payload rule"). Used by
`spec/make_fixtures.py` (generation) and `spec/validate_ledger.py` (validation +
replay), so validator and generator can never drift apart on minting semantics.

Ledger derivation rules (00-SPEC.md §2/§4):
  - page.declare  → a decision recorded; slot appended to its ref
  - page.fault    → a re-decide; `page.id.contentHash` names the ref's FIRST slot
                    (the resident original the re-request matched), never the new one
  - each decision = one xCABI turn (0-based decision index; session.open is line 1)
"""

import hashlib
import json

ARGS_LIMIT = 120  # T6 truncation rule, ident.ts @ 361043e


def canonical_json(value) -> str:
    """Comparator-stable canonical JSON: recursively sorted keys, minimal separators."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def hex8(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()[:8]


def content_hash(content: str) -> str:
    """`sha256:<8 hex>` — xCABI content-identity digest form."""
    return "sha256:" + hex8(content)


def approx4(content: str) -> int:
    """`apx4-char` declared-token approximation (ceil(chars/4); 0 for empty)."""
    return (len(content) + 3) // 4


def decide_ref(state, labels) -> str:
    """`tool:decide(<canonicalArgs>)` with the T6 >120-char truncation + #<hash> tail.

    canonicalArgs = {"labels": sorted-unique uids, "state": <state>}. model_pin and
    seed are deliberately EXCLUDED (00-SPEC §2): identical (state, labels) under
    another pin/seed is a priced re-decide, not a new identity.
    """
    args = {"labels": sorted(set(labels)), "state": state}
    canonical = canonical_json(args)
    trimmed = (
        canonical
        if len(canonical) <= ARGS_LIMIT
        else canonical[:ARGS_LIMIT] + "#" + hex8(canonical)
    )
    return f"tool:decide({trimmed})"


def response_hash(response) -> str:
    """contentHash over canonicalJson({confidence, key, probabilities})."""
    return content_hash(
        canonical_json(
            {
                "confidence": response["confidence"],
                "key": response["key"],
                "probabilities": response["probabilities"],
            }
        )
    )


def derive_page_table(ledger_lines, record_lines) -> dict:
    """Re-derive the ledger's page table deterministically (used by BOTH the
    generator for baselines and the validator's --replay mode)."""
    events = [json.loads(line) for line in ledger_lines if line]
    if not events or events[0]["type"] != "session.open":
        raise ValueError("first ledger line must be session.open")
    session = {
        key: events[0][key]
        for key in ("agentId", "model", "provider", "budgetTokens", "tokenizerId")
    }
    pages: dict[str, dict] = {}
    for event in events[1:]:
        etype = event["type"]
        page = event["page"]
        # xCABI asymmetry: fault carries a FLAT pageId; declare nests under page.id
        ref = page["logicalRef"] if etype == "page.fault" else page["id"]["logicalRef"]
        entry = pages.setdefault(ref, {"ref": ref, "slots": [], "faults": []})
        if etype == "page.declare":
            entry["slots"].append(
                {"contentHash": page["id"]["contentHash"], "tokens": page["tokens"]}
            )
        elif etype == "page.fault":
            # xCABI asymmetry: fault carries a FLAT pageId (page.logicalRef/contentHash),
            # unlike page.declare's nested page.id
            entry["faults"].append(
                {
                    "turn": event["turn"],
                    "source": event["source"],
                    "contentHash": page["contentHash"],
                }
            )
        else:
            raise ValueError(f"event out of the decision-ledger profile: {etype}")
    stats = {
        "decisions": len([l for l in record_lines if l]),
        "declared_slots": sum(len(p["slots"]) for p in pages.values()),
        "re_decides": sum(len(p["faults"]) for p in pages.values()),
    }
    return {
        "format": "decide-page-table/0.1",
        "session": session,
        "pages": [pages[ref] for ref in sorted(pages)],
        "stats": stats,
    }


def render_page_table(table) -> bytes:
    """Byte-deterministic canonical rendering (the --replay roundtrip artifact)."""
    return (json.dumps(table, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
