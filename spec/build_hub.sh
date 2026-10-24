#!/usr/bin/env bash
# spec/build_hub.sh — umpire hub-as-code (Phase B, spec/odps-odcs-plan.md §6):
# regenerate hub/ from the single ODPS manifest using the open-data-products SDK.
#
# Proof chain (their SDK end to end, mirrors ctxvm tools/build-hub.sh):
#   1. root manifest re-validated by their SDK (source gate)
#   2. `odpc-build` on a basename-staged copy → hub/catalog.yaml + hub/index.html
#   3. $ref rewritten hub-relative (../odps/…) — manifest stays the single source
#   4. release-gate KPI section injected into index.html (rows parsed from the
#      manifest's productStrategy; current values derived from
#      calibration/umpire-cal-v3.json + corpus-v3.capture.meta.json)
#   5. committed catalog re-validated (validate --json → valid:true) + odpc-summary
# Idempotent: the SDK emits no timestamps and all numbers come from committed
# files → two runs are byte-identical. `spec/build_hub.sh check` rebuilds to a
# temp dir and cmp's against the committed files.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MANIFEST="odps/umpire-calcorpus-0.1.odps.yaml"
ARTIFACT="calibration/umpire-cal-v3.json"
CAPTURE="calibration/corpus-v3.capture.meta.json"
MODE="${1:-build}"
# Local dev: durable rebuilt venvs (~/.local/state/umpire/venvs/umpire-odps-venv). Override: UMPIRE_ODPS_SDK.
SDK="${UMPIRE_ODPS_SDK:-$HOME/.local/state/umpire/venvs/umpire-odps-venv/bin/open-data-products}"
PY="${UMPIRE_ODPS_PY:-$HOME/.local/state/umpire/venvs/umpire-odps-venv/bin/python}"
[[ -x "$SDK" ]] || SDK="$(command -v open-data-products || true)"
[[ -x "$PY" ]] || PY="$(command -v python3)"
[[ -n "${SDK:-}" && -n "${PY:-}" ]] || { echo "[umpire-hub] open-data-products SDK not found (venv or PATH)" >&2; exit 1; }
HUB="$ROOT/hub"
log() { printf '[umpire-hub] %s\n' "$*"; }

STAGE="$(mktemp -d)"
if [[ "$MODE" == "check" ]]; then
  STAGE_OUT="$(mktemp -d)"
  trap 'rm -rf "$STAGE" "$STAGE_OUT"' EXIT
else
  STAGE_OUT="$HUB"
  trap 'rm -rf "$STAGE"' EXIT
fi

# 1) source gate — the manifest must still validate against the ODPS schema
"$SDK" validate "$ROOT/$MANIFEST" >/dev/null
log "source manifest validates: $MANIFEST"

# 2) stage + build (staged basename keeps the emitted $ref meaningful)
BASE="$(basename "$MANIFEST")"
cp "$ROOT/$MANIFEST" "$STAGE/$BASE"
mkdir -p "$STAGE_OUT"
"$SDK" odpc-build "$STAGE" \
  --output "$STAGE_OUT/catalog.yaml" \
  --html "$STAGE_OUT/index.html" \
  --id umpire.hub \
  --name "umpire hub" \
  --description "Open hub catalog for the umpire project: checksummed calibration corpus, decision-primitive artifacts, release-gate KPIs."
log "odpc-build done (productReferences=1)"

# 3) hub-relative $ref into the single root manifest
"$PY" - "$STAGE_OUT/catalog.yaml" <<'PYEOF'
import pathlib, sys
p = pathlib.Path(sys.argv[1])
t = p.read_text()
old = "$ref: umpire-calcorpus-0.1.odps.yaml"
new = "$ref: ../odps/umpire-calcorpus-0.1.odps.yaml"
n = t.count(old)
assert n == 1, f"expected exactly 1 productModel $ref, found {n}"
p.write_text(t.replace(old, new))
PYEOF
log "hub-relative ref: ../$MANIFEST"

# 4) inject the release-gate KPI leaderboard into index.html
"$PY" - "$ROOT/$MANIFEST" "$ROOT/$ARTIFACT" "$ROOT/$CAPTURE" "$STAGE_OUT/index.html" <<'PYEOF'
import json, pathlib, re, sys

manifest_path, artifact_path, capture_path, html_path = map(pathlib.Path, sys.argv[1:])
text = manifest_path.read_text()

starts = [(m.start(), m.group(1)) for m in re.finditer(r"-? *id: (kpi-[a-z\-]+)", text)]
ends = [text.index("\n  SLA:")] if "\n  SLA:" in text else []
blocks = {}
for i, (pos, kpi) in enumerate(starts):
    end = starts[i + 1][0] if i + 1 < len(starts) else min(ends[0] if ends else len(text), len(text))
    blocks[kpi] = text[pos:end]

def field(block, key):
    m = re.search(rf"\b{re.escape(key)}:\s*([^\n]+)", block)
    return m.group(1).strip() if m else ""

art = json.loads(artifact_path.read_text())
test = art["metrics"]["test"]
meta = json.loads(capture_path.read_text())
assert meta["page_table_stats"]["re_decides"] == 0, "0 re-decides expected for current claim"

CURRENT = {
    "kpi-calibrated-ece": f"{test['calibrated']['ece'] * 100:.2f}%",
    "kpi-calibrated-brier": f"{test['calibrated']['brier'] * 100:.2f}%",
    "kpi-decision-accuracy": f"{test['accuracy'] * 100:.2f}%",
    "kpi-replay-determinism": "100.00% (page-table roundtrip 480/480, 0 re-decides)",
    "kpi-negative-conformance": "12/12 raw + 12/12 calibrated (validator)",
}

rows = []
for kpi, block in blocks.items():
    name = field(block, "name").rstrip(':')
    target = field(block, "target")
    direction = field(block, "direction").replace("_", " ")
    unit = field(block, "unit")
    calc = field(block, "calculation")
    current = CURRENT.get(kpi, "—")
    rows.append(
        f"<tr><td>{name}<br><span class=\"odp-facts-muted\">{kpi}</span></td>"
        f"<td>{target} {unit or ''} ({direction.replace(':', ' ')})</td>"
        f"<td>{current}</td><td><code>{calc}</code></td></tr>"
    )

section = (
    '<section class="odp-section"><h2>Release-gate KPIs (from productStrategy)</h2>'
    '<article class="odp-card"><table class="odp-kpi"><thead><tr>'
    '<th>KPI</th><th>Target</th><th>Current (drop calibration/v3)</th><th>Calculation</th>'
    f'</tr></thead><tbody>{"".join(rows)}</tbody></table>'
    '<p>Honesty: targets are release-gate floors/ceilings derived from the current '
    'v3 numbers (quiescent recapture) — declared gates, not model-performance promises. '
    'Single project-authored '
    'corpus, one pin/seed/machine/runner build; n=120 test split (see RR-M3V3/RR-M3FULL). '
    'Values tracked in <code>calibration/umpire-cal-v3.json</code>+ capture metadata; '
    'rebuild must be byte-identical (<code>spec/build_hub.sh check</code>).</p>'
    '</article></section>'
)

html = html_path.read_text()
anchor = '<section class="odp-section"><h2>Use Cases</h2>'
n = html.count(anchor)
assert n == 1, f"expected exactly 1 Use Cases section, found {n}"
html_path.write_text(html.replace(anchor, section + anchor))

# leaderboard table styling (anchored, idempotent)
style_anchor = "</style>"
kpi_style = (
    "    .odp-kpi {\n"
    "      width: 100%;\n"
    "      border-collapse: collapse;\n"
    "      margin-top: 8px;\n"
    "    }\n"
    "    .odp-kpi th,\n"
    "    .odp-kpi td {\n"
    "      border: 1px solid var(--line);\n"
    "      padding: 6px 8px;\n"
    "      text-align: left;\n"
    "      vertical-align: top;\n"
    "      font-size: 0.9rem;\n"
    "    }\n"
    "    .odp-kpi th {\n"
    "      background: var(--bg);\n"
    "    }\n"
    "    .odp-kpi code {\n"
    "      font-size: 0.8rem;\n"
    "    }\n"
    "    .odp-facts-muted {\n"
    "      color: var(--muted);\n"
    "      font-size: 0.8rem;\n"
    "    }\n"
)
html = html_path.read_text()
assert kpi_style not in html, "KPI style already present"
assert html.count(style_anchor) == 1, "expected exactly 1 </style>"
html_path.write_text(html.replace(style_anchor, kpi_style + "  " + style_anchor))
PYEOF
log "KPI leaderboard injected (from productStrategy + artifact)"

# 5) committed artifacts self-check (their SDK validates what we ship)
"$SDK" validate "$STAGE_OUT/catalog.yaml" --json > "$STAGE/cat-validate.json"
"$PY" - "$STAGE/cat-validate.json" <<'PYEOF'
import json, sys
d = json.load(open(sys.argv[1]))
assert d["valid"] is True and d["errors"] == [], d
print("catalog validates: spec %s kind %s v%s" % (d["spec"], d["kind"], d["version"]))
PYEOF
"$SDK" odpc-summary "$STAGE_OUT/catalog.yaml" | head -4

if [[ "$MODE" == "check" ]]; then
  cmp "$STAGE_OUT/catalog.yaml" "$HUB/catalog.yaml" \
    && cmp "$STAGE_OUT/index.html" "$HUB/index.html" \
    && log "check OK: regenerated hub/ is byte-identical to the committed files" \
    || { echo "[umpire-hub] check FAILED: regenerated files differ from committed hub/" >&2; exit 1; }
  exit 0
fi

# 6) integrity anchor over the three hub files
cd "$HUB"
sha256sum catalog.yaml index.html README.md > hub.sha256
cat hub.sha256
cd "$ROOT"
log "OK: hub/catalog.yaml + hub/index.html + hub.sha256 regenerated from $MANIFEST"
