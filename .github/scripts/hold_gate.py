#!/usr/bin/env python3
"""Fail when a pull request about to merge carries a "do not merge yet" label.

Run as a required status check, so that GitHub — not one of our wrappers —
refuses the merge. See .github/workflows/hold-gate.yml for why that distinction
is the whole point.

Two contexts, one verdict:

* ``pull_request`` — the label is read straight off the event payload. This is
  the run that actually stops things: a PR cannot enter the merge queue while a
  required check is red, so a held PR never becomes a queue entry.
* ``merge_group`` — the queue re-runs required checks against the batched tree.
  This run exists mainly so the context is reported there at all (a required
  check that is silent on ``merge_group`` wedges the queue forever), but it is
  not merely ceremonial: it re-reads the labels, so a label applied in the
  window between enqueueing and merging is still caught.

The batch case is handled explicitly. A merge group may carry up to five pull
requests while ``head_ref`` names only one of them, so every referenced PR in
the group is checked. Missing the other four would make the gate hold in the
common single-entry case and quietly pass in exactly the busy case where a
mistake is most likely.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys

LABELS_FILE = ".github/hold-labels.txt"

# `gh-readonly-queue/main/pr-633-<base sha>` — the queue's own branch naming.
QUEUE_REF = re.compile(r"/pr-(\d+)-[0-9a-f]{6,}$")

# `feat(docs): a thing (#629)` — how a squash merge records the PR it came from.
SQUASH_SUBJECT = re.compile(r"\(#(\d+)\)\s*$", re.MULTILINE)


def fail(message: str) -> None:
    print(f"::error::{message}")
    sys.exit(1)


def load_hold_labels() -> set[str]:
    """The label list, as data. Fails closed if it cannot be read.

    An unreadable list is not "nothing is held" — it is "we do not know", and
    the failure mode this whole gate exists to prevent is a merge that proceeds
    because nobody could see a reason to stop it.
    """
    try:
        with open(LABELS_FILE, encoding="utf-8") as handle:
            raw = handle.read()
    except OSError as err:
        fail(f"cannot read {LABELS_FILE} ({err}); refusing rather than guessing that nothing is held")

    labels = {
        line.strip().lower()
        for line in raw.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    if not labels:
        fail(f"{LABELS_FILE} lists no labels; a gate that can never fire is not a gate")
    return labels


def gh_api(path: str) -> object:
    """One authenticated API read. A failure is fatal, never an empty answer."""
    try:
        out = subprocess.run(
            ["gh", "api", path],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    except subprocess.CalledProcessError as err:
        fail(f"GET {path} failed: {err.stderr.strip()[:400]}")
    try:
        return json.loads(out)
    except json.JSONDecodeError as err:
        fail(f"GET {path} returned something that is not JSON ({err})")


def labels_of(repo: str, number: int) -> list[str]:
    payload = gh_api(f"repos/{repo}/pulls/{number}")
    return [label["name"] for label in payload.get("labels", [])]


def pull_requests_in_merge_group(repo: str) -> list[int]:
    """Every pull request the batch is about to merge.

    `head_ref` names the last entry; the rest are recoverable from the commits
    between the group's base and head, because a squash merge puts `(#N)` in the
    subject. Both sources are used and the union taken — if the subject
    convention ever changes, the ref still yields the entry that matters most,
    and if the ref format changes, the subjects still yield the batch.
    """
    numbers: set[int] = set()

    head_ref = os.environ.get("MERGE_GROUP_HEAD_REF", "")
    if match := QUEUE_REF.search(head_ref):
        numbers.add(int(match.group(1)))

    base_sha = os.environ.get("MERGE_GROUP_BASE_SHA", "")
    head_sha = os.environ.get("MERGE_GROUP_HEAD_SHA", "")
    if base_sha and head_sha:
        compare = gh_api(f"repos/{repo}/compare/{base_sha}...{head_sha}")
        for commit in compare.get("commits", []):
            subject = commit.get("commit", {}).get("message", "").splitlines()[:1]
            if subject and (match := SQUASH_SUBJECT.search(subject[0])):
                numbers.add(int(match.group(1)))

    if not numbers:
        fail(
            "could not work out which pull requests this merge group contains "
            f"(head_ref={head_ref!r}, base={base_sha!r}, head={head_sha!r}); "
            "refusing rather than merging a batch whose labels were never read"
        )
    return sorted(numbers)


def main() -> None:
    hold_labels = load_hold_labels()
    repo = os.environ["REPO"]
    event = os.environ.get("EVENT_NAME", "")

    if event == "pull_request":
        number = os.environ.get("PR_NUMBER", "")
        if not number.isdigit():
            fail(f"no pull request number on a {event} event (got {number!r})")
        candidates = [int(number)]
    elif event == "merge_group":
        candidates = pull_requests_in_merge_group(repo)
    else:
        fail(f"unexpected event {event!r}; this gate only knows pull_request and merge_group")

    held: list[str] = []
    for number in candidates:
        names = labels_of(repo, number)
        blocking = sorted(name for name in names if name.lower() in hold_labels)
        shown = ", ".join(names) if names else "(none)"
        if blocking:
            held.append(f"#{number} carries {', '.join(blocking)}")
            print(f"HELD    #{number}: labels = {shown}")
        else:
            print(f"clear   #{number}: labels = {shown}")

    if held:
        fail(
            "refusing to merge — " + "; ".join(held) + ". "
            "Remove the label when the thing it is waiting for has happened. "
            "This check is what makes the label mean something on every merge "
            "path, including the queue and the web Merge button."
        )

    print(f"\nNo hold labels on {', '.join('#' + str(n) for n in candidates)} — clear to merge.")


if __name__ == "__main__":
    main()
