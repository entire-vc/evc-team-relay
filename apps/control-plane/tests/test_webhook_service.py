"""Tests for webhook SSRF protection.

Level 1 (resolve_pinned_ip): pure unit tests, no DB/network.
Level 2 (deliver_webhook): real db_session fixture, mocked httpx (true
external boundary — no network calls) and mocked/monkeypatched DNS resolution.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.orm import Session

from app.db import models
from app.services import webhook_service

# ── resolve_pinned_ip — pure unit tests ──────────────────────


class TestResolvePinnedIp:
    def test_rejects_private_literal_ip(self):
        with pytest.raises(ValueError, match="private, loopback, or reserved"):
            webhook_service.resolve_pinned_ip("10.0.0.5")

    def test_rejects_loopback_literal_ip(self):
        with pytest.raises(ValueError, match="private, loopback, or reserved"):
            webhook_service.resolve_pinned_ip("127.0.0.1")

    def test_rejects_localhost_hostname(self):
        with pytest.raises(ValueError, match="localhost"):
            webhook_service.resolve_pinned_ip("localhost")

    def test_rejects_internal_suffix(self):
        with pytest.raises(ValueError, match="internal"):
            webhook_service.resolve_pinned_ip("service.internal")

    def test_accepts_public_literal_ip(self):
        assert webhook_service.resolve_pinned_ip("8.8.8.8") == "8.8.8.8"

    def test_dns_resolution_to_public_ip_returns_that_ip(self):
        with patch(
            "app.services.webhook_service.socket.getaddrinfo",
            return_value=[(2, 1, 6, "", ("93.184.216.34", 0))],
        ):
            assert webhook_service.resolve_pinned_ip("example.com") == "93.184.216.34"

    def test_dns_rebinding_to_private_ip_is_rejected(self):
        """The core DNS-rebinding case: a hostname that resolves to a private
        address at delivery time must be blocked, even though the same
        hostname could have resolved publicly when the webhook was created."""
        with patch(
            "app.services.webhook_service.socket.getaddrinfo",
            return_value=[(2, 1, 6, "", ("169.254.169.254", 0))],
        ):
            with pytest.raises(ValueError, match="private or reserved"):
                webhook_service.resolve_pinned_ip("attacker-controlled.example.com")

    def test_unresolvable_host_is_rejected(self):
        with patch(
            "app.services.webhook_service.socket.getaddrinfo",
            side_effect=OSError("nodename nor servname provided"),
        ):
            with pytest.raises(ValueError, match="could not be resolved"):
                webhook_service.resolve_pinned_ip("nonexistent.example.invalid")


# ── deliver_webhook — DNS-rebinding-at-delivery-time integration ──────


def _make_webhook(db: Session, url: str = "https://example.com/hook") -> models.Webhook:
    webhook = models.Webhook(
        id=uuid.uuid4(),
        user_id=None,
        name="test-hook",
        url=url,
        secret=webhook_service.generate_webhook_secret(),
        events=["share.created"],
        active=True,
        failure_count=0,
    )
    db.add(webhook)
    db.commit()
    db.refresh(webhook)
    return webhook


class TestDeliverWebhookSsrfGuard:
    @pytest.mark.asyncio
    async def test_delivery_blocked_when_host_rebinds_to_private_ip(self, db_session: Session):
        """Webhook was valid at create time; by delivery time its DNS record
        now points at a private address. deliver_webhook must refuse to send
        rather than letting httpx do its own (unvalidated) DNS lookup."""
        webhook = _make_webhook(db_session)
        delivery = webhook_service.queue_webhook_delivery(
            db_session, webhook, "share.created", {"foo": "bar"}
        )

        with patch(
            "app.services.webhook_service.socket.getaddrinfo",
            return_value=[(2, 1, 6, "", ("127.0.0.1", 0))],
        ):
            with patch("app.services.webhook_service.httpx.AsyncClient") as mock_client_cls:
                result = await webhook_service.deliver_webhook(db_session, delivery)

        assert result is False
        # The whole point of the fix: httpx must never even be constructed —
        # no unvalidated DNS lookup / connection attempt happens.
        mock_client_cls.assert_not_called()
        assert "blocked" in (delivery.response_body or "").lower()

    @pytest.mark.asyncio
    async def test_delivery_connects_to_pinned_ip_not_original_host(self, db_session: Session):
        """When the host resolves safely, the actual httpx connection target
        must be the validated IP (not the hostname, which httpx would
        re-resolve on its own)."""
        webhook = _make_webhook(db_session, url="https://example.com/hook")
        delivery = webhook_service.queue_webhook_delivery(
            db_session, webhook, "share.created", {"foo": "bar"}
        )

        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_response.text = "ok"

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "app.services.webhook_service.socket.getaddrinfo",
            return_value=[(2, 1, 6, "", ("93.184.216.34", 0))],
        ):
            with patch("app.services.webhook_service.httpx.AsyncClient", return_value=mock_client):
                result = await webhook_service.deliver_webhook(db_session, delivery)

        assert result is True
        called_url = mock_client.post.call_args.args[0]
        called_kwargs = mock_client.post.call_args.kwargs
        assert "93.184.216.34" in called_url
        assert "example.com" not in called_url
        assert called_kwargs["headers"]["Host"] == "example.com"
        assert called_kwargs["extensions"] == {"sni_hostname": "example.com"}
