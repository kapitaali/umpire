#!/usr/bin/env python3
"""Rehearse .github/workflows/ci.yml locally, in a pristine clone.

Why this exists: `tools/run-ci.sh` is the local mirror, but a mirror can drift from the
workflow it claims to mirror — and a workflow only lies once it runs after a push. This
script closes that gap. It clones the committed tree (no venvs/, no node_modules, no
dist/ — exactly what actions/checkout gives you), then executes each job's `run` steps, in
order, with a throwaway virtualenv per job, stopping a job at its first failure the way
Actions does.

What it does NOT do: execute `uses:` steps. The runners they install (node 26, python 3.14,
rust stable) are whatever the host has; the script prints those versions so the record says
what was actually exercised. A green rehearsal means "the step text is self-sufficient on
this host", not "GitHub ran it".

Usage:
  python3 tools/ci-rehearsal.py                # all jobs
  python3 tools/ci-rehearsal.py --job schema   # one job
  python3 tools/ci-rehearsal.py --keep         # keep the clone for inspection
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"


def load_jobs():
    """Parse the workflow. Uses the repo's ODPS venv for PyYAML when available."""
    candidates = [ROOT / "venvs" / "odps" / "bin" / "python", Path(sys.executable)]
    for py in candidates:
        if not py.exists():
            continue
        probe = subprocess.run(
            [str(py), "-c", "import yaml, json; print(json.dumps(1))"],
            capture_output=True, text=True,
        )
        if probe.returncode != 0:
            continue
        data = subprocess.run(
            [str(py), "-c",
             "import yaml, json, sys; print(json.dumps(yaml.safe_load(open(sys.argv[1]))))",
             str(WORKFLOW)],
            capture_output=True, text=True,
        )
        if data.returncode != 0:
            continue
        return json.loads(data.stdout)["jobs"]
    # Neither candidate has PyYAML: bootstrap one in a throwaway venv. The tool must work
    # in a repo with no venvs/ at all (umpire), not just here.
    bootstrap = Path(tempfile.mkdtemp(prefix="ctxvm-ci-rehearsal-parse-"))
    try:
        venv.create(bootstrap / "v", with_pip=True)
        py = bootstrap / "v" / "bin" / "python"
        install = subprocess.run([str(py), "-m", "pip", "install", "--quiet", "pyyaml"],
                                 capture_output=True, text=True)
        if install.returncode != 0:
            sys.exit(f"ci-rehearsal: could not install pyyaml: {install.stderr.strip()[:200]}")
        data = subprocess.run(
            [str(py), "-c",
             "import yaml, json, sys; print(json.dumps(yaml.safe_load(open(sys.argv[1]))))",
             str(WORKFLOW)],
            capture_output=True, text=True,
        )
        if data.returncode != 0:
            sys.exit(f"ci-rehearsal: workflow did not parse: {data.stderr.strip()[:200]}")
        return json.loads(data.stdout)["jobs"]
    finally:
        shutil.rmtree(bootstrap, ignore_errors=True)


def run(cmd, cwd, env):
    return subprocess.run(cmd, shell=True, cwd=cwd, env=env, capture_output=True, text=True)


def tail(text, n=3):
    lines = [l for l in (text or "").strip().splitlines() if l.strip()]
    return "\n".join(lines[-n:])


def rehearse_job(name, job, clone: Path, workdir: Path):
    steps = job.get("steps", [])
    venv_dir = workdir / f"venv-{name}"
    if venv_dir.exists():
        shutil.rmtree(venv_dir)
    venv.create(venv_dir, with_pip=True)
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("CTXVM_") and k not in ("PYTHONPATH", "VIRTUAL_ENV")}
    env["PATH"] = f"{venv_dir / 'bin'}{os.pathsep}{env.get('PATH', '')}"
    env["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"

    print(f"\n=== job: {name} ({len(steps)} steps)")
    failed = None
    for i, step in enumerate(steps, 1):
        label = step.get("name") or step.get("uses") or f"step {i}"
        if "uses" in step and "run" not in step:
            print(f"  - [{i:02d}] {label}  (uses: skipped — runner-provided)")
            continue
        cmd = step["run"]
        cwd = clone / (step.get("working-directory") or ".")
        result = run(cmd, cwd, env)
        if result.returncode != 0:
            print(f"  ✖ [{i:02d}] {label}  → exit {result.returncode}")
            print("        " + tail(result.stderr or result.stdout, 6).replace("\n", "\n        "))
            failed = label
            break
        print(f"  ✔ [{i:02d}] {label}")
    return failed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--job", default=None)
    ap.add_argument("--keep", action="store_true")
    args = ap.parse_args()

    jobs = load_jobs()
    if args.job:
        if args.job not in jobs:
            sys.exit(f"no such job: {args.job} (have: {', '.join(jobs)})")
        jobs = {args.job: jobs[args.job]}

    workdir = Path(tempfile.mkdtemp(prefix="ctxvm-ci-rehearsal-"))
    clone = workdir / "repo"
    print(f"clone:   {clone}")
    clone_run = subprocess.run(
        ["git", "clone", "--quiet", str(ROOT), str(clone)],
        capture_output=True, text=True,
    )
    if clone_run.returncode != 0:
        sys.exit(f"clone failed: {clone_run.stderr.strip()}")

    # Rehearse what would be PUSHED, not just what is committed: overlay the dirty tree
    # (tracked edits + untracked non-ignored files) and commit it inside the clone. The
    # hub job runs `git diff --exit-code hub/`, so the overlay has to be a commit or that
    # step would fail on an uncommitted tree rather than on a real hub drift.
    dirty = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT,
                           capture_output=True, text=True).stdout.strip()
    if dirty:
        diff = subprocess.run(["git", "diff", "HEAD", "--binary"], cwd=ROOT,
                              capture_output=True, text=True).stdout
        if diff:
            applied = subprocess.run(["git", "apply", "--whitespace=nowarn", "-"],
                                     cwd=clone, input=diff, capture_output=True, text=True)
            if applied.returncode != 0:
                sys.exit(f"could not overlay working-tree diff: {applied.stderr.strip()}")
        untracked = subprocess.run(
            ["git", "ls-files", "--others", "--exclude-standard"], cwd=ROOT,
            capture_output=True, text=True).stdout.split("\n")
        for rel in filter(None, untracked):
            src, dst = ROOT / rel, clone / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
        subprocess.run(["git", "add", "-A"], cwd=clone, check=True)
        subprocess.run(["git", "-c", "user.name=rehearsal", "-c", "user.email=r@localhost",
                        "commit", "--quiet", "-m", "rehearsal overlay (working tree)"],
                       cwd=clone, check=True)
        print(f"overlay: {len(untracked) - untracked.count('')} untracked file(s) + tracked diff")

    for tool in ("node", "npm", "python3", "cargo"):
        exe = shutil.which(tool)
        version = ""
        if exe:
            version = (subprocess.run([exe, "--version"], capture_output=True, text=True).stdout or "").strip()
        print(f"host {tool:8s} {version or '(absent)'}")

    failures = {}
    for name, job in jobs.items():
        failed = rehearse_job(name, job, clone, workdir)
        if failed:
            failures[name] = failed

    print()
    if failures:
        for name, step in failures.items():
            print(f"FAIL {name}: {step}")
        print(f"rehearsal FAILED ({len(failures)}/{len(jobs)} jobs)")
        rc = 1
    else:
        print(f"rehearsal OK — {len(jobs)} job(s), every run step green in a pristine clone")
        rc = 0

    if args.keep:
        print(f"kept: {workdir}")
    else:
        shutil.rmtree(workdir, ignore_errors=True)
    return rc


if __name__ == "__main__":
    sys.exit(main())
