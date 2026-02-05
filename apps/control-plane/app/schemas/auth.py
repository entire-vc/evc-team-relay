from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, EmailStr, Field

from app.schemas.user import UserRead


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    is_admin: bool = False


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str | None = None
    token_type: Literal["bearer"] = "bearer"
    expires_in: int | None = None  # seconds until access token expires


class TokenPayload(BaseModel):
    sub: str
    exp: datetime
    iat: datetime


class LogoutResponse(BaseModel):
    ok: bool = True


class MeResponse(UserRead):
    pass


class RefreshTokenRequest(BaseModel):
    """Request schema for refresh token endpoint."""

    refresh_token: str = Field(min_length=64, max_length=64)


class SessionInfo(BaseModel):
    """Schema for session information."""

    id: str
    device_name: str | None
    user_agent: str | None
    ip_address: str | None
    last_activity: datetime
    created_at: datetime
    is_current: bool  # True if this is the session making the request

    model_config = {"from_attributes": True}


class RevokeSessionResponse(BaseModel):
    """Response schema for session revocation."""

    ok: bool = True


class RevokeAllSessionsResponse(BaseModel):
    """Response schema for revoking all sessions."""

    revoked_count: int


class PasswordResetRequest(BaseModel):
    """Request schema for password reset request."""

    email: EmailStr


class PasswordResetConfirm(BaseModel):
    """Request schema for password reset confirmation."""

    token: str = Field(min_length=64, max_length=64)
    new_password: str = Field(min_length=8, max_length=128)


class EmailVerificationRequest(BaseModel):
    """Request schema for sending verification email."""

    pass  # No additional fields needed - uses current authenticated user


class EmailVerificationResponse(BaseModel):
    """Response schema for email verification request."""

    ok: bool = True
    message: str = "Verification email sent. Please check your inbox."


class EmailVerificationStatus(BaseModel):
    """Response schema for email verification status."""

    email_verified: bool
    email: str


# =============================================================================
# Two-Factor Authentication Schemas
# =============================================================================


class TotpEnableResponse(BaseModel):
    """Response schema for enabling 2FA."""

    secret: str  # Base32-encoded secret for manual entry
    qr_code_base64: str  # Base64-encoded PNG QR code image
    backup_codes: list[str]  # 10 backup codes for recovery
    uri: str  # otpauth:// URI for manual entry


class TotpVerifyRequest(BaseModel):
    """Request schema for verifying TOTP code."""

    code: str = Field(
        min_length=6, max_length=8, description="6-digit TOTP code or 8-char backup code"
    )


class TotpVerifyResponse(BaseModel):
    """Response schema for TOTP verification."""

    ok: bool = True


class TotpDisableRequest(BaseModel):
    """Request schema for disabling 2FA."""

    code: str = Field(min_length=6, max_length=8, description="Current TOTP code or backup code")


class TotpDisableResponse(BaseModel):
    """Response schema for disabling 2FA."""

    ok: bool = True


class TotpStatusResponse(BaseModel):
    """Response schema for 2FA status."""

    enabled: bool
    backup_codes_remaining: int


class LoginWith2FARequest(BaseModel):
    """Request schema for login with 2FA verification."""

    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    totp_code: str = Field(
        min_length=6, max_length=8, description="6-digit TOTP code or backup code"
    )
