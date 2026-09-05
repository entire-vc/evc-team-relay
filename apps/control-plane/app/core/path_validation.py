"""Shared relative-path validation for anything a vault file's own name can
reach: web-publish sync-write (shares.py), mesh-artifact upload (web.py).

History: the original check was an ALLOW-list (`^[\\w.\\-/ ]+$`), extended
reactively per incident — once already, to add Cyrillic (MR #213, for the
letter "ё"). An allow-list of "characters we've seen so far in acceptable
filenames" keeps discovering new legitimate filenames it rejects (parens,
comma, apostrophe, &, +, %, em dash, emoji — all ordinary in real vault
notes, including filenames Obsidian's own conflict-copy naming produces).

The actual security property this check exists for is path safety — no
traversal, no absolute paths, no control characters that could confuse a
filesystem, HTTP header, or terminal. That is a DENY-list property: reject
what's dangerous, allow everything else. Obsidian itself already forbids
`* " \\ < > : | ?` in filenames (they can never arrive from a real vault),
so rejecting those is free and meaningful; almost everything else a human
can type in a filename is legitimate content.
"""

from __future__ import annotations

import unicodedata

from fastapi import HTTPException, status

# Characters Obsidian itself never allows in a filename — safe to reject
# unconditionally, since a real vault file can never carry one of these.
_OBSIDIAN_FORBIDDEN_CHARS = frozenset('*"\\<>:|?')

MAX_PATH_LENGTH = 512
MAX_PATH_DEPTH = 6


def validate_relative_path(path: str) -> str:
    """Raise 400 if `path` is unsafe as a relative vault-file path.

    Deny-list, not allow-list (see module docstring for why): rejects
    traversal, absolute paths, empty/dot segments, control characters
    (C0/C1, DEL, Unicode bidi/format controls), and the literal characters
    Obsidian itself forbids in filenames. Everything else — parentheses,
    comma, apostrophe, &, +, %, em dash, emoji, non-Latin scripts — passes,
    because all of it is ordinary, real vault content.

    Returns the path unchanged on success (matches the pre-existing
    `_validate_file_path`/`_validate_upload_path` call convention).
    """
    if not path:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="path is required")
    if path.startswith("/"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="path must be relative (no leading /)"
        )
    if len(path) > MAX_PATH_LENGTH:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"path too long (max {MAX_PATH_LENGTH} chars)",
        )

    segments = path.split("/")
    if path.count("/") > MAX_PATH_DEPTH:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"path too deep (max {MAX_PATH_DEPTH} levels)",
        )
    if any(seg in ("", ".", "..") for seg in segments):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="path traversal not allowed"
        )

    for ch in path:
        if ch in _OBSIDIAN_FORBIDDEN_CHARS:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="path contains invalid characters"
            )
        category = unicodedata.category(ch)
        # Cc = control (C0/C1, incl. DEL/NUL); Cf = format (bidi overrides,
        # zero-width joiners, BOM); Zl/Zp = U+2028/U+2029 (line/paragraph
        # separator) — the one pair of whitespace characters that behaves
        # like a control character in practice (splits a line in contexts
        # that only expect \n to do that) rather than like ordinary
        # printable space. None of these can be a real vault filename
        # character; all are classic obfuscation/injection vectors. Ordinary
        # space (Zs) is deliberately NOT in this set — it's legitimate
        # filename content.
        if category in ("Cc", "Cf", "Zl", "Zp"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="path contains invalid characters"
            )

    return path
