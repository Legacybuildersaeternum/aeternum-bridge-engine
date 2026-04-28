"""Phase 40 — Cohort Engine API routes (tribe, region, and movement-based grouping)."""
from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel
from typing import Any, Optional

from services import registry
from services import cohorts as cohort_service

router = APIRouter(tags=["Cohorts"], prefix="/cohorts")


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------

class CreateCohortRequest(BaseModel):
    name: str
    type: str
    description: str
    origin_region: Optional[str] = None


class JoinCohortRequest(BaseModel):
    user_id: str
    cohort_id: str


class LeaveCohortRequest(BaseModel):
    user_id: str
    cohort_id: str


class CohortMessageRequest(BaseModel):
    user_id: str
    cohort_id: str
    message_text: str


class SuggestRequest(BaseModel):
    origin_region: Optional[str] = None
    return_reconnection_interest: Optional[str] = None
    heritage_country: Optional[str] = None
    heritage_group: Optional[str] = None


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/create")
def create_cohort(
    payload: CreateCohortRequest,
    x_session_id: Optional[str] = Header(default=None),
) -> dict[str, Any]:
    """Create a new cohort."""
    cohort = cohort_service.create_cohort(
        name=payload.name,
        cohort_type=payload.type,
        description=payload.description,
        origin_region=payload.origin_region,
    )
    registry.write_activity_event(
        event_type="cohort_created",
        message=f"Cohort created: '{cohort['name']}' (type: {cohort['type']}).",
        session_id=x_session_id,
        extra={"cohort_id": cohort["cohort_id"]},
    )
    return {"success": True, "cohort": cohort}


@router.get("/list")
def list_cohorts() -> list[dict[str, Any]]:
    """Return all cohorts with member counts."""
    return cohort_service.list_cohorts()


@router.get("/user/{user_id}")
def get_user_cohorts(user_id: str) -> list[dict[str, Any]]:
    """Return cohorts a user has joined."""
    return cohort_service.get_user_cohorts(user_id)


@router.post("/suggest")
def suggest_cohorts(payload: SuggestRequest) -> list[dict[str, Any]]:
    """Return suggested cohorts based on user profile."""
    return cohort_service.suggest_cohorts_for_user(
        origin_region=payload.origin_region,
        return_reconnection_interest=payload.return_reconnection_interest,
        heritage_country=payload.heritage_country,
        heritage_group=payload.heritage_group,
    )


@router.post("/join")
def join_cohort(
    payload: JoinCohortRequest,
    x_session_id: Optional[str] = Header(default=None),
) -> dict[str, Any]:
    """Join a cohort. Returns the new membership record."""
    try:
        membership = cohort_service.join_cohort(
            user_id=payload.user_id,
            cohort_id=payload.cohort_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    cohort = cohort_service.get_cohort(payload.cohort_id) or {}
    registry.write_activity_event(
        event_type="cohort_joined",
        message=f"User {payload.user_id} joined cohort '{cohort.get('name', payload.cohort_id)}'.",
        user_id=payload.user_id,
        session_id=x_session_id,
        extra={"cohort_id": payload.cohort_id},
    )
    registry.refresh_user_trust(payload.user_id, reason="cohort_joined", session_id=x_session_id)
    return {"success": True, "membership": membership}


@router.post("/leave")
def leave_cohort(
    payload: LeaveCohortRequest,
    x_session_id: Optional[str] = Header(default=None),
) -> dict[str, Any]:
    """Leave a cohort."""
    found = cohort_service.leave_cohort(
        user_id=payload.user_id,
        cohort_id=payload.cohort_id,
    )
    if not found:
        raise HTTPException(status_code=404, detail="Active membership not found.")

    cohort = cohort_service.get_cohort(payload.cohort_id) or {}
    registry.write_activity_event(
        event_type="cohort_left",
        message=f"User {payload.user_id} left cohort '{cohort.get('name', payload.cohort_id)}'.",
        user_id=payload.user_id,
        session_id=x_session_id,
        extra={"cohort_id": payload.cohort_id},
    )
    return {"success": True}


@router.get("/{cohort_id}/members")
def get_cohort_members(cohort_id: str) -> list[dict[str, Any]]:
    """Return active members of a cohort."""
    cohort = cohort_service.get_cohort(cohort_id)
    if cohort is None:
        raise HTTPException(status_code=404, detail=f"Cohort '{cohort_id}' not found.")
    return cohort_service.get_cohort_members(cohort_id)


@router.get("/{cohort_id}/messages")
def get_cohort_messages(
    cohort_id: str,
    limit: int = Query(default=50, ge=1, le=200),
) -> list[dict[str, Any]]:
    """Return recent messages for a cohort (members only enforced client-side for Phase 40)."""
    cohort = cohort_service.get_cohort(cohort_id)
    if cohort is None:
        raise HTTPException(status_code=404, detail=f"Cohort '{cohort_id}' not found.")
    return cohort_service.get_cohort_messages(cohort_id, limit=limit)


@router.post("/message")
def send_cohort_message(
    payload: CohortMessageRequest,
    x_session_id: Optional[str] = Header(default=None),
) -> dict[str, Any]:
    """Send a broadcast message to a cohort."""
    try:
        msg = cohort_service.send_cohort_message(
            user_id=payload.user_id,
            cohort_id=payload.cohort_id,
            message_text=payload.message_text,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    registry.write_activity_event(
        event_type="cohort_message_sent",
        message=f"User {payload.user_id} sent a message in cohort '{payload.cohort_id}'.",
        user_id=payload.user_id,
        session_id=x_session_id,
        extra={"cohort_id": payload.cohort_id, "message_id": msg["message_id"]},
    )
    return {"success": True, "message_id": msg["message_id"]}


@router.get("/{cohort_id}")
def get_cohort(cohort_id: str) -> dict[str, Any]:
    """Return a single cohort by ID."""
    cohort = cohort_service.get_cohort(cohort_id)
    if cohort is None:
        raise HTTPException(status_code=404, detail=f"Cohort '{cohort_id}' not found.")
    return cohort
