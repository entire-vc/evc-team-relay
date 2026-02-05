"""Slug generation utilities for web publishing."""

from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import models

# Reserved slugs that cannot be used for shares
RESERVED_SLUGS = {
    "login",
    "logout",
    "register",
    "api",
    "v1",
    "admin",
    "robots.txt",
    "sitemap.xml",
    "favicon.ico",
    "health",
    "docs",
    "openapi.json",
    "static",
    "assets",
    "_app",
}

# Cyrillic to Latin transliteration map (simplified Russian)
TRANSLITERATION_MAP = {
    "а": "a",
    "б": "b",
    "в": "v",
    "г": "g",
    "д": "d",
    "е": "e",
    "ё": "yo",
    "ж": "zh",
    "з": "z",
    "и": "i",
    "й": "j",
    "к": "k",
    "л": "l",
    "м": "m",
    "н": "n",
    "о": "o",
    "п": "p",
    "р": "r",
    "с": "s",
    "т": "t",
    "у": "u",
    "ф": "f",
    "х": "h",
    "ц": "c",
    "ч": "ch",
    "ш": "sh",
    "щ": "sch",
    "ъ": "",
    "ы": "y",
    "ь": "",
    "э": "e",
    "ю": "yu",
    "я": "ya",
}


def transliterate(text: str) -> str:
    """
    Transliterate Cyrillic text to Latin.

    Args:
        text: Input text with Cyrillic characters

    Returns:
        Transliterated text
    """
    result = []
    for char in text.lower():
        if char in TRANSLITERATION_MAP:
            result.append(TRANSLITERATION_MAP[char])
        else:
            result.append(char)
    return "".join(result)


def slugify(text: str) -> str:
    """
    Convert text to URL-friendly slug.

    Process:
    1. Transliterate Cyrillic to Latin
    2. Convert to lowercase
    3. Replace path separators with hyphens
    4. Remove file extension
    5. Replace spaces and special chars with hyphens
    6. Remove consecutive hyphens
    7. Trim hyphens from start/end
    8. Truncate to 100 characters

    Args:
        text: Input text (e.g., file path)

    Returns:
        URL-safe slug

    Examples:
        "Projects/My Document.md" -> "projects-my-document"
        "Мой Проект/Документ.md" -> "moj-proekt-dokument"
        "Test___File  .md" -> "test-file"
    """
    # Transliterate Cyrillic
    text = transliterate(text)

    # Convert to lowercase
    text = text.lower()

    # Replace path separators with hyphens
    text = text.replace("/", "-").replace("\\", "-")

    # Remove file extension (only .md, .canvas at the end)
    text = re.sub(r"\.(?:md|canvas)$", "", text)

    # Replace any non-alphanumeric characters (except hyphens) with hyphens
    text = re.sub(r"[^a-z0-9-]+", "-", text)

    # Remove consecutive hyphens
    text = re.sub(r"-+", "-", text)

    # Trim hyphens from start and end
    text = text.strip("-")

    # Truncate to 100 characters
    if len(text) > 100:
        text = text[:100].rstrip("-")

    return text


def is_slug_available(db: Session, slug: str, exclude_share_id: str | None = None) -> bool:
    """
    Check if a slug is available for use.

    Args:
        db: Database session
        slug: Slug to check
        exclude_share_id: Optional share ID to exclude from check (for updates)

    Returns:
        True if slug is available, False otherwise
    """
    import uuid

    # Check reserved slugs
    if slug.lower() in RESERVED_SLUGS:
        return False

    # Check database
    stmt = select(models.Share).where(models.Share.web_slug == slug)
    if exclude_share_id:
        # Convert string UUID to UUID object
        exclude_uuid = (
            uuid.UUID(exclude_share_id) if isinstance(exclude_share_id, str) else exclude_share_id
        )
        stmt = stmt.where(models.Share.id != exclude_uuid)

    existing = db.execute(stmt).scalar_one_or_none()
    return existing is None


def generate_unique_slug(db: Session, path: str, exclude_share_id: str | None = None) -> str:
    """
    Generate a unique slug from a path, handling collisions.

    If the base slug is taken, appends -2, -3, etc. until a unique slug is found.
    If path generates an empty slug, uses a UUID-based fallback.

    Args:
        db: Database session
        path: File path to generate slug from
        exclude_share_id: Optional share ID to exclude from uniqueness check

    Returns:
        Unique slug

    Examples:
        "Projects/Doc.md" -> "projects-doc"
        "Projects/Doc.md" (collision) -> "projects-doc-2"
        "..." (empty after slugify) -> "share-<uuid-prefix>"
    """
    base_slug = slugify(path)

    # Fallback for empty slugs
    if not base_slug:
        import uuid

        base_slug = f"share-{str(uuid.uuid4())[:8]}"

    # Check base slug
    if is_slug_available(db, base_slug, exclude_share_id):
        return base_slug

    # Try with numeric suffixes
    counter = 2
    while counter < 1000:  # Safety limit
        candidate = f"{base_slug}-{counter}"
        if is_slug_available(db, candidate, exclude_share_id):
            return candidate
        counter += 1

    # Fallback to UUID if we somehow can't find a unique slug
    import uuid

    return f"{base_slug[:50]}-{str(uuid.uuid4())[:8]}"
