"""Phase 39 — Messages API routes (connection-locked, identity-bound)."""
from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel
from typing import Any, Optional
from services import registry, messages as message_service
from services.connections import check_connection_safety

router = APIRouter(tags=["Messages"], prefix="/messages")


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------

class SendMessageRequest(BaseModel):
    sender_id: str
    receiver_id: str
    message_text: str


class MarkReadRequest(BaseModel):
    message_id: str


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/send")
def send_message(
    payload: SendMessageRequest,
    x_session_id: Optional[str] = Header(default=None),
) -> dict[str, Any]:
    """Send a message between two connected users."""
    users = [u.model_dump(mode="json") for u in registry.get_registrations()]
    connection_requests = registry._load_connection_requests()
    sender = next((u for u in users if str(u.get("user_id") or "") == payload.sender_id), None)
    receiver = next((u for u in users if str(u.get("user_id") or "") == payload.receiver_id), None)
    if not sender or not receiver:
        raise HTTPException(status_code=400, detail="Sender or receiver not found.")

    safety = check_connection_safety(sender, receiver)
    if not bool(safety.get("allowed", True)):
        raise HTTPException(status_code=403, detail=str(safety.get("warning") or "Message not allowed."))

    try:
        msg = message_service.send_message(
            sender_id=payload.sender_id,
            receiver_id=payload.receiver_id,
            message_text=payload.message_text,
            users=users,
            connection_requests=connection_requests,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # Log activity event
    registry.write_activity_event(
        event_type="message_sent",
        message=f"Message sent from {payload.sender_id} to {payload.receiver_id}.",
        user_id=payload.sender_id,
        session_id=x_session_id,
        extra={
            "target_user_id": payload.receiver_id,
            "connection_id": msg.get("connection_id", ""),
        },
    )

    registry.refresh_user_trust(payload.sender_id, reason="message_sent", session_id=x_session_id)

    return {
        "success": True,
        "message_id": msg["message_id"],
        "warning": safety.get("warning"),
    }


@router.get("/conversation")
def get_conversation(
    user_a: str = Query(...),
    user_b: str = Query(...),
) -> list[dict[str, Any]]:
    """Return all messages exchanged between user_a and user_b, sorted oldest-first."""
    return message_service.get_conversation(user_a=user_a, user_b=user_b)


@router.post("/read")
def mark_read(
    payload: MarkReadRequest,
    x_session_id: Optional[str] = Header(default=None),
) -> dict[str, Any]:
    """Mark a message as read."""
    updated = message_service.mark_message_read(payload.message_id)
    if not updated:
        raise HTTPException(status_code=404, detail="Message not found")

    # Log activity
    registry.write_activity_event(
        event_type="message_read",
        message=f"Message {payload.message_id} marked as read.",
        user_id=None,
        session_id=x_session_id,
        extra={"message_id": payload.message_id},
    )

    return {"success": True, "message_id": payload.message_id, "status": "read"}


@router.get("/connected-users")
def get_connected_users(
    user_id: str = Query(...),
) -> list[dict[str, Any]]:
    """Return list of users connected (accepted) to the given user_id, for messaging UI."""
    users = [u.model_dump(mode="json") for u in registry.get_registrations()]
    connection_requests = registry._load_connection_requests()
    return message_service.get_connected_users(
        user_id=user_id,
        users=users,
        connection_requests=connection_requests,
    )


@router.get("/recent")
def get_recent_messages(
    limit: int = Query(default=10, ge=1, le=100),
) -> list[dict[str, Any]]:
    """Return recent message metadata (no content) for admin visibility."""
    return message_service.get_recent_messages(limit=limit)
