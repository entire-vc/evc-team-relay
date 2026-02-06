"""Tests for CWT token generation, verification, and Ed25519 keypair management.

Level 1: Unit tests — no external dependencies, pure crypto/CBOR validation.
"""

from __future__ import annotations

import base64
import time

import cbor2
import pytest
from cryptography.hazmat.primitives.asymmetric import ed25519

from app.core.security import (
    COSE_SIGN1_TAG,
    CWT_CLAIM_IAT,
    CWT_CLAIM_ISS,
    CWT_CLAIM_SCOPE,
    CWT_TAG,
    create_relay_token_cwt,
    generate_ed25519_keypair,
    load_or_generate_relay_keypair,
    verify_relay_token_cwt,
)


# ── Helpers ──────────────────────────────────────────────────


def _make_keypair() -> tuple[ed25519.Ed25519PrivateKey, ed25519.Ed25519PublicKey]:
    """Generate a fresh Ed25519 keypair for tests."""
    private_key = ed25519.Ed25519PrivateKey.generate()
    return private_key, private_key.public_key()


def _decode_cwt_raw(token_b64: str) -> tuple[bytes, dict, bytes, bytes]:
    """Decode a CWT token into (protected_header_cbor, claims_map, payload_cbor, signature).

    Returns the raw COSE_Sign1 components for structural assertions.
    """
    padding = 4 - len(token_b64) % 4
    if padding != 4:
        token_b64 += "=" * padding
    token_bytes = base64.urlsafe_b64decode(token_b64)

    outer = cbor2.loads(token_bytes)
    assert isinstance(outer, cbor2.CBORTag) and outer.tag == CWT_TAG

    inner = outer.value
    if isinstance(inner, cbor2.CBORTag):
        assert inner.tag == COSE_SIGN1_TAG
        cose_sign1 = inner.value
    else:
        cose_sign1 = inner

    assert isinstance(cose_sign1, list) and len(cose_sign1) == 4
    protected_cbor, _unprotected, payload_cbor, signature = cose_sign1
    claims = cbor2.loads(payload_cbor)
    return protected_cbor, claims, payload_cbor, signature


# ── Ed25519 Keypair Generation ───────────────────────────────


class TestEd25519Keypair:
    def test_generate_returns_pem_and_base64(self):
        private_pem, public_b64 = generate_ed25519_keypair()

        assert private_pem.startswith("-----BEGIN PRIVATE KEY-----")
        assert private_pem.strip().endswith("-----END PRIVATE KEY-----")

        # Public key should be valid base64, 32 bytes raw
        public_bytes = base64.b64decode(public_b64)
        assert len(public_bytes) == 32

    def test_generate_unique_keys(self):
        """Each call produces a different keypair."""
        _, pub1 = generate_ed25519_keypair()
        _, pub2 = generate_ed25519_keypair()
        assert pub1 != pub2

    def test_load_or_generate_without_existing_key(self):
        """When no private key is set, generates a new keypair."""

        class FakeSettings:
            relay_private_key = None
            relay_key_id = "test_key"

        private_key, public_b64, key_id = load_or_generate_relay_keypair(FakeSettings())

        assert isinstance(private_key, ed25519.Ed25519PrivateKey)
        assert len(base64.b64decode(public_b64)) == 32
        assert key_id.startswith("relay_cp_")

    def test_load_or_generate_with_pem_key(self):
        """When PEM private key is provided, loads it correctly."""
        pem, expected_pub = generate_ed25519_keypair()

        class FakeSettings:
            relay_private_key = pem
            relay_key_id = "my_key_id"

        private_key, public_b64, key_id = load_or_generate_relay_keypair(FakeSettings())

        assert isinstance(private_key, ed25519.Ed25519PrivateKey)
        assert public_b64 == expected_pub
        assert key_id == "my_key_id"

    def test_load_or_generate_with_base64_encoded_pem(self):
        """When private key is base64-encoded PEM (from .env), decodes it."""
        pem, expected_pub = generate_ed25519_keypair()
        b64_pem = base64.b64encode(pem.encode("utf-8")).decode("utf-8")

        class FakeSettings:
            relay_private_key = b64_pem
            relay_key_id = "b64_key"

        private_key, public_b64, key_id = load_or_generate_relay_keypair(FakeSettings())

        assert public_b64 == expected_pub
        assert key_id == "b64_key"


# ── CWT Token Structure ─────────────────────────────────────


class TestCWTTokenStructure:
    """Verify the CBOR/COSE structure matches what y-sweet expects."""

    def test_outer_tag_is_cwt_61(self):
        private_key, _ = _make_keypair()
        token = create_relay_token_cwt(private_key, "k1", "doc-123", "write", 60)

        padding = 4 - len(token) % 4
        if padding != 4:
            token += "=" * padding
        raw = base64.urlsafe_b64decode(token)
        outer = cbor2.loads(raw)

        assert isinstance(outer, cbor2.CBORTag)
        assert outer.tag == CWT_TAG  # 61

    def test_inner_tag_is_cose_sign1_18(self):
        private_key, _ = _make_keypair()
        token = create_relay_token_cwt(private_key, "k1", "doc-123", "write", 60)

        padding = 4 - len(token) % 4
        if padding != 4:
            token += "=" * padding
        raw = base64.urlsafe_b64decode(token)
        outer = cbor2.loads(raw)
        inner = outer.value

        assert isinstance(inner, cbor2.CBORTag)
        assert inner.tag == COSE_SIGN1_TAG  # 18

    def test_protected_header_only_alg_eddsa(self):
        """Protected header must be {1: -8} (alg: EdDSA) — NO kid."""
        private_key, _ = _make_keypair()
        token = create_relay_token_cwt(private_key, "k1", "doc-123", "write", 60)

        protected_cbor, _, _, _ = _decode_cwt_raw(token)
        protected = cbor2.loads(protected_cbor)

        assert protected == {1: -8}, f"Expected {{1: -8}}, got {protected}"

    def test_no_kid_in_protected_header(self):
        """y-sweet rejects tokens with kid in protected header."""
        private_key, _ = _make_keypair()
        token = create_relay_token_cwt(private_key, "some_kid", "doc-123", "write", 60)

        protected_cbor, _, _, _ = _decode_cwt_raw(token)
        protected = cbor2.loads(protected_cbor)

        assert 4 not in protected, "kid (label 4) must NOT be in protected header"
        assert len(protected) == 1, f"Only alg expected, got {protected}"

    def test_unprotected_header_is_empty(self):
        private_key, _ = _make_keypair()
        token = create_relay_token_cwt(private_key, "k1", "doc-123", "write", 60)

        padding = 4 - len(token) % 4
        if padding != 4:
            token += "=" * padding
        raw = base64.urlsafe_b64decode(token)
        outer = cbor2.loads(raw)
        cose = outer.value.value if isinstance(outer.value, cbor2.CBORTag) else outer.value
        unprotected = cose[1]

        assert unprotected == {}

    def test_base64url_no_padding(self):
        """Token must be base64url without '=' padding."""
        private_key, _ = _make_keypair()
        token = create_relay_token_cwt(private_key, "k1", "doc-123", "write", 60)

        assert "=" not in token
        assert "+" not in token
        assert "/" not in token


# ── CWT Claims ───────────────────────────────────────────────


class TestCWTClaims:
    """Verify the claims payload matches y-sweet requirements."""

    def test_contains_only_iss_iat_scope(self):
        """y-sweet expects ONLY iss, iat, scope — no exp, no aud."""
        private_key, _ = _make_keypair()
        token = create_relay_token_cwt(private_key, "k1", "doc-123", "write", 60)

        _, claims, _, _ = _decode_cwt_raw(token)

        expected_keys = {CWT_CLAIM_ISS, CWT_CLAIM_IAT, CWT_CLAIM_SCOPE}
        assert set(claims.keys()) == expected_keys, f"Unexpected claims: {claims}"

    def test_no_exp_claim(self):
        """exp (4) must NOT be present — y-sweet rejects it."""
        private_key, _ = _make_keypair()
        token = create_relay_token_cwt(private_key, "k1", "doc-123", "write", 60)

        _, claims, _, _ = _decode_cwt_raw(token)
        assert 4 not in claims, f"exp claim found: {claims}"

    def test_no_aud_claim(self):
        """aud (3) must NOT be present — y-sweet rejects it."""
        private_key, _ = _make_keypair()
        # Even when audience is passed, it should NOT appear in claims
        token = create_relay_token_cwt(
            private_key, "k1", "doc-123", "write", 60, audience="wss://relay.test"
        )

        _, claims, _, _ = _decode_cwt_raw(token)
        assert 3 not in claims, f"aud claim found: {claims}"

    def test_iss_claim(self):
        private_key, _ = _make_keypair()
        token = create_relay_token_cwt(private_key, "k1", "doc-123", "write", 60)

        _, claims, _, _ = _decode_cwt_raw(token)
        assert claims[CWT_CLAIM_ISS] == "relay-control-plane"

    def test_custom_issuer(self):
        private_key, _ = _make_keypair()
        token = create_relay_token_cwt(
            private_key, "k1", "doc-123", "write", 60, issuer="my-issuer"
        )

        _, claims, _, _ = _decode_cwt_raw(token)
        assert claims[CWT_CLAIM_ISS] == "my-issuer"

    def test_iat_is_recent_unix_timestamp(self):
        private_key, _ = _make_keypair()
        before = int(time.time())
        token = create_relay_token_cwt(private_key, "k1", "doc-123", "write", 60)
        after = int(time.time())

        _, claims, _, _ = _decode_cwt_raw(token)
        iat = claims[CWT_CLAIM_IAT]

        assert before <= iat <= after

    def test_scope_write_mode(self):
        private_key, _ = _make_keypair()
        token = create_relay_token_cwt(private_key, "k1", "my-doc-id", "write", 60)

        _, claims, _, _ = _decode_cwt_raw(token)
        assert claims[CWT_CLAIM_SCOPE] == "doc:my-doc-id:rw"

    def test_scope_read_mode(self):
        private_key, _ = _make_keypair()
        token = create_relay_token_cwt(private_key, "k1", "my-doc-id", "read", 60)

        _, claims, _, _ = _decode_cwt_raw(token)
        assert claims[CWT_CLAIM_SCOPE] == "doc:my-doc-id:r"

    def test_scope_with_uuid_doc_id(self):
        private_key, _ = _make_keypair()
        doc_id = "c2676a37-ff3d-426a-b00b-18c7bc9dc7fc"
        token = create_relay_token_cwt(private_key, "k1", doc_id, "write", 60)

        _, claims, _, _ = _decode_cwt_raw(token)
        assert claims[CWT_CLAIM_SCOPE] == f"doc:{doc_id}:rw"


# ── CWT Signature Verification ──────────────────────────────


class TestCWTVerification:
    """Roundtrip: create → verify."""

    def test_roundtrip_verify(self):
        private_key, public_key = _make_keypair()
        token = create_relay_token_cwt(private_key, "k1", "doc-abc", "write", 60)

        claims = verify_relay_token_cwt(public_key, token)

        assert claims["iss"] == "relay-control-plane"
        assert claims["scope"] == "doc:doc-abc:rw"
        assert "iat" in claims

    def test_roundtrip_read_mode(self):
        private_key, public_key = _make_keypair()
        token = create_relay_token_cwt(private_key, "k1", "doc-xyz", "read", 60)

        claims = verify_relay_token_cwt(public_key, token)
        assert claims["scope"] == "doc:doc-xyz:r"

    def test_wrong_key_rejects(self):
        """Signature verified with a different key must fail."""
        private_key, _ = _make_keypair()
        _, other_public_key = _make_keypair()

        token = create_relay_token_cwt(private_key, "k1", "doc-1", "write", 60)

        with pytest.raises(ValueError, match="Signature verification failed"):
            verify_relay_token_cwt(other_public_key, token)

    def test_tampered_payload_rejects(self):
        """Modified payload must fail signature verification."""
        private_key, public_key = _make_keypair()
        token = create_relay_token_cwt(private_key, "k1", "doc-1", "write", 60)

        # Decode, tamper, re-encode
        padding = 4 - len(token) % 4
        if padding != 4:
            token += "=" * padding
        token_bytes = base64.urlsafe_b64decode(token)

        outer = cbor2.loads(token_bytes)
        inner = outer.value
        cose = inner.value if isinstance(inner, cbor2.CBORTag) else inner

        # Change claims
        tampered_claims = {CWT_CLAIM_ISS: "evil-issuer", CWT_CLAIM_IAT: 0, CWT_CLAIM_SCOPE: "doc:hacked:rw"}
        cose[2] = cbor2.dumps(tampered_claims)  # replace payload

        # Re-encode with original signature (now invalid)
        tampered_cose = cbor2.dumps(cbor2.CBORTag(COSE_SIGN1_TAG, cose))
        tampered_cwt = cbor2.dumps(cbor2.CBORTag(CWT_TAG, cbor2.loads(tampered_cose)))
        tampered_b64 = base64.urlsafe_b64encode(tampered_cwt).decode().rstrip("=")

        with pytest.raises(ValueError, match="Signature verification failed"):
            verify_relay_token_cwt(public_key, tampered_b64)

    def test_invalid_base64_rejects(self):
        _, public_key = _make_keypair()

        with pytest.raises(Exception):
            verify_relay_token_cwt(public_key, "not-valid-cwt-token!!!")

    def test_non_cwt_cbor_rejects(self):
        """Valid CBOR but not a CWT tag should fail."""
        _, public_key = _make_keypair()

        # Encode a plain CBOR map (no CWT tag)
        fake = base64.urlsafe_b64encode(cbor2.dumps({"hello": "world"})).decode().rstrip("=")

        with pytest.raises(ValueError, match="Expected CWT tag 61"):
            verify_relay_token_cwt(public_key, fake)
