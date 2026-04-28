"""
Aeternum Bridge Engine — Phase 39: Identity-Based Messaging Service.

Append-only message persistence, connection-locked send, and conversation retrieval.
No message deletion or editing is permitted in this phase.
"""
import json
import logging
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

MESSAGES_FILE = Path(__file__).resolve().parents[1] / "data" / "messages.json"
_EMPTY_MESSAGES_STORE: dict[str, Any] = {"messages": []}
_MESSAGES_LOCK = threading.RLock()

_ACCEPTED_CONNECTION_STATUSES = {"accepted", "connection_completed"}


# ---------------------------------------------------------------------------
# Persistence helpers
# ---------------------------------------------------------------------------

def _ensure_file() -> None:
    MESSAGES_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not MESSAGES_FILE.exists():
        with MESSAGES_FILE.open("w", encoding="utf-8") as f:
            json.dump(_EMPTY_MESSAGES_STORE, f, indent=2)
        logger.info("Created new messages file at %s", MESSAGES_FILE)


def _load_store() -> dict[str, Any]:
    _ensure_file()
    try:
        with MESSAGES_FILE.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict) or not isinstance(data.get("messages"), list):
            logger.warning("Messages file invalid shape; resetting.")
            return dict(_EMPTY_MESSAGES_STORE)
        return data
    except (json.JSONDecodeError, OSError):
        logger.warning("Messages file unreadable; returning empty store.")
        return dict(_EMPTY_MESSAGES_STORE)


def _save_message(message: dict[str, Any]) -> None:
    """Append a single message to the messages file (append-only)."""
    with _MESSAGES_LOCK:
        _ensure_file()
        store = _load_store()
        messages = list(store.get("messages") or [])
        messages.append(message)
        temp = MESSAGES_FILE.with_suffix(".json.tmp")
        try:
            with temp.open("w", encoding="utf-8") as f:
                json.dump({"messages": messages}, f, indent=2, ensure_ascii=False)
            temp.replace(MESSAGES_FILE)
        except Exception:
            logger.exception("Failed to write messages file.")
            if temp.exists():
                temp.unlink(missing_ok=True)
            raise


def _update_message_status(message_id: str, status: str) -> bool:
    """Update status of a single message in-place. Returns True on success."""
    with _MESSAGES_LOCK:
        _ensure_file()
        store = _load_store()
        messages = list(store.get("messages") or [])
        found = False
        for msg in messages:
            if msg.get("message_id") == message_id:
                msg["status"] = status
                found = True
                break
        if not found:
            return False
        temp = MESSAGES_FILE.with_suffix(".json.tmp")
        try:
            with temp.open("w", encoding="utf-8") as f:
                json.dump({"messages": messages}, f, indent=2, ensure_ascii=False)
            temp.replace(MESSAGES_FILE)
        except Exception:
            logger.exception("Failed to update message status.")
            if temp.exists():
                temp.unlink(missing_ok=True)
            raise
        return True


# ---------------------------------------------------------------------------
# Connection validation helper
# ---------------------------------------------------------------------------

def _users_are_connected(user_a: str, user_b: str, connection_requests: list[dict[str, Any]]) -> tuple[bool, Optional[str]]:
    """Return (is_connected, connection_id) if both users share an accepted connection."""
    for req in connection_requests:
        status = str(req.get("status") or "")
        if status not in _ACCEPTED_CONNECTION_STATUSES:
            continue
        req_id = str(req.get("requester_user_id") or "")
        tgt_id = str(req.get("target_user_id") or "")
        if (req_id == user_a and tgt_id == user_b) or (req_id == user_b and tgt_id == user_a):
            return True, str(req.get("request_id") or "")
    return False, None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def send_message(
    sender_id: str,
    receiver_id: str,
    message_text: str,
    *,
    users: list[dict[str, Any]],
    connection_requests: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    Send a message from sender to receiver.

    Validates:
    - Both users exist in registry
    - Users share an accepted connection
    - message_text is non-empty (max 2000 chars)

    Returns the created message record.
    Raises ValueError on validation failures.
    """
    sender_id = str(sender_id or "").strip()
    receiver_id = str(receiver_id or "").strip()
    message_text = str(message_text or "").strip()

    if not sender_id:
        raise ValueError("sender_id is required")
    if not receiver_id:
        raise ValueError("receiver_id is required")
    if sender_id == receiver_id:
        raise ValueError("sender and receiver must be different users")
    if not message_text:
        raise ValueError("message_text must not be empty")
    if len(message_text) > 2000:
        raise ValueError("message_text exceeds 2000 character limit")

    user_ids = {str(u.get("user_id") or "").strip() for u in users if isinstance(u, dict)}
    if sender_id not in user_ids:
        raise ValueError(f"Sender '{sender_id}' not found in registry")
    if receiver_id not in user_ids:
        raise ValueError(f"Receiver '{receiver_id}' not found in registry")

    connected, connection_id = _users_are_connected(sender_id, receiver_id, connection_requests)
    if not connected:
        raise ValueError(
            "Messaging is only permitted between connected users. "
            "Both users must have an accepted connection before messaging."
        )

    message_id = "msg_" + uuid.uuid4().hex[:16]
    timestamp = datetime.now(timezone.utc).isoformat()

    message: dict[str, Any] = {
        "message_id": message_id,
        "sender_id": sender_id,
        "receiver_id": receiver_id,
        "connection_id": connection_id or "",
        "message_text": message_text,
        "timestamp": timestamp,
        "status": "sent",
    }
    _save_message(message)
    logger.info("Message sent message_id=%s sender=%s receiver=%s", message_id, sender_id, receiver_id)
    return message


def get_conversation(user_a: str, user_b: str) -> list[dict[str, Any]]:
    """
    Return all messages exchanged between user_a and user_b, sorted ASC by timestamp.
    """
    user_a = str(user_a or "").strip()
    user_b = str(user_b or "").strip()
    if not user_a or not user_b:
        return []

    store = _load_store()
    messages = store.get("messages") or []

    thread = [
        msg for msg in messages
        if isinstance(msg, dict) and (
            (msg.get("sender_id") == user_a and msg.get("receiver_id") == user_b)
            or (msg.get("sender_id") == user_b and msg.get("receiver_id") == user_a)
        )
    ]
    thread.sort(key=lambda m: str(m.get("timestamp") or ""))
    return thread


def mark_message_read(message_id: str) -> bool:
    """Mark a single message as read. Returns True if found and updated."""
    message_id = str(message_id or "").strip()
    if not message_id:
        return False
    return _update_message_status(message_id, "read")


def get_total_messages_count() -> int:
    """Return total number of messages stored."""
    store = _load_store()
    return len(store.get("messages") or [])


def get_recent_messages(limit: int = 10) -> list[dict[str, Any]]:
    """Return the most recent messages (newest first), without message_text content."""
    store = _load_store()
    messages = list(store.get("messages") or [])
    messages.sort(key=lambda m: str(m.get("timestamp") or ""), reverse=True)
    result = []
    for msg in messages[:limit]:
        if isinstance(msg, dict):
            result.append({
                "message_id": msg.get("message_id"),
                "sender_id": msg.get("sender_id"),
                "receiver_id": msg.get("receiver_id"),
                "connection_id": msg.get("connection_id"),
                "timestamp": msg.get("timestamp"),
                "status": msg.get("status"),
            })
    return result


def get_connected_users(
    user_id: str,
    users: list[dict[str, Any]],
    connection_requests: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """
    Return a list of user profiles that are connected (accepted) to the given user_id.
    Each entry: {user_id, full_name, family_name, connection_id}
    """
    user_id = str(user_id or "").strip()
    if not user_id:
        return []

    users_by_id = {str(u.get("user_id") or "").strip(): u for u in users if isinstance(u, dict)}
    connected = []

    for req in connection_requests:
        status = str(req.get("status") or "")
        if status not in _ACCEPTED_CONNECTION_STATUSES:
            continue
        req_user = str(req.get("requester_user_id") or "")
        tgt_user = str(req.get("target_user_id") or "")
        other_id = None
        if req_user == user_id:
            other_id = tgt_user
        elif tgt_user == user_id:
            other_id = req_user
        if other_id and other_id in users_by_id:
            other = users_by_id[other_id]
            # Phase 41: Ancestor/deceased records cannot participate in messaging.
            if other.get("ancestor_record") or other.get("is_deceased"):
                continue
            if str(other.get("living_status", "living")).lower() == "deceased":
                continue
            connected.append({
                "user_id": other_id,
                "full_name": str(other.get("full_name") or ""),
                "family_name": str(other.get("family_name") or ""),
                "connection_id": str(req.get("request_id") or ""),
            })
    return connected
