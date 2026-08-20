"""Tests for Settings.effective_relay_audience (#f975dd60).

Level 1: Unit tests — pure config derivation, no external dependencies.
"""

from __future__ import annotations

from app.core.config import Settings


class TestEffectiveRelayAudience:
    def test_explicit_relay_audience_wins(self):
        settings = Settings(
            relay_public_url="wss://tr.entire.vc/doc/ws",
            relay_audience="https://custom.example.com",
        )
        assert settings.effective_relay_audience == "https://custom.example.com"

    def test_derived_from_relay_public_url_when_unset(self):
        """Default case: strip scheme/path from relay_public_url, force https.

        This must NOT just reuse relay_public_url verbatim — that carries the
        wss:// scheme and /doc/ws path clients use, which relay.toml's
        [server].url (the value relay-server compares aud against) does not.
        """
        settings = Settings(relay_public_url="wss://tr.entire.vc/doc/ws", relay_audience=None)
        assert settings.effective_relay_audience == "https://tr.entire.vc"

    def test_derived_default_has_no_path_or_query(self):
        settings = Settings(
            relay_public_url="wss://relay.example.com/doc/ws?foo=bar", relay_audience=None
        )
        assert settings.effective_relay_audience == "https://relay.example.com"

    def test_default_token_issuer_is_relay_server(self):
        """Must default to a value in relay-server's VALID_ISSUERS allowlist
        (auth.rs) for the currently-deployed image — NOT 'relay-control-plane',
        which is not accepted by evc-relay-server:0.9.9.
        """
        settings = Settings()
        assert settings.relay_token_issuer == "relay-server"


class TestRelayDocWsUrl:
    """Tests for Settings.relay_doc_ws_url (#b1e88884).

    relay-server deprecated the flat /doc/ws/:doc_id route in favor of
    /d/:doc_id/ws/:doc_id2 (doc_id doubled). The client-side YSweetProvider
    appends "/<doc_id>" to whatever URL we return, so relay_doc_ws_url must
    return ".../d/<doc_id>/ws" for the final connect URL to land on the
    doubled-doc_id route with matching segments.
    """

    def test_builds_per_doc_path_dropping_configured_path(self):
        """A leftover /doc/ws suffix on RELAY_PUBLIC_URL must be ignored —
        only scheme+host survive, and the doc-specific /d/{doc_id}/ws path
        is appended fresh."""
        settings = Settings(relay_public_url="wss://tr.entire.vc/doc/ws")
        assert settings.relay_doc_ws_url("abc-123") == "wss://tr.entire.vc/d/abc-123/ws"

    def test_bare_relay_public_url_also_works(self):
        settings = Settings(relay_public_url="wss://tr.entire.vc")
        assert settings.relay_doc_ws_url("abc-123") == "wss://tr.entire.vc/d/abc-123/ws"

    def test_different_doc_ids_get_different_urls(self):
        settings = Settings(relay_public_url="wss://relay.test")
        assert settings.relay_doc_ws_url("doc-a") == "wss://relay.test/d/doc-a/ws"
        assert settings.relay_doc_ws_url("doc-b") == "wss://relay.test/d/doc-b/ws"
