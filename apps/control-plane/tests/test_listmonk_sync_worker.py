"""Tests for TR-50 (#ec96809c): sys.exit(1) on missing config + restart:unless-stopped crash-loop.

listmonk_sync_worker.main() used to sys.exit(1) when LISTMONK_URL/LISTMONK_API_PASSWORD
were unset. Under `restart: unless-stopped` (infra/docker-compose.yml) that turns a
config gap into an infinite restart storm — a missing env var is a config defect, not
a transient fault, and restarting the process can never fix it (env vars are fixed at
container start). Fixed to log an error and idle the sync loop instead of exiting,
matching the existing email_worker/lifecycle_worker convention (log once, no-op the
cycle, keep looping).

Only the DB session factory and the Listmonk service are mocked (the true external/
infra boundaries for this worker's main loop); the disabled/enabled branching logic
under test runs for real.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.workers import listmonk_sync_worker


def _fake_settings(*, listmonk_enabled: bool) -> SimpleNamespace:
    return SimpleNamespace(
        listmonk_enabled=listmonk_enabled,
        listmonk_url="https://lists.example.com",
        listmonk_list_id=8,
        listmonk_sync_interval=300,
    )


async def _stop_after_one_iteration(_seconds) -> None:
    listmonk_sync_worker.shutdown_flag = True


@pytest.fixture(autouse=True)
def _reset_shutdown_flag():
    listmonk_sync_worker.shutdown_flag = False
    yield
    listmonk_sync_worker.shutdown_flag = False


class TestMainIdlesInsteadOfExitingOnMissingConfig:
    @pytest.mark.asyncio
    async def test_disabled_config_returns_normally_and_skips_the_sync_cycle(self):
        """Pre-fix this called sys.exit(1), which raises SystemExit straight
        through this await — a bare `await main()` completing normally is
        itself proof the crash-exit path is gone. It must also never touch
        the DB while disabled (no per-row upsert spam against a client that
        was never started)."""
        settings = _fake_settings(listmonk_enabled=False)
        fake_svc = SimpleNamespace(start=AsyncMock(), stop=AsyncMock())
        fake_sessionmaker = MagicMock()

        with (
            patch.object(listmonk_sync_worker, "get_settings", return_value=settings),
            patch.object(listmonk_sync_worker, "get_listmonk_service", return_value=fake_svc),
            patch.object(listmonk_sync_worker, "get_sessionmaker", return_value=fake_sessionmaker),
            patch("asyncio.sleep", new=AsyncMock(side_effect=_stop_after_one_iteration)),
        ):
            await listmonk_sync_worker.main()

        fake_sessionmaker.assert_not_called()
        fake_svc.start.assert_awaited_once()
        fake_svc.stop.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_disabled_config_keeps_looping_across_multiple_cycles(self):
        """Not just 'doesn't exit once' — the idle loop must be durable
        across repeated cycles, since a real container stays up indefinitely
        under restart: unless-stopped until someone fixes the config."""
        settings = _fake_settings(listmonk_enabled=False)
        fake_svc = SimpleNamespace(start=AsyncMock(), stop=AsyncMock())
        fake_sessionmaker = MagicMock()

        calls = {"n": 0}

        async def stop_after_three(_seconds):
            calls["n"] += 1
            if calls["n"] >= 3:
                listmonk_sync_worker.shutdown_flag = True

        with (
            patch.object(listmonk_sync_worker, "get_settings", return_value=settings),
            patch.object(listmonk_sync_worker, "get_listmonk_service", return_value=fake_svc),
            patch.object(listmonk_sync_worker, "get_sessionmaker", return_value=fake_sessionmaker),
            patch("asyncio.sleep", new=AsyncMock(side_effect=stop_after_three)),
        ):
            await listmonk_sync_worker.main()

        assert calls["n"] == 3
        fake_sessionmaker.assert_not_called()

    @pytest.mark.asyncio
    async def test_enabled_config_still_runs_the_sync_cycle(self):
        """Regression guard: the normal enabled path must be unchanged."""
        settings = _fake_settings(listmonk_enabled=True)
        fake_svc = SimpleNamespace(start=AsyncMock(), stop=AsyncMock())
        fake_sessionmaker = MagicMock(return_value=MagicMock())

        with (
            patch.object(listmonk_sync_worker, "get_settings", return_value=settings),
            patch.object(listmonk_sync_worker, "get_listmonk_service", return_value=fake_svc),
            patch.object(listmonk_sync_worker, "get_sessionmaker", return_value=fake_sessionmaker),
            patch.object(
                listmonk_sync_worker, "_run_backfill_if_due", new=AsyncMock()
            ) as backfill_mock,
            patch.object(
                listmonk_sync_worker, "_run_incremental", new=AsyncMock()
            ) as incremental_mock,
            patch("asyncio.sleep", new=AsyncMock(side_effect=_stop_after_one_iteration)),
        ):
            await listmonk_sync_worker.main()

        backfill_mock.assert_awaited_once()
        incremental_mock.assert_awaited_once()
        fake_sessionmaker.assert_called_once()
