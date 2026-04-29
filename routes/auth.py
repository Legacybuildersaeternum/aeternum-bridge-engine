"""Phase 48 — Auth API routes (register, login, logout, me)."""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from services import auth as auth_service

router = APIRouter(tags=["Auth"], prefix="/auth")


class RegisterRequest(BaseModel):
    email: str
    password: str


class LoginRequest(BaseModel):
    email: str
    password: str


class LogoutRequest(BaseModel):
    token: str


@router.post("/register", status_code=201)
def auth_register(payload: RegisterRequest) -> dict[str, Any]:
    """Create a new user account and immediately return a live session token."""
    try:
        auth_service.create_account(payload.email, payload.password)
        result = auth_service.login(payload.email, payload.password)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return result


@router.post("/login")
def auth_login(payload: LoginRequest) -> dict[str, Any]:
    """Validate credentials and return a session token."""
    try:
        result = auth_service.login(payload.email, payload.password)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    return result


@router.post("/logout")
def auth_logout(
    payload: Optional[LogoutRequest] = None,
    x_session_token: Optional[str] = Header(default=None, alias="X-Session-Token"),
) -> dict[str, str]:
    """Invalidate the current session token."""
    token = (payload.token if payload else None) or x_session_token or ""
    auth_service.logout(token)
    return {"message": "Logged out."}


@router.get("/me")
def auth_me(
    x_session_token: Optional[str] = Header(default=None, alias="X-Session-Token"),
) -> dict[str, Any]:
    """Return current account info for a valid session token."""
    session = auth_service.get_account_by_token(x_session_token or "")
    if not session:
        raise HTTPException(status_code=401, detail="Not authenticated.")
    return {
        "account_id": session["account_id"],
        "email": session["email"],
    }
