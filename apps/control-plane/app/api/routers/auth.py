from __future__ import annotations

import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy.orm import Session

from app.api import deps
from app.core import security
from app.core.config import get_settings
from app.db import models
from app.db.session import get_db
from app.schemas import auth as auth_schema
from app.schemas import user as user_schema
from app.services import (
    audit_service,
    auth_service,
    email_service,
    password_service,
    session_service,
    totp_service,
    verification_service,
)
from app.services.notification_service import get_notification_service

router = APIRouter(prefix="/auth", tags=["auth"])
limiter = Limiter(key_func=get_remote_address)
templates = Jinja2Templates(directory="app/templates")


@router.post("/register", response_model=user_schema.UserRead, status_code=201)
def register_user(
    payload: auth_schema.RegisterRequest,
    db: Session = Depends(get_db),
    current_admin: models.User = Depends(deps.get_current_admin),
):
    return auth_service.register_user(db, payload, actor_user_id=current_admin.id)


@router.post("/login", response_model=auth_schema.TokenResponse)
@limiter.limit("10/minute")  # Max 10 login attempts per minute per IP
async def login(
    request: Request,
    payload: auth_schema.LoginRequest,
    db: Session = Depends(get_db),
):
    # Extract device/client information
    device_name = request.headers.get("x-device-name")
    user_agent = request.headers.get("user-agent")
    ip_address = request.client.host if request.client else None

    # First authenticate to check if 2FA is enabled
    user = auth_service.authenticate_user(db, payload.email, payload.password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    # Check if 2FA is enabled - require /auth/login/2fa endpoint
    if user.totp_enabled:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="2FA is enabled. Use /auth/login/2fa endpoint with totp_code.",
            headers={"X-2FA-Required": "true"},
        )

    result = auth_service.login(
        db=db,
        payload=payload,
        device_name=device_name,
        user_agent=user_agent,
        ip_address=ip_address,
    )

    # Log successful login
    audit_service.log_action(
        db=db,
        action=models.AuditAction.USER_LOGIN,
        actor_user_id=user.id,
        ip_address=ip_address,
        user_agent=user_agent,
    )

    db.commit()

    # Queue notifications (session created + login webhook)
    notification_service = get_notification_service()
    await notification_service.notify_session_created(db, user, device_name, ip_address, user_agent)
    await notification_service.notify_user_login(db, user, ip_address, user_agent)

    return result


@router.post("/logout", response_model=auth_schema.LogoutResponse)
async def logout(
    request: Request,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(deps.get_current_user),
) -> auth_schema.LogoutResponse:
    ip_address = request.client.host if request.client else None
    user_agent = request.headers.get("user-agent")

    # Log logout
    audit_service.log_action(
        db=db,
        action=models.AuditAction.USER_LOGOUT,
        actor_user_id=current_user.id,
        ip_address=ip_address,
        user_agent=user_agent,
    )

    # Queue logout notification (webhook only)
    notification_service = get_notification_service()
    await notification_service.notify_user_logout(db, current_user, ip_address, user_agent)

    return auth_schema.LogoutResponse()


@router.get("/me", response_model=user_schema.UserRead)
def get_me(current_user: models.User = Depends(deps.get_current_user)) -> models.User:
    return current_user


@router.post("/refresh", response_model=auth_schema.TokenResponse)
@limiter.limit("30/minute")  # Max 30 refresh requests per minute per IP
def refresh_token(
    request: Request,
    payload: auth_schema.RefreshTokenRequest,
    db: Session = Depends(get_db),
):
    """Refresh access token using refresh token.

    This endpoint implements single-use refresh tokens. Each refresh:
    1. Validates the provided refresh token
    2. Issues a new access token
    3. Rotates the refresh token (old one becomes invalid)
    4. Updates session last_activity timestamp

    Rate limited to 30 requests per minute per IP.
    """
    settings = get_settings()

    # Validate refresh token
    session = session_service.validate_refresh_token(db, payload.refresh_token)
    if not session:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token",
        )

    # Rotate refresh token (single-use pattern)
    _, new_refresh_token = session_service.rotate_refresh_token(db, session.id)

    # Create new access token with session_id
    new_access_token = security.create_access_token(
        str(session.user_id), session_id=str(session.id)
    )

    # Log token refresh
    audit_service.log_action(
        db=db,
        action=models.AuditAction.TOKEN_REFRESHED,
        actor_user_id=session.user_id,
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )

    db.commit()

    return auth_schema.TokenResponse(
        access_token=new_access_token,
        refresh_token=new_refresh_token,
        expires_in=settings.access_token_expire_minutes * 60,
    )


@router.get("/sessions", response_model=list[auth_schema.SessionInfo])
def list_sessions(
    request: Request,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(deps.get_current_user),
) -> list[auth_schema.SessionInfo]:
    """List all active sessions for the current user.

    Returns a list of all active sessions with device information.
    The current session (identified by the JWT token) is marked with is_current=True.
    """
    # Get session_id from JWT token if present
    token = request.headers.get("authorization", "").replace("Bearer ", "")
    current_session_id = None
    if token:
        try:
            payload = security.decode_access_token(token)
            current_session_id = payload.get("session_id")
        except Exception:
            pass  # Token might not have session_id (older tokens)

    # Get all active sessions for user
    sessions = session_service.get_user_sessions(db, current_user.id)

    # Convert to response schema
    result = []
    for session in sessions:
        result.append(
            auth_schema.SessionInfo(
                id=str(session.id),
                device_name=session.device_name,
                user_agent=session.user_agent,
                ip_address=session.ip_address,
                last_activity=session.last_activity,
                created_at=session.created_at,
                is_current=(str(session.id) == current_session_id),
            )
        )

    return result


@router.delete("/sessions/{session_id}", response_model=auth_schema.RevokeSessionResponse)
def revoke_session(
    session_id: str,
    request: Request,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(deps.get_current_user),
) -> auth_schema.RevokeSessionResponse:
    """Revoke a specific session.

    The session must belong to the current user. After revocation, the refresh token
    associated with this session will no longer be valid.
    """
    # Validate session_id format
    try:
        session_uuid = uuid.UUID(session_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid session ID format",
        )

    # Verify session exists and belongs to current user
    session = session_service.get_session_by_id(db, session_uuid)
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found",
        )

    if session.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot revoke another user's session",
        )

    # Revoke the session
    session_service.revoke_session(db, session_uuid)

    # Log session revocation
    audit_service.log_action(
        db=db,
        action=models.AuditAction.SESSION_REVOKED,
        actor_user_id=current_user.id,
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
        details={"session_id": session_id},
    )

    db.commit()

    return auth_schema.RevokeSessionResponse()


@router.delete("/sessions", response_model=auth_schema.RevokeAllSessionsResponse)
def revoke_all_sessions(
    request: Request,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(deps.get_current_user),
) -> auth_schema.RevokeAllSessionsResponse:
    """Revoke all sessions (logout everywhere).

    This will invalidate all refresh tokens for the current user, effectively
    logging them out of all devices. This includes the current session.
    """
    # Revoke all sessions for user
    revoked_count = session_service.revoke_all_user_sessions(db, current_user.id)

    # Log session revocation
    audit_service.log_action(
        db=db,
        action=models.AuditAction.SESSION_REVOKED,
        actor_user_id=current_user.id,
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
        details={"revoked_count": revoked_count, "all_sessions": True},
    )

    db.commit()

    return auth_schema.RevokeAllSessionsResponse(revoked_count=revoked_count)


@router.post("/password-reset/request", response_model=dict)
@limiter.limit("3/hour")  # Max 3 password reset requests per hour per IP
async def request_password_reset(
    request: Request,
    payload: auth_schema.PasswordResetRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
) -> dict:
    """Request password reset email.

    This endpoint always returns success to prevent email enumeration attacks.
    If the email exists, a reset email will be sent. If not, no email is sent but
    the response is identical.

    Rate limited to 3 requests per hour per IP address.
    """
    settings = get_settings()

    # Try to create reset token
    reset_token = password_service.create_reset_token(
        db, payload.email, expires_hours=settings.password_reset_expire_hours
    )

    # If user exists, send reset email in background
    if reset_token:
        # Build reset URL
        reset_url = f"{request.base_url}auth/password-reset/{reset_token}"

        # Send email in background task
        email_svc = email_service.EmailService(settings)
        background_tasks.add_task(email_svc.send_password_reset, payload.email, reset_url)

        # Log password reset request
        audit_service.log_action(
            db=db,
            action=models.AuditAction.PASSWORD_RESET_REQUESTED,
            ip_address=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"),
            details={"email": payload.email},
        )

    db.commit()

    # Always return success (prevent email enumeration)
    return {"ok": True, "message": "If the email exists, a password reset link has been sent."}


@router.get("/password-reset/{token}", response_class=HTMLResponse)
def password_reset_form(
    token: str,
    request: Request,
    db: Session = Depends(get_db),
) -> HTMLResponse:
    """Display password reset form (SSR).

    Shows a form if the token is valid, or an error page if the token is invalid/expired.
    """
    # Validate token
    user = password_service.validate_reset_token(db, token)

    if not user:
        # Token is invalid or expired
        return templates.TemplateResponse(
            request=request,
            name="password_reset.html",
            context={
                "token": token,
                "error": (
                    "This password reset link is invalid or has expired. "
                    "Please request a new one."
                ),
                "success": False,
            },
        )

    # Token is valid, show form
    return templates.TemplateResponse(
        request=request,
        name="password_reset.html",
        context={
            "token": token,
            "error": None,
            "success": False,
        },
    )


@router.post("/password-reset/confirm", response_class=HTMLResponse)
async def confirm_password_reset_form(
    request: Request,
    token: str = Form(...),
    new_password: str = Form(...),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    """Complete password reset (form submission).

    This endpoint handles the HTML form submission from the reset page.
    """
    # Validate password length
    if len(new_password) < 8:
        return templates.TemplateResponse(
            request=request,
            name="password_reset.html",
            context={
                "token": token,
                "error": "Password must be at least 8 characters long.",
                "success": False,
            },
        )

    # Get user before reset (token becomes invalid after reset)
    user = password_service.validate_reset_token(db, token)

    # Complete password reset
    success = password_service.complete_reset(db, token, new_password)

    if not success:
        return templates.TemplateResponse(
            request=request,
            name="password_reset.html",
            context={
                "token": token,
                "error": (
                    "This password reset link is invalid or has expired. "
                    "Please request a new one."
                ),
                "success": False,
            },
        )

    ip_address = request.client.host if request.client else None
    user_agent = request.headers.get("user-agent")

    if user:
        audit_service.log_action(
            db=db,
            action=models.AuditAction.PASSWORD_RESET_COMPLETED,
            actor_user_id=user.id,
            ip_address=ip_address,
            user_agent=user_agent,
        )

        # Queue password changed notification
        notification_service = get_notification_service()
        await notification_service.notify_password_changed(
            db,
            user,
            changed_by_admin=False,
            actor=None,
            ip_address=ip_address,
            user_agent=user_agent,
        )

    db.commit()

    # Show success message
    return templates.TemplateResponse(
        request=request,
        name="password_reset.html",
        context={
            "token": token,
            "error": None,
            "success": True,
        },
    )


# =============================================================================
# Email Verification Endpoints
# =============================================================================


@router.get("/email/verify/status", response_model=auth_schema.EmailVerificationStatus)
def email_verification_status(
    current_user: models.User = Depends(deps.get_current_user),
) -> auth_schema.EmailVerificationStatus:
    """Get current user's email verification status.

    Returns whether the user's email has been verified.
    """
    return auth_schema.EmailVerificationStatus(
        email_verified=current_user.email_verified,
        email=current_user.email,
    )


@router.post("/email/verify/request", response_model=auth_schema.EmailVerificationResponse)
@limiter.limit("3/hour")  # Max 3 verification requests per hour per IP
async def request_email_verification(
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(deps.get_current_user),
) -> auth_schema.EmailVerificationResponse:
    """Request email verification email.

    Sends a verification email to the current user's email address.
    If the email is already verified, returns success but doesn't send an email.

    Rate limited to 3 requests per hour per IP address.
    """
    settings = get_settings()

    # If already verified, return success without sending email
    if current_user.email_verified:
        return auth_schema.EmailVerificationResponse(
            ok=True,
            message="Email is already verified.",
        )

    # Create verification token
    verification_token = verification_service.create_verification_token(
        db, current_user.id, expires_hours=settings.email_verification_expire_hours
    )

    # Build verification URL
    verification_url = f"{request.base_url}auth/email/verify/{verification_token}"

    # Send email in background task
    email_svc = email_service.EmailService(settings)
    background_tasks.add_task(
        email_svc.send_verification_email, current_user.email, verification_url
    )

    # Log verification request
    audit_service.log_action(
        db=db,
        action=models.AuditAction.EMAIL_VERIFICATION_SENT,
        actor_user_id=current_user.id,
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )

    db.commit()

    return auth_schema.EmailVerificationResponse()


@router.get("/email/verify/{token}", response_class=HTMLResponse)
def verify_email(
    token: str,
    request: Request,
    db: Session = Depends(get_db),
) -> HTMLResponse:
    """Verify email address (SSR).

    Validates the verification token and marks the user's email as verified.
    Shows a success page if verified, or an error page if token is invalid/expired.
    """
    # Complete verification
    user = verification_service.complete_verification(db, token)

    if not user:
        # Token is invalid or expired
        return templates.TemplateResponse(
            request=request,
            name="email_verification.html",
            context={
                "title": "Verification Failed",
                "subtitle": "",
                "error": (
                    "This verification link is invalid or has expired. "
                    "Please request a new verification email."
                ),
                "success": None,
            },
        )

    # Log verification
    audit_service.log_action(
        db=db,
        action=models.AuditAction.EMAIL_VERIFIED,
        actor_user_id=user.id,
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )

    db.commit()

    # Show success message
    return templates.TemplateResponse(
        request=request,
        name="email_verification.html",
        context={
            "title": "Email Verified",
            "subtitle": f"Your email address {user.email} has been verified.",
            "error": None,
            "success": "Your email has been successfully verified!",
        },
    )


# =============================================================================
# Two-Factor Authentication Endpoints
# =============================================================================


@router.get("/2fa/status", response_model=auth_schema.TotpStatusResponse)
def get_2fa_status(
    current_user: models.User = Depends(deps.get_current_user),
    db: Session = Depends(get_db),
) -> auth_schema.TotpStatusResponse:
    """Get current user's 2FA status.

    Returns whether 2FA is enabled and how many backup codes remain.
    """
    status = totp_service.get_user_totp_status(db, current_user.id)
    return auth_schema.TotpStatusResponse(
        enabled=status["enabled"],
        backup_codes_remaining=status["backup_codes_remaining"],
    )


@router.post("/2fa/enable", response_model=auth_schema.TotpEnableResponse)
def enable_2fa(
    request: Request,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(deps.get_current_user),
) -> auth_schema.TotpEnableResponse:
    """Generate TOTP secret and backup codes for 2FA setup.

    This endpoint generates the secret and backup codes but does NOT enable 2FA yet.
    The user must verify the setup by calling /auth/2fa/verify with a valid TOTP code
    to actually enable 2FA.

    Returns:
        - secret: Base32-encoded TOTP secret for manual entry
        - qr_code_base64: Base64-encoded PNG QR code for scanning
        - backup_codes: 10 single-use recovery codes
        - uri: otpauth:// URI for manual entry in authenticator apps
    """
    if current_user.totp_enabled:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="2FA is already enabled. Disable it first to reconfigure.",
        )

    # Generate new TOTP secret
    secret = totp_service.generate_totp_secret()
    settings = get_settings()
    issuer = settings.server_name or "Relay Control Plane"

    # Generate provisioning URI
    uri = totp_service.get_totp_uri(secret, current_user.email, issuer)

    # Generate QR code
    qr_code_base64 = totp_service.generate_qr_code_base64(uri)

    # Generate backup codes
    backup_codes = totp_service.generate_backup_codes(10)

    # Store secret temporarily (but don't enable yet)
    # We store it but keep totp_enabled=False until verified
    current_user.totp_secret_encrypted = secret
    current_user.backup_codes_encrypted = totp_service.encode_backup_codes(backup_codes)
    db.commit()

    return auth_schema.TotpEnableResponse(
        secret=secret,
        qr_code_base64=qr_code_base64,
        backup_codes=backup_codes,
        uri=uri,
    )


@router.post("/2fa/verify", response_model=auth_schema.TotpVerifyResponse)
def verify_2fa_setup(
    payload: auth_schema.TotpVerifyRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(deps.get_current_user),
) -> auth_schema.TotpVerifyResponse:
    """Verify TOTP setup and enable 2FA.

    The user must call /auth/2fa/enable first to generate the secret,
    then call this endpoint with a valid TOTP code from their authenticator app
    to confirm the setup and actually enable 2FA.
    """
    if current_user.totp_enabled:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="2FA is already enabled.",
        )

    if not current_user.totp_secret_encrypted:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No 2FA setup in progress. Call /auth/2fa/enable first.",
        )

    # Verify the code
    if not totp_service.verify_totp_code(current_user.totp_secret_encrypted, payload.code):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid TOTP code. Please check your authenticator app and try again.",
        )

    # Enable 2FA
    current_user.totp_enabled = True
    db.commit()

    # Log 2FA enabled
    audit_service.log_action(
        db=db,
        action=models.AuditAction.TOTP_ENABLED,
        actor_user_id=current_user.id,
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )

    db.commit()

    return auth_schema.TotpVerifyResponse()


@router.post("/2fa/disable", response_model=auth_schema.TotpDisableResponse)
def disable_2fa(
    payload: auth_schema.TotpDisableRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(deps.get_current_user),
) -> auth_schema.TotpDisableResponse:
    """Disable 2FA for the current user.

    Requires a valid TOTP code or backup code to confirm the action.
    """
    if not current_user.totp_enabled:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="2FA is not enabled.",
        )

    # Verify the code (TOTP or backup)
    is_valid, was_backup = totp_service.verify_user_totp(db, current_user.id, payload.code)
    if not is_valid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid code. Please enter a valid TOTP code or backup code.",
        )

    # Disable 2FA
    totp_service.disable_totp_for_user(db, current_user.id)

    # Log 2FA disabled
    audit_service.log_action(
        db=db,
        action=models.AuditAction.TOTP_DISABLED,
        actor_user_id=current_user.id,
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
        details={"used_backup_code": was_backup},
    )

    db.commit()

    return auth_schema.TotpDisableResponse()


@router.post("/login/2fa", response_model=auth_schema.TokenResponse)
@limiter.limit("10/minute")  # Rate limit 2FA login attempts
async def login_with_2fa(
    request: Request,
    payload: auth_schema.LoginWith2FARequest,
    db: Session = Depends(get_db),
) -> auth_schema.TokenResponse:
    """Login with email, password, and 2FA code.

    Use this endpoint when the user has 2FA enabled. The regular /auth/login
    endpoint will return a 403 error for users with 2FA enabled, prompting
    the client to use this endpoint instead.
    """
    settings = get_settings()

    # Authenticate with email and password first
    user = auth_service.authenticate_user(db, payload.email, payload.password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    if not user.totp_enabled:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="2FA is not enabled for this account. Use /auth/login instead.",
        )

    # Verify 2FA code
    is_valid, was_backup = totp_service.verify_user_totp(db, user.id, payload.totp_code)
    if not is_valid:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid 2FA code",
        )

    # Log backup code usage
    if was_backup:
        audit_service.log_action(
            db=db,
            action=models.AuditAction.TOTP_BACKUP_USED,
            actor_user_id=user.id,
            ip_address=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"),
        )

    # Create session
    device_name = request.headers.get("x-device-name")
    user_agent = request.headers.get("user-agent")
    ip_address = request.client.host if request.client else None

    session, refresh_token = session_service.create_session(
        db=db,
        user_id=user.id,
        device_name=device_name,
        user_agent=user_agent,
        ip_address=ip_address,
        expires_days=settings.refresh_token_expire_days,
    )

    # Create access token with session_id
    access_token = security.create_access_token(str(user.id), session_id=str(session.id))

    # Log login
    audit_service.log_action(
        db=db,
        action=models.AuditAction.USER_LOGIN,
        actor_user_id=user.id,
        ip_address=ip_address,
        user_agent=user_agent,
        details={"method": "password_2fa"},
    )

    db.commit()

    # Queue notifications (session created + login webhook)
    notification_service = get_notification_service()
    await notification_service.notify_session_created(db, user, device_name, ip_address, user_agent)
    await notification_service.notify_user_login(db, user, ip_address, user_agent)

    return auth_schema.TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=settings.access_token_expire_minutes * 60,
    )
