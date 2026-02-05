from __future__ import annotations

import base64
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519
from passlib.context import CryptContext

from app.core.config import get_settings

logger = logging.getLogger(__name__)

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)


def create_access_token(
    subject: str, expires_minutes: int | None = None, session_id: str | None = None
) -> str:
    settings = get_settings()
    expire_delta = timedelta(minutes=expires_minutes or settings.access_token_expire_minutes)
    to_encode = {
        "sub": subject,
        "exp": utcnow() + expire_delta,
        "iat": utcnow(),
    }
    if session_id:
        to_encode["session_id"] = session_id
    return jwt.encode(to_encode, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> dict[str, Any]:
    settings = get_settings()
    return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])


# Ed25519 keypair generation and relay token signing


def generate_ed25519_keypair() -> tuple[str, str]:
    """Generate Ed25519 keypair for relay server authentication.

    Returns:
        tuple: (private_key_pem, public_key_base64)
    """
    private_key = ed25519.Ed25519PrivateKey.generate()

    # Export private key as PEM
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")

    # Export public key as raw base64 (for relay.toml)
    public_key = private_key.public_key()
    public_bytes = public_key.public_bytes(
        encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw
    )
    public_base64 = base64.b64encode(public_bytes).decode("utf-8")

    return (private_pem, public_base64)


def load_or_generate_relay_keypair(
    settings,
) -> tuple[ed25519.Ed25519PrivateKey, str, str]:
    """Load existing private key or generate new keypair.

    Args:
        settings: Application settings

    Returns:
        tuple: (private_key_object, public_key_base64, key_id)
    """
    if settings.relay_private_key:
        # Load existing private key from PEM
        # Support both base64-encoded (from .env) and PEM format
        private_key_str = settings.relay_private_key
        if not private_key_str.startswith("-----BEGIN"):
            # Base64-encoded - decode to PEM
            import base64 as b64

            private_key_str = b64.b64decode(private_key_str).decode("utf-8")

        private_key = serialization.load_pem_private_key(
            private_key_str.encode("utf-8"), password=None
        )
        public_bytes = private_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw
        )
        public_base64 = base64.b64encode(public_bytes).decode("utf-8")
        key_id = settings.relay_key_id
    else:
        # Generate new keypair on first run
        private_pem, public_base64 = generate_ed25519_keypair()
        private_key = serialization.load_pem_private_key(private_pem.encode("utf-8"), password=None)
        key_id = f"relay_cp_{datetime.now(timezone.utc).strftime('%Y_%m_%d')}"

        # Log for operator to save (not logging private key for security)
        logger.warning(
            f"Generated new Ed25519 keypair:\n"
            f"Key ID: {key_id}\n"
            f"Public Key: {public_base64}\n"
            f"Add to relay.toml:\n"
            f"[[auth]]\n"
            f'key_id = "{key_id}"\n'
            f'public_key = "{public_base64}"\n\n'
            f"To persist, set RELAY_PRIVATE_KEY and RELAY_KEY_ID environment variables"
        )

    return (private_key, public_base64, key_id)


def create_relay_token(
    private_key: ed25519.Ed25519PrivateKey,
    key_id: str,
    doc_id: str,
    mode: str,
    expires_minutes: int,
    audience: str | None = None,
) -> str:
    """Create Ed25519-signed JWT for relay-server authentication.

    Args:
        private_key: Ed25519 private key object
        key_id: Key identifier for JWT header (kid)
        doc_id: Document ID for relay access
        mode: Access mode ("read" or "write")
        expires_minutes: Token TTL in minutes
        audience: Relay server URL for 'aud' claim (e.g., wss://tr.example.com)

    Returns:
        Signed JWT token string
    """
    now = utcnow()
    payload = {
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=expires_minutes)).timestamp()),
        "doc": doc_id,
        "mode": mode,
    }

    if audience:
        payload["aud"] = audience

    return jwt.encode(payload, private_key, algorithm="EdDSA", headers={"kid": key_id})
