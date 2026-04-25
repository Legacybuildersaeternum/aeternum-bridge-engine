from fastapi import APIRouter, Header, HTTPException, Query
from fastapi.responses import PlainTextResponse
from datetime import datetime, timezone
from typing import Any, Optional
from models.user import (
    BackupResponse,
    FamilyGroupResponse,
    RegistrationUpdateRequest,
    RelationshipUpdateRequest,
    StatsResponse,
    UserRecord,
)
from services import registry

router = APIRouter(tags=["Admin"])


@router.get("/stats", response_model=StatsResponse)
def get_stats() -> StatsResponse:
    """Return aggregate statistics for the diaspora registry."""
    return registry.get_stats()


@router.get("/registrations", response_model=list[UserRecord])
def get_registrations() -> list[UserRecord]:
    """Return all saved registrations for admin review."""
    return registry.get_registrations()


@router.get("/families", response_model=list[FamilyGroupResponse])
def get_families() -> list[FamilyGroupResponse]:
    """Return grouped family data with member relationship graph details."""
    return registry.get_families()


@router.get("/family-tree/{family_id}")
def get_family_tree(
    family_id: str,
    x_session_id: Optional[str] = Header(default=None),
    x_user_id: Optional[str] = Header(default=None),
) -> dict[str, Any]:
    """Return hierarchical family tree data for a specific family."""
    try:
        return registry.get_family_tree(
            family_id,
            session_id=x_session_id,
            user_id=x_user_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/activity-log")
def get_activity_log(
    limit: int = Query(default=200, ge=1, le=1000),
    session_id: Optional[str] = Query(default=None),
    user_id: Optional[str] = Query(default=None),
    event_type: Optional[str] = Query(default=None),
) -> list[dict[str, Any]]:
    """Return legacy activity log entries with optional session/user/event_type filters."""
    return registry.get_activity_log(limit=limit, session_id=session_id, user_id=user_id, event_type=event_type)


@router.patch("/registrations/{user_id}", response_model=UserRecord)
def patch_registration(
    user_id: str,
    payload: RegistrationUpdateRequest,
    x_session_id: Optional[str] = Header(default=None),
) -> UserRecord:
    """Update any editable registration fields for an existing user."""
    try:
        return registry.update_registration(user_id, payload, session_id=x_session_id)
    except ValueError as exc:
        message = str(exc)
        if message == "Registration not found":
            raise HTTPException(status_code=404, detail=message) from exc
        raise HTTPException(status_code=400, detail=message) from exc


@router.patch("/registrations/{user_id}/relationship", response_model=UserRecord)
def patch_registration_relationship(
    user_id: str,
    payload: RelationshipUpdateRequest,
    x_session_id: Optional[str] = Header(default=None),
) -> UserRecord:
    """Update relationship mapping fields for an existing registration."""
    try:
        return registry.update_registration_relationship(user_id, payload, session_id=x_session_id)
    except ValueError as exc:
        message = str(exc)
        if message == "Registration not found":
            raise HTTPException(status_code=404, detail=message) from exc
        raise HTTPException(status_code=400, detail=message) from exc


@router.delete("/registrations/{user_id}")
def delete_registration(
    user_id: str,
    x_session_id: Optional[str] = Header(default=None),
) -> dict[str, str]:
    """Delete a registration and clear relationships that referenced the deleted user."""
    try:
        return registry.delete_registration(user_id, session_id=x_session_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


# Export endpoints for backup and archival
@router.get("/export/registrations", response_model=list[UserRecord])
def export_registrations(
    x_session_id: Optional[str] = Header(default=None),
    x_user_id: Optional[str] = Header(default=None),
) -> list[UserRecord]:
    """Export all registrations as JSON for backup and records."""
    data = registry.get_registrations()
    registry.write_activity_event(
        event_type="export_download_clicked",
        message="Registrations JSON export downloaded.",
        user_id=x_user_id,
        session_id=x_session_id,
    )
    return data


@router.get("/export/registrations.csv", response_class=PlainTextResponse)
def export_registrations_csv(
    x_session_id: Optional[str] = Header(default=None),
    x_user_id: Optional[str] = Header(default=None),
) -> str:
    """Export all registrations as CSV for spreadsheet import."""
    data = registry.export_registrations_csv()
    registry.write_activity_event(
        event_type="export_download_clicked",
        message="Registrations CSV export downloaded.",
        user_id=x_user_id,
        session_id=x_session_id,
    )
    return data


@router.get("/export/families", response_model=list[FamilyGroupResponse])
def export_families(
    x_session_id: Optional[str] = Header(default=None),
    x_user_id: Optional[str] = Header(default=None),
) -> list[FamilyGroupResponse]:
    """Export all family groups as JSON for backup."""
    data = registry.get_families()
    registry.write_activity_event(
        event_type="export_download_clicked",
        message="Families JSON export downloaded.",
        user_id=x_user_id,
        session_id=x_session_id,
    )
    return data


@router.get("/export/stats", response_model=StatsResponse)
def export_stats(
    x_session_id: Optional[str] = Header(default=None),
    x_user_id: Optional[str] = Header(default=None),
) -> StatsResponse:
    """Export aggregate statistics as JSON for records."""
    data = registry.get_stats()
    registry.write_activity_event(
        event_type="export_download_clicked",
        message="Stats JSON export downloaded.",
        user_id=x_user_id,
        session_id=x_session_id,
    )
    return data


@router.get("/export/full_backup", response_model=BackupResponse)
def export_full_backup(
    x_session_id: Optional[str] = Header(default=None),
    x_user_id: Optional[str] = Header(default=None),
) -> BackupResponse:
    """Export complete diaspora registry backup with timestamp and all data."""
    stats = registry.get_stats()
    payload = BackupResponse(
        generated_at=datetime.now(timezone.utc).isoformat(),
        registrations=registry.get_registrations(),
        families=registry.get_families(),
        stats=stats,
        insights_summary={
            "total_users": str(stats.total_users),
            "total_families": str(stats.total_family_groups),
            "interested_in_return": str(stats.total_interested_in_return),
            "with_contact_info": str(stats.total_with_contact_info),
        },
    )
    registry.write_activity_event(
        event_type="full_backup_downloaded",
        message="Full backup JSON downloaded.",
        user_id=x_user_id,
        session_id=x_session_id,
    )
    return payload
