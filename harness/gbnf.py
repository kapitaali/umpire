"""GBNF grammar + prompt template for `json-label-enum-v0` (the pinned decoding id).

The grammar makes the contract guarantees §3.1/§3.2 (key ∈ labels, never free text)
a *decoding constraint*, per proposal §4: the model physically cannot emit a label
that is not in the request's list, and cannot trail off into prose — the grammar is
the only thing after the `<think></think>` prefix.

GBNF literal strings are character sequences, so multi-token labels work; the only
M1 limitation is probability attribution when two labels share a first-character run
(a warning, not a failure — see harness/umpire.py `harvest_probs`).
"""


def gbnf_escape(label: str) -> str:
    """Escapes a label for safe use inside a GBNF string literal."""
    out = []
    for ch in label:
        if ch in ("\\", '"'):
            out.append("\\" + ch)
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\r":
            out.append("\\r")
        elif ch == "\t":
            out.append("\\t")
        elif not (0x20 <= ord(ch) <= 0x7E):
            out.append(f"\\x{ord(ch):02x}")
        else:
            out.append(ch)
    return "".join(out)


def safe_labels(labels) -> list[str]:
    """M1 label guard: safe-ASCII single-line uids (grammar literals must stay exact)."""
    import sys
    bad = [
        l
        for l in labels
        if (not l) or min(map(ord, l)) < 0x20 or max(map(ord, l)) > 0x7E
    ]
    if bad:
        print(f"fatal: labels not safe-ASCII for json-label-enum-v0: {bad}", file=sys.stderr)
        raise SystemExit(2)
    return list(labels)


def label_enum_grammar(labels) -> str:
    """Response grammar for json-label-enum-v0:

        {"key": "<label>", "probabilities": {"<label>": <num>, ...}}

    The `key` slot admits any requested label; the `probabilities` object lists the
    labels in SORTED order (harness-owned canonical order). The model's emitted
    numbers are never trusted — the harness overwrites them with the token-level
    constrained distribution (see harvest_probs in umpire.py); the grammar only
    needs them parseable.
    """
    sorted_labels = sorted(set(labels))
    # quoted literals: each alternate is the GBNF literal matching the chars
    # "label" (quotes included) — first-class JSON output
    alts = " | ".join(
        '"' + '\\"' + gbnf_escape(l) + '\\"' + '"' for l in sorted_labels
    )
    g = []
    g.append(
        'root ::= "{" ws "\\"key\\"" ws ":" ws lbl ws "," ws '
        '"\\"probabilities\\"" ws ":" ws probs ws "}"'
    )
    g.append("lbl ::= " + alts)
    g.append("probkey ::= " + alts)
    g.append(
        'probs ::= "{" ws probkey ws ":" ws num '
        '("," ws probkey ws ":" ws num)* ws "}"'
    )
    g.append('num ::= [0-9]+ ("." [0-9]+)?')
    g.append("ws ::= [ \\t]*")
    return "\n".join(g) + "\n"


def qwen3_prompt(system: str, text: str) -> str:
    """Hand-rendered Qwen3 chat template, pinned (part of the `decoding` pin).

    Non-thinking mode via the empty `<think></think>` block (Qwen3 model-card
    guidance): keeps latency inside the conceded 100–600 ms band.
    """
    return (
        "<|im_start|>system\n"
        f"{system}\n"
        "<|im_end|>\n"
        "<|im_start|>user\n"
        f"{text}\n"
        "<|im_end|>\n"
        "<|im_start|>assistant\n"
        "<think>\n\n</think>\n\n"
    )


def decision_system(labels) -> str:
    sorted_labels = sorted(set(labels))
    listing = ", ".join(f'"{l}"' for l in sorted_labels)
    return (
        "You are a typed decision primitive. Output exactly one JSON object, no prose: "
        "{\"key\": <one of the labels>, \"probabilities\": {<label>: <normalized probability>}}. "
        f"Allowed labels: {listing}."
    )
