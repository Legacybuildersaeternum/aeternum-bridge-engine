from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import PlainTextResponse
from datetime import datetime, timezone
from typing import Any, Optional
from pydantic import BaseModel
from models.user import (
    BackupResponse,
    ConnectionRequestCreateRequest,
    ConnectionRequestDecisionRequest,
    ConnectionRequestRecord,
    DuplicateActionResponse,
    DuplicateFamilyGroupResponse,
    DuplicateIgnoreRequest,
    DuplicateMergeRequest,
    DuplicateReviewLaterRequest,
    FamilyConnectionRequestPayload,
    FamilyConnectionRequestResponse,
    FindFamilyMatchResult,
    FindFamilySearchRequest,
    FamilyGroupResponse,
    PendingFamilyConnectionRequestRecord,
    RegistrationUpdateRequest,
    RelationshipSuggestionResponse,
    RelationshipUpdateRequest,
    StatsResponse,
    UserRecord,
)
from services import registry
from services.security import is_admin_passcode, require_admin_passcode, require_session_or_admin

router = APIRouter(tags=["Admin"])


class AdminLoginRequest(BaseModel):
    passcode: str


def _build_public_stats(stats: StatsResponse) -> StatsResponse:
    return StatsResponse(
        total_users=stats.total_users,
        total_families=stats.total_families,
        total_family_groups=stats.total_family_groups,
        total_living_users=0,
        total_ancestor_records=0,
        total_unknown_status_users=0,
        largest_family_size=0,
        total_interested_in_return=0,
        total_with_contact_info=0,
        entry_agreement_accepted_count=0,
        ecosystem_updates_opt_in_count=0,
        return_reconnection_interest_distribution={},
        onboarding_completed_count=0,
        onboarding_started_not_completed_count=0,
        total_messages_count=0,
        total_cohorts=0,
        total_cohort_memberships=0,
        region_distribution=stats.region_distribution,
        travel_timeframe_distribution={},
        state_distribution={},
        country_distribution={},
        role_distribution={},
        household_position_distribution={},
        region_travel_timeframe_combinations={},
        region_interest_combinations={},
    )


@router.post("/admin/login")
def admin_login(payload: AdminLoginRequest) -> dict[str, Any]:
    if not is_admin_passcode(payload.passcode):
        raise HTTPException(status_code=403, detail="Invalid admin passcode.")
    return {"success": True, "admin_mode": True}


@router.get("/stats", response_model=StatsResponse)
def get_stats(x_admin_passcode: Optional[str] = Header(default=None, alias="X-Admin-Passcode")) -> StatsResponse:
    """Return aggregate statistics for the diaspora registry."""
    stats = registry.get_stats()
    if is_admin_passcode(x_admin_passcode):
        return stats
    return _build_public_stats(stats)


@router.get("/registry-safety")
def get_registry_safety(_admin: None = Depends(require_admin_passcode)) -> dict[str, Any]:
    """Return live registry safety and backup metadata for admin visibility."""
    return registry.get_registry_safety_status()


@router.get("/registrations", response_model=list[UserRecord])
def get_registrations(
    account: Optional[Any] = Depends(require_session_or_admin),
) -> list[UserRecord]:
    """Return registrations. Admin sees all; logged-in user sees only their own."""
    owner_id = account["account_id"] if account else None
    return registry.get_registrations(owner_account_id=owner_id)


@router.get("/families", response_model=list[FamilyGroupResponse])
def get_families(
    account: Optional[Any] = Depends(require_session_or_admin),
) -> list[FamilyGroupResponse]:
    """Return family groups. Admin sees all; logged-in user sees only their own."""
    owner_id = account["account_id"] if account else None
    return registry.get_families(owner_account_id=owner_id)


@router.get("/family-tree/{family_id}")
def get_family_tree(
    family_id: str,
    x_session_id: Optional[str] = Header(default=None),
    x_user_id: Optional[str] = Header(default=None),
    account: Optional[Any] = Depends(require_session_or_admin),
) -> dict[str, Any]:
    """Return hierarchical family tree data for a specific family."""
    if account is not None:
        allowed_family_ids = {
            item.family_id for item in registry.get_families(owner_account_id=account["account_id"])
        }
        if family_id not in allowed_family_ids:
            raise HTTPException(status_code=403, detail="You do not have access to this family tree.")
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
    _admin: None = Depends(require_admin_passcode),
) -> list[dict[str, Any]]:
    """Return legacy activity log entries with optional session/user/event_type filters."""
    return registry.get_activity_log(limit=limit, session_id=session_id, user_id=user_id, event_type=event_type)


@router.get("/relationship-suggestions", response_model=list[RelationshipSuggestionResponse])
def get_relationship_suggestions(
    family_id: Optional[str] = Query(default=None),
    _admin: None = Depends(require_admin_passcode),
) -> list[RelationshipSuggestionResponse]:
    """Return confidence-scored candidate relationships for currently unlinked or incomplete profiles."""
    return registry.get_relationship_suggestions(family_id=family_id)


@router.get("/duplicate-profiles", response_model=list[DuplicateFamilyGroupResponse])
def get_duplicate_profiles(_admin: None = Depends(require_admin_passcode)) -> list[DuplicateFamilyGroupResponse]:
    """Return duplicate profile candidates grouped by family."""
    return registry.get_duplicate_profiles()


@router.get("/duplicate-profiles/{family_id}", response_model=DuplicateFamilyGroupResponse)
def get_duplicate_profiles_by_family(
    family_id: str,
    _admin: None = Depends(require_admin_passcode),
) -> DuplicateFamilyGroupResponse:
    """Return duplicate profile candidates for one family."""
    groups = registry.get_duplicate_profiles(family_id=family_id)
    if groups:
        return groups[0]

    family = next((item for item in registry.get_families() if item.family_id == family_id), None)
    if not family:
        raise HTTPException(status_code=404, detail="Family not found")
    return DuplicateFamilyGroupResponse(
        family_id=family.family_id,
        family_name=family.family_name,
        candidates=[],
    )


@router.post("/find-family/search", response_model=list[FindFamilyMatchResult])
def search_find_family(
    payload: FindFamilySearchRequest,
    x_session_id: Optional[str] = Header(default=None),
    _admin: None = Depends(require_admin_passcode),
) -> list[FindFamilyMatchResult]:
    """Search possible family matches using safe identity hints without auto-linking."""
    try:
        return registry.find_family_matches(payload, session_id=x_session_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/connection-requests", response_model=ConnectionRequestRecord)
def create_connection_request(
    payload: ConnectionRequestCreateRequest,
    x_session_id: Optional[str] = Header(default=None),
    _admin: None = Depends(require_admin_passcode),
) -> ConnectionRequestRecord:
    """Create a safe reconnection request (no auto-merge, no auto-link)."""
    try:
        return registry.create_connection_request(payload, session_id=x_session_id)
    except ValueError as exc:
        message = str(exc)
        status_code = 404 if "not found" in message.lower() else 400
        raise HTTPException(status_code=status_code, detail=message) from exc


@router.post("/family-connection/request", response_model=FamilyConnectionRequestResponse)
def create_family_connection_request(
    payload: FamilyConnectionRequestPayload,
    x_session_id: Optional[str] = Header(default=None),
    _admin: None = Depends(require_admin_passcode),
) -> FamilyConnectionRequestResponse:
    """Create a pending outside-verification family connection request."""
    try:
        return registry.create_family_connection_request(payload, session_id=x_session_id)
    except ValueError as exc:
        message = str(exc)
        registry.write_activity_event(
            event_type="family_connection_request_failed",
            message=f"Connection request failed: {message}",
            user_id=str(payload.requester_user_id or "") or None,
            session_id=x_session_id,
        )
        status_code = 404 if "not found" in message.lower() else 400
        raise HTTPException(status_code=status_code, detail=message) from exc


@router.get("/family-connection/requests/pending", response_model=list[PendingFamilyConnectionRequestRecord])
def get_pending_family_connection_requests(
    _admin: None = Depends(require_admin_passcode),
) -> list[PendingFamilyConnectionRequestRecord]:
    """List pending family connection requests that require outside verification."""
    return registry.get_pending_family_connection_requests()


@router.post("/connection-request/create", response_model=FamilyConnectionRequestResponse)
def create_connection_request_v2(
    payload: FamilyConnectionRequestPayload,
    x_session_id: Optional[str] = Header(default=None),
    _admin: None = Depends(require_admin_passcode),
) -> FamilyConnectionRequestResponse:
    """Create a safe outside-verification family connection request."""
    try:
        return registry.create_family_connection_request(payload, session_id=x_session_id)
    except ValueError as exc:
        message = str(exc)
        registry.write_activity_event(
            event_type="family_connection_request_failed",
            message=f"Connection request failed: {message}",
            user_id=str(payload.requester_user_id or "") or None,
            session_id=x_session_id,
        )
        status_code = 404 if "not found" in message.lower() else 400
        raise HTTPException(status_code=status_code, detail=message) from exc


@router.get("/connection-requests", response_model=list[PendingFamilyConnectionRequestRecord])
def get_all_connection_requests(
    _admin: None = Depends(require_admin_passcode),
) -> list[PendingFamilyConnectionRequestRecord]:
    """List all connection requests for admin review, regardless of status."""
    return registry.get_all_connection_requests()


@router.patch("/connection-request/{request_id}/accept", response_model=PendingFamilyConnectionRequestRecord)
def admin_accept_connection_request(
    request_id: str,
    x_session_id: Optional[str] = Header(default=None),
    _admin: None = Depends(require_admin_passcode),
) -> PendingFamilyConnectionRequestRecord:
    """Admin accepts a connection request and immediately links both users."""
    try:
        return registry.admin_accept_connection_request(request_id, session_id=x_session_id)
    except ValueError as exc:
        message = str(exc)
        status_code = 404 if "not found" in message.lower() else 400
        raise HTTPException(status_code=status_code, detail=message) from exc


@router.patch("/connection-request/{request_id}/reject", response_model=PendingFamilyConnectionRequestRecord)
def admin_reject_connection_request(
    request_id: str,
    x_session_id: Optional[str] = Header(default=None),
    _admin: None = Depends(require_admin_passcode),
) -> PendingFamilyConnectionRequestRecord:
    """Admin rejects a connection request. Users are not linked."""
    try:
        return registry.admin_reject_connection_request(request_id, session_id=x_session_id)
    except ValueError as exc:
        message = str(exc)
        status_code = 404 if "not found" in message.lower() else 400
        raise HTTPException(status_code=status_code, detail=message) from exc


@router.get("/connection-requests/incoming", response_model=list[ConnectionRequestRecord])
def get_incoming_connection_requests(
    user_id: str = Query(...),
    _admin: None = Depends(require_admin_passcode),
) -> list[ConnectionRequestRecord]:
    """List incoming connection requests for a specific user."""
    return registry.get_connection_requests_for_user(user_id, direction="incoming")


@router.get("/connection-requests/outgoing", response_model=list[ConnectionRequestRecord])
def get_outgoing_connection_requests(
    user_id: str = Query(...),
    _admin: None = Depends(require_admin_passcode),
) -> list[ConnectionRequestRecord]:
    """List outgoing connection requests for a specific user."""
    return registry.get_connection_requests_for_user(user_id, direction="outgoing")


@router.post("/connection-requests/{request_id}/accept", response_model=ConnectionRequestRecord)
def accept_incoming_connection_request(
    request_id: str,
    payload: ConnectionRequestDecisionRequest,
    x_session_id: Optional[str] = Header(default=None),
    _admin: None = Depends(require_admin_passcode),
) -> ConnectionRequestRecord:
    """Receiver accepts and starts external verification (still no link/merge)."""
    try:
        return registry.accept_connection_request(request_id, payload.acting_user_id, session_id=x_session_id)
    except ValueError as exc:
        message = str(exc)
        status_code = 404 if "not found" in message.lower() else 400
        raise HTTPException(status_code=status_code, detail=message) from exc


@router.post("/connection-requests/{request_id}/decline", response_model=ConnectionRequestRecord)
def decline_incoming_connection_request(
    request_id: str,
    payload: ConnectionRequestDecisionRequest,
    x_session_id: Optional[str] = Header(default=None),
    _admin: None = Depends(require_admin_passcode),
) -> ConnectionRequestRecord:
    """Receiver declines request."""
    try:
        return registry.decline_connection_request(request_id, payload.acting_user_id, session_id=x_session_id)
    except ValueError as exc:
        message = str(exc)
        status_code = 404 if "not found" in message.lower() else 400
        raise HTTPException(status_code=status_code, detail=message) from exc


@router.post("/connection-requests/{request_id}/confirm", response_model=ConnectionRequestRecord)
def confirm_connection_request(
    request_id: str,
    payload: ConnectionRequestDecisionRequest,
    x_session_id: Optional[str] = Header(default=None),
    _admin: None = Depends(require_admin_passcode),
) -> ConnectionRequestRecord:
    """Record external-contact verification confirmation for either requester or receiver."""
    try:
        return registry.confirm_connection_request_verification(request_id, payload.acting_user_id, session_id=x_session_id)
    except ValueError as exc:
        message = str(exc)
        status_code = 404 if "not found" in message.lower() else 400
        raise HTTPException(status_code=status_code, detail=message) from exc


@router.post("/connection-requests/{request_id}/complete", response_model=ConnectionRequestRecord)
def complete_connection_request(
    request_id: str,
    payload: ConnectionRequestDecisionRequest,
    x_session_id: Optional[str] = Header(default=None),
    _admin: None = Depends(require_admin_passcode),
) -> ConnectionRequestRecord:
    """Finalize connection after both parties confirmed external verification."""
    try:
        return registry.complete_connection_request(request_id, payload.acting_user_id, session_id=x_session_id)
    except ValueError as exc:
        message = str(exc)
        status_code = 404 if "not found" in message.lower() else 400
        raise HTTPException(status_code=status_code, detail=message) from exc


@router.post("/duplicate-profiles/merge", response_model=DuplicateActionResponse)
def merge_duplicate_profiles(
    payload: DuplicateMergeRequest,
    x_session_id: Optional[str] = Header(default=None),
    _admin: None = Depends(require_admin_passcode),
) -> DuplicateActionResponse:
    """Safely merge duplicate profile into a primary profile without deleting records."""
    try:
        return registry.merge_duplicate_profile(
            payload.primary_user_id,
            payload.duplicate_user_id,
            session_id=x_session_id,
        )
    except ValueError as exc:
        message = str(exc)
        status_code = 404 if "not found" in message.lower() else 400
        raise HTTPException(status_code=status_code, detail=message) from exc


@router.post("/duplicate-profiles/ignore", response_model=DuplicateActionResponse)
def ignore_duplicate_profile(
    payload: DuplicateIgnoreRequest,
    x_session_id: Optional[str] = Header(default=None),
    _admin: None = Depends(require_admin_passcode),
) -> DuplicateActionResponse:
    """Mark duplicate candidate as reviewed/ignored without merging records."""
    try:
        return registry.ignore_duplicate_profile(
            payload.primary_user_id,
            payload.duplicate_user_id,
            session_id=x_session_id,
        )
    except ValueError as exc:
        message = str(exc)
        status_code = 404 if "not found" in message.lower() else 400
        raise HTTPException(status_code=status_code, detail=message) from exc


@router.post("/duplicate-profiles/review-later", response_model=DuplicateActionResponse)
def review_later_duplicate_profile(
    payload: DuplicateReviewLaterRequest,
    x_session_id: Optional[str] = Header(default=None),
    _admin: None = Depends(require_admin_passcode),
) -> DuplicateActionResponse:
    """Mark duplicate candidate as review-later for deferred assessment without merging records."""
    try:
        return registry.review_later_duplicate_profile(
            payload.primary_user_id,
            payload.duplicate_user_id,
            session_id=x_session_id,
        )
    except ValueError as exc:
        message = str(exc)
        status_code = 404 if "not found" in message.lower() else 400
        raise HTTPException(status_code=status_code, detail=message) from exc


@router.patch("/registrations/{user_id}", response_model=UserRecord)
def patch_registration(
    user_id: str,
    payload: RegistrationUpdateRequest,
    x_session_id: Optional[str] = Header(default=None),
    _admin: None = Depends(require_admin_passcode),
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
    _admin: None = Depends(require_admin_passcode),
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
    _admin: None = Depends(require_admin_passcode),
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
    _admin: None = Depends(require_admin_passcode),
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
    _admin: None = Depends(require_admin_passcode),
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
    _admin: None = Depends(require_admin_passcode),
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
    _admin: None = Depends(require_admin_passcode),
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
    _admin: None = Depends(require_admin_passcode),
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
