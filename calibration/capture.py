#!/usr/bin/env python3
"""Capture corpus-v1 as real decisions against a running pinned llama-server.

Every corpus example is sent through the M1 harness once, in corpus order. The
result is three artifacts:

  calibration/corpus.ledger.jsonl   xCABI-0.1 profile ledger (one declare/page)
  calibration/corpus.records.jsonl  decide-record/0.1 sidecar (raw, no calibration)
  calibration/raw-v1.jsonl          fit input: id/split/gold/correct + full record

plus the deterministic page-table baseline and a meta file with hashes, pin
identity, latency stats, and accuracy. Raw probabilities are the model's, never
the corpus labels: gold is used only to mark `correct` for later fitting/eval.

Requires the pinned 1.7B runner on disk and a llama-server at --server. The pin
is verified against harness/pins.json (binary + GGUF hashes) before any call.

Usage:
    python3 calibration/capture.py --server http://127.0.0.1:8081
    python3 calibration/capture.py ... --resume       # continue a stopped run
    python3 calibration/capture.py ... --limit 4      # smoke (partial artifact)

A partial capture must not be used for fitting; fit.py enforces 160/160.
"""

import argparse
import datetime as dt
import hashlib
import json
import pathlib
import platform
import socket
import statistics
import sys
import urllib.request

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "harness"))
sys.path.insert(0, str(ROOT / "spec"))

import umpire  # noqa: E402
from ledger_tools import derive_page_table, render_page_table  # noqa: E402

CORPUS = HERE / "corpus-v1.jsonl"
LEDGER = HERE / "corpus.ledger.jsonl"
RECORDS = HERE / "corpus.records.jsonl"
RAW = HERE / "raw-v1.jsonl"
PAGE_TABLE = HERE / "corpus.page-table.json"
META = HERE / "corpus.meta.json"


def sha256_file(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def load_corpus(path: pathlib.Path) -> list[dict]:
    rows = [json.loads(line) for line in path.read_text().splitlines() if line]
    if len(rows) != 160:
        raise SystemExit(f"corpus must have 160 examples, found {len(rows)}")
    ids = [r.get("id") for r in rows]
    if len(set(ids)) != len(ids):
        raise SystemExit("corpus ids are not unique")
    labels = set(rows[0]["labels"])
    for r in rows:
        if set(r["labels"]) != labels:
            raise SystemExit(f"{r.get('id')}: inconsistent label set")
        if r["gold"] not in labels:
            raise SystemExit(f"{r.get('id')}: gold not in labels")
        if r["split"] not in {"train", "validation", "test"}:
            raise SystemExit(f"{r.get('id')}: bad split")
        if not isinstance(r["state"], dict) or "text" not in r["state"]:
            raise SystemExit(f"{r.get('id')}: state must carry text")
    return rows


def health(server: str) -> None:
    url = server.rstrip("/") + "/health"
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            if r.status != 200:
                raise SystemExit(f"server health returned {r.status}")
    except Exception as exc:
        raise SystemExit(f"llama-server not ready at {url}: {exc}") from None


def verify_pin(pin_id: str) -> dict:
    pin = umpire.PINS[pin_id]
    binary = ROOT / pin["runner"]["binary"]
    model = ROOT / "models" / pin["gguf"]["file"]
    for path, want, label in (
        (binary, pin["runner"]["binary_sha256"].removeprefix("sha256:"), "runner"),
        (model, pin["gguf"]["sha256"].removeprefix("sha256:"), "GGUF"),
    ):
        if not path.exists():
            raise SystemExit(f"missing {label}: {path}")
        got = sha256_file(path)
        if got != want:
            raise SystemExit(f"{label} hash mismatch for {path}: {got} != {want}")
    return pin


def append_jsonl(path: pathlib.Path, obj: dict) -> None:
    with path.open("a") as f:
        f.write(json.dumps(obj, separators=(",", ":"), ensure_ascii=True) + "\n")
        f.flush()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="capture umpire M2 corpus decisions")
    ap.add_argument("--server", default="http://127.0.0.1:8081")
    ap.add_argument("--corpus", type=pathlib.Path, default=CORPUS)
    ap.add_argument("--pin", default="qwen3-1.7b")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--ledger", type=pathlib.Path, default=LEDGER)
    ap.add_argument("--records", type=pathlib.Path, default=RECORDS)
    ap.add_argument("--raw", type=pathlib.Path, default=RAW)
    ap.add_argument("--page-table", type=pathlib.Path, default=PAGE_TABLE)
    ap.add_argument("--meta", type=pathlib.Path, default=META)
    ap.add_argument("--limit", type=int, default=None, help="capture only N missing examples (smoke)")
    ap.add_argument("--resume", action="store_true", help="continue an interrupted full capture")
    args = ap.parse_args(argv)

    rows = load_corpus(args.corpus)
    health(args.server)
    pin = verify_pin(args.pin)

    existed = [p for p in (args.ledger, args.records, args.raw) if p.exists()]
    if existed and not args.resume:
        raise SystemExit(
            "output artifacts already exist; use --resume to continue or move them aside: "
            + ", ".join(str(p) for p in existed)
        )

    done = set()
    if args.resume and args.raw.exists():
        for line in args.raw.read_text().splitlines():
            if line:
                done.add(json.loads(line)["id"])
        n_ledger = sum(
            1
            for line in args.ledger.read_text().splitlines()
            if line and json.loads(line)["type"] == "page.declare"
        ) if args.ledger.exists() else 0
        n_records = sum(1 for line in args.records.read_text().splitlines() if line) if args.records.exists() else 0
        if not (n_ledger == n_records == len(done)):
            raise SystemExit(
                "cannot resume: raw/ledger/records disagree "
                f"(raw={len(done)}, declares={n_ledger}, records={n_records}); "
                "a crash landed between writes, move the three files aside and recapture"
            )
        print(f"resume: {len(done)}/160 already captured")

    session_open = {
        "type": "session.open",
        "agentId": "umpire",
        "model": pin["model"],
        "provider": "local",
        "budgetTokens": 0,
        "tokenizerId": "apx4-char",
    }
    # LedgerWriter owns the session.open envelope; decide() creates its own writer,
    # so initialize the envelope once here before the first append.
    umpire.LedgerWriter(args.ledger, args.records, session_open)

    started = dt.datetime.now(dt.timezone.utc).isoformat()
    t0 = dt.datetime.now(dt.timezone.utc)
    todo = [r for r in rows if r["id"] not in done]
    if args.limit is not None:
        todo = todo[: args.limit]
    results = []
    for i, row in enumerate(todo, start=1):
        response, record = umpire.decide(
            args.server,
            row["state"],
            row["labels"],
            args.pin,
            args.seed,
            args.ledger,
            args.records,
        )
        correct = record["key"] == row["gold"]
        append_jsonl(
            args.raw,
            {
                "id": row["id"],
                "split": row["split"],
                "gold": row["gold"],
                "correct": correct,
                "record": record,
            },
        )
        results.append((row, record, correct))
        print(
            f"[{i:3d}/{len(todo):3d}] {row['id']} {row['split']:10s} "
            f"gold={row['gold']:9s} key={record['key']:9s} "
            f"conf={record['confidence']:.4f} lat={record.get('latency_ms', '?')}ms "
            f"{'ok' if correct else 'WRONG'}",
            flush=True,
        )

    # A partial capture is not fit material, but keep its page table self-consistent.
    ledger_lines = args.ledger.read_text().splitlines() if args.ledger.exists() else []
    record_lines = args.records.read_text().splitlines() if args.records.exists() else []
    raw_lines = args.raw.read_text().splitlines() if args.raw.exists() else []
    if len(raw_lines) == len(record_lines):
        table = derive_page_table(ledger_lines, record_lines)
        args.page_table.write_bytes(render_page_table(table))
    else:
        table = None

    raw_entries = [json.loads(line) for line in raw_lines if line]
    latencies = [e["record"].get("latency_ms") for e in raw_entries if e["record"].get("latency_ms") is not None]
    by_split = {}
    for split in ("train", "validation", "test"):
        subset = [e for e in raw_entries if e["split"] == split]
        by_split[split] = {
            "n": len(subset),
            "correct": sum(1 for e in subset if e["correct"]),
            "accuracy": round(sum(1 for e in subset if e["correct"]) / len(subset), 4) if subset else None,
        }
    finished = dt.datetime.now(dt.timezone.utc).isoformat()
    meta = {
        "schema": "umpire-calibration-capture/0.1",
        "corpus": {
            "path": str(args.corpus.relative_to(ROOT)),
            "sha256": "sha256:" + sha256_file(args.corpus),
            "examples": len(rows),
        },
        "pin": {
            "id": args.pin,
            "model": pin["model"],
            "weights_hash": pin["weights_hash"],
            "decoding": pin["decoding"],
            "prompt_template_sha256": pin["prompt_template_sha256"],
            "runner_binary_sha256": pin["runner"]["binary_sha256"],
            "gguf_sha256": pin["gguf"]["sha256"],
            "server_invocation": pin["server"]["invocation"],
        },
        "seed": args.seed,
        "server": args.server,
        "capture": {
            "started_at": started,
            "finished_at": finished,
            "wall_seconds": round((dt.datetime.now(dt.timezone.utc) - t0).total_seconds(), 1),
            "host_sha256": "sha256:"
            + hashlib.sha256(socket.gethostname().encode("utf-8")).hexdigest()[:16],
            "platform": platform.platform(),
            "python": platform.python_version(),
            "command": " ".join(["python3", *sys.argv]),
            "complete": len(raw_entries) == len(rows),
            "captured": len(raw_entries),
        },
        "counts": {
            "total": len(raw_entries),
            "per_split": by_split,
            "correct": sum(1 for e in raw_entries if e["correct"]),
            "accuracy": round(sum(1 for e in raw_entries if e["correct"]) / len(raw_entries), 4) if raw_entries else None,
        },
        "latency_ms": {
            "n": len(latencies),
            "min": min(latencies) if latencies else None,
            "median": int(statistics.median(latencies)) if latencies else None,
            "mean": int(statistics.mean(latencies)) if latencies else None,
            "max": max(latencies) if latencies else None,
        },
        "hashes": {
            "raw_sha256": "sha256:" + sha256_file(args.raw) if args.raw.exists() else None,
            "ledger_sha256": "sha256:" + sha256_file(args.ledger) if args.ledger.exists() else None,
            "records_sha256": "sha256:" + sha256_file(args.records) if args.records.exists() else None,
            "page_table_sha256": "sha256:" + sha256_file(args.page_table) if args.page_table.exists() else None,
        },
        "git": {
            "head": _git(["rev-parse", "HEAD"]),
            "dirty": bool(_git(["status", "--porcelain"])),
        },
        "notes": [
            "raw probabilities are pre-calibration; gold is used only for correct/fit/eval",
            "records are raw (no calibrated block); fitting is a separate offline step",
            "contentHash excludes latency_ms and any calibrated view",
        ],
    }
    if table is not None:
        meta["page_table_stats"] = table["stats"]
    args.meta.write_text(json.dumps(meta, indent=2) + "\n")
    print(
        f"\ncaptured {len(raw_entries)}/{len(rows)} | "
        f"accuracy={meta['counts']['accuracy']} | "
        f"latency median={meta['latency_ms']['median']}ms max={meta['latency_ms']['max']}ms"
    )
    print(f"wrote {args.raw.name}, {args.ledger.name}, {args.records.name}, {args.page_table.name}, {args.meta.name}")
    return 0 if len(raw_entries) == len(rows) else 3


def _git(args: list[str]) -> str | None:
    import subprocess

    try:
        return subprocess.run(
            ["git", *args], cwd=ROOT, capture_output=True, text=True, check=True
        ).stdout.strip() or None
    except Exception:
        return None


if __name__ == "__main__":
    raise SystemExit(main())
