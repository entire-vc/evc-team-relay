from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import InstanceSetting

# Default branding values
DEFAULT_BRANDING = {
    "name": "Relay Server",
    "logo_url": "/static/img/evc-ava.svg",
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


def get_branding(db: Session) -> dict[str, str]:
    """Get instance branding settings.

    Returns:
        Dictionary with keys: name, logo_url, favicon_url
    """
    name = get_setting(db, "branding_name") or DEFAULT_BRANDING["name"]
    logo_url = get_setting(db, "branding_logo_url") or DEFAULT_BRANDING["logo_url"]
    favicon_url = get_setting(db, "branding_favicon_url") or DEFAULT_BRANDING["favicon_url"]

    return {
        "name": name,
        "logo_url": logo_url,
        "favicon_url": favicon_url,
    }


def set_branding(db: Session, name: str, logo_url: str, favicon_url: str) -> dict[str, str]:
    """Set instance branding settings.

    Args:
        db: Database session
        name: Instance name
        logo_url: Logo URL
        favicon_url: Favicon URL

    Returns:
        Updated branding settings
    """
    set_setting(db, "branding_name", name)
    set_setting(db, "branding_logo_url", logo_url)
    set_setting(db, "branding_favicon_url", favicon_url)

    return {
        "name": name,
        "logo_url": logo_url,
        "favicon_url": favicon_url,
    }
