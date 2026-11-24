"""Render the GitHub Release body and prove its `docker pull` commands work.

Run:
  python3 scripts/release_notes.py render <owner/repo> <tag>   # body to stdout
  python3 scripts/release_notes.py check <notes.md>             # inspect every ref

Why this exists: the release body used to say `docker pull ...:${{ github.ref_name }}`,
i.e. `:v1.13.10`, while docker/metadata-action (`type=semver,pattern={{version}}`)
publishes `1.13.10` and `1.13` — never a `v`-prefixed tag. Every release page
handed self-hosters a first command that 404'd on ghcr. Rendering the body in
one place lets smoke-test run the exact lines users copy, before the release
exists, instead of a template that nothing ever executed.
"""

import re
import subprocess
import sys
from pathlib import Path

IMAGES = ("control-plane", "relay-server", "web-publish")
PULL_RE = re.compile(r"^\s*docker pull\s+(\S+)\s*$", re.MULTILINE)


def image_version(tag: str) -> str:
    """Tag `v1.2.3` → published image version `1.2.3` (metadata-action semver)."""
    version = tag.removeprefix("v")
    if not re.fullmatch(r"\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?", version):
        raise ValueError(f"tag {tag!r} is not a semver release tag")
    return version


def render(repository: str, tag: str) -> str:
    version = image_version(tag)
    pulls = "\n".join(
        f"docker pull ghcr.io/{repository}/{image}:{version}" for image in IMAGES
    )
    return (
        "## Docker Images\n\n"
        f"```bash\n{pulls}\n```\n\n"
        f"See [installation guide](https://github.com/{repository}/blob/main/docs/installation.md)"
        " for setup instructions.\n"
    )


def pull_refs(notes: str) -> list[str]:
    refs = PULL_RE.findall(notes)
    if not refs:
        raise ValueError(
            "release notes contain no `docker pull` line — nothing to verify"
        )
    return refs


def check(notes: str, inspect=None) -> None:
    """Fail unless every `docker pull` ref in the notes resolves in the registry."""
    if inspect is None:
        inspect = _imagetools_inspect
    missing = [ref for ref in pull_refs(notes) if not inspect(ref)]
    if missing:
        raise ValueError(
            "release notes pull refs that do not exist: " + ", ".join(missing)
        )


def _imagetools_inspect(ref: str) -> bool:
    result = subprocess.run(
        ["docker", "buildx", "imagetools", "inspect", ref],
        capture_output=True,
        text=True,
        check=False,
    )
    status = (
        "ok" if result.returncode == 0 else f"MISSING ({result.stderr.strip()[-200:]})"
    )
    print(f"{ref}: {status}")
    return result.returncode == 0


def main(argv: list[str]) -> int:
    if len(argv) == 3 and argv[0] == "render":
        sys.stdout.write(render(argv[1], argv[2]))
        return 0
    if len(argv) == 2 and argv[0] == "check":
        try:
            check(Path(argv[1]).read_text())
        except ValueError as exc:
            print(f"FAIL: {exc}", file=sys.stderr)
            return 1
        print("all release-note pull refs resolve")
        return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
