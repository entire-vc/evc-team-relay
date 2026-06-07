"""Listmonk subscriber sync service for team-relay-users list.

Syncs TR users to Listmonk list 8 (team-relay-users). Runs two modes:
- Event-driven: polls users.created_at cursor for new registrations.
- Nightly reconcile: full backfill of all active users.

Subscribers are skipped if user_email_preferences.lifecycle_emails = false
(missing row treated as true per lifecycle plan). Attributes stored per
subscriber: casdoor (bool), registered_at (ISO), has_share (bool).
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)

_USERS_QUERY = text(
    """
    SELECT DISTINCT ON (u.id)
        u.id::text,
        u.email,
        u.created_at AS registered_at,
        (u.casdoor_id IS NOT NULL OR oa.user_id IS NOT NULL) AS casdoor,
        EXISTS(
            SELECT 1 FROM shares s WHERE s.owner_user_id = u.id
        ) AS has_share,
        COALESCE(oa.name, split_part(u.email, '@', 1)) AS name,
        COALESCE(pref.lifecycle_emails, true) AS lifecycle_emails
    FROM users u
    LEFT JOIN user_oauth_accounts oa ON oa.user_id = u.id
    LEFT JOIN user_email_preferences pref ON pref.user_id = u.id
    WHERE u.is_active = true
    ORDER BY u.id, oa.created_at DESC NULLS LAST
    """
)

_NEW_USERS_SINCE_QUERY = text(
    """
    SELECT DISTINCT ON (u.id)
        u.id::text,
        u.email,
        u.created_at AS registered_at,
        (u.casdoor_id IS NOT NULL OR oa.user_id IS NOT NULL) AS casdoor,
        EXISTS(
            SELECT 1 FROM shares s WHERE s.owner_user_id = u.id
        ) AS has_share,
        COALESCE(oa.name, split_part(u.email, '@', 1)) AS name,
        COALESCE(pref.lifecycle_emails, true) AS lifecycle_emails
    FROM users u
    LEFT JOIN user_oauth_accounts oa ON oa.user_id = u.id
    LEFT JOIN user_email_preferences pref ON pref.user_id = u.id
    WHERE u.is_active = true AND u.created_at > :since
    ORDER BY u.id, oa.created_at DESC NULLS LAST
    """
)


class ListmonkService:
    def __init__(self) -> None:
        self._settings = get_settings()
        self._client: httpx.AsyncClient | None = None

    def _client_or_raise(self) -> httpx.AsyncClient:
        if self._client is None:
            raise RuntimeError("ListmonkService not started — call start() first")
        return self._client

    async def start(self) -> None:
        settings = self._settings
        if not settings.listmonk_enabled:
            logger.warning("Listmonk sync disabled — LISTMONK_URL or LISTMONK_API_PASSWORD not set")
            return
        self._client = httpx.AsyncClient(
            base_url=settings.listmonk_url,
            auth=(settings.listmonk_api_user, settings.listmonk_api_password),
            timeout=15.0,
        )

    async def stop(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    # ------------------------------------------------------------------
    # Core subscriber upsert
    # ------------------------------------------------------------------

    async def upsert_subscriber(
        self,
        email: str,
        name: str,
        casdoor: bool,
        registered_at: datetime,
        has_share: bool,
        lifecycle_emails_on: bool,
    ) -> bool:
        """Upsert one subscriber into the Listmonk list.

        Returns True if synced, False if skipped due to opt-out.
        """
        if not lifecycle_emails_on:
            logger.debug("Skipping %s — lifecycle_emails=false", email)
            return False

        client = self._client_or_raise()
        settings = self._settings

        attribs: dict[str, Any] = {
            "casdoor": casdoor,
            "registered_at": registered_at.isoformat(),
            "has_share": has_share,
        }
        payload = {
            "email": email,
            "name": name,
            "status": "enabled",
            "lists": [settings.listmonk_list_id],
            "attribs": attribs,
            "preconfirm_subscriptions": True,
        }

        resp = await client.post("/api/subscribers", json=payload)

        if resp.status_code == 200:
            return True

        if resp.status_code == 409:
            # Already exists — fetch by email and update attribs + list membership
            return await self._update_existing(client, email, name, attribs)

        resp.raise_for_status()
        return False

    async def _update_existing(
        self,
        client: httpx.AsyncClient,
        email: str,
        name: str,
        attribs: dict[str, Any],
    ) -> bool:
        settings = self._settings
        # Lookup subscriber by email
        search = await client.get(
            "/api/subscribers",
            params={"query": f"subscribers.email = '{email}'", "per_page": 1},
        )
        search.raise_for_status()
        results = search.json().get("data", {}).get("results", [])
        if not results:
            logger.warning("Subscriber %s returned 409 but not found in lookup — skipping", email)
            return False

        sub = results[0]
        sub_id = sub["id"]
        existing_lists = [lst["id"] for lst in sub.get("lists", [])]
        if settings.listmonk_list_id not in existing_lists:
            existing_lists.append(settings.listmonk_list_id)

        # Merge attribs (keep any existing keys, overwrite ours)
        merged_attribs = {**sub.get("attribs", {}), **attribs}

        put_resp = await client.put(
            f"/api/subscribers/{sub_id}",
            json={
                "email": email,
                "name": name,
                "status": sub.get("status", "enabled"),
                "lists": existing_lists,
                "attribs": merged_attribs,
                "preconfirm_subscriptions": True,
            },
        )
        put_resp.raise_for_status()
        return True

    # ------------------------------------------------------------------
    # Batch sync methods called by the worker
    # ------------------------------------------------------------------

    async def sync_new_users(self, db: Session, since: datetime) -> tuple[int, datetime | None]:
        """Sync users created after `since`. Returns (synced_count, max_created_at)."""
        rows = db.execute(_NEW_USERS_SINCE_QUERY, {"since": since}).fetchall()
        if not rows:
            return 0, None

        synced = 0
        max_ts: datetime | None = None

        for row in rows:
            try:
                ok = await self.upsert_subscriber(
                    email=row.email,
                    name=row.name,
                    casdoor=bool(row.casdoor),
                    registered_at=row.registered_at,
                    has_share=bool(row.has_share),
                    lifecycle_emails_on=bool(row.lifecycle_emails),
                )
                if ok:
                    synced += 1
                # Advance max_ts regardless (so cursor moves past opt-out users too)
                if max_ts is None or row.registered_at > max_ts:
                    max_ts = row.registered_at
            except Exception:
                logger.exception("Failed to upsert subscriber %s", row.email)

        return synced, max_ts

    async def backfill_all(self, db: Session) -> int:
        """Full reconcile — upsert every active user. Returns count synced."""
        rows = db.execute(_USERS_QUERY).fetchall()
        synced = 0
        skipped = 0

        for row in rows:
            try:
                ok = await self.upsert_subscriber(
                    email=row.email,
                    name=row.name,
                    casdoor=bool(row.casdoor),
                    registered_at=row.registered_at,
                    has_share=bool(row.has_share),
                    lifecycle_emails_on=bool(row.lifecycle_emails),
                )
                if ok:
                    synced += 1
                else:
                    skipped += 1
            except Exception:
                logger.exception("Failed to upsert subscriber %s", row.email)

        logger.info("Backfill complete: synced=%d skipped=%d total=%d", synced, skipped, len(rows))
        return synced

    async def get_list_subscriber_count(self) -> int | None:
        """Return current subscriber count from Listmonk API for acceptance check."""
        client = self._client_or_raise()
        resp = await client.get(f"/api/lists/{self._settings.listmonk_list_id}")
        if resp.status_code != 200:
            return None
        data = resp.json().get("data", {})
        return data.get("subscriber_count")


def get_listmonk_service() -> ListmonkService:
    return ListmonkService()
