from __future__ import annotations

import base64
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import cbor2
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


# CWT claim labels (RFC 8392)
CWT_CLAIM_ISS = 1  # issuer
CWT_CLAIM_SUB = 2  # subject
CWT_CLAIM_AUD = 3  # audience
CWT_CLAIM_EXP = 4  # expiration
CWT_CLAIM_IAT = 6  # issued at

# y-sweet custom claims (private use range)
CWT_CLAIM_SCOPE = -80201
CWT_CLAIM_CHANNEL = -80202

# CBOR tags
CWT_TAG = 61  # CWT wrapper tag (RFC 8392)
COSE_SIGN1_TAG = 18  # COSE_Sign1 tag


def create_relay_token_cwt(
    private_key: ed25519.Ed25519PrivateKey,
    key_id: str,
    doc_id: str,
    mode: str,
    expires_minutes: int,
    audience: str | None = None,
    issuer: str = "relay-control-plane",
) -> str:
    """Create CWT (CBOR Web Token) for y-sweet relay-server authentication.

    y-sweet expects CWT tokens with:
    - CWT tag 61 wrapper
    - COSE_Sign1 with Ed25519 signature (EdDSA algorithm)
    - Claims: iss, aud, exp, iat, scope (-80201)
    - Scope format: "doc:{doc_id}:rw" or "doc:{doc_id}:r"

    Args:
        private_key: Ed25519 private key object
        key_id: Key identifier for COSE header (kid)
        doc_id: Document ID for relay access
        mode: Access mode ("read" or "write")
        expires_minutes: Token TTL in minutes
        audience: Relay server URL for 'aud' claim
        issuer: Token issuer (default: "relay-control-plane")

    Returns:
        Base64url-encoded CWT token string
    """
    now = utcnow()

    # Build scope string: "doc:{doc_id}:rw" or "doc:{doc_id}:r"
    auth_code = "rw" if mode == "write" else "r"
    scope = f"doc:{doc_id}:{auth_code}"

    # Build claims map with integer keys (RFC 8392)
    # Note: y-sweet expects minimal claims - only iss, iat, scope
    # Do NOT include exp or aud - y-sweet native tokens don't have them
    claims = {
        CWT_CLAIM_ISS: issuer,
        CWT_CLAIM_IAT: int(now.timestamp()),
        CWT_CLAIM_SCOPE: scope,
    }

    # Encode claims to CBOR
    claims_cbor = cbor2.dumps(claims)

    # Create COSE_Sign1 message
    # Protected header: algorithm (EdDSA = -8) only
    # Note: y-sweet does NOT include kid in protected header
    protected = {1: -8}  # alg: EdDSA

    # Encode protected header
    protected_cbor = cbor2.dumps(protected)

    # Create Sig_structure for signing: ["Signature1", protected, external_aad, payload]
    sig_structure = ["Signature1", protected_cbor, b"", claims_cbor]
    sig_structure_cbor = cbor2.dumps(sig_structure)

    # Sign with Ed25519
    signature = private_key.sign(sig_structure_cbor)

    # Build COSE_Sign1 structure: [protected, unprotected, payload, signature]
    cose_sign1 = [protected_cbor, {}, claims_cbor, signature]

    # Encode COSE_Sign1 with tag 18
    cose_sign1_cbor = cbor2.dumps(cbor2.CBORTag(COSE_SIGN1_TAG, cose_sign1))

    # Wrap with CWT tag 61
    cwt_cbor = cbor2.dumps(cbor2.CBORTag(CWT_TAG, cbor2.loads(cose_sign1_cbor)))

    # Base64url encode for transport (no padding)
    token_b64 = base64.urlsafe_b64encode(cwt_cbor).decode("utf-8").rstrip("=")

    return token_b64


def verify_relay_token_cwt(
    public_key: ed25519.Ed25519PublicKey,
    token: str,
    expected_audience: str | None = None,
) -> dict:
    """Verify a CWT token and extract claims.

    Args:
        public_key: Ed25519 public key for verification
        token: Base64url-encoded CWT token
        expected_audience: Expected audience (optional validation)

    Returns:
        Dict with decoded claims

    Raises:
        ValueError: If token is invalid or verification fails
    """
    # Decode base64url (add padding if needed)
    padding = 4 - len(token) % 4
    if padding != 4:
        token += "=" * padding
    token_bytes = base64.urlsafe_b64decode(token)

    # Parse outer CBOR (should be CWT tag 61)
    outer = cbor2.loads(token_bytes)
    if not isinstance(outer, cbor2.CBORTag) or outer.tag != CWT_TAG:
        raise ValueError(f"Expected CWT tag 61, got: {outer}")

    # Parse inner (should be COSE_Sign1 tag 18)
    inner = outer.value
    if isinstance(inner, cbor2.CBORTag):
        if inner.tag != COSE_SIGN1_TAG:
            raise ValueError(f"Expected COSE_Sign1 tag 18, got tag: {inner.tag}")
        cose_sign1 = inner.value
    else:
        cose_sign1 = inner

    if not isinstance(cose_sign1, list) or len(cose_sign1) != 4:
        raise ValueError("Invalid COSE_Sign1 structure")

    protected_cbor, _unprotected, payload, signature = cose_sign1

    # Verify signature
    sig_structure = ["Signature1", protected_cbor, b"", payload]
    sig_structure_cbor = cbor2.dumps(sig_structure)

    try:
        public_key.verify(signature, sig_structure_cbor)
    except Exception as e:
        raise ValueError(f"Signature verification failed: {e}")

    # Parse claims
    claims_map = cbor2.loads(payload)

    # Convert integer keys to named claims
    claims = {}
    key_mapping = {
        CWT_CLAIM_ISS: "iss",
        CWT_CLAIM_SUB: "sub",
        CWT_CLAIM_AUD: "aud",
        CWT_CLAIM_EXP: "exp",
        CWT_CLAIM_IAT: "iat",
        CWT_CLAIM_SCOPE: "scope",
        CWT_CLAIM_CHANNEL: "channel",
    }

    for k, v in claims_map.items():
        if k in key_mapping:
            claims[key_mapping[k]] = v
        else:
            claims[k] = v

    # Validate audience if specified
    if expected_audience and claims.get("aud") != expected_audience:
        raise ValueError(
            f"Audience mismatch: expected '{expected_audience}', got '{claims.get('aud')}'"
        )

    return claims
