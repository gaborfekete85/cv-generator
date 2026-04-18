"""Clerk-based authentication for FastAPI.

We verify Clerk session JWTs against Clerk's public JWKS endpoint. This needs
only the publishable key (which encodes the Frontend API URL) — the secret
key is NOT required for session verification. Keep it in env anyway so we
can call Clerk's backend API later (listing users, organisations, etc.)
without another config step.

If `CLERK_PUBLISHABLE_KEY` is not set, authentication is DISABLED and every
endpoint is reachable anonymously. That keeps local dev / offline testing
working out of the box without a Clerk account.
"""

from __future__ import annotations

import base64
import logging
import os
from functools import lru_cache
from typing import Optional

import jwt
from fastapi import Header, HTTPException


log = logging.getLogger(__name__)


# --- Config resolution -----------------------------------------------------

def _derive_frontend_api(pk: str) -> str:
    """Extract the Clerk Frontend API URL from a publishable key.

    Clerk publishable keys are of the form ``pk_<env>_<base64(api-url$)>``.
    Decoding the third segment yields e.g. ``allowed-piglet-40.clerk.accounts.dev$``.
    """
    parts = (pk or "").split("_", 2)
    if len(parts) < 3 or not parts[2]:
        return ""
    encoded = parts[2]
    try:
        decoded = base64.b64decode(encoded + "=" * (-len(encoded) % 4)).decode()
    except Exception as e:  # noqa: BLE001
        log.warning("Failed to decode Clerk publishable key: %s", e)
        return ""
    return "https://" + decoded.rstrip("$").rstrip()


CLERK_PUBLISHABLE_KEY = os.environ.get("CLERK_PUBLISHABLE_KEY", "").strip()
CLERK_SECRET_KEY = os.environ.get("CLERK_SECRET_KEY", "").strip()  # reserved
CLERK_FRONTEND_API = (
    os.environ.get("CLERK_FRONTEND_API", "").strip()
    or _derive_frontend_api(CLERK_PUBLISHABLE_KEY)
)


def auth_enabled() -> bool:
    """True iff Clerk is fully configured."""
    return bool(CLERK_PUBLISHABLE_KEY) and bool(CLERK_FRONTEND_API)


# --- JWT verification ------------------------------------------------------

@lru_cache(maxsize=1)
def _jwks_client() -> "jwt.PyJWKClient":
    # Cached across requests — PyJWKClient has its own internal key cache too.
    return jwt.PyJWKClient(f"{CLERK_FRONTEND_API}/.well-known/jwks.json")


def require_auth(authorization: Optional[str] = Header(None)) -> dict:
    """FastAPI dependency — verifies a Clerk session JWT from the
    ``Authorization: Bearer <token>`` header.

    Returns the decoded JWT claims (``sub`` is the Clerk user id).

    Raises 401 on missing / invalid / expired tokens. Returns a placeholder
    anonymous user when auth is disabled (no Clerk config).
    """
    if not auth_enabled():
        return {"sub": "anonymous", "auth_disabled": True}

    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(
            status_code=401,
            detail="Missing or malformed Authorization header",
        )

    token = authorization.split(" ", 1)[1].strip()
    if not token:
        raise HTTPException(status_code=401, detail="Empty bearer token")

    try:
        signing_key = _jwks_client().get_signing_key_from_jwt(token)
        payload = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            # Clerk session tokens don't include an `aud` — skip audience check.
            options={"verify_aud": False},
            issuer=CLERK_FRONTEND_API,
        )
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Session expired")
    except jwt.InvalidTokenError as e:
        raise HTTPException(status_code=401, detail=f"Invalid token: {e}")

    return payload
