from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.db.models import InstanceSetting

# Default branding values.
# logo_url is PNG (TR-62) — this value is also used as og:image/twitter:image
# on the web-publish share pages, and most social-card crawlers (Twitter/X,
# etc.) don't render SVG. favicon_url stays SVG — browsers render SVG
# favicons fine, and that surface isn't scraped by social crawlers.
DEFAULT_BRANDING = {
    "name": "Relay Server",
    "logo_url": "/static/img/evc-ava.png",
    "favicon_url": "/static/img/evc-ava.svg",
}


def get_setting(db: Session, key: str) -> str | None:
    """Get instance setting value by key.

    Args:
        db: Database session
        key: Setting key

    Returns:
        Setting value or None if not found
    """
    result = db.execute(select(InstanceSetting).where(InstanceSetting.key == key))
    setting = result.scalar_one_or_none()
    return setting.value if setting else None


def set_setting(db: Session, key: str, value: str) -> None:
    """Set instance setting value.

    Args:
        db: Database session
        key: Setting key
        value: Setting value
    """
    result = db.execute(select(InstanceSetting).where(InstanceSetting.key == key))
    setting = result.scalar_one_or_none()

    if setting:
        setting.value = value
    else:
        setting = InstanceSetting(key=key, value=value)
        db.add(setting)

    db.commit()


def _this_instance_origin() -> str | None:
    """This instance's own externally-reachable origin, no trailing slash.

    Priority: settings.control_plane_public_url (Daedalus's review on
    #5f51a2dd, correcting an earlier wrong claim in this function that the
    field is "never wired" — docker-compose.yml's control-plane service has
    no CONTROL_PLANE_PUBLIC_URL in its `environment:` block, but it does have
    `env_file: ./.env`, which loads the WHOLE file into the container, not
    just keys also listed under `environment:`. Confirmed live inside both
    running containers: `docker compose exec control-plane env` shows the
    real per-host value on both tr-relay-vm and tr-ru-vm) → then
    cors_allowed_origins (kept as a fallback: it's read the same env_file
    way, but unlike control_plane_public_url it is NOT guaranteed to be set
    — tr-relay-vm's real .env has no CORS/ORIGIN key at all, so where it
    "works" today that's the Field default in config.py coincidentally
    matching that host's domain, not a property of the key) → None (leave
    the URL relative — a relative path is a lesser failure than silently
    absolutizing to the wrong domain).

    The bash-side equivalent for the same "what's my own origin" question is
    resolve_smoke_url() in scripts/deploy.sh (#08e44245) — same priority
    shape (an explicit override, then CONTROL_PLANE_PUBLIC_URL, then a
    weaker fallback, then give up cleanly) for the same reason. Keep the two
    in sync if the underlying per-host env keys ever change; there is no
    code-level sharing between them (bash vs. Python, deploy-time vs.
    runtime), so this comment is the only link.
    """
    settings = get_settings()
    placeholder = Settings.model_fields["control_plane_public_url"].default
    candidate = (settings.control_plane_public_url or "").strip()
    if candidate != placeholder and (
        candidate.startswith("http://") or candidate.startswith("https://")
    ):
        return candidate.rstrip("/")

    origins = settings.cors_allowed_origins or ""
    first = origins.split(",")[0].strip()
    if first.startswith("http://") or first.startswith("https://"):
        return first.rstrip("/")
    return None


def _absolutize(url: str, origin: str | None) -> str:
    """Resolve a relative branding URL against this instance's own origin.

    logo_url/favicon_url are consumed by clients with no page to resolve a
    relative path against — the Obsidian plugin's server list, in
    particular, has no base URL of its own (#5f51a2dd: an instance that
    never set an explicit absolute branding URL served a bare
    "/static/img/..." path, which the plugin could not render). Already-
    absolute URLs (any scheme, e.g. "http://"/"https://") pass through
    unchanged — this only fills in a missing origin, never rewrites one an
    admin explicitly set.
    """
    if not url or not url.startswith("/") or not origin:
        return url
    return f"{origin}{url}"


def get_branding(db: Session) -> dict[str, str]:
    """Get instance branding settings.

    Returns:
        Dictionary with keys: name, logo_url, favicon_url, custom_head_code, custom_body_code
    """
    name = get_setting(db, "branding_name") or DEFAULT_BRANDING["name"]
    logo_url = get_setting(db, "branding_logo_url") or DEFAULT_BRANDING["logo_url"]
    favicon_url = get_setting(db, "branding_favicon_url") or DEFAULT_BRANDING["favicon_url"]
    custom_head_code = get_setting(db, "branding_custom_head_code") or ""
    custom_body_code = get_setting(db, "branding_custom_body_code") or ""

    origin = _this_instance_origin()

    return {
        "name": name,
        "logo_url": _absolutize(logo_url, origin),
        "favicon_url": _absolutize(favicon_url, origin),
        "custom_head_code": custom_head_code,
        "custom_body_code": custom_body_code,
    }


def set_branding(
    db: Session,
    name: str,
    logo_url: str,
    favicon_url: str,
    custom_head_code: str = "",
    custom_body_code: str = "",
) -> dict[str, str]:
    """Set instance branding settings.

    Args:
        db: Database session
        name: Instance name
        logo_url: Logo URL
        favicon_url: Favicon URL
        custom_head_code: Custom HTML/JS to inject into <head>
        custom_body_code: Custom HTML/JS to inject into <body>

    Returns:
        Updated branding settings
    """
    set_setting(db, "branding_name", name)
    set_setting(db, "branding_logo_url", logo_url)
    set_setting(db, "branding_favicon_url", favicon_url)
    set_setting(db, "branding_custom_head_code", custom_head_code)
    set_setting(db, "branding_custom_body_code", custom_body_code)

    return {
        "name": name,
        "logo_url": logo_url,
        "favicon_url": favicon_url,
        "custom_head_code": custom_head_code,
        "custom_body_code": custom_body_code,
    }
