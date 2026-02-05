from __future__ import annotations

from fastapi import APIRouter, Request

from app.schemas import key as key_schema

router = APIRouter(prefix="/keys", tags=["keys"])


@router.get("/public", response_model=key_schema.PublicKeyResponse)
def get_public_key(request: Request):
    """
    Get the relay server public key for JWT verification.

    This endpoint returns the public key that relay servers should use
    to verify JWT tokens issued by this control plane.

    Returns:
        Public key in base64 format and key ID
    """
    # Keys are stored in app state during startup
    public_key = request.app.state.relay_public_key
    key_id = request.app.state.relay_key_id

    return key_schema.PublicKeyResponse(
        key_id=key_id,
        public_key=public_key,
        algorithm="EdDSA",
    )
