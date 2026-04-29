import os
from typing import Any, Optional

from fastapi import Header, HTTPException

_FALLBACK_ADMIN_PASSCODE = "local-dev-admin-passcode"


def get_expected_admin_passcode() -> str:
    value = str(os.getenv("ADMIN_PASSCODE") or "").strip()
    return value or _FALLBACK_ADMIN_PASSCODE


def is_admin_passcode(passcode: Optional[str]) -> bool:
    normalized = str(passcode or "").strip()
    if not normalized:
        return False
    return normalized == get_expected_admin_passcode()


def require_admin_passcode(
    x_admin_passcode: Optional[str] = Header(default=None, alias="X-Admin-Passcode"),
) -> None:
    if not is_admin_passcode(x_admin_passcode):
        raise HTTPException(status_code=403, detail="Admin authorization required.")


# ---------------------------------------------------------------------------
# Phase 48: Session-based auth helpers
# ---------------------------------------------------------------------------

def get_session_account(
    x_session_token: Optional[str] = Header(default=None, alias="X-Session-Token"),
) -> Optional[dict[str, Any]]:
    """Return the authenticated account dict or None (does NOT raise)."""
    from services.auth import get_account_by_token  # local import to avoid circular
    return get_account_by_token(x_session_token or "")


def require_session_or_admin(
    x_session_token: Optional[str] = Header(default=None, alias="X-Session-Token"),
    x_admin_passcode: Optional[str] = Header(default=None, alias="X-Admin-Passcode"),
) -> Optional[dict[str, Any]]:
    """Return the account dict if session is valid, or None if admin passcode is valid.
    Raises 401 if neither is present."""
    if is_admin_passcode(x_admin_passcode):
        return None  # admin mode — no owner filtering
    from services.auth import get_account_by_token
    account = get_account_by_token(x_session_token or "")
    if account is None:
        raise HTTPException(status_code=401, detail="Authentication required. Please log in.")
    return account


def optional_session_account(
    x_session_token: Optional[str] = Header(default=None, alias="X-Session-Token"),
    x_admin_passcode: Optional[str] = Header(default=None, alias="X-Admin-Passcode"),
) -> Optional[dict[str, Any]]:
    """Return account dict (logged-in user), None with admin passcode (admin mode), or raise 401.
    Does NOT raise — returns None for both unauthenticated and admin-mode callers."""
    if is_admin_passcode(x_admin_passcode):
        return None  # admin bypass
    from services.auth import get_account_by_token
    return get_account_by_token(x_session_token or "")
