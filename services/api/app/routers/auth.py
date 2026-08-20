"""Login and account management.

Only POST /login is reachable without a token (wired up unprotected in
main.py, unlike every other router which gets Depends(get_current_user)
applied at include_router time). Everything else here needs a valid
session, and the /users/* endpoints additionally need an admin role
(see auth.require_admin).

There's deliberately no self-service signup endpoint: new accounts are
always admin-created (POST /users), the same "admin invites people in"
model Grafana defaults to, rather than an open registration + approval
queue -- fewer moving parts, and nothing public-facing to rate-limit or
spam-protect.
"""
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from .. import crud, schemas
from ..auth import create_access_token, get_current_user, hash_password, require_admin, verify_password
from ..db import get_db
from ..models import User

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


@router.post("/login", response_model=schemas.LoginResponse)
def login(payload: schemas.LoginRequest, db: Session = Depends(get_db)):
    user = crud.get_user_by_username(db, payload.username)
    # Same generic message whether the username doesn't exist or the
    # password is wrong -- doesn't tell an attacker which one to fix.
    invalid = HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid username or password")
    if user is None or not verify_password(payload.password, user.password_hash):
        raise invalid
    if not user.is_active:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "this account has been disabled")

    token = create_access_token(user)
    return schemas.LoginResponse(access_token=token, user=user)


@router.get("/me", response_model=schemas.UserOut)
def me(user: User = Depends(get_current_user)):
    return user


@router.post("/change-password", response_model=schemas.UserOut)
def change_password(
    payload: schemas.ChangePasswordRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Self-service password change -- the only way a non-admin ever sets
    their own password, including clearing must_change_password after an
    admin-issued temporary one."""
    if not verify_password(payload.current_password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "current password is incorrect")
    user.password_hash = hash_password(payload.new_password)
    user.must_change_password = False
    db.commit()
    db.refresh(user)
    return user


@router.get("/users", response_model=list[schemas.UserOut], dependencies=[Depends(require_admin)])
def list_users(db: Session = Depends(get_db)):
    return crud.list_users(db)


@router.post("/users", response_model=schemas.UserOut, status_code=status.HTTP_201_CREATED,
             dependencies=[Depends(require_admin)])
def create_user(payload: schemas.UserCreate, db: Session = Depends(get_db)):
    """Admin creates an account with a temporary password. must_change_password
    is always forced True here (see models.User's docstring) -- the admin
    hands the temporary password to the person out of band, and Cortex
    requires them to set their own on first login."""
    try:
        return crud.create_user(
            db,
            username=payload.username,
            password_hash=hash_password(payload.password),
            role=payload.role.value,
            must_change_password=True,
        )
    except crud.DuplicateUserError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc


@router.patch("/users/{user_id}", response_model=schemas.UserOut, dependencies=[Depends(require_admin)])
def update_user(
    user_id: uuid.UUID,
    payload: schemas.UserUpdate,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    target = crud.get_user(db, user_id)
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "user not found")

    if payload.role is not None:
        if target.id == current_user.id and payload.role.value != "admin":
            # An admin demoting themselves is how you end up with zero
            # admins able to fix it -- block it, same reasoning as the
            # is_active self-lockout guard below.
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "you can't remove your own admin role")
        target.role = payload.role.value

    if payload.is_active is not None:
        if target.id == current_user.id and not payload.is_active:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "you can't deactivate your own account")
        target.is_active = payload.is_active

    if payload.new_password is not None:
        target.password_hash = hash_password(payload.new_password)
        target.must_change_password = True

    db.commit()
    db.refresh(target)
    return target
