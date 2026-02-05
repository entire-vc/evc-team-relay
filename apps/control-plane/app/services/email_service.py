"""Email service for sending system emails.

This module handles:
- SMTP email delivery
- Jinja2 template rendering
- Email queue management
- Various notification types (invites, security alerts, etc.)
"""

from __future__ import annotations

import smtplib
import uuid
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import TYPE_CHECKING

from jinja2 import Environment, FileSystemLoader, select_autoescape
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.db.models import EmailQueue, EmailStatus, UserEmailPreferences

if TYPE_CHECKING:
    pass

logger = get_logger(__name__)

# Email types for tracking and preferences
EMAIL_TYPE_INVITE_NOTIFICATION = "invite_notification"
EMAIL_TYPE_INVITE_ACCEPTED = "invite_accepted"
EMAIL_TYPE_MEMBER_ADDED = "member_added"
EMAIL_TYPE_SHARE_DELETED = "share_deleted"
EMAIL_TYPE_PASSWORD_RESET = "password_reset"
EMAIL_TYPE_EMAIL_VERIFICATION = "email_verification"
EMAIL_TYPE_SECURITY_NEW_SESSION = "security_new_session"
EMAIL_TYPE_SECURITY_PASSWORD_CHANGED = "security_password_changed"

# Retry intervals for email queue (seconds)
EMAIL_RETRY_INTERVALS = [60, 300, 900]  # 1min, 5min, 15min
MAX_EMAIL_RETRIES = len(EMAIL_RETRY_INTERVALS)

# Template directory
TEMPLATE_DIR = Path(__file__).parent.parent / "templates" / "emails"


class EmailService:
    """Email service for sending system emails via SMTP."""

    def __init__(self, settings: Settings | None = None):
        if settings is None:
            settings = get_settings()

        self.smtp_host = settings.smtp_host
        self.smtp_port = settings.smtp_port
        self.smtp_user = settings.smtp_user
        self.smtp_password = settings.smtp_password
        self.smtp_use_tls = settings.smtp_use_tls
        self.from_email = settings.email_from
        self.reply_to = settings.email_reply_to
        self.email_enabled = settings.email_enabled
        self.server_name = settings.server_name

        # Initialize Jinja2 environment
        self._init_templates()

    def _init_templates(self) -> None:
        """Initialize Jinja2 template environment."""
        if TEMPLATE_DIR.exists():
            self.jinja_env = Environment(
                loader=FileSystemLoader(str(TEMPLATE_DIR)),
                autoescape=select_autoescape(["html", "xml"]),
            )
        else:
            logger.warning(f"Email template directory not found: {TEMPLATE_DIR}")
            self.jinja_env = None

    def _get_base_context(self) -> dict:
        """Get base context for all email templates."""
        settings = get_settings()
        return {
            "server_name": self.server_name,
            "base_url": str(settings.relay_public_url)
            .replace("wss://", "https://")
            .replace("ws://", "http://"),
            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
            "year": datetime.now().year,
        }

    def _render_template(self, template_name: str, context: dict) -> tuple[str | None, str | None]:
        """Render email template.

        Args:
            template_name: Template name without extension
            context: Template context variables

        Returns:
            Tuple of (html_content, text_content)
        """
        if not self.jinja_env:
            return None, None

        full_context = {**self._get_base_context(), **context}
        html_content = None
        text_content = None

        try:
            html_template = self.jinja_env.get_template(f"{template_name}.html")
            html_content = html_template.render(**full_context)
        except Exception as e:
            logger.warning(f"Failed to render HTML template {template_name}: {e}")

        try:
            text_template = self.jinja_env.get_template(f"{template_name}.txt")
            text_content = text_template.render(**full_context)
        except Exception as e:
            logger.warning(f"Failed to render text template {template_name}: {e}")

        return html_content, text_content

    def _create_mime_message(
        self, to_email: str, subject: str, text_body: str, html_body: str | None = None
    ) -> MIMEMultipart:
        """Create MIME message with both text and HTML parts.

        Args:
            to_email: Recipient email address
            subject: Email subject
            text_body: Plain text body
            html_body: Optional HTML body

        Returns:
            MIMEMultipart message
        """
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = self.from_email
        msg["To"] = to_email

        if self.reply_to:
            msg["Reply-To"] = self.reply_to

        # Attach plain text version
        msg.attach(MIMEText(text_body, "plain", "utf-8"))

        # Attach HTML version if available
        if html_body:
            msg.attach(MIMEText(html_body, "html", "utf-8"))

        return msg

    def _send_smtp(self, msg: MIMEMultipart) -> bool:
        """Send email via SMTP.

        Args:
            msg: MIME message to send

        Returns:
            True if sent successfully
        """
        if not self.smtp_host:
            logger.warning("SMTP host not configured, cannot send email")
            return False

        try:
            if self.smtp_use_tls:
                server = smtplib.SMTP(self.smtp_host, self.smtp_port)
                server.starttls()
            else:
                server = smtplib.SMTP(self.smtp_host, self.smtp_port)

            if self.smtp_user and self.smtp_password:
                server.login(self.smtp_user, self.smtp_password)

            server.send_message(msg)
            server.quit()

            logger.info(
                "Email sent successfully",
                extra={"to": msg["To"], "subject": msg["Subject"]},
            )
            return True

        except Exception as e:
            logger.error(
                "Failed to send email via SMTP",
                extra={"to": msg["To"], "error": str(e)},
            )
            return False

    def _log_email(self, to_email: str, subject: str, body: str, email_type: str) -> None:
        """Log email to console (for development/testing)."""
        logger.info(
            f"[EMAIL] {email_type}",
            extra={
                "to": to_email,
                "subject": subject,
                "body_preview": body[:200] + "..." if len(body) > 200 else body,
            },
        )

    async def send_email(
        self,
        to_email: str,
        subject: str,
        text_body: str,
        html_body: str | None = None,
        email_type: str = "generic",
    ) -> bool:
        """Send email immediately.

        Args:
            to_email: Recipient email
            subject: Email subject
            text_body: Plain text body
            html_body: Optional HTML body
            email_type: Email type for logging/metrics

        Returns:
            True if sent successfully
        """
        if not self.email_enabled:
            self._log_email(to_email, subject, text_body, email_type)
            return True

        msg = self._create_mime_message(to_email, subject, text_body, html_body)
        return self._send_smtp(msg)

    def queue_email(
        self,
        db: Session,
        to_email: str,
        subject: str,
        text_body: str,
        html_body: str,
        email_type: str,
    ) -> EmailQueue:
        """Queue email for async delivery.

        Args:
            db: Database session
            to_email: Recipient email
            subject: Email subject
            text_body: Plain text body
            html_body: HTML body
            email_type: Email type for tracking

        Returns:
            Created EmailQueue instance
        """
        email = EmailQueue(
            to_email=to_email,
            subject=subject,
            body_text=text_body,
            body_html=html_body,
            email_type=email_type,
            status=EmailStatus.PENDING,
            next_retry_at=datetime.now(timezone.utc),
        )

        db.add(email)
        db.commit()
        db.refresh(email)

        logger.info(
            "Email queued",
            extra={
                "email_id": str(email.id),
                "to": to_email,
                "type": email_type,
            },
        )

        return email

    async def process_queued_email(self, db: Session, email: EmailQueue) -> bool:
        """Process a queued email.

        Args:
            db: Database session
            email: EmailQueue instance

        Returns:
            True if sent successfully
        """
        email.attempt_count += 1

        if not self.email_enabled:
            # Just log and mark as sent
            self._log_email(email.to_email, email.subject, email.body_text, email.email_type)
            email.status = EmailStatus.SENT
            email.sent_at = datetime.now(timezone.utc)
            email.next_retry_at = None
            db.add(email)
            db.commit()
            return True

        msg = self._create_mime_message(
            email.to_email, email.subject, email.body_text, email.body_html
        )

        success = self._send_smtp(msg)

        if success:
            email.status = EmailStatus.SENT
            email.sent_at = datetime.now(timezone.utc)
            email.next_retry_at = None
        else:
            if email.attempt_count >= MAX_EMAIL_RETRIES:
                email.status = EmailStatus.FAILED
                email.error_message = "Max retries exceeded"
                email.next_retry_at = None
                logger.warning(
                    "Email max retries exceeded",
                    extra={"email_id": str(email.id), "attempts": email.attempt_count},
                )
            else:
                retry_interval = EMAIL_RETRY_INTERVALS[email.attempt_count - 1]
                email.next_retry_at = datetime.now(timezone.utc) + timedelta(seconds=retry_interval)
                email.error_message = "SMTP delivery failed, scheduled for retry"
                logger.info(
                    "Email scheduled for retry",
                    extra={
                        "email_id": str(email.id),
                        "attempt": email.attempt_count,
                        "retry_in": retry_interval,
                    },
                )

        db.add(email)
        db.commit()

        return success

    def get_pending_emails(self, db: Session, limit: int = 100) -> list[EmailQueue]:
        """Get pending emails ready for processing.

        Args:
            db: Database session
            limit: Maximum number of emails to return

        Returns:
            List of EmailQueue instances
        """
        now = datetime.now(timezone.utc)

        stmt = (
            select(EmailQueue)
            .where(
                EmailQueue.status == EmailStatus.PENDING,
                EmailQueue.next_retry_at <= now,
            )
            .order_by(EmailQueue.next_retry_at)
            .limit(limit)
        )

        return list(db.execute(stmt).scalars().all())

    def get_user_email_preferences(
        self, db: Session, user_id: uuid.UUID
    ) -> UserEmailPreferences | None:
        """Get user's email preferences.

        Args:
            db: Database session
            user_id: User ID

        Returns:
            UserEmailPreferences or None
        """
        return db.execute(
            select(UserEmailPreferences).where(UserEmailPreferences.user_id == user_id)
        ).scalar_one_or_none()

    def get_or_create_preferences(self, db: Session, user_id: uuid.UUID) -> UserEmailPreferences:
        """Get or create user's email preferences with defaults.

        Args:
            db: Database session
            user_id: User ID

        Returns:
            UserEmailPreferences instance
        """
        prefs = self.get_user_email_preferences(db, user_id)

        if not prefs:
            prefs = UserEmailPreferences(user_id=user_id)
            db.add(prefs)
            db.commit()
            db.refresh(prefs)

        return prefs

    def should_send_email(
        self,
        db: Session,
        user_id: uuid.UUID,
        email_type: str,
    ) -> bool:
        """Check if email should be sent based on user preferences.

        Security alerts are always sent regardless of preferences.

        Args:
            db: Database session
            user_id: User ID
            email_type: Email type

        Returns:
            True if email should be sent
        """
        # Security alerts always sent
        if email_type in (
            EMAIL_TYPE_SECURITY_NEW_SESSION,
            EMAIL_TYPE_SECURITY_PASSWORD_CHANGED,
            EMAIL_TYPE_PASSWORD_RESET,
            EMAIL_TYPE_EMAIL_VERIFICATION,
        ):
            return True

        prefs = self.get_user_email_preferences(db, user_id)

        # Default to True if no preferences set
        if not prefs:
            return True

        # Map email types to preference fields
        if email_type in (EMAIL_TYPE_INVITE_NOTIFICATION, EMAIL_TYPE_INVITE_ACCEPTED):
            return prefs.invite_notifications

        if email_type in (EMAIL_TYPE_SHARE_DELETED,):
            return prefs.share_update_notifications

        if email_type in (EMAIL_TYPE_MEMBER_ADDED,):
            return prefs.member_notifications

        return True

    # High-level email methods

    async def send_invite_notification(
        self,
        db: Session,
        to_email: str,
        inviter_email: str,
        share_path: str,
        share_kind: str,
        role: str,
        invite_url: str,
        expires_at: datetime | None = None,
    ) -> bool:
        """Send invite notification email.

        Args:
            db: Database session
            to_email: Recipient email
            inviter_email: Who sent the invite
            share_path: Path of the share
            share_kind: Type of share (doc/folder)
            role: Role being granted
            invite_url: URL to accept invite
            expires_at: Optional expiration datetime

        Returns:
            True if queued successfully
        """
        subject = f"You've been invited to collaborate on {share_path}"

        html_body, text_body = self._render_template(
            "invite-notification",
            {
                "inviter_email": inviter_email,
                "share_path": share_path,
                "share_kind": share_kind,
                "role": role,
                "invite_url": invite_url,
                "expires_at": expires_at.strftime("%Y-%m-%d %H:%M UTC") if expires_at else None,
                "recipient_email": to_email,
            },
        )

        if not text_body:
            text_body = (
                f"{inviter_email} has invited you to collaborate on {share_path}.\n\n"
                f"Role: {role}\n"
                f"Accept invite: {invite_url}\n"
            )
            if expires_at:
                text_body += (
                    f"\nThis invite expires on {expires_at.strftime('%Y-%m-%d %H:%M UTC')}."
                )

        self.queue_email(
            db,
            to_email,
            subject,
            text_body,
            html_body or text_body,
            EMAIL_TYPE_INVITE_NOTIFICATION,
        )
        return True

    async def send_invite_accepted(
        self,
        db: Session,
        owner_email: str,
        owner_user_id: uuid.UUID,
        new_member_email: str,
        share_path: str,
        role: str,
    ) -> bool:
        """Send notification to owner when invite is accepted.

        Args:
            db: Database session
            owner_email: Share owner's email
            owner_user_id: Share owner's user ID
            new_member_email: Email of user who accepted
            share_path: Path of the share
            role: Role granted

        Returns:
            True if queued successfully
        """
        if not self.should_send_email(db, owner_user_id, EMAIL_TYPE_INVITE_ACCEPTED):
            return False

        subject = f"{new_member_email} accepted your invite to {share_path}"

        html_body, text_body = self._render_template(
            "invite-accepted",
            {
                "new_member_email": new_member_email,
                "share_path": share_path,
                "role": role,
                "recipient_email": owner_email,
            },
        )

        if not text_body:
            text_body = (
                f"{new_member_email} has accepted your invite and joined {share_path} as {role}."
            )

        self.queue_email(
            db,
            owner_email,
            subject,
            text_body,
            html_body or text_body,
            EMAIL_TYPE_INVITE_ACCEPTED,
        )
        return True

    async def send_member_added(
        self,
        db: Session,
        member_email: str,
        member_user_id: uuid.UUID,
        adder_email: str,
        share_path: str,
        role: str,
    ) -> bool:
        """Send notification when user is added as member.

        Args:
            db: Database session
            member_email: New member's email
            member_user_id: New member's user ID
            adder_email: Who added them
            share_path: Path of the share
            role: Role granted

        Returns:
            True if queued successfully
        """
        if not self.should_send_email(db, member_user_id, EMAIL_TYPE_MEMBER_ADDED):
            return False

        subject = f"You've been added to {share_path}"

        html_body, text_body = self._render_template(
            "member-added",
            {
                "adder_email": adder_email,
                "share_path": share_path,
                "role": role,
                "recipient_email": member_email,
            },
        )

        if not text_body:
            text_body = f"{adder_email} has added you to {share_path} as {role}."

        self.queue_email(
            db,
            member_email,
            subject,
            text_body,
            html_body or text_body,
            EMAIL_TYPE_MEMBER_ADDED,
        )
        return True

    async def send_share_deleted(
        self,
        db: Session,
        member_email: str,
        member_user_id: uuid.UUID,
        share_path: str,
        share_kind: str,
    ) -> bool:
        """Send notification when share is deleted.

        Args:
            db: Database session
            member_email: Member's email
            member_user_id: Member's user ID
            share_path: Path of deleted share
            share_kind: Type of share

        Returns:
            True if queued successfully
        """
        if not self.should_send_email(db, member_user_id, EMAIL_TYPE_SHARE_DELETED):
            return False

        subject = f"Share '{share_path}' has been deleted"

        html_body, text_body = self._render_template(
            "share-deleted",
            {
                "share_path": share_path,
                "share_kind": share_kind,
                "recipient_email": member_email,
            },
        )

        if not text_body:
            text_body = (
                f"The {share_kind} '{share_path}' has been deleted and is no longer accessible."
            )

        self.queue_email(
            db,
            member_email,
            subject,
            text_body,
            html_body or text_body,
            EMAIL_TYPE_SHARE_DELETED,
        )
        return True

    async def send_password_reset(self, to_email: str, reset_url: str) -> bool:
        """Send password reset email.

        Args:
            to_email: Recipient email address
            reset_url: Password reset URL with token

        Returns:
            True if email sent successfully
        """
        subject = "Password Reset Request"

        html_body, text_body = self._render_template(
            "password-reset",
            {"reset_url": reset_url, "recipient_email": to_email},
        )

        if not text_body:
            text_body = (
                f"You requested a password reset.\n\n"
                f"Click the link below to reset your password:\n{reset_url}\n\n"
                f"If you didn't request this, you can safely ignore this email."
            )

        return await self.send_email(
            to_email, subject, text_body, html_body, EMAIL_TYPE_PASSWORD_RESET
        )

    async def send_verification_email(self, to_email: str, verification_url: str) -> bool:
        """Send email verification email.

        Args:
            to_email: Recipient email address
            verification_url: Email verification URL with token

        Returns:
            True if email sent successfully
        """
        subject = "Verify Your Email Address"

        html_body, text_body = self._render_template(
            "email-verification",
            {"verification_url": verification_url, "recipient_email": to_email},
        )

        if not text_body:
            text_body = (
                f"Please verify your email address by clicking the link below:\n\n"
                f"{verification_url}\n\n"
                f"If you didn't create an account, you can safely ignore this email."
            )

        return await self.send_email(
            to_email, subject, text_body, html_body, EMAIL_TYPE_EMAIL_VERIFICATION
        )

    async def send_security_new_session(
        self,
        db: Session,
        to_email: str,
        user_id: uuid.UUID,
        device_name: str | None,
        ip_address: str | None,
        user_agent: str | None,
    ) -> bool:
        """Send security alert for new session.

        Args:
            db: Database session
            to_email: User's email
            user_id: User's ID
            device_name: Device name
            ip_address: IP address
            user_agent: User agent string

        Returns:
            True if queued successfully
        """
        subject = "New login to your account"

        html_body, text_body = self._render_template(
            "security-new-session",
            {
                "device_name": device_name or "Unknown device",
                "ip_address": ip_address or "Unknown",
                "user_agent": user_agent or "Unknown",
                "recipient_email": to_email,
            },
        )

        if not text_body:
            text_body = (
                f"A new login was detected on your account.\n\n"
                f"Device: {device_name or 'Unknown'}\n"
                f"IP Address: {ip_address or 'Unknown'}\n"
                f"Time: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}\n\n"
                f"If this wasn't you, please change your password immediately."
            )

        self.queue_email(
            db,
            to_email,
            subject,
            text_body,
            html_body or text_body,
            EMAIL_TYPE_SECURITY_NEW_SESSION,
        )
        return True

    async def send_security_password_changed(
        self,
        db: Session,
        to_email: str,
        user_id: uuid.UUID,
        changed_by_admin: bool = False,
    ) -> bool:
        """Send security alert for password change.

        Args:
            db: Database session
            to_email: User's email
            user_id: User's ID
            changed_by_admin: Whether change was made by admin

        Returns:
            True if queued successfully
        """
        subject = "Your password was changed"

        html_body, text_body = self._render_template(
            "security-password-changed",
            {
                "changed_by_admin": changed_by_admin,
                "recipient_email": to_email,
            },
        )

        if not text_body:
            initiator = "an administrator" if changed_by_admin else "you"
            text_body = (
                f"Your password was changed by {initiator}.\n\n"
                f"Time: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}\n\n"
                f"If you didn't make this change, please contact support immediately."
            )

        self.queue_email(
            db,
            to_email,
            subject,
            text_body,
            html_body or text_body,
            EMAIL_TYPE_SECURITY_PASSWORD_CHANGED,
        )
        return True


# Singleton instance
_email_service: EmailService | None = None


def get_email_service() -> EmailService:
    """Get or create email service singleton."""
    global _email_service
    if _email_service is None:
        _email_service = EmailService()
    return _email_service
