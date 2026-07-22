"""Unified notification service for webhooks and emails.

This module orchestrates both webhook and email notifications for various events.
It is called by routers after successful actions to ensure users and integrations
are notified appropriately.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.models import Share, ShareInvite, ShareMember, User, UserSession
from app.services import webhook_service
from app.services.email_service import EmailService, get_email_service

if TYPE_CHECKING:
    pass

logger = get_logger(__name__)


class NotificationService:
    """Unified notification service orchestrating webhooks and emails."""

    def __init__(self, email_service: EmailService | None = None):
        self.email_service = email_service or get_email_service()

    def _build_webhook_payload(
        self,
        event_type: str,
        data: dict,
        actor: User | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> dict:
        """Build standardized webhook payload.

        Args:
            event_type: Event type string
            data: Event-specific data
            actor: User who triggered the event
            ip_address: Request IP address
            user_agent: Request user agent

        Returns:
            Complete webhook payload dict
        """
        event_id = uuid.uuid4()
        payload = {
            "event_id": str(event_id),
            "event_type": event_type,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "data": data,
        }

        if actor:
            payload["data"]["actor"] = {
                "user_id": str(actor.id),
                "email": actor.email,
            }

        if ip_address or user_agent:
            payload["context"] = {}
            if ip_address:
                payload["context"]["ip_address"] = ip_address
            if user_agent:
                payload["context"]["user_agent"] = user_agent

        return payload

    def _queue_webhooks(
        self,
        db: Session,
        event_type: str,
        payload: dict,
        user_id: uuid.UUID | None = None,
    ) -> int:
        """Find matching webhooks and queue deliveries.

        Args:
            db: Database session
            event_type: Event type
            payload: Webhook payload
            user_id: User ID for user-scoped webhooks

        Returns:
            Number of webhooks queued
        """
        webhooks = webhook_service.find_matching_webhooks(db, event_type, user_id)
        event_id = uuid.UUID(payload["event_id"])

        for webhook in webhooks:
            webhook_service.queue_webhook_delivery(db, webhook, event_type, payload, event_id)

        if webhooks:
            logger.info(
                f"Queued {len(webhooks)} webhook deliveries",
                extra={
                    "event_type": event_type,
                    "event_id": str(event_id),
                    "webhook_count": len(webhooks),
                },
            )

        return len(webhooks)

    # Share events

    async def notify_share_created(
        self,
        db: Session,
        share: Share,
        actor: User,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> None:
        """Notify about share creation.

        Args:
            db: Database session
            share: Created share
            actor: User who created the share
            ip_address: Request IP
            user_agent: Request user agent
        """
        event_type = "share.created"

        data = {
            "share": {
                "id": str(share.id),
                "kind": share.kind,
                "path": share.path,
                "visibility": share.visibility,
                "owner_user_id": str(share.owner_user_id),
                "created_at": share.created_at.isoformat() if share.created_at else None,
            }
        }

        payload = self._build_webhook_payload(event_type, data, actor, ip_address, user_agent)

        # Queue webhooks for owner and admin
        self._queue_webhooks(db, event_type, payload, share.owner_user_id)

        logger.info(
            "Share created notification sent",
            extra={"share_id": str(share.id), "event_type": event_type},
        )

    async def notify_share_updated(
        self,
        db: Session,
        share: Share,
        actor: User,
        changes: dict,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> None:
        """Notify about share update.

        Args:
            db: Database session
            share: Updated share
            actor: User who updated the share
            changes: Dict of changed fields
            ip_address: Request IP
            user_agent: Request user agent
        """
        event_type = "share.updated"

        data = {
            "share": {
                "id": str(share.id),
                "kind": share.kind,
                "path": share.path,
                "visibility": share.visibility,
                "owner_user_id": str(share.owner_user_id),
            },
            "changes": changes,
        }

        payload = self._build_webhook_payload(event_type, data, actor, ip_address, user_agent)

        self._queue_webhooks(db, event_type, payload, share.owner_user_id)

        logger.info(
            "Share updated notification sent",
            extra={"share_id": str(share.id), "event_type": event_type},
        )

    async def notify_share_deleted(
        self,
        db: Session,
        share: Share,
        members: list[ShareMember],
        actor: User,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> None:
        """Notify about share deletion.

        Args:
            db: Database session
            share: Deleted share
            members: List of share members to notify
            actor: User who deleted the share
            ip_address: Request IP
            user_agent: Request user agent
        """
        event_type = "share.deleted"

        data = {
            "share": {
                "id": str(share.id),
                "kind": share.kind,
                "path": share.path,
                "visibility": share.visibility,
                "owner_user_id": str(share.owner_user_id),
            }
        }

        payload = self._build_webhook_payload(event_type, data, actor, ip_address, user_agent)

        self._queue_webhooks(db, event_type, payload, share.owner_user_id)

        # Send email to all members
        for member in members:
            if member.user and member.user.email:
                await self.email_service.send_share_deleted(
                    db,
                    member.user.email,
                    member.user.id,
                    share.path,
                    share.kind,
                )

        logger.info(
            "Share deleted notification sent",
            extra={
                "share_id": str(share.id),
                "event_type": event_type,
                "members_notified": len(members),
            },
        )

    # Member events

    async def notify_member_added(
        self,
        db: Session,
        share: Share,
        member: ShareMember,
        actor: User,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> None:
        """Notify about new member added to share.

        Args:
            db: Database session
            share: Share
            member: New member
            actor: User who added the member
            ip_address: Request IP
            user_agent: Request user agent
        """
        event_type = "share.member.added"

        data = {
            "share": {
                "id": str(share.id),
                "kind": share.kind,
                "path": share.path,
            },
            "member": {
                "user_id": str(member.user_id),
                "email": member.user.email if member.user else None,
                "role": member.role,
            },
        }

        payload = self._build_webhook_payload(event_type, data, actor, ip_address, user_agent)

        self._queue_webhooks(db, event_type, payload, share.owner_user_id)

        # Send email to new member
        if member.user and member.user.email:
            await self.email_service.send_member_added(
                db,
                member.user.email,
                member.user.id,
                actor.email,
                share.path,
                member.role,
            )

        logger.info(
            "Member added notification sent",
            extra={
                "share_id": str(share.id),
                "member_user_id": str(member.user_id),
                "event_type": event_type,
            },
        )

    async def notify_member_updated(
        self,
        db: Session,
        share: Share,
        member: ShareMember,
        old_role: str,
        actor: User,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> None:
        """Notify about member role update.

        Args:
            db: Database session
            share: Share
            member: Updated member
            old_role: Previous role
            actor: User who updated the member
            ip_address: Request IP
            user_agent: Request user agent
        """
        event_type = "share.member.updated"

        data = {
            "share": {
                "id": str(share.id),
                "kind": share.kind,
                "path": share.path,
            },
            "member": {
                "user_id": str(member.user_id),
                "email": member.user.email if member.user else None,
                "role": member.role,
                "previous_role": old_role,
            },
        }

        payload = self._build_webhook_payload(event_type, data, actor, ip_address, user_agent)

        self._queue_webhooks(db, event_type, payload, share.owner_user_id)

        logger.info(
            "Member updated notification sent",
            extra={
                "share_id": str(share.id),
                "member_user_id": str(member.user_id),
                "event_type": event_type,
            },
        )

    async def notify_member_removed(
        self,
        db: Session,
        share: Share,
        member_user_id: uuid.UUID,
        member_email: str | None,
        actor: User,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> None:
        """Notify about member removal from share.

        Args:
            db: Database session
            share: Share
            member_user_id: Removed member's user ID
            member_email: Removed member's email
            actor: User who removed the member
            ip_address: Request IP
            user_agent: Request user agent
        """
        event_type = "share.member.removed"

        data = {
            "share": {
                "id": str(share.id),
                "kind": share.kind,
                "path": share.path,
            },
            "member": {
                "user_id": str(member_user_id),
                "email": member_email,
            },
        }

        payload = self._build_webhook_payload(event_type, data, actor, ip_address, user_agent)

        self._queue_webhooks(db, event_type, payload, share.owner_user_id)

        logger.info(
            "Member removed notification sent",
            extra={
                "share_id": str(share.id),
                "member_user_id": str(member_user_id),
                "event_type": event_type,
            },
        )

    # Invite events

    async def notify_invite_created(
        self,
        db: Session,
        invite: ShareInvite,
        share: Share,
        actor: User,
        invite_url: str,
        recipient_email: str | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> None:
        """Notify about invite creation.

        Args:
            db: Database session
            invite: Created invite
            share: Share the invite is for
            actor: User who created the invite
            invite_url: Full URL to redeem the invite
            recipient_email: Email to send invite to (if email-based invite)
            ip_address: Request IP
            user_agent: Request user agent
        """
        event_type = "invite.created"

        data = {
            "invite": {
                "id": str(invite.id),
                "share_id": str(invite.share_id),
                "role": invite.role,
                "max_uses": invite.max_uses,
                "expires_at": invite.expires_at.isoformat() if invite.expires_at else None,
            },
            "share": {
                "id": str(share.id),
                "kind": share.kind,
                "path": share.path,
            },
        }

        if recipient_email:
            data["recipient_email"] = recipient_email

        payload = self._build_webhook_payload(event_type, data, actor, ip_address, user_agent)

        self._queue_webhooks(db, event_type, payload, share.owner_user_id)

        # Send email to recipient if provided
        if recipient_email:
            await self.email_service.send_invite_notification(
                db,
                recipient_email,
                actor.email,
                share.path,
                share.kind,
                invite.role,
                invite_url,
                invite.expires_at,
            )

        logger.info(
            "Invite created notification sent",
            extra={
                "invite_id": str(invite.id),
                "share_id": str(share.id),
                "event_type": event_type,
                "email_sent": bool(recipient_email),
            },
        )

    async def notify_invite_redeemed(
        self,
        db: Session,
        invite: ShareInvite,
        share: Share,
        redeemer: User,
        owner: User,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> None:
        """Notify about invite redemption.

        Args:
            db: Database session
            invite: Redeemed invite
            share: Share
            redeemer: User who redeemed the invite
            owner: Share owner to notify
            ip_address: Request IP
            user_agent: Request user agent
        """
        event_type = "invite.redeemed"

        data = {
            "invite": {
                "id": str(invite.id),
                "share_id": str(invite.share_id),
                "role": invite.role,
            },
            "share": {
                "id": str(share.id),
                "kind": share.kind,
                "path": share.path,
            },
            "redeemer": {
                "user_id": str(redeemer.id),
                "email": redeemer.email,
            },
        }

        payload = self._build_webhook_payload(event_type, data, redeemer, ip_address, user_agent)

        self._queue_webhooks(db, event_type, payload, share.owner_user_id)

        # Notify share owner
        if owner.email:
            await self.email_service.send_invite_accepted(
                db,
                owner.email,
                owner.id,
                redeemer.email,
                share.path,
                invite.role,
            )

        logger.info(
            "Invite redeemed notification sent",
            extra={
                "invite_id": str(invite.id),
                "share_id": str(share.id),
                "redeemer_id": str(redeemer.id),
                "event_type": event_type,
            },
        )

    async def notify_invite_revoked(
        self,
        db: Session,
        invite: ShareInvite,
        share: Share,
        actor: User,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> None:
        """Notify about invite revocation.

        Args:
            db: Database session
            invite: Revoked invite
            share: Share
            actor: User who revoked the invite
            ip_address: Request IP
            user_agent: Request user agent
        """
        event_type = "invite.revoked"

        data = {
            "invite": {
                "id": str(invite.id),
                "share_id": str(invite.share_id),
                "role": invite.role,
            },
            "share": {
                "id": str(share.id),
                "kind": share.kind,
                "path": share.path,
            },
        }

        payload = self._build_webhook_payload(event_type, data, actor, ip_address, user_agent)

        self._queue_webhooks(db, event_type, payload, share.owner_user_id)

        logger.info(
            "Invite revoked notification sent",
            extra={
                "invite_id": str(invite.id),
                "share_id": str(share.id),
                "event_type": event_type,
            },
        )

    # Auth events

    def _should_send_new_session_email(
        self,
        db: Session,
        user_id: uuid.UUID,
        device_name: str | None,
        ip_address: str | None,
        user_agent: str | None,
    ) -> bool:
        """TR-36: suppress the "New login" email if an identical-looking
        session already got one within the suppression window.

        Keys on the FULL (device_name, ip_address, user_agent) tuple, not
        device_name alone — device_name is frequently uninformative: the
        OAuth login path (oauth.py) always passes the fixed literal
        f"OAuth ({provider})" for every session regardless of the real
        device, and the password path only sets it from an optional
        X-Device-Name header most clients don't send (None otherwise). Either
        one alone would collapse a user's genuinely different devices into
        one suppression bucket — a real second device logging in via the
        same OAuth provider within the window would go silently unnotified,
        which is the opposite of what a security alert is for. ip_address +
        user_agent narrow the match back down to "actually looks like the
        same client", which is what a reconnect loop actually reproduces.

        The UserSession row for the CURRENT login is already committed by
        the time this runs (auth_service.login()/the OAuth callback create
        it, then db.commit(), before notify_session_created is called) — so
        "any matching session in the window besides the newest one" is the
        signal, not "any session at all" (which would always be true for
        the very login triggering this check).

        Known residual gap (accepted, not fixed here — a plain
        select-then-decide has no locking): two logins from an identical
        client landing in the same instant on different worker processes
        could both pass this check and both send. Failure direction is
        "occasional duplicate email", not "a genuinely new device stays
        silent" — the correct side to fail on for a security notification.
        """
        settings = get_settings()
        cutoff = datetime.now(timezone.utc) - timedelta(
            hours=settings.security_new_session_email_suppression_hours
        )
        stmt = (
            select(UserSession.id)
            .where(
                UserSession.user_id == user_id,
                UserSession.device_name == device_name,
                UserSession.ip_address == ip_address,
                UserSession.user_agent == user_agent,
                UserSession.created_at >= cutoff,
            )
            .order_by(UserSession.created_at.desc())
            .limit(2)
        )
        recent_session_ids = db.execute(stmt).scalars().all()
        # 0 or 1 (just the current session) => nothing sent yet in this window.
        return len(recent_session_ids) <= 1

    async def notify_session_created(
        self,
        db: Session,
        user: User,
        device_name: str | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> None:
        """Notify about new session creation (login).

        Args:
            db: Database session
            user: User who logged in
            device_name: Device name
            ip_address: Request IP
            user_agent: Request user agent
        """
        event_type = "session.created"

        data = {
            "user": {
                "user_id": str(user.id),
                "email": user.email,
            },
            "session": {
                "device_name": device_name,
                "ip_address": ip_address,
            },
        }

        payload = self._build_webhook_payload(event_type, data, user, ip_address, user_agent)

        self._queue_webhooks(db, event_type, payload, user.id)

        # Send security email — suppressed if an identical-looking session
        # already got one within the suppression window (TR-36).
        if user.email:
            if self._should_send_new_session_email(
                db, user.id, device_name, ip_address, user_agent
            ):
                await self.email_service.send_security_new_session(
                    db,
                    user.email,
                    user.id,
                    device_name,
                    ip_address,
                    user_agent,
                )
            else:
                logger.info(
                    "Suppressed duplicate new-session email",
                    extra={"user_id": str(user.id), "device_name": device_name},
                )

        logger.info(
            "Session created notification sent",
            extra={
                "user_id": str(user.id),
                "event_type": event_type,
            },
        )

    async def notify_password_changed(
        self,
        db: Session,
        user: User,
        changed_by_admin: bool = False,
        actor: User | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> None:
        """Notify about password change.

        Args:
            db: Database session
            user: User whose password was changed
            changed_by_admin: Whether change was made by admin
            actor: User who made the change (if admin)
            ip_address: Request IP
            user_agent: Request user agent
        """
        event_type = "user.password_reset"

        data = {
            "user": {
                "user_id": str(user.id),
                "email": user.email,
            },
            "changed_by_admin": changed_by_admin,
        }

        payload = self._build_webhook_payload(
            event_type, data, actor or user, ip_address, user_agent
        )

        self._queue_webhooks(db, event_type, payload, user.id)

        # Send security email
        if user.email:
            await self.email_service.send_security_password_changed(
                db,
                user.email,
                user.id,
                changed_by_admin,
            )

        logger.info(
            "Password changed notification sent",
            extra={
                "user_id": str(user.id),
                "event_type": event_type,
                "changed_by_admin": changed_by_admin,
            },
        )

    async def notify_user_login(
        self,
        db: Session,
        user: User,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> None:
        """Notify about user login (webhook only, no email).

        Args:
            db: Database session
            user: User who logged in
            ip_address: Request IP
            user_agent: Request user agent
        """
        event_type = "user.login"

        data = {
            "user": {
                "user_id": str(user.id),
                "email": user.email,
            },
        }

        payload = self._build_webhook_payload(event_type, data, user, ip_address, user_agent)

        self._queue_webhooks(db, event_type, payload, user.id)

        logger.info(
            "User login notification sent",
            extra={
                "user_id": str(user.id),
                "event_type": event_type,
            },
        )

    async def notify_user_logout(
        self,
        db: Session,
        user: User,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> None:
        """Notify about user logout (webhook only).

        Args:
            db: Database session
            user: User who logged out
            ip_address: Request IP
            user_agent: Request user agent
        """
        event_type = "user.logout"

        data = {
            "user": {
                "user_id": str(user.id),
                "email": user.email,
            },
        }

        payload = self._build_webhook_payload(event_type, data, user, ip_address, user_agent)

        self._queue_webhooks(db, event_type, payload, user.id)

        logger.info(
            "User logout notification sent",
            extra={
                "user_id": str(user.id),
                "event_type": event_type,
            },
        )

    # Admin events (admin webhooks only)

    async def notify_user_created(
        self,
        db: Session,
        user: User,
        actor: User,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> None:
        """Notify about user creation by admin (admin webhooks only).

        Args:
            db: Database session
            user: Created user
            actor: Admin who created the user
            ip_address: Request IP
            user_agent: Request user agent
        """
        event_type = "user.created"

        data = {
            "user": {
                "user_id": str(user.id),
                "email": user.email,
                "is_admin": user.is_admin,
            },
        }

        payload = self._build_webhook_payload(event_type, data, actor, ip_address, user_agent)

        # Admin events only go to admin webhooks (user_id=None)
        self._queue_webhooks(db, event_type, payload, None)

        logger.info(
            "User created notification sent",
            extra={
                "user_id": str(user.id),
                "event_type": event_type,
            },
        )

    async def notify_user_updated(
        self,
        db: Session,
        user: User,
        changes: dict,
        actor: User,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> None:
        """Notify about user update by admin (admin webhooks only).

        Args:
            db: Database session
            user: Updated user
            changes: Dict of changed fields
            actor: Admin who updated the user
            ip_address: Request IP
            user_agent: Request user agent
        """
        event_type = "user.updated"

        data = {
            "user": {
                "user_id": str(user.id),
                "email": user.email,
            },
            "changes": changes,
        }

        payload = self._build_webhook_payload(event_type, data, actor, ip_address, user_agent)

        # Admin events only go to admin webhooks
        self._queue_webhooks(db, event_type, payload, None)

        logger.info(
            "User updated notification sent",
            extra={
                "user_id": str(user.id),
                "event_type": event_type,
            },
        )

    async def notify_user_deleted(
        self,
        db: Session,
        user_id: uuid.UUID,
        user_email: str,
        actor: User,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> None:
        """Notify about user deletion by admin (admin webhooks only).

        Args:
            db: Database session
            user_id: Deleted user's ID
            user_email: Deleted user's email
            actor: Admin who deleted the user
            ip_address: Request IP
            user_agent: Request user agent
        """
        event_type = "user.deleted"

        data = {
            "user": {
                "user_id": str(user_id),
                "email": user_email,
            },
        }

        payload = self._build_webhook_payload(event_type, data, actor, ip_address, user_agent)

        # Admin events only go to admin webhooks
        self._queue_webhooks(db, event_type, payload, None)

        logger.info(
            "User deleted notification sent",
            extra={
                "user_id": str(user_id),
                "event_type": event_type,
            },
        )


# Singleton instance
_notification_service: NotificationService | None = None


def get_notification_service() -> NotificationService:
    """Get or create notification service singleton."""
    global _notification_service
    if _notification_service is None:
        _notification_service = NotificationService()
    return _notification_service
