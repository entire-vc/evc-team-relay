"""System emails follow the deployment's EMAIL_LOCALE (Mesh #0e42e4dd).

teamrelay.ru customers must receive the offer §13.3 data-deletion notice in
Russian; every other deployment keeps the English emails byte-for-byte.
These tests assert on rendered *content* (subject + text + HTML as queued),
not merely on the absence of an error.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

import pytest
from jinja2 import Environment, FileSystemLoader, meta
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.db.models import EmailQueue
from app.services import email_i18n, email_service
from app.services.email_service import TEMPLATE_DIR

CYRILLIC = re.compile(r"[А-Яа-яЁё]")
DELETION_DATE = datetime(2026, 10, 24, tzinfo=timezone.utc)


def make_service(locale: str) -> email_service.EmailService:
    settings = get_settings().model_copy(update={"email_locale": locale})
    return email_service.EmailService(settings)


def queued(db: Session, to_email: str) -> EmailQueue:
    return db.query(EmailQueue).filter(EmailQueue.to_email == to_email).one()


class TestDeletionScheduled:
    @pytest.mark.asyncio
    async def test_ru_renders_russian_subject_and_body(self, db_session: Session):
        to = "ru-scheduled@example.com"
        await make_service("ru").send_billing_cancellation_deletion_scheduled(
            db_session, to, DELETION_DATE, 30
        )
        row = queued(db_session, to)

        assert row.subject == "Подписка отменена — данные будут удалены 24.10.2026"
        for body in (row.body_text, row.body_html):
            # Everything §13.3 requires the customer to be told.
            assert "24.10.2026" in body  # date
            assert "30 дн." in body  # retention period
            assert "п. 13.3 оферты" in body
            assert "содержимое ваших" in body and "история версий" in body
            assert "веб-публикации" in body  # what is deleted
            assert "выгрузить данные средствами плагина" in body  # export path
            assert "оформить подписку снова" in body  # resubscribe cancels deletion
            assert "удаление будет отменено" in body
        assert '<html lang="ru">' in row.body_html
        # No English leaked from the shared template or base footer.
        assert "Your subscription" not in row.body_html
        assert "All rights reserved" not in row.body_html

    @pytest.mark.asyncio
    async def test_en_is_unchanged(self, db_session: Session):
        to = "en-scheduled@example.com"
        await make_service("en").send_billing_cancellation_deletion_scheduled(
            db_session, to, DELETION_DATE, 30
        )
        row = queued(db_session, to)

        assert row.subject == "Your subscription was cancelled — data deletion scheduled"
        assert "YOUR DATA WILL BE DELETED ON 2026-10-24" in row.body_text
        assert "30 days" in row.body_text
        assert "Your data will be deleted on 2026-10-24" in row.body_html
        assert '<html lang="en">' in row.body_html
        for text in (row.subject, row.body_text, row.body_html):
            assert not CYRILLIC.search(text)


class TestDeletionExecuted:
    @pytest.mark.asyncio
    async def test_ru_and_en(self, db_session: Session):
        await make_service("ru").send_billing_cancellation_deletion_executed(
            db_session, "ru-executed@example.com"
        )
        await make_service("en").send_billing_cancellation_deletion_executed(
            db_session, "en-executed@example.com"
        )
        ru = queued(db_session, "ru-executed@example.com")
        en = queued(db_session, "en-executed@example.com")

        assert ru.subject == "Ваши данные удалены"
        assert "удалены без возможности восстановления" in ru.body_text
        assert "веб-публикации" in ru.body_html
        assert en.subject == "Your data has been deleted"
        assert "permanently deleted" in en.body_text
        assert not CYRILLIC.search(en.subject + en.body_text + en.body_html)


class TestOtherEmailsFollowLocale:
    @pytest.mark.asyncio
    async def test_ru_invite_uses_translated_role_kind_date_and_subject(self, db_session: Session):
        to = "ru-invite@example.com"
        await make_service("ru").send_invite_notification(
            db_session,
            to,
            inviter_email="owner@example.com",
            share_path="Продукт",
            share_kind="folder",
            role="editor",
            invite_url="https://cp.teamrelay.ru/invite/abc",
            expires_at=datetime(2026, 10, 1, 9, 30, tzinfo=timezone.utc),
        )
        row = queued(db_session, to)

        assert row.subject == "Вас приглашают к совместной работе: Продукт"
        assert "Папка: Продукт" in row.body_text
        assert "Ваша роль: Редактор" in row.body_text
        assert "Действует до: 01.10.2026 09:30 UTC" in row.body_text
        assert "https://cp.teamrelay.ru/invite/abc" in row.body_html

    @pytest.mark.asyncio
    async def test_en_invite_unchanged(self, db_session: Session):
        to = "en-invite@example.com"
        await make_service("en").send_invite_notification(
            db_session,
            to,
            inviter_email="owner@example.com",
            share_path="Product",
            share_kind="folder",
            role="editor",
            invite_url="https://cp.tr.entire.vc/invite/abc",
            expires_at=datetime(2026, 10, 1, 9, 30, tzinfo=timezone.utc),
        )
        row = queued(db_session, to)

        assert row.subject == "You've been invited to collaborate on Product"
        assert "Folder: Product" in row.body_text
        assert "Your role: Editor" in row.body_text
        assert "Expires: 2026-10-01 09:30 UTC" in row.body_text

    @pytest.mark.asyncio
    async def test_ru_security_and_payment_subjects(self, db_session: Session):
        svc = make_service("ru")
        await svc.send_billing_payment_failed(db_session, "ru-pay@example.com")
        row = queued(db_session, "ru-pay@example.com")

        assert row.subject == "Платёж не прошёл — обновите способ оплаты"
        assert "Обновите способ оплаты" in row.body_text

    @pytest.mark.asyncio
    async def test_ru_password_reset_and_verification_have_russian_bodies(self):
        svc = make_service("ru")
        html, text = svc._render_template("password-reset", {"reset_url": "https://x/reset"})
        assert html is None  # plain-text only, same as English today
        assert "Вы запросили сброс пароля" in text and "https://x/reset" in text

        _, text = svc._render_template("email-verification", {"verification_url": "https://x/v"})
        assert "Подтвердите адрес электронной почты" in text and "https://x/v" in text
        assert email_i18n.subject("ru", "password_reset") == "Сброс пароля"


class TestLocaleSelection:
    def test_default_is_english(self):
        assert Settings.model_fields["email_locale"].default == "en"

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [("ru", "ru"), ("RU", "ru"), ("ru-RU", "ru"), ("ru_RU", "ru"), ("en", "en"), (None, "en")],
    )
    def test_normalizes_supported(self, raw, expected):
        assert email_i18n.normalize_locale(raw) == expected

    def test_unsupported_locale_falls_back_to_english(self):
        assert email_i18n.normalize_locale("de") == "en"
        assert make_service("de").locale == "en"

    def test_untranslated_template_falls_back_to_shared_one(self):
        # lifecycle-* have no ru/ copy (already Russian): they must still render,
        # inside the Russian base layout.
        svc = make_service("ru")
        assert not (TEMPLATE_DIR / "ru" / "lifecycle-no-share-24h.html").exists()
        html, text = svc._render_template(
            "lifecycle-no-share-24h", {"name": "Анна", "unsubscribe_url": "https://x/u"}
        )
        assert "Привет, Анна!" in text
        assert "Это письмо отправлено" in html  # ru/base.html footer


def _template_vars(directory, name: str) -> set[str]:
    env = Environment(loader=FileSystemLoader(str(directory)))
    # Parsing only needs the filters to exist; EmailService registers the real ones.
    env.filters.update(role_label=str, kind_label=str)
    source = env.loader.get_source(env, name)[0]
    return meta.find_undeclared_variables(env.parse(source))


# Already-Russian templates: shared by every locale, no ru/ twin needed.
_SHARED_ONLY = {"lifecycle-inactive-after-share", "lifecycle-no-share-24h"}
_ROOT_TEMPLATES = sorted(
    p.name for p in TEMPLATE_DIR.iterdir() if p.is_file() and p.suffix in {".html", ".txt"}
)


@pytest.mark.parametrize(
    "name", [n for n in _ROOT_TEMPLATES if n.split(".")[0] not in _SHARED_ONLY]
)
def test_every_english_template_has_a_russian_twin_using_the_same_variables(name):
    ru_path = TEMPLATE_DIR / "ru" / name
    assert ru_path.exists(), f"missing Russian template: ru/{name}"
    ru_vars = _template_vars(TEMPLATE_DIR / "ru", name)
    en_vars = _template_vars(TEMPLATE_DIR, name)
    assert (
        ru_vars <= en_vars
    ), f"ru/{name} uses variables the context never provides: {ru_vars - en_vars}"
    if name != "base.html":  # the Russian footer intentionally drops the © year line
        assert (
            en_vars <= ru_vars
        ), f"ru/{name} lost context the English one shows: {en_vars - ru_vars}"
