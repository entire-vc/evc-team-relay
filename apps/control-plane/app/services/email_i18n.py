"""Locale support for system emails.

The locale is a per-deployment setting (``EMAIL_LOCALE``), not a per-user one:
a deployment serves one market. English is the source of truth; a locale only
overrides what it translates, and everything else falls back to English.

Bodies live in ``templates/emails/<locale>/`` (searched before the shared
directory); subjects, date formats and the small vocabulary that templates
interpolate (roles, share kinds) live here.
"""

from __future__ import annotations

import logging
from datetime import datetime

logger = logging.getLogger(__name__)

DEFAULT_LOCALE = "en"
SUPPORTED_LOCALES = ("en", "ru")

# Subjects, keyed by message. English is the fallback for a missing key.
SUBJECTS: dict[str, dict[str, str]] = {
    "en": {
        "invite_notification": "You've been invited to collaborate on {share_path}",
        "invite_accepted": "{new_member_email} accepted your invite to {share_path}",
        "member_added": "You've been added to {share_path}",
        "share_deleted": "Share '{share_path}' has been deleted",
        "password_reset": "Password Reset Request",
        "email_verification": "Verify Your Email Address",
        "security_new_session": "New login to your account",
        "security_password_changed": "Your password was changed",
        "billing_cancellation_deletion_scheduled": (
            "Your subscription was cancelled — data deletion scheduled"
        ),
        "billing_cancellation_deletion_executed": "Your data has been deleted",
        "billing_payment_failed": "Payment failed — please update your payment method",
    },
    "ru": {
        "invite_notification": "Вас приглашают к совместной работе: {share_path}",
        "invite_accepted": "{new_member_email} принял(а) ваше приглашение в {share_path}",
        "member_added": "Вас добавили в Шару {share_path}",
        "share_deleted": "Шара «{share_path}» удалена",
        "password_reset": "Сброс пароля",
        "email_verification": "Подтвердите адрес электронной почты",
        "security_new_session": "Новый вход в вашу учётную запись",
        "security_password_changed": "Ваш пароль изменён",
        "billing_cancellation_deletion_scheduled": (
            "Подписка отменена — данные будут удалены {deletion_date}"
        ),
        "billing_cancellation_deletion_executed": "Ваши данные удалены",
        "billing_payment_failed": "Платёж не прошёл — обновите способ оплаты",
    },
}

# Word-for-word values the templates print with ``role|capitalize`` /
# ``share_kind|capitalize`` in English. Unknown values pass through unchanged.
ROLE_LABELS: dict[str, dict[str, str]] = {
    "en": {"viewer": "Viewer", "editor": "Editor"},
    "ru": {"viewer": "Читатель", "editor": "Редактор"},
}
SHARE_KIND_LABELS: dict[str, dict[str, str]] = {
    "en": {"doc": "Doc", "folder": "Folder"},
    "ru": {"doc": "Документ", "folder": "Папка"},
}

_DATE_FORMATS: dict[str, tuple[str, str, str]] = {
    # (date, date + hours/minutes, date + seconds)
    "en": ("%Y-%m-%d", "%Y-%m-%d %H:%M UTC", "%Y-%m-%d %H:%M:%S UTC"),
    "ru": ("%d.%m.%Y", "%d.%m.%Y %H:%M UTC", "%d.%m.%Y %H:%M:%S UTC"),
}


def normalize_locale(value: str | None) -> str:
    """Return a supported locale; unknown or empty values fall back to English."""
    locale = (value or DEFAULT_LOCALE).strip().lower().replace("_", "-").split("-")[0]
    if locale in SUPPORTED_LOCALES:
        return locale
    logger.warning(
        "Unsupported EMAIL_LOCALE %r, falling back to %r (supported: %s)",
        value,
        DEFAULT_LOCALE,
        ", ".join(SUPPORTED_LOCALES),
    )
    return DEFAULT_LOCALE


def subject(locale: str, key: str, **values: str) -> str:
    """Render the subject for ``key`` in ``locale`` (English if untranslated)."""
    template = SUBJECTS.get(locale, {}).get(key) or SUBJECTS[DEFAULT_LOCALE][key]
    return template.format(**values)


def format_date(locale: str, value: datetime) -> str:
    return value.strftime(_DATE_FORMATS.get(locale, _DATE_FORMATS[DEFAULT_LOCALE])[0])


def format_datetime(locale: str, value: datetime, *, seconds: bool = False) -> str:
    formats = _DATE_FORMATS.get(locale, _DATE_FORMATS[DEFAULT_LOCALE])
    return value.strftime(formats[2] if seconds else formats[1])


def role_label(locale: str, role: str) -> str:
    return ROLE_LABELS.get(locale, ROLE_LABELS[DEFAULT_LOCALE]).get(role, str(role).capitalize())


def share_kind_label(locale: str, kind: str) -> str:
    return SHARE_KIND_LABELS.get(locale, SHARE_KIND_LABELS[DEFAULT_LOCALE]).get(
        kind, str(kind).capitalize()
    )
