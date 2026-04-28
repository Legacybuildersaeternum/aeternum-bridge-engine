"""Phase 41 — Origin Discovery API routes (Africa Can Find You)."""
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from typing import Any, Optional

from models.user import RegistrationUpdateRequest
from services import registry
from services.origin_discovery import find_diaspora_profiles_for_origin

router = APIRouter(tags=["Origin Discovery"], prefix="/origin-matches")


@router.get("/search")
def search_origin_matches(
    origin_region: Optional[str] = Query(default=None, description="Heritage region to match (e.g. west_africa)"),
    origin_country: Optional[str] = Query(default=None, description="Heritage country to match (e.g. Nigeria)"),
    heritage_group: Optional[str] = Query(default=None, description="Heritage group / tribe / community"),
) -> list[dict[str, Any]]:
    """
    Search diaspora users who have opted in to origin community discovery.

    Only returns living users who have explicitly enabled discoverable_by_origin_communities.
    No email, phone, or private contact info is exposed.
    Display names are masked.
    """
    users = [u.model_dump(mode="json") for u in registry.get_registrations()]
    return find_diaspora_profiles_for_origin(
        origin_region=origin_region,
        origin_country=origin_country,
        heritage_group=heritage_group,
        users=users,
    )


class UpdateDiscoverabilityRequest(BaseModel):
    user_id: str
    discoverable_by_origin_communities: Optional[bool] = None
    open_to_cultural_guides: Optional[bool] = None
    open_to_relocation_guidance: Optional[bool] = None
    preferred_contact_scope: Optional[str] = None


@router.post("/update-discoverability")
def update_discoverability(payload: UpdateDiscoverabilityRequest) -> dict[str, Any]:
    """
    Update a user's Africa Can Find You discoverability preferences.
    Only updates fields that are explicitly provided (None = no change).
    """
    updates: dict[str, Any] = {}
    if payload.discoverable_by_origin_communities is not None:
        updates["discoverable_by_origin_communities"] = payload.discoverable_by_origin_communities
    if payload.open_to_cultural_guides is not None:
        updates["open_to_cultural_guides"] = payload.open_to_cultural_guides
    if payload.open_to_relocation_guidance is not None:
        updates["open_to_relocation_guidance"] = payload.open_to_relocation_guidance
    if payload.preferred_contact_scope is not None:
        valid_scopes = {"connections_only", "cohort_members", "verified_guides_only", "private"}
        if payload.preferred_contact_scope not in valid_scopes:
            raise HTTPException(status_code=400, detail=f"preferred_contact_scope must be one of: {valid_scopes}")
        updates["preferred_contact_scope"] = payload.preferred_contact_scope

    if not updates:
        return {"success": True, "message": "No changes requested.", "user_id": payload.user_id}

    try:
        registry.update_registration(payload.user_id, RegistrationUpdateRequest(**updates))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=f"User '{payload.user_id}' not found") from exc

    registry.write_activity_event(
        event_type="discoverability_updated",
        message=f"Discoverability preferences updated for user {payload.user_id}.",
        user_id=payload.user_id,
        extra=updates,
    )
    return {"success": True, "user_id": payload.user_id, "updated_fields": list(updates.keys())}
