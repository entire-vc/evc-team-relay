"""Tests for listmonk_service.py — TR-34 (case-sensitivity) + U1 (SQL injection).

TR-34: Listmonk stores/matches subscriber emails lowercase internally. A
mixed-case registration email POSTs fine the first time, but the 409-conflict
lookup in _update_existing() compared against the ORIGINAL mixed-case email —
which never matches Listmonk's lowercased row — so attribs/list membership
silently stopped updating on every subsequent sync, forever.

U1: the same lookup builds its `query` param via an f-string:
`f"subscribers.email = '{email}'"`. Listmonk's GET /api/subscribers `query`
param is a raw Postgres SQL WHERE-clause fragment executed against Listmonk's
own DB. A bare `'` is valid in an RFC 5321 local-part (o'brien@x.com is a
legal email) and breaks out of the string literal — a genuine SQL injection
surface on Listmonk's backend.

Only httpx (the real external boundary — network calls to Listmonk) is
mocked; the escaping/normalization logic under test runs for real.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.listmonk_service import ListmonkService, _sql_string_literal


def _make_response(status_code: int, json_data: dict | None = None) -> AsyncMock:
    resp = AsyncMock()
    resp.status_code = status_code
    resp.json = lambda: json_data or {}
    resp.raise_for_status = lambda: None
    return resp


def _make_service(client: AsyncMock) -> ListmonkService:
    service = ListmonkService()
    service._client = client
    return service


class TestSqlStringLiteralEscaping:
    def test_no_special_characters_unchanged(self):
        assert _sql_string_literal("plain@example.com") == "plain@example.com"

    def test_single_quote_is_doubled(self):
        # The actual regression: this is how you escape a value for safe
        # embedding in a single-quoted Postgres string literal — a bare `'`
        # would otherwise close the literal early.
        assert _sql_string_literal("o'brien@x.com") == "o''brien@x.com"

    def test_multiple_quotes_all_escaped(self):
        assert _sql_string_literal("a'b'c") == "a''b''c"


class TestUpsertSubscriberCaseNormalization:
    @pytest.mark.asyncio
    async def test_post_payload_uses_lowercased_email(self):
        client = AsyncMock()
        client.post = AsyncMock(return_value=_make_response(200))
        service = _make_service(client)

        await service.upsert_subscriber(
            email="Jimenezisaac021@GMAIL.com",
            name="Isaac",
            casdoor=True,
            registered_at=datetime.now(timezone.utc),
            has_share=False,
            lifecycle_emails_on=True,
        )

        posted_payload = client.post.call_args.kwargs["json"]
        assert posted_payload["email"] == "jimenezisaac021@gmail.com"

    @pytest.mark.asyncio
    async def test_409_conflict_lookup_uses_lowercased_email_not_original_case(self):
        """This is the actual regression: pre-fix, the lookup query embedded
        the ORIGINAL mixed-case email, which never matches Listmonk's
        lowercased row — the subscriber is "found via 409" but not via the
        lookup, and the WARNING/no-op repeats on every future sync."""
        client = AsyncMock()
        client.post = AsyncMock(return_value=_make_response(409))
        client.get = AsyncMock(
            return_value=_make_response(
                200,
                {
                    "data": {
                        "results": [{"id": 42, "lists": [], "attribs": {}, "status": "enabled"}]
                    }
                },
            )
        )
        client.put = AsyncMock(return_value=_make_response(200))
        service = _make_service(client)

        result = await service.upsert_subscriber(
            email="Jimenezisaac021@GMAIL.com",
            name="Isaac",
            casdoor=True,
            registered_at=datetime.now(timezone.utc),
            has_share=False,
            lifecycle_emails_on=True,
        )

        assert result is True
        lookup_query = client.get.call_args.kwargs["params"]["query"]
        assert lookup_query == "subscribers.email = 'jimenezisaac021@gmail.com'"
        # PUT also carries the normalized email through.
        put_payload = client.put.call_args.kwargs["json"]
        assert put_payload["email"] == "jimenezisaac021@gmail.com"


class TestUpdateExistingLookupEscaping:
    @pytest.mark.asyncio
    async def test_email_with_single_quote_is_escaped_in_lookup_query(self):
        client = AsyncMock()
        client.post = AsyncMock(return_value=_make_response(409))
        client.get = AsyncMock(return_value=_make_response(200, {"data": {"results": []}}))
        service = _make_service(client)

        await service.upsert_subscriber(
            email="o'brien@x.com",
            name="O'Brien",
            casdoor=False,
            registered_at=datetime.now(timezone.utc),
            has_share=False,
            lifecycle_emails_on=True,
        )

        lookup_query = client.get.call_args.kwargs["params"]["query"]
        # Escaped: the literal stays syntactically closed. Pre-fix this was
        # "subscribers.email = 'o'brien@x.com'" — an unescaped `'` breaking
        # out of the string literal mid-fragment.
        assert lookup_query == "subscribers.email = 'o''brien@x.com'"
        assert "= 'o'brien" not in lookup_query


def _make_row(email: str, registered_at: datetime):
    return SimpleNamespace(
        id=f"id-{email}",
        email=email,
        registered_at=registered_at,
        casdoor=True,
        has_share=False,
        name=email.split("@")[0],
        lifecycle_emails=True,
    )


class TestSyncNewUsersCursorOnPartialFailure:
    """TR-49 regression: a per-row exception must not let the cursor jump past it.

    Pre-fix, `max_ts` advanced on every row that didn't itself raise — so a
    later, successfully-synced row would drag the watermark past an earlier
    row that failed, and the failed row's `registered_at` would never again
    satisfy `created_at > :since` on the next cycle. That user silently
    stopped receiving lifecycle syncs for up to 24h (until the nightly
    backfill caught it via full reconcile).
    """

    @pytest.mark.asyncio
    async def test_cursor_freezes_at_first_failed_row_not_at_last_row(self):
        base = datetime(2026, 7, 20, tzinfo=timezone.utc)
        rows = [
            _make_row("a@example.com", base),
            _make_row("b@example.com", base + timedelta(minutes=1)),  # fails
            _make_row("c@example.com", base + timedelta(minutes=2)),  # synced after the failure
        ]

        db = MagicMock()
        db.execute.return_value.fetchall.return_value = rows

        service = ListmonkService()

        async def fake_upsert(*, email, **kwargs):
            if email == "b@example.com":
                raise RuntimeError("boom")
            return True

        with patch.object(service, "upsert_subscriber", new=AsyncMock(side_effect=fake_upsert)):
            synced, max_ts = await service.sync_new_users(db, since=base - timedelta(days=1))

        # a@ (before the failure) + c@ (attempted after, still succeeds) = 2 synced.
        assert synced == 2
        # The cursor must stay at a@'s timestamp — the last row that synced
        # successfully BEFORE the failure — so b@'s created_at is still
        # > the persisted cursor and gets re-fetched + retried next cycle.
        assert max_ts == base
        assert max_ts < rows[1].registered_at
        assert max_ts < rows[2].registered_at

    @pytest.mark.asyncio
    async def test_no_failures_advances_cursor_to_last_row(self):
        base = datetime(2026, 7, 20, tzinfo=timezone.utc)
        rows = [
            _make_row("a@example.com", base),
            _make_row("b@example.com", base + timedelta(minutes=1)),
        ]
        db = MagicMock()
        db.execute.return_value.fetchall.return_value = rows

        service = ListmonkService()
        with patch.object(service, "upsert_subscriber", new=AsyncMock(return_value=True)):
            synced, max_ts = await service.sync_new_users(db, since=base - timedelta(days=1))

        assert synced == 2
        assert max_ts == rows[-1].registered_at
