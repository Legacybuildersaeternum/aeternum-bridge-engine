from typing import Optional

from fastapi import APIRouter, Header, HTTPException
from models.user import UserRegistration, RegistrationResponse
from services import registry

router = APIRouter(tags=["Users"])

_SESSION_EVENT_TYPES = {
    "session_started",
    "session_active",
    "registration_started",
    "registration_submitted",
    "family_group_created",
    "family_tree_viewed",
    "export_download_clicked",
    "full_backup_downloaded",
    "admin_dashboard_viewed",
    "activity_log_viewed",
}


@router.post("/session/activity")
def track_session_activity(
    payload: dict[str, Optional[str]],
    x_session_id: Optional[str] = Header(default=None),
    x_user_id: Optional[str] = Header(default=None),
) -> dict[str, str]:
    """Record session-level client activity events from the UI."""
    event_type = str(payload.get("event_type") or "").strip()
    if event_type not in _SESSION_EVENT_TYPES:
        allowed = ", ".join(sorted(_SESSION_EVENT_TYPES))
        raise HTTPException(
            status_code=400,
            detail=(
                f"Unsupported session activity event '{event_type}'. "
                f"Allowed event types: {allowed}"
            ),
        )

    message = str(payload.get("message") or "").strip()
    if not message:
        default_messages = {
            "session_started": "A new browser session started.",
            "session_active": "Session heartbeat received.",
            "registration_started": "Registration flow started.",
        }
        message = default_messages[event_type]

    session_id = str(payload.get("session_id") or "").strip() or x_session_id
    user_id = str(payload.get("user_id") or "").strip() or x_user_id
    family_id = str(payload.get("family_id") or "").strip() or None
    family_name = str(payload.get("family_name") or "").strip() or None

    registry.write_activity_event(
        event_type=event_type,
        message=message,
        user_id=user_id or None,
        family_id=family_id,
        family_name=family_name,
        session_id=session_id,
    )
    return {"message": "Session activity logged."}


@router.post("/register_user", response_model=RegistrationResponse, status_code=201)
def register_user(
    payload: UserRegistration,
    x_session_id: Optional[str] = Header(default=None),
) -> RegistrationResponse:
    """Register a new diaspora member and assign them to a family group."""
    record = registry.register_user(payload, session_id=x_session_id)
    return RegistrationResponse(
        user_id=record.user_id,
        family_id=record.family_id,
        message="Registration successful.",
    )
