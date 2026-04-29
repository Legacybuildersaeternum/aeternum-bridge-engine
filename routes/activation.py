"""
Aeternum Bridge Engine — Phase 42: Activation & Similar Users API Routes.

GET /activation/status/{user_id}    — user activation checklist
GET /activation/similar/{user_id}   — "people like you" matching
"""
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from services import registry
from services.activation import get_user_activation_status, get_similar_users
from services.security import require_admin_passcode

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Activation"], prefix="/activation", dependencies=[Depends(require_admin_passcode)])


def _load_all_data() -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Load users, cohort memberships, and connection requests."""
    users = [u.model_dump(mode="json") for u in registry.get_registrations()]

    from services import cohorts as cohort_svc
    store = cohort_svc._load_store()
    memberships = list(store.get("memberships") or [])

    from services.registry import _load_connection_requests
    connection_requests = _load_connection_requests()

    return users, memberships, connection_requests


@router.get("/status/{user_id}")
def get_activation_status(user_id: str) -> dict[str, Any]:
    """
    Return activation checklist state for a user.

    Checklist:
    1. has_cohort
    2. has_origin_data
    3. discovery_enabled
    4. has_connections
    5. has_family_members
    """
    users, memberships, connection_requests = _load_all_data()
    status = get_user_activation_status(
        user_id,
        users=users,
        memberships=memberships,
        connection_requests=connection_requests,
    )
    if not status["found"]:
        raise HTTPException(status_code=404, detail=f"User '{user_id}' not found.")

    registry.write_activity_event(
        event_type="ACTIVATION_STATUS_VIEWED",
        message=f"Activation checklist viewed for user {user_id}.",
        user_id=user_id,
    )

    completed_steps = [
        key for key in (
            "has_cohort",
            "has_origin_data",
            "discovery_enabled",
            "has_connections",
            "has_family_members",
        )
        if bool(status.get(key))
    ]
    if completed_steps:
        registry.write_activity_event(
            event_type="ACTIVATION_STEP_COMPLETED",
            message=f"Activation progress for {user_id}: {len(completed_steps)} step(s) complete.",
            user_id=user_id,
            extra={"completed_steps": completed_steps},
        )

    return status


@router.get("/similar/{user_id}")
def get_similar(user_id: str, max_results: int = 5) -> dict[str, Any]:
    """
    Return masked profiles of users who share heritage or relocation intent.

    Only returns opted-in living users. No PII exposed.
    """
    if max_results < 1 or max_results > 20:
        max_results = 5

    users, _, _ = _load_all_data()
    result = get_similar_users(user_id, users=users, max_results=max_results)

    if not any(u.get("user_id") == user_id for u in users):
        raise HTTPException(status_code=404, detail=f"User '{user_id}' not found.")

    registry.write_activity_event(
        event_type="SIMILAR_USERS_VIEWED",
        message=f"Similar users viewed for user {user_id}. Count: {result['count']}.",
        user_id=user_id,
        extra={"similar_count": result["count"]},
    )
    return result
