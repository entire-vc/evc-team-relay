#!/usr/bin/env python3
"""check-drift.py — liveness guard for tr-relay-vm's /opt/relay, mirroring
bob/monitoring/tw-mon/scripts/check-twmon-config-drift.py's C1 (byte-drift)
check. Mesh #206eb904 subtask 4/6.

A tracked copy nobody compares against prod is documentation, not control.
This is the half of the story that makes tracking mean something.

Deploy mode is COPY (tr-relay-vm is a separate host from wherever this repo
is checked out), so a straight byte comparison of tracked-vs-prod is the
right check — same reasoning as the tw-mon script's own header.

Template files (relay/relay.toml.example) are rendered against .env before
comparing, since the tracked copy is a template with {{VAR}} placeholders,
not a literal mirror — diffing the raw template would always show drift.

For bind_mount entries this also checks the container's OWN view of the
file (`docker exec <container> sha256sum <container_path>`), not just the
host path. A single-file bind mount can detach from its host path
independently of anything this script or deploy.sh does — host-vs-git
clean does not imply the running container sees that content. Measured
live (#206eb904, 2026-08-20): a real detach on this exact Caddyfile sat
unnoticed for weeks because nothing had ever compared the container's side
against anything — host-vs-git was clean throughout, because the host file
itself was never the broken half.

Exit: 0 clean · 1 drift found · 2 could not run (ssh/manifest/env failure).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path


def ssh(host: str, cmd: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["ssh", "-n", "-o", "BatchMode=yes", host, cmd],
        capture_output=True, text=True,
    )


def render_relay_toml(template_text: str, env_path: Path) -> str | None:
    if not env_path.exists():
        return None
    user = pw = None
    for line in env_path.read_text().splitlines():
        if line.startswith("MINIO_ROOT_USER="):
            user = line.split("=", 1)[1]
        elif line.startswith("MINIO_ROOT_PASSWORD="):
            pw = line.split("=", 1)[1]
    if user is None or pw is None:
        return None
    return template_text.replace("{{MINIO_ROOT_USER}}", user).replace(
        "{{MINIO_ROOT_PASSWORD}}", pw
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo-root", default=str(Path(__file__).resolve().parent))
    ap.add_argument("--env-file", default=None, help="real .env for rendering template files")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    repo_root = Path(args.repo_root)
    manifest_path = repo_root / "manifest.json"
    if not manifest_path.exists():
        print(f"[ERROR] manifest not found: {manifest_path}", file=sys.stderr)
        return 2
    manifest = json.loads(manifest_path.read_text())
    host = manifest["host"]
    prod_root = manifest["prod_root"]
    env_path = Path(args.env_file) if args.env_file else repo_root / ".env"

    drift, ok, skipped = [], 0, []

    for entry in manifest["files"]:
        if not entry.get("deployable"):
            skipped.append(entry["tracked"])
            continue
        tracked_path = repo_root / entry["tracked"]
        prod_path = f"{prod_root}/{entry['prod']}"
        if not tracked_path.exists():
            drift.append((entry["tracked"], "MISSING FROM REPO"))
            continue

        if entry.get("template"):
            rendered = render_relay_toml(tracked_path.read_text(), env_path)
            if rendered is None:
                skipped.append(f"{entry['tracked']} (no env file to render against — pass --env-file)")
                continue
            local_bytes = rendered.encode()
        else:
            local_bytes = tracked_path.read_bytes()
        local_sha = hashlib.sha256(local_bytes).hexdigest()

        r = ssh(host, f"sha256sum '{prod_path}' 2>/dev/null")
        if r.returncode != 0 or not r.stdout.strip():
            drift.append((entry["tracked"], "MISSING FROM PROD (or unreadable)"))
            continue
        prod_sha = r.stdout.split()[0]

        if prod_sha != local_sha:
            drift.append((entry["tracked"], f"DRIFT (git {local_sha[:12]} != prod {prod_sha[:12]})"))
            continue

        if entry.get("bind_mount"):
            container = entry.get("container")
            container_path = entry.get("container_path")
            if not container or not container_path:
                drift.append((entry["tracked"], "bind_mount=true but manifest has no container/container_path — cannot verify the mount is actually attached"))
                continue
            r = ssh(host, f"docker exec {container} sha256sum '{container_path}' 2>/dev/null")
            if r.returncode != 0 or not r.stdout.strip():
                drift.append((entry["tracked"], f"could not read {container}:{container_path} — container down or path wrong"))
                continue
            container_sha = r.stdout.split()[0]
            if container_sha != local_sha:
                drift.append((entry["tracked"], f"MOUNT DETACHED — host file matches git ({local_sha[:12]}) but container {container} sees {container_sha[:12]} at {container_path}. The host-side file is fine; the bind mount itself is stale. Fix: docker compose restart the affected service."))
                continue

        ok += 1
        if args.verbose:
            print(f"    [PASS] {entry['tracked']}: prod matches git" + (f", container {entry.get('container')} confirmed attached" if entry.get("bind_mount") else ""))

    print(f"\n{ok} file(s) clean, {len(drift)} drifted, {len(skipped)} skipped")
    for t in skipped:
        print(f"  [SKIP] {t}")
    for t, reason in drift:
        print(f"  [FAIL] {t}: {reason}")

    if drift:
        print("\nRepair: deploy.sh (git -> prod)")
        print("        or commit the prod-side change into this repo if prod is right")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
