"""Unit tests for app.core.path_validation.validate_relative_path (#546ce7e3).

HTTP-level regression coverage (the actual sync-write endpoint accepting a
conflict-copy filename end-to-end) lives in
test_agent_key_sync_protocol.py::TestDenyListPathValidation. These are the
narrower, faster unit tests for cases that don't survive an HTTP client's own
URL construction cleanly (control characters, bidi overrides) plus a direct
pin on every rule the function enforces.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.core.path_validation import validate_relative_path


@pytest.mark.parametrize(
    "path",
    [
        "note (relay conflict 2026-09-05T20-25-23-268Z).md",
        "Meeting (2026-09-01).md",
        "Paper [draft].md",
        "Q3 план — итоги.md",
        "it's mine.md",
        "A&B.md",
        "note+1.md",
        "50% done.md",
        "a,b.md",
        "заметка 🎯.md",
        "sub folder/деньги 2026.md",
        "café.md",
        "Ёлка.md",
        "Всё о релизе.md",
    ],
)
def test_realistic_filenames_accepted(path: str) -> None:
    assert validate_relative_path(path) == path


@pytest.mark.parametrize(
    "path",
    [
        "",
        "/abs/path",
        "../etc/passwd",
        "a/../../b",
        "a/./b",
        "a//b",
        "a/",
        "a\\b",
        "bad?name.md",
        "x|y.md",
        "a<b.md",
        'quote".md',
        "star*.md",
        "colon:name.md",
        "note\x00.md",  # NUL
        "note\x1f.md",  # C0 control
        "note\x7f.md",  # DEL
        "note\x9f.md",  # C1 control
        "left‮right.md",  # RTL override (bidi spoofing)
        "zero​width.md",  # zero-width space (Cf category)
        "bom﻿.md",  # byte-order mark (Cf category)
        "line break.md",  # U+2028 LINE SEPARATOR
        "para graph.md",  # U+2029 PARAGRAPH SEPARATOR
        "a" * 513,
        "/".join(["seg"] * 8),  # 7 slashes > MAX_PATH_DEPTH (6)
    ],
)
def test_unsafe_paths_rejected_with_400(path: str) -> None:
    with pytest.raises(HTTPException) as exc_info:
        validate_relative_path(path)
    assert exc_info.value.status_code == 400


def test_max_depth_boundary_accepted() -> None:
    path = "/".join(["seg"] * 7)  # exactly 6 slashes == MAX_PATH_DEPTH
    assert validate_relative_path(path) == path


def test_max_length_boundary_accepted() -> None:
    path = "a" * 512
    assert validate_relative_path(path) == path
