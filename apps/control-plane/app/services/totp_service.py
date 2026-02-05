"""TOTP Two-Factor Authentication service."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import secrets
import uuid
from typing import Any

import pyotp
import qrcode
from sqlalchemy.orm import Session

from app.db import models


def generate_totp_secret() -> str:
    """Generate a new TOTP secret (base32 encoded).

    Returns:
        Base32-encoded secret suitable for TOTP generation.
    """
    return pyotp.random_base32()


def get_totp_uri(secret: str, email: str, issuer: str = "Relay Control Plane") -> str:
    """Get TOTP provisioning URI for QR code generation.

    Args:
        secret: Base32-encoded TOTP secret
        email: User's email address
        issuer: Service name shown in authenticator app

    Returns:
        otpauth:// URI for TOTP provisioning
    """
    totp = pyotp.TOTP(secret)
    return totp.provisioning_uri(name=email, issuer_name=issuer)


def generate_qr_code_base64(uri: str) -> str:
    """Generate QR code as base64-encoded PNG image.

    Args:
        uri: The TOTP provisioning URI

    Returns:
        Base64-encoded PNG image data (without data: prefix)
    """
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        box_size=10,
        border=4,
    )
    qr.add_data(uri)
    qr.make(fit=True)

    img = qr.make_image(fill_color="black", back_color="white")

    # Convert to bytes
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    buffer.seek(0)

    # Encode as base64
    return base64.b64encode(buffer.getvalue()).decode("utf-8")


def verify_totp_code(secret: str, code: str, window: int = 1) -> bool:
    """Verify a TOTP code.

    Args:
        secret: Base32-encoded TOTP secret
        code: The 6-digit code to verify
        window: Number of time periods before/after to accept (for clock skew)

    Returns:
        True if code is valid, False otherwise
    """
    # Normalize code (remove spaces and dashes)
    code = code.replace(" ", "").replace("-", "")

    if len(code) != 6 or not code.isdigit():
        return False

    totp = pyotp.TOTP(secret)
    return totp.verify(code, valid_window=window)


def generate_backup_codes(count: int = 10) -> list[str]:
    """Generate backup codes for 2FA recovery.

    Args:
        count: Number of backup codes to generate

    Returns:
        List of backup codes (8 characters each)
    """
    codes = []
    for _ in range(count):
        # Generate 8-character alphanumeric code
        code = secrets.token_hex(4).upper()
        codes.append(code)
    return codes


def hash_backup_code(code: str) -> str:
    """Hash a backup code for storage.

    Args:
        code: Plain backup code

    Returns:
        SHA-256 hash of the code
    """
    normalized = code.upper().replace(" ", "").replace("-", "")
    return hashlib.sha256(normalized.encode()).hexdigest()


def encode_backup_codes(codes: list[str]) -> str:
    """Encode backup codes as JSON for storage.

    Args:
        codes: List of hashed backup codes with used status

    Returns:
        JSON-encoded string
    """
    # Store as list of {"hash": "...", "used": false}
    code_data = [{"hash": hash_backup_code(code), "used": False} for code in codes]
    return json.dumps(code_data)


def decode_backup_codes(encoded: str | None) -> list[dict[str, Any]]:
    """Decode backup codes from storage.

    Args:
        encoded: JSON-encoded backup codes

    Returns:
        List of {"hash": "...", "used": bool} dicts
    """
    if not encoded:
        return []
    try:
        return json.loads(encoded)
    except json.JSONDecodeError:
        return []


def verify_backup_code(encoded_codes: str | None, code: str) -> tuple[bool, str | None]:
    """Verify a backup code and mark it as used.

    Args:
        encoded_codes: JSON-encoded backup codes from database
        code: The backup code to verify

    Returns:
        Tuple of (is_valid, updated_encoded_codes or None if invalid)
    """
    codes = decode_backup_codes(encoded_codes)
    code_hash = hash_backup_code(code)

    for code_entry in codes:
        if code_entry["hash"] == code_hash and not code_entry["used"]:
            # Mark as used
            code_entry["used"] = True
            return True, json.dumps(codes)

    return False, None


def get_remaining_backup_codes(encoded_codes: str | None) -> int:
    """Get count of remaining (unused) backup codes.

    Args:
        encoded_codes: JSON-encoded backup codes from database

    Returns:
        Number of unused backup codes
    """
    codes = decode_backup_codes(encoded_codes)
    return sum(1 for code in codes if not code["used"])


def enable_totp_for_user(
    db: Session,
    user_id: uuid.UUID,
    secret: str,
    backup_codes: list[str],
) -> bool:
    """Enable TOTP 2FA for a user.

    Args:
        db: Database session
        user_id: User ID
        secret: TOTP secret to store
        backup_codes: List of plain backup codes

    Returns:
        True if enabled successfully
    """
    user = db.get(models.User, user_id)
    if not user:
        return False

    # Store secret (plaintext for now - TODO: encrypt in production)
    user.totp_secret_encrypted = secret
    user.totp_enabled = True
    user.backup_codes_encrypted = encode_backup_codes(backup_codes)

    db.commit()
    return True


def disable_totp_for_user(
    db: Session,
    user_id: uuid.UUID,
) -> bool:
    """Disable TOTP 2FA for a user.

    Args:
        db: Database session
        user_id: User ID

    Returns:
        True if disabled successfully
    """
    user = db.get(models.User, user_id)
    if not user:
        return False

    user.totp_secret_encrypted = None
    user.totp_enabled = False
    user.backup_codes_encrypted = None

    db.commit()
    return True


def get_user_totp_status(db: Session, user_id: uuid.UUID) -> dict[str, Any]:
    """Get TOTP status for a user.

    Args:
        db: Database session
        user_id: User ID

    Returns:
        Dict with enabled status and backup codes remaining
    """
    user = db.get(models.User, user_id)
    if not user:
        return {"enabled": False, "backup_codes_remaining": 0}

    return {
        "enabled": user.totp_enabled,
        "backup_codes_remaining": get_remaining_backup_codes(user.backup_codes_encrypted),
    }


def verify_user_totp(
    db: Session,
    user_id: uuid.UUID,
    code: str,
) -> tuple[bool, bool]:
    """Verify TOTP code for a user.

    Args:
        db: Database session
        user_id: User ID
        code: TOTP code or backup code to verify

    Returns:
        Tuple of (is_valid, was_backup_code)
    """
    user = db.get(models.User, user_id)
    if not user or not user.totp_enabled or not user.totp_secret_encrypted:
        return False, False

    # First try TOTP code
    if verify_totp_code(user.totp_secret_encrypted, code):
        return True, False

    # Try backup code
    is_valid, updated_codes = verify_backup_code(user.backup_codes_encrypted, code)
    if is_valid and updated_codes:
        user.backup_codes_encrypted = updated_codes
        db.commit()
        return True, True

    return False, False
