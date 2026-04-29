import os
from typing import Optional

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
