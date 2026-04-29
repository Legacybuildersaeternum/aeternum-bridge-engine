"""Phase 48 — Account, authentication, and session management.

Storage: data/users.json and data/sessions.json (local JSON for MVP).
Password: hashlib PBKDF2-HMAC-SHA256 with per-account salt. No raw passwords stored.
Sessions: random 64-hex-char token, stored server-side with expiry metadata.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import secrets
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

_DATA_DIR = Path(__file__).resolve().parents[1] / "data"
_USERS_FILE = _DATA_DIR / "users.json"
_SESSIONS_FILE = _DATA_DIR / "sessions.json"
_USERS_BACKUP_FILE = _DATA_DIR / "users.backup.json"
_SESSIONS_BACKUP_FILE = _DATA_DIR / "sessions.backup.json"
_USERS_LOCK = threading.RLock()
_SESSIONS_LOCK = threading.RLock()

# Sessions expire after 7 days of inactivity for MVP.
_SESSION_TTL_DAYS = 7
_PBKDF2_ITERATIONS = 260_000  # NIST 2023 minimum


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _ensure_data_dir() -> None:
    _DATA_DIR.mkdir(parents=True, exist_ok=True)


def _atomic_json_write(path: Path, payload: list[dict[str, Any]]) -> None:
    """Write JSON atomically to reduce partial-write corruption risk."""
    _ensure_data_dir()
    tmp_path = path.with_suffix(f"{path.suffix}.tmp")
    with tmp_path.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False)
        fh.flush()
        os.fsync(fh.fileno())
    tmp_path.replace(path)


def _load_json_list(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
        return data if isinstance(data, list) else []


def _load_users() -> list[dict[str, Any]]:
    _ensure_data_dir()
    try:
        users = _load_json_list(_USERS_FILE)
        if users:
            return users
        backup_users = _load_json_list(_USERS_BACKUP_FILE)
        if backup_users and not _USERS_FILE.exists():
            # Recover missing primary users file from backup snapshot.
            _atomic_json_write(_USERS_FILE, backup_users)
            return backup_users
        return backup_users
    except Exception:
        logger.exception("Failed to load users.json, attempting backup recovery")
        try:
            backup_users = _load_json_list(_USERS_BACKUP_FILE)
            if backup_users:
                _atomic_json_write(_USERS_FILE, backup_users)
            return backup_users
        except Exception:
            logger.exception("Failed to recover users from backup")
            return []


def _save_users(users: list[dict[str, Any]]) -> None:
    _atomic_json_write(_USERS_FILE, users)
    _atomic_json_write(_USERS_BACKUP_FILE, users)


def _load_sessions() -> list[dict[str, Any]]:
    _ensure_data_dir()
    try:
        sessions = _load_json_list(_SESSIONS_FILE)
        if sessions:
            return sessions
        backup_sessions = _load_json_list(_SESSIONS_BACKUP_FILE)
        if backup_sessions and not _SESSIONS_FILE.exists():
            _atomic_json_write(_SESSIONS_FILE, backup_sessions)
            return backup_sessions
        return backup_sessions
    except Exception:
        logger.exception("Failed to load sessions.json, attempting backup recovery")
        try:
            backup_sessions = _load_json_list(_SESSIONS_BACKUP_FILE)
            if backup_sessions:
                _atomic_json_write(_SESSIONS_FILE, backup_sessions)
            return backup_sessions
        except Exception:
            logger.exception("Failed to recover sessions from backup")
            return []


def _save_sessions(sessions: list[dict[str, Any]]) -> None:
    _atomic_json_write(_SESSIONS_FILE, sessions)
    _atomic_json_write(_SESSIONS_BACKUP_FILE, sessions)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _expiry_iso() -> str:
    return (datetime.now(timezone.utc) + timedelta(days=_SESSION_TTL_DAYS)).isoformat()


def _is_expired(session: dict[str, Any]) -> bool:
    exp = session.get("expires_at", "")
    if not exp:
        return False
    try:
        return datetime.fromisoformat(exp) < datetime.now(timezone.utc)
    except Exception:
        return True


# ---------------------------------------------------------------------------
# Password hashing
# ---------------------------------------------------------------------------

def _hash_password(password: str, salt: str) -> str:
    """Return PBKDF2-HMAC-SHA256 hex digest of password + salt."""
    return hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        _PBKDF2_ITERATIONS,
    ).hex()


def _verify_password(password: str, salt: str, stored_hash: str) -> bool:
    candidate = _hash_password(password, salt)
    return secrets.compare_digest(candidate, stored_hash)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def create_account(email: str, password: str) -> dict[str, Any]:
    """Create a new account.  Raises ValueError on bad input or duplicate email."""
    email = email.strip().lower()
    password = password.strip()

    if not email or "@" not in email:
        raise ValueError("A valid email address is required.")
    if len(password) < 8:
        raise ValueError("Password must be at least 8 characters.")

    with _USERS_LOCK:
        users = _load_users()
        if any(u.get("email") == email for u in users):
            raise ValueError("An account with this email already exists.")

        salt = secrets.token_hex(32)
        account_id = "acct_" + secrets.token_hex(12)
        now = _now_iso()
        record: dict[str, Any] = {
            "account_id": account_id,
            "email": email,
            "password_hash": _hash_password(password, salt),
            "salt": salt,
            "created_at": now,
            "last_login_at": None,
        }
        users.append(record)
        _save_users(users)

    return {"account_id": account_id, "email": email, "created_at": now}


def login(email: str, password: str) -> dict[str, Any]:
    """Validate credentials and return a new session token.  Raises ValueError on failure."""
    email = email.strip().lower()
    password = password.strip()

    with _USERS_LOCK:
        users = _load_users()
        account = next((u for u in users if u.get("email") == email), None)
        if account is None or not _verify_password(password, account.get("salt", ""), account.get("password_hash", "")):
            raise ValueError("Invalid email or password.")

        # Update last login
        account["last_login_at"] = _now_iso()
        _save_users(users)

    token = secrets.token_hex(32)
    now = _now_iso()
    session: dict[str, Any] = {
        "token": token,
        "account_id": account["account_id"],
        "email": account["email"],
        "created_at": now,
        "last_active_at": now,
        "expires_at": _expiry_iso(),
    }
    with _SESSIONS_LOCK:
        sessions = _load_sessions()
        # Purge expired sessions to keep file tidy
        sessions = [s for s in sessions if not _is_expired(s)]
        sessions.append(session)
        _save_sessions(sessions)

    return {
        "token": token,
        "account_id": account["account_id"],
        "email": account["email"],
    }


def logout(token: str) -> None:
    """Invalidate a session token."""
    token = (token or "").strip()
    if not token:
        return
    with _SESSIONS_LOCK:
        sessions = _load_sessions()
        sessions = [s for s in sessions if s.get("token") != token]
        _save_sessions(sessions)


def get_account_by_token(token: str) -> Optional[dict[str, Any]]:
    """Return the session record for a valid, non-expired token, or None."""
    token = (token or "").strip()
    if not token:
        return None
    with _SESSIONS_LOCK:
        sessions = _load_sessions()
        session = next((s for s in sessions if s.get("token") == token), None)
        if session is None or _is_expired(session):
            return None
        # Refresh last_active_at
        session["last_active_at"] = _now_iso()
        session["expires_at"] = _expiry_iso()
        _save_sessions(sessions)
    return session


def get_account_by_id(account_id: str) -> Optional[dict[str, Any]]:
    """Return account record (without password fields) by account_id."""
    with _USERS_LOCK:
        users = _load_users()
        account = next((u for u in users if u.get("account_id") == account_id), None)
        if account is None:
            return None
        return {"account_id": account["account_id"], "email": account["email"], "created_at": account["created_at"], "last_login_at": account.get("last_login_at")}
