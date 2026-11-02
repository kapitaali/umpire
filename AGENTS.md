# AGENTS.md — umpire

Instructions for any agent working in this repository. Read this first, then `README.md`, then the
report of record for the area you are touching (`reports/`). This repo is the consumer of the
[ctxvm]/xCABI trace standard; it contributes nothing back into that standard.

## 1. What this repo is

`umpire` is an open, JEV-style decision primitive. One round trip maps an opaque `state` onto
caller-owned `labels` and returns one `key`, raw label probabilities, a confidence, an optional
corpus-fitted calibration view, and an auditable identity (`cal-v1:<hash>`, `isotonic/1`). Decisions
mint xCABI **RFC-0.1** traces.

Two things are load-bearing and easy to break:

- **The pinned model.** `harness/pins.json` pins each model by SHA-256, including its prompt
  template (`prompt_template_sha256`). A drifted or unstamped template is a hard error, not a silent
  behaviour change. `models/` and `runners/` are gitignored by design: the bytes are pinned by hash,
  not committed.
- **The calibration artifact.** `calibration/umpire-cal-v3.json` is reproducible from the corpus and
  is checked by hash in the gate. Never edit it by hand.

## 2. The one command that decides whether you are done

```
bash tools/run-ci.sh          # prints "ALL GATES GREEN" or fails
```

Offline only — no model, no llama-server, no network beyond package installs. All gates run from the
**repo root** and that matters: the calibration checksum files list repo-root-relative paths, so
`cd calibration && sha256sum -c` fails on every line. Use `UMPIRE_PY=<python>` to choose an
interpreter.

Companions:

- `python3 tools/ci-rehearsal.py` — runs each job's `run:` steps in a clone of the committed tree.
- `bash tools/determinism-matrix.sh` — replays the same derivation under several interpreters and
  `PYTHONHASHSEED` values and compares hashes.

The LIVE gates (`harness/test_m1.py` against the pinned server, the M2 live gate) are deliberately
not in CI: they need a running llama-server and belong to a capture session.

## 3. Layout

```
spec/validate_ledger.py        the validator: schema + semantic rules + negatives
spec/make_fixtures.py          fixture generator (fixtures/ are generated, not hand-edited)
spec/migrate_rfc01.py          RFC-0.1 migration for fixtures
spec/determinism.py            page-table derivation + hash (stdlib only, runs in a bare container)
spec/calibration_tools.py      corpus metrics (ECE, Brier, accuracy)
spec/calibration_ci.py         absolute ECE/Brier with bootstrap intervals
spec/transfer_eval.py          W9 label-set transfer evaluation + drift check
spec/build_hub.sh              ODPS hub artefacts
fixtures/                      ledger/records pairs + negative cases
calibration/                   corpus, fitted artifacts, transfer arms, checksums
harness/                       pins, template routing, live test against the pinned server
mcp_server.py                  the canonical MCP server (stdlib JSON-RPC, six tools)
hermes/                        Hermes plugin bundle for this server (README, setup, config example)
reports/                       RR-<CARD>-<date>.md, LOG.md (append-only), RR-DAY-<date>.md
design/                        pre-registrations and design notes (e.g. W9-TRANSFER.md)
tools/                         run-ci.sh, ci-rehearsal.py, determinism-matrix.sh
```

## 4. Laws. Breaking one is a bug even if the tests pass.

1. **Negatives must fail for the stated reason.** A negative case that trips on some other rule is a
   vacuous test. Where a hash is needed to reach the rule under test, precompute it — do not let the
   case pass for the wrong cause.
2. **Version-branch the validator, never loosen it.** v0 traces validate against 12 negatives, RFC-0.1
   traces against 14. A v0.1-only field on a v0 trace is an error, not a warning.
3. **The determinism object is the derivation bytes** —
   `sha256(render_page_table(derive_page_table(ledger, records)))` — not the validator's prose.
   Keep it stdlib-only so it runs anywhere. Report the environments it ran in; say "page-table replay
   determinism", never "deterministic" unqualified.
4. **Pre-register experiments before running them.** Arm corpora, rules, thresholds and stopping
   conditions are frozen in `design/` first (see `design/W9-TRANSFER.md`). If the rule was wrong,
   write an amendment before the next run — do not adjust it afterwards.
5. **A negative result is a result.** Declare it in advance and report it with the same prominence as
   a positive one. Do not extend an experiment to chase significance; state the effect size and the
   interval you actually have.
6. **Fixtures are generated.** Change `spec/make_fixtures.py` (or the migration script) and regenerate;
   never hand-edit `fixtures/*.jsonl`.
7. **Checksums are repo-root-relative.** Run `sha256sum -c` from the root. A new file inside
   `calibration/` can crash the validator's glob — check the loader when you add one.
8. **No publication without the owner.** No release, tag, registry push, plugin-index submission or
   public post. `models/` downloads are pinned and hashed; they are not committed.

## 5. Verification culture

- Every number in a report is reproducible by a command in this repo, with n, seed, model and machine
  stated. Intervals, not point estimates, for anything with noise.
- Cite Git repositories, papers and upstream code with a locator at first mention. Mark anything
  unverified `UNVERIFIED`.
- Prefer a table of results plus the repro command over prose. No marketing language.
- If a gate fails, stop and report rather than working around it.

## 6. Recording work

1. `reports/RR-<CARD>-<date>.md` — what was done, repro commands, numbers with intervals, limitations,
   deviations, open questions.
2. A row appended to `reports/LOG.md` (append-only).
3. A section in `reports/RR-DAY-<date>.md`, plus a day-close line in the handoff document when a day
   ends.

## 7. Git and secrets

- **Never push.** The owner pushes.
- Never commit `.env` files, tokens, credentials, or downloaded model bytes.
- Commits are attributed `kapitaali <teppo@kapitaali.com>`.
- Do not stage another agent's work in progress. `git status` and `git log --oneline -5` first.
- `git rev-list --count origin/main..HEAD` reads the stale local tracking ref here; use
  `git ls-remote --heads origin` for the real remote head.

## 8. Test commands

```
bash tools/run-ci.sh                                    everything (authoritative)
python3 spec/validate_ledger.py <pairs...> --replay    ledger conformance + replay
python3 spec/determinism.py check                       committed hash table vs this environment
python3 spec/calibration_ci.py check calibration/cal-precision-v3.json
python3 spec/transfer_eval.py check calibration/transfer/verdict.json
python3 spec/test_packaging.py                          packaging invariants
python3 -m unittest mcp_server_test.py                  MCP server contract
python3 -m unittest calibration/test_calibration.py     calibration corpus/fit invariants
python3 -m unittest harness/test_replay_audit.py        replay audit
python3 -m unittest tools/install_pins_test.py          installer invariants
```

The gate prints the authoritative totals; do not hardcode counts in docs.

## 9. Current state (keep this section current)

- Calibration artifact `cal-v1:057a5a35a9d4ec71` (`isotonic/1`), evaluated once on the 120-row test
  split: accuracy 0.7083, ECE 0.0302 [0.0286, 0.1201], Brier 0.1726 [0.1409, 0.2077]. One corpus,
  one pin, one machine — do not generalise it.
- W9 label-set transfer is closed: no detectable degradation at n=180 by the frozen rule, and the
  recommendation is on record — stop extending, grow the held-out corpus instead.
- Open, owner-gated: the four baselines (spend/training/API), a second corpus for domain transfer, and
  a public release (install realism ships a fetcher and quickstart without publishing anything).
