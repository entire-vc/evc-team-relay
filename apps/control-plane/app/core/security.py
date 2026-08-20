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
    """Verify a plaintext password against a stored hash.

    OAuth-only accounts are created with password_hash="" (oauth_service.py)
    since they have no password — passlib can't identify an empty string as a
    hash and raises UnknownHashError rather than returning False (TR-20; that
    used to surface as an unhandled 500 from /auth/login instead of a 401).

    UnknownHashError is a ValueError subclass; passlib's bcrypt backend also
    raises a *bare* ValueError (not UnknownHashError) for a hash that has a
    recognizable scheme prefix but is otherwise malformed — e.g. a truncated
    salt. Catch ValueError, not just UnknownHashError, to fail closed on both
    shapes rather than just the empty-string one. hashed_password always
    comes from the DB (never attacker-controlled), so failing closed here
    only ever denies a login/share-password check, never allows one.
    """
    try:
        return pwd_context.verify(plain_password, hashed_password)
    except ValueError as e:
        # The empty-string OAuth-only case is routine and expected; anything
        # else reaching here (a real but malformed stored hash) is more
        # likely a genuine data issue, so log it — the alternative is an
        # ordinary-looking 401 with zero trace of the underlying corruption.
        # Log only the exception type, never str(e) — passlib's ValueError
        # message can echo back the malformed hash it failed to parse, and
        # this is password-verification code. Rule still fires on proximity
        # to plain_password/hashed_password in scope; nothing secret is
        # actually interpolated below (only the exception's type name).
        # nosemgrep: python.lang.security.audit.logging.logger-credential-leak.python-logger-credential-disclosure  # noqa: E501
        logger.warning(
            "Password verification failed on an unverifiable hash (%s)",
            type(e).__name__,
        )
        return False


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


def create_file_token(
    subject: str,
    share_id: str,
    path: str,
    sha256: str,
    content_type: str,
    content_length: int,
    expires_minutes: int = 10,
) -> str:
    """Mint a short-lived, path-scoped token for attachment (CAS) access.

    Deliberately NOT create_access_token with extra claims: this token must
    never be usable as a general session credential (see the "scope" check
    in deps._get_user_from_token) — it grants access to exactly one file's
    HEAD/download-url/upload-url for a few minutes, nothing else.
    """
    settings = get_settings()
    expire_delta = timedelta(minutes=expires_minutes)
    to_encode = {
        "sub": subject,
        "scope": "file",
        "share_id": share_id,
        "path": path,
        "sha256": sha256,
        "content_type": content_type,
        "content_length": content_length,
        "exp": utcnow() + expire_delta,
        "iat": utcnow(),
    }
    return jwt.encode(to_encode, settings.jwt_secret, algorithm=settings.jwt_algorithm)


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
    if not settings.relay_private_key:
        raise RuntimeError(
            "RELAY_PRIVATE_KEY is required but not set. "
            "Generate with: openssl genpkey -algorithm ed25519 -out relay_private.pem "
            "&& openssl base64 -A -in relay_private.pem — see docs/installation.md step 2."
        )

    # Load private key — supports both Base64-encoded (from .env) and raw PEM formats
    private_key_str = settings.relay_private_key
    if not private_key_str.startswith("-----BEGIN"):
        import base64 as b64

        private_key_str = b64.b64decode(private_key_str).decode("utf-8")

    private_key = serialization.load_pem_private_key(private_key_str.encode("utf-8"), password=None)
    public_bytes = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw
    )
    public_base64 = base64.b64encode(public_bytes).decode("utf-8")
    key_id = settings.relay_key_id

    return (private_key, public_base64, key_id)


# CWT claim labels (RFC 8392)
CWT_CLAIM_ISS = 1  # issuer
CWT_CLAIM_SUB = 2  # subject
CWT_CLAIM_AUD = 3  # audience
CWT_CLAIM_EXP = 4  # expiration
CWT_CLAIM_IAT = 6  # issued at

# y-sweet custom claims (private use range)
CWT_CLAIM_SCOPE = -80201
CWT_CLAIM_CHANNEL = -80202
# Control-plane extension: share binding for confused-deputy mitigation (H6).
# Confirmed NOT enforced by relay-server as of 2026-07-21 (TR-22 investigation) —
# our fork's cwt.rs/auth.rs never reads claim -80203. Emitted for forward-compat only.
CWT_CLAIM_SHARE = -80203

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
    share_id: str | None = None,
) -> str:
    """Create CWT (CBOR Web Token) for relay-server authentication.

    Token structure:
    - CWT tag 61 wrapper
    - COSE_Sign1 with Ed25519 signature (EdDSA algorithm)
    - Protected header: alg (1) + kid (4, bstr)
    - Claims: iss, iat, exp, aud, scope (-80201), share (-80203)
    - Scope format: "doc:{doc_id}:rw" or "doc:{doc_id}:r"

    Security notes (H6, re-verified TR-22 2026-07-21 against our own fork —
    ghcr.io/entire-vc/evc-relay-server, see docs/adr-relay-server-own-fork.md):
    - exp is included for TTL enforcement and IS enforced by relay-server, both at
      WS-connect (auth.rs verify_token_with_channel) and on every subsequent message
      (y-sweet-core/src/doc_connection.rs DocConnection::send checks expiration_time
      and closes the socket with "Token expired" — not a connect-only check). This is
      no longer a System3 unknown; it's our own code, covered by
      crates/relay/tests/token_expiration_integration_test.rs.
    - share_id (-80203) is still NOT read or enforced by relay-server — confirmed by
      inspection of crates/y-sweet-core/src/cwt.rs parse_claims_map and
      crates/y-sweet-core/src/auth.rs (neither references claim -80203). It is emitted
      here for forward-compat but provides no confused-deputy protection today. Real
      fix requires relay-server changes — track separately, do NOT assume this claim
      does anything yet.
    - Because there is no jti/revocation-list, remove_member cannot invalidate a
      token already handed to the ex-member — settings.relay_token_ttl_minutes (see
      config.py) is the only lever bounding that exposure window (TR-22, #f63a2bea).
    - kid/aud/iss (2026-08-20, #f975dd60): the previous version of this docstring said
      "aud is omitted: y-sweet rejects tokens with the aud claim" and omitted kid from
      the protected header entirely. That was true of upstream y-sweet; it has been
      false since 2025-10, when we moved to our own fork (evc-relay-server), which
      REQUIRES kid + aud, and validates iss against a fixed allowlist when present.
      Result: every CWT issued since the CWT migration (bec43fb, 2026-02-06) failed
      relay-server's auth check — collab was fully broken in prod for ~6 months,
      silently, because the missing-kid failure path (auth.rs find_verifying_key)
      never logs. Root-caused + reproduced live in #f3cb5365; do not reintroduce any
      of these three omissions.

    Args:
        private_key: Ed25519 private key object
        key_id: Key identifier for COSE header (kid) — REQUIRED for relay-server to find
            the right verification key; must match a [[auth]] key_id in relay.toml.
        doc_id: Document ID for relay access
        mode: Access mode ("read" or "write")
        expires_minutes: Token TTL in minutes
        audience: Expected audience — MUST equal relay.toml's [server].url exactly
            (see Settings.effective_relay_audience). Required: relay-server rejects
            tokens with no aud claim (MissingAudience) once it knows to expect one.
        issuer: Token issuer. Must be one of relay-server's VALID_ISSUERS allowlist
            ("relay-server", "auth.system3.dev", "auth.system3.md" as of image 0.9.9) —
            see Settings.relay_token_issuer. Default kept at "relay-control-plane" for
            call-site backward-compat, but callers going through token_service.py
            override this via settings.relay_token_issuer.
        share_id: Share UUID the token was issued against (added as CWT_CLAIM_SHARE)

    Returns:
        Base64url-encoded CWT token string
    """
    now = utcnow()

    # Build scope string: "doc:{doc_id}:rw" or "doc:{doc_id}:r"
    auth_code = "rw" if mode == "write" else "r"
    scope = f"doc:{doc_id}:{auth_code}"

    claims = {
        CWT_CLAIM_ISS: issuer,
        CWT_CLAIM_IAT: int(now.timestamp()),
        CWT_CLAIM_EXP: int((now + timedelta(minutes=expires_minutes)).timestamp()),
        CWT_CLAIM_SCOPE: scope,
    }

    # relay-server (evc-relay-server fork) requires aud — see docstring above.
    if audience:
        claims[CWT_CLAIM_AUD] = audience

    # Bind token to the issuing share — confused-deputy mitigation (H6).
    # Full enforcement requires relay-server (System3) to validate this claim.
    if share_id:
        claims[CWT_CLAIM_SHARE] = share_id

    # Encode claims to CBOR
    claims_cbor = cbor2.dumps(claims)

    # Create COSE_Sign1 message
    # Protected header: algorithm (EdDSA = -8) + key ID (COSE param 4, bstr).
    # relay-server's find_verifying_key (auth.rs) reads kid to pick which
    # relay.toml [[auth]] entry to verify against; without it, tokens only work
    # by accident (fallback loop over keys_without_id) or not at all if the
    # matching key has a key_id configured, as prod does (KeyMismatch, silently).
    protected = {1: -8, 4: key_id.encode("utf-8")}  # alg: EdDSA, kid: bstr

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
