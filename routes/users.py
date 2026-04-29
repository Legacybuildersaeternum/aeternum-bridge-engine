from typing import Any, Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from models.user import UserRegistration, RegistrationResponse
from services import registry
from services.security import is_admin_passcode, get_session_account

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
    "find_family_request_clicked",
    "find_family_not_a_match",
    "register_family_lookup_started",
    "register_family_lookup_results",
    "register_family_request_prompted",
    "register_family_request_created",
}


class SessionActivityPayload(BaseModel):
    event_type: str
    message: Optional[str] = None
    session_id: Optional[str] = None
    user_id: Optional[str] = None
    family_id: Optional[str] = None
    family_name: Optional[str] = None

    model_config = {
        "json_schema_extra": {
            "example": {
                "event_type": "activity_log_viewed",
                "family_id": "fam_96b8f576c788",
                "family_name": "Ward",
                "user_id": "usr_df06afdbc503",
                "message": "Activity log viewed from admin interface.",
            }
        }
    }


@router.post("/session/activity")
def track_session_activity(
    payload: SessionActivityPayload,
    x_session_id: Optional[str] = Header(default=None),
    x_user_id: Optional[str] = Header(default=None),
) -> dict[str, str]:
    """Record session-level client activity events from the UI."""
    payload_data = payload.model_dump()
    event_type = str(payload_data.get("event_type") or "").strip()
    if event_type not in _SESSION_EVENT_TYPES:
        allowed = ", ".join(sorted(_SESSION_EVENT_TYPES))
        raise HTTPException(
            status_code=400,
            detail=(
                f"Unsupported session activity event '{event_type}'. "
                f"Allowed event types: {allowed}"
            ),
        )

    message = str(payload_data.get("message") or "").strip()
    if not message:
        default_messages = {
            "session_started": "A new browser session started.",
            "session_active": "Session heartbeat received.",
            "registration_started": "Registration flow started.",
        }
        message = default_messages[event_type]

    session_id = str(payload_data.get("session_id") or "").strip() or x_session_id
    user_id = str(payload_data.get("user_id") or "").strip() or x_user_id
    family_id = str(payload_data.get("family_id") or "").strip() or None
    family_name = str(payload_data.get("family_name") or "").strip() or None

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
    x_session_token: Optional[str] = Header(default=None, alias="X-Session-Token"),
    x_admin_passcode: Optional[str] = Header(default=None, alias="X-Admin-Passcode"),
) -> RegistrationResponse:
    """Register a new diaspora member and assign them to a family group."""
    # Require login unless admin passcode is present.
    account = get_session_account(x_session_token or "")
    if account is None and not is_admin_passcode(x_admin_passcode):
        raise HTTPException(
            status_code=401,
            detail="Please create an account or log in before registering a family representative.",
        )
    owner_account_id = account["account_id"] if account else None
    record = registry.register_user(payload, session_id=x_session_id, owner_account_id=owner_account_id)
    return RegistrationResponse(
        user_id=record.user_id,
        family_id=record.family_id,
        message="Registration successful.",
    )
