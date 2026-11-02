#!/usr/bin/env python3
"""tools/install-pins.py — download the pinned GGUF weights named in harness/pins.json.

Why this exists: ``models/`` is gitignored by design (.gitignore: "local inference
artifacts are hashes-pinned in harness/pins.json, not committed"), so a fresh clone
has neither weights nor a runner. The pins are the single committed source of truth
(``gguf.repo`` / ``gguf.file`` / ``gguf.sha256`` / ``gguf.bytes``); this tool fetches
exactly those bytes and fails HARD on any mismatch — files that do not hash to their
stamp are a hard error, never a silent behavior change. That is the same fail-hard
identity law the harness applies to prompt templates on every ``decide``.

Usage:
  python3 tools/install-pins.py [--pin ID ...] [--check] [--force]
                                [--pins harness/pins.json] [--models-dir models]

- default: download what is missing; verify sha256 + byte size before renames;
  write a ``<file>.sha256`` sidecar (a local artifact — the committed hash carrier
  stays pins.json).
- ``--check``: verify existing files against the stamps without touching the
  network — the offline stamp verifier for a machine that already has weights.
- ``--force``: re-download a file that exists but FAILS verification; an existing
  file that verifies is never re-fetched even under --force.
- Interrupted downloads land in ``<file>.part`` and RESUME via HTTP Range on the
  next run — 2 GB artifacts over a flaky link should not restart from zero.

W9 install-realism card (reports/RR-W9INSTALL-20261102.md): in-repo half,
stdlib-only, no spinner, no config.
"""

from __future__ import annotations

import hashlib
import os
import pathlib
import sys
import urllib.error
import urllib.parse
import urllib.request

CHUNK = 1024 * 1024          # 1 MiB streaming chunks
PROGRESS_STEP = 256 * CHUNK  # print one line per ~256 MiB
HF = "https://huggingface.co"


def load_pins(path: str) -> dict:
    import json
    return json.loads(pathlib.Path(path).read_text())

def resolve_pins(pins: dict, ids: list[str] | None) -> list[str]:
    """Selected pin ids; ValueError on an unknown id (main turns it into exit 2)."""
    if not ids:
        return sorted(pins)
    for pid in ids:
        if pid not in pins:
            raise ValueError(f"unknown pin {pid!r} (configured: {', '.join(sorted(pins))})")
    # preserve caller order, drop duplicates
    return list(dict.fromkeys(ids))

def url_for(pin: dict) -> str:
    gguf = pin["gguf"]
    file = urllib.parse.quote(str(gguf["file"]))
    return f"{HF}/{gguf['repo']}/resolve/main/{file}"

def part_for(final: pathlib.Path) -> pathlib.Path:
    """<file> → <file>.part, the resumable staging name (same stem, .gguf → .gguf.part)."""
    return final.with_suffix(final.suffix + ".part")

def digest(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()

def verify(final: pathlib.Path, want_sha: str, want_bytes: int) -> None:
    """Fail-hard on any stamp mismatch: digest, prefix, and size are all checked."""
    want = str(want_sha)
    if not want.startswith("sha256:"):
        raise RuntimeError(f"pin stamp {want!r} is not a sha256: digest — pins.json defect")
    got = digest(final)
    if got != want[len("sha256:"):]:
        raise RuntimeError(
            f"{final.name}: sha256 mismatch — got {got}, stamp wants {want} "
            "(weights do not match the pinned identity; refusing to install)"
        )
    size = final.stat().st_size
    if size != int(want_bytes):
        raise RuntimeError(
            f"{final.name}: byte size {size} != stamp {want_bytes} "
            "(digest matched but the stamp disagrees — refusing to install)"
        )

def sidecar_path(final):
    return final.with_suffix(final.suffix + ".sha256")

def write_sidecar(final: pathlib.Path, want_sha: str) -> None:
    """Convenience artifact (sha256sum -c format); the committed carrier stays pins.json."""
    sha = str(want_sha)[len("sha256:"):]
    sc = sidecar_path(final)
    sc.write_text(f"{sha}  {final.name}\n")
    print(f"  sidecar {sc.name} (local artifact; pins.json stays the committed carrier)")

def check_finished_part(part: pathlib.Path, want_sha: str, want_bytes: int) -> bool:
    """A completed .part from a previous run: right size + right sha → finishable."""
    if not part.exists():
        return False
    if part.stat().st_size != int(want_bytes):
        return False
    try:
        return digest(part) == str(want_sha)[len("sha256:"):]
    except OSError:
        return False

def download(pin: dict, models_dir: pathlib.Path, force: bool) -> str:
    """One pin; returns 'already' | 'downloaded'. Exits nonzero on stamp mismatch."""
    gguf = pin["gguf"]
    want_sha = gguf["sha256"]
    want_bytes = int(gguf["bytes"])
    final = models_dir / str(gguf["file"])
    part = part_for(final)

    if final.exists():
        try:
            verify(final, want_sha, want_bytes)
        except RuntimeError as e:
            if not force:
                raise
            final.unlink()
        else:
            print(f"{final.name}: stamp verified ({want_bytes} bytes)")
            write_sidecar(final, want_sha)
            return "already"

    models_dir.mkdir(parents=True, exist_ok=True)
    url = url_for(pin)
    req_headers = {"User-Agent": "umpire-install-pins"}
    resume = 0
    if part.exists() and not force:
        if check_finished_part(part, want_sha, want_bytes):
            os.replace(part, final)
            print(f"{final.name}: completed download found — verified, installed")
            write_sidecar(final, want_sha)
            return "already"
        if need_resume(part, want_bytes):
            resume = part.stat().st_size
            req_headers["Range"] = f"bytes={resume}-"
            print(f"{final.name}: resuming at {resume} bytes (kept .part)")

    print(f"{final.name}: fetching {url} ({want_bytes} bytes)")
    try:
        req = urllib.request.Request(url, headers=req_headers)
        with urllib.request.urlopen(req, timeout=90) as resp:
            mode = "ab" if resume else "wb"
            written = resume
            prev = written
            with open(part, mode) as fh:
                while True:
                    chunk = resp.read(CHUNK)
                    if not chunk:
                        break
                    fh.write(chunk)
                    written += len(chunk)
                    if (written // PROGRESS_STEP) != (prev // PROGRESS_STEP):
                        print(f"  … {written // (1024 * 1024)} MiB downloaded")
                    prev = written
    except urllib.error.HTTPError as e:
        if e.code == 416:  # server says the range is past EOF: stale part, restart clean
            part.unlink(missing_ok=True)
            return download(pin, models_dir, force=force)
        part_size = part.stat().st_size if part.exists() else 0
        print(f"download failed (part now {part_size} bytes): {e}", file=sys.stderr)
        if part_size:
            print("the .part file is kept for the next run to resume", file=sys.stderr)
        raise SystemExit(1)
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        part_size = part.stat().st_size if part.exists() else 0
        print(f"download failed (part now {part_size} bytes): {e}", file=sys.stderr)
        if part_size:
            print("the .part file is kept for the next run to resume", file=sys.stderr)
        raise SystemExit(1)

    if not check_finished_part(part, want_sha, want_bytes):
        got_size = part.stat().st_size if part.exists() else 0
        got_sha = digest(part) if part.exists() else None
        raise SystemExit(
            f"{final.name}: download did not produce stamped bytes — "
            f"size {got_size} != {want_bytes}; digest {got_sha} (kept as .part)"
        )
    os.replace(part, final)
    print(f"{final.name}: sha256 + size verified against the stamp")
    write_sidecar(final, want_sha)
    return "downloaded"

def need_resume(part: pathlib.Path, want_bytes: int) -> bool:
    if not part.exists():
        return False
    size = part.stat().st_size
    return 0 < size < int(want_bytes)

def main(argv=None) -> int:
    import argparse
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--pins", default=str(pathlib.Path(__file__).resolve().parent.parent / "harness" / "pins.json"),
                   help="pins registry path (default harness/pins.json)")
    p.add_argument("--models-dir", default=str(pathlib.Path(__file__).resolve().parent.parent / "models"))
    p.add_argument("--pin", action="append", dest="pin_ids", help="pin id (repeatable; default all)")
    p.add_argument("--check", action="store_true", help="verify existing files offline; no network")
    p.add_argument("--force", action="store_true", help="re-download files that fail verification")
    args = p.parse_args(argv)

    try:
        pins = load_pins(args.pins)
        ids = resolve_pins(pins, args.pin_ids)
    except (OSError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    models_dir = pathlib.Path(args.models_dir)
    models_dir.mkdir(parents=True, exist_ok=True)

    missing = []
    for pid in ids:
        pin = pins[pid]
        gguf = pin["gguf"]
        final = models_dir / str(gguf["file"])
        if args.check:
            if not final.exists():
                print(f"{pid}: {final.name} MISSING")
                missing.append(pid)
                continue
            try:
                verify(final, gguf["sha256"], gguf["bytes"])
            except RuntimeError as e:
                print(f"{pid}: {e}")
                return 1
            print(f"{pid}: {final.name} stamp verified ({gguf['bytes']} bytes)")
            continue
        try:
            download(pin, models_dir, force=args.force)
        except (RuntimeError, SystemExit) as e:
            code = getattr(e, "code", None) or 1
            print(f"error: {e}", file=sys.stderr)
            return int(code) if int(code) else 1
    if args.check and missing:
        print(f"missing pins: {', '.join(missing)} — run without --check to fetch them", file=sys.stderr)
        return 1
    return 0

if __name__ == "__main__":
    sys.exit(main())
