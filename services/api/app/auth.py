"""Username/password auth for Cortex.

Replaces the single shared CORTEX_API_KEY (see security.require_api_key,
now only kept around for anything that still explicitly wants it) with real
per-user accounts: bcrypt-hashed passwords in the `users` table (models.User)
and short-lived JWTs handed out by POST /api/v1/auth/login.

Trust model, end to end:
- The Next.js frontend never lets the browser see the JWT. Its own
  POST /api/auth/login route (services/web/app/api/auth/login/route.ts)
  calls this API's /api/v1/auth/login, then stores the returned token in an
  httpOnly cookie set by Next.js itself. Every services/web/app/api/*
  route handler reads that cookie server-side and forwards it here as
  `Authorization: Bearer <token>` (see services/web/lib/serverAuth.ts).
- This module only ever has to verify a bearer token it already signed --
  it doesn't need to know anything about cookies.
"""
import os
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from . import models
from .db import get_db

# HS256 shared-secret signing. Generate a real one with e.g.
# `python -c "import secrets; print(secrets.token_urlsafe(48))"` and set it
# via CORTEX_JWT_SECRET -- there is intentionally no hardcoded fallback
# in production images (see infra/.env.example); the dev-only default below
# only kicks in if the env var is entirely unset, and is loud about it.
JWT_SECRET = os.environ.get("CORTEX_JWT_SECRET")
if not JWT_SECRET:
    JWT_SECRET = "dev-insecure-secret-change-me"
    import logging

    logging.getLogger(__name__).warning(
        "CORTEX_JWT_SECRET is not set -- using an insecure default. "
        "Set CORTEX_JWT_SECRET before running Cortex anywhere but a local sandbox."
    )

JWT_ALGORITHM = "HS256"
# How long an issued token stays valid. Short-ish on purpose since there's
# no refresh-token/revocation list yet (see the module docstring in
# routers/auth.py for the tradeoff) -- a logged-in session just re-hits
# /login (frontend does this transparently) rather than silently running
# forever on a stolen token.
JWT_TTL_MINUTES = int(os.environ.get("CORTEX_JWT_TTL_MINUTES", "480"))


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        # Malformed/legacy hash -- treat as "doesn't match" rather than 500ing.
        return False


def create_access_token(user: models.User) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user.id),
        "username": user.username,
        "role": user.role,
        "iat": now,
        "exp": now + timedelta(minutes=JWT_TTL_MINUTES),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def _decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "session expired, please log in again")
    except jwt.InvalidTokenError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid session")


def get_current_user(
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> models.User:
    """Every business router (nodes, dashboard, topology, ...) depends on
    this -- wired in at app.include_router(..., dependencies=[Depends(get_current_user)])
    in main.py rather than repeated on each route -- so nothing behind
    /api/v1/ is reachable without a valid, currently-active account."""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "not authenticated")
    token = authorization.split(" ", 1)[1].strip()
    claims = _decode_token(token)

    user = db.get(models.User, claims.get("sub"))
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "account not found or disabled")
    return user


def require_admin(user: models.User = Depends(get_current_user)) -> models.User:
    """Gate for account management and infra-mutating endpoints (creating/
    deleting nodes, running the knowledge-base ingest, managing other
    users) -- the operations a Grafana-style "Admin" role, not just any
    logged-in "Viewer", should be able to do."""
    if user.role != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "admin privileges required")
    return user
