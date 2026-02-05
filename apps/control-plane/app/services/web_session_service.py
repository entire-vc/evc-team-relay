"""Web session management for protected share access."""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

import jwt
from fastapi import HTTPException, status

from app.core.config import get_settings
from app.core.security import utcnow


class WebSessionService:
    """Service for creating and validating web share session tokens.

    These tokens are separate from user JWT tokens and are used specifically
    for web share access control (protected shares).
    """

    @staticmethod
    def create_web_session(share_id: int | str, hours: int = 24) -> str:
        """Create a signed session token for web share access.

        Args:
            share_id: ID of the share being accessed
            hours: Session duration in hours (default 24h)

        Returns:
            Signed JWT token for session
        """
        settings = get_settings()
        now = utcnow()
        expires_at = now + timedelta(hours=hours)

        payload = {
            "sub": str(share_id),  # Subject is share_id
            "type": "web_session",  # Token type for identification
            "iat": int(now.timestamp()),
            "exp": int(expires_at.timestamp()),
            "jti": str(uuid.uuid4()),  # Unique token ID
        }

        # Sign with JWT_SECRET (same as user tokens, but different payload structure)
        token = jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)
        return token

    @staticmethod
    def validate_web_session(token: str, share_id: int | str) -> bool:
        """Validate a web session token for a specific share.

        Args:
            token: JWT token to validate
            share_id: Expected share ID

        Returns:
            True if token is valid for this share, False otherwise

        Raises:
            HTTPException: If token is invalid or expired
        """
        settings = get_settings()

        try:
            payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])

            # Check token type
            if payload.get("type") != "web_session":
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Invalid token type",
                )

            # Check share_id matches
            if payload.get("sub") != str(share_id):
                return False

            return True

        except jwt.ExpiredSignatureError:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Session expired, please re-authenticate",
            )
        except jwt.InvalidTokenError:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid session token",
            )

    @staticmethod
    def decode_web_session(token: str) -> dict[str, Any]:
        """Decode a web session token without validation.

        Useful for reading token contents before validation.

        Args:
            token: JWT token to decode

        Returns:
            Token payload

        Raises:
            HTTPException: If token format is invalid
        """
        settings = get_settings()

        try:
            payload = jwt.decode(
                token,
                settings.jwt_secret,
                algorithms=[settings.jwt_algorithm],
                options={"verify_signature": True},
            )
            return payload
        except jwt.InvalidTokenError as e:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail=f"Invalid token: {e}"
            )
