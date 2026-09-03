#!/usr/bin/env python3
"""check-drift.py — liveness guard for teamrelay.ru's /opt/relay on alyssa.

Unlike tr-relay-vm (a separate host from wherever the repo is checked out,
requiring an SSH byte-comparison — see that host's own check-drift.py),
this instance's git checkout lives ON THE SAME HOST at /opt/relay/repo/,
and the runtime config files (Caddyfile, docker-compose.yml,
docker-compose.override.yml) are symlinks into that checkout, not copies.
So the drift class this guards against is different and narrower:

  1. A tracked file got replaced by a real file again (the exact landmine
     found live 2026-09-03, W5 #c09befac: pre-git-ification copies of
     docker-compose.yml/.override.yml were left sitting at /opt/relay/,
     and Docker Compose auto-discovers those two filenames with no -f
     flags — a bare `docker compose up` from /opt/relay would silently
     use the stale file instead of the symlink).
  2. The checkout has uncommitted local changes or isn't on origin/main's
     tip (config edited in place again, defeating the whole point of
     moving it into git).

Run from anywhere; defaults assume the standard alyssa layout.
Exit: 0 clean · 1 drift found · 2 could not run.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

TRACKED_LINKS = {
    "Caddyfile": "repo/infra/Caddyfile",
    "docker-compose.yml": "repo/infra/deployments/teamrelay-ru/docker-compose.yml",
    "docker-compose.override.yml": "repo/infra/deployments/teamrelay-ru/docker-compose.override.yml",
}


def run(cmd: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--relay-root", default="/opt/relay")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    relay_root = Path(args.relay_root)
    repo_root = relay_root / "repo"
    drift: list[str] = []

    # 1. Each tracked runtime file must be a symlink, and must resolve to
    #    the expected git-tracked path -- not a real file that could
    #    silently diverge from what's in git.
    for name, target in TRACKED_LINKS.items():
        p = relay_root / name
        if not p.exists():
            drift.append(f"{name}: MISSING (expected a symlink -> {target})")
            continue
        if not p.is_symlink():
            drift.append(
                f"{name}: is a REAL FILE, not a symlink -- this is exactly the "
                f"landmine class this guard exists to catch (a bare "
                f"`docker compose up` auto-discovers this filename and would "
                f"silently use it instead of the git-tracked config)"
            )
            continue
        resolved = str(p.readlink())
        if resolved != target:
            drift.append(f"{name}: symlink points to {resolved!r}, expected {target!r}")
        if args.verbose:
            print(f"  OK  {name} -> {resolved}")

    # 2. The checkout itself must be clean and on origin/main's tip -- a
    #    tracked file is only as good as the checkout it lives in.
    if not repo_root.is_dir():
        drift.append(f"repo checkout missing entirely: {repo_root}")
    else:
        status = run(["git", "status", "--porcelain"], cwd=repo_root)
        if status.returncode != 0:
            drift.append(f"git status failed in {repo_root}: {status.stderr.strip()}")
        elif status.stdout.strip():
            drift.append(
                f"repo checkout has uncommitted changes:\n{status.stdout}"
            )

        branch = run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=repo_root)
        if branch.stdout.strip() != "main":
            drift.append(f"repo checkout is on branch {branch.stdout.strip()!r}, not main")

        run(["git", "fetch", "origin", "main"], cwd=repo_root)
        local_head = run(["git", "rev-parse", "HEAD"], cwd=repo_root).stdout.strip()
        remote_head = run(["git", "rev-parse", "origin/main"], cwd=repo_root).stdout.strip()
        if local_head != remote_head:
            drift.append(
                f"repo checkout HEAD ({local_head[:8]}) is behind origin/main "
                f"({remote_head[:8]}) -- run `git -C {repo_root} pull --ff-only`"
            )
        elif args.verbose:
            print(f"  OK  repo at origin/main tip ({local_head[:8]})")

    if drift:
        print("[DRIFT FOUND]", file=sys.stderr)
        for d in drift:
            print(f"  - {d}", file=sys.stderr)
        return 1
    print("[OK] no drift")
    return 0


if __name__ == "__main__":
    sys.exit(main())
