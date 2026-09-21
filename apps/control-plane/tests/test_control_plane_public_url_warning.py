"""Startup warning for a CONTROL_PLANE_PUBLIC_URL left at its placeholder (GH #252).

Level 1: unit tests on the pure Settings property, plus the startup hook through the
real build_app() wiring. The warning must never raise: some installs use localhost
on purpose.
"""

from __future__ import annotations

import logging

import pytest

from app.core.config import Settings, get_settings
from app.main import build_app

PLACEHOLDER = "http://localhost:8000"


class TestControlPlanePublicUrlWarning:
    def test_warns_when_placeholder_and_relay_is_public(self):
        settings = Settings(
            control_plane_public_url=PLACEHOLDER, relay_public_url="wss://relay.example.com"
        )
        message = settings.control_plane_public_url_warning
        assert message is not None
        assert "CONTROL_PLANE_PUBLIC_URL" in message
        assert "relay.example.com" in message
        assert "attachment" in message

    def test_no_warning_when_value_set_explicitly(self):
        settings = Settings(
            control_plane_public_url="https://cp.example.com",
            relay_public_url="wss://relay.example.com",
        )
        assert settings.control_plane_public_url_warning is None

    @pytest.mark.parametrize(
        "relay_url",
        [
            "wss://relay.localhost",  # the code default
            "wss://localhost",
            "ws://127.0.0.1:8080",
            "ws://0.0.0.0:8080",
        ],
    )
    def test_no_warning_when_relay_is_local(self, relay_url):
        """localhost on purpose (dev / single machine): the default is not a mistake."""
        settings = Settings(control_plane_public_url=PLACEHOLDER, relay_public_url=relay_url)
        assert settings.control_plane_public_url_warning is None

    def test_blank_value_counts_as_unset_only_when_it_is_the_placeholder(self):
        """A blank explicit value is not the placeholder: not this warning's job."""
        settings = Settings(control_plane_public_url="", relay_public_url="wss://relay.example.com")
        assert settings.control_plane_public_url_warning is None


class TestStartupHook:
    def _run_hook(self, monkeypatch, caplog, **env):
        for key, value in env.items():
            monkeypatch.setenv(key, value)
        get_settings.cache_clear()
        try:
            app = build_app()
            hooks = [
                h for h in app.router.on_startup if h.__name__ == "_warn_control_plane_public_url"
            ]
            assert len(hooks) == 1, "startup hook not registered"
            with caplog.at_level(logging.WARNING, logger="app.main"):
                hooks[0]()
        finally:
            get_settings.cache_clear()
        return [r for r in caplog.records if "CONTROL_PLANE_PUBLIC_URL" in r.getMessage()]

    def test_logs_one_warning_at_startup(self, monkeypatch, caplog):
        records = self._run_hook(
            monkeypatch,
            caplog,
            RELAY_PUBLIC_URL="wss://relay.example.com",
            CONTROL_PLANE_PUBLIC_URL=PLACEHOLDER,
        )
        assert len(records) == 1
        assert records[0].levelno == logging.WARNING

    def test_no_log_when_explicit(self, monkeypatch, caplog):
        records = self._run_hook(
            monkeypatch,
            caplog,
            RELAY_PUBLIC_URL="wss://relay.example.com",
            CONTROL_PLANE_PUBLIC_URL="https://cp.example.com",
        )
        assert records == []
