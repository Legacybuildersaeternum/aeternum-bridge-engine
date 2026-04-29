"""Phase 44 — Movement routes."""

from typing import Any, Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel

from services import movements as movement_service
from services import proofs as proof_service
from services import registry
from services.security import require_admin_passcode

router = APIRouter(tags=["Movements"], prefix="/movements", dependencies=[Depends(require_admin_passcode)])


class CreateMovementRequest(BaseModel):
    user_id: str
    title: str
    region: str
    country: str
    target_date: str


class JoinMovementRequest(BaseModel):
    user_id: str
    movement_id: str


class UpdateMovementStatusRequest(BaseModel):
    user_id: str
    movement_id: str
    status: str


class AssignMovementRoleRequest(BaseModel):
    user_id: str
    movement_id: str
    role: str


@router.get("/list")
def list_movements(
    x_session_id: Optional[str] = Header(default=None),
    user_id: Optional[str] = None,
) -> list[dict[str, Any]]:
    movements = movement_service.list_movements()
    for m in movements:
        m["member_count"] = movement_service.get_member_count(m)
        m["status_counts"] = movement_service.get_status_counts(m)
        m["accepted_proof_count"] = proof_service.get_accepted_proof_count_for_movement(
            str(m.get("movement_id") or "")
        )
    registry.write_activity_event(
        event_type="MOVEMENT_LIST_LOADED",
        message="Movement list loaded.",
        user_id=user_id,
        session_id=x_session_id,
        extra={"count": len(movements)},
    )
    return movements


@router.post("/create")
def create_movement(
    payload: CreateMovementRequest,
    x_session_id: Optional[str] = Header(default=None),
) -> dict[str, Any]:
    movement = movement_service.create_movement(
        user_id=payload.user_id,
        title=payload.title,
        region=payload.region,
        country=payload.country,
        target_date=payload.target_date,
    )
    registry.write_activity_event(
        event_type="MOVEMENT_CREATED",
        message=f"Movement created: {movement['title']} ({movement['movement_id']}).",
        user_id=payload.user_id,
        session_id=x_session_id,
        extra={
            "movement_id": movement["movement_id"],
            "country": movement.get("country"),
            "target_date": movement.get("target_date"),
        },
    )
    return {"success": True, "movement": movement}


@router.post("/join")
def join_movement(
    payload: JoinMovementRequest,
    x_session_id: Optional[str] = Header(default=None),
) -> dict[str, Any]:
    users = [u.model_dump(mode="json") for u in registry.get_registrations()]
    user = next((u for u in users if str(u.get("user_id") or "") == payload.user_id), None)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    warning = None
    if int(user.get("trust_score") or 0) < 30:
        warning = "Low trust — proceed with caution"

    try:
        membership = movement_service.join_movement(payload.user_id, payload.movement_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    registry.write_activity_event(
        event_type="MOVEMENT_JOINED",
        message=f"User {payload.user_id} joined movement {payload.movement_id}.",
        user_id=payload.user_id,
        session_id=x_session_id,
        extra={"movement_id": payload.movement_id, "warning": warning},
    )
    return {"success": True, "membership": membership, "warning": warning}


@router.post("/status")
def update_status(
    payload: UpdateMovementStatusRequest,
    x_session_id: Optional[str] = Header(default=None),
) -> dict[str, Any]:
    try:
        membership = movement_service.update_movement_status(
            payload.user_id,
            payload.movement_id,
            payload.status,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    registry.write_activity_event(
        event_type="MOVEMENT_STATUS_UPDATED",
        message=(
            f"User {payload.user_id} updated movement status "
            f"for {payload.movement_id} to {payload.status}."
        ),
        user_id=payload.user_id,
        session_id=x_session_id,
        extra={"movement_id": payload.movement_id, "movement_status": payload.status},
    )
    return {"success": True, "membership": membership}


@router.post("/role")
def assign_role(
    payload: AssignMovementRoleRequest,
    x_session_id: Optional[str] = Header(default=None),
) -> dict[str, Any]:
    try:
        membership = movement_service.assign_role(
            payload.user_id,
            payload.movement_id,
            payload.role,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    registry.write_activity_event(
        event_type="MOVEMENT_ROLE_ASSIGNED",
        message=f"Role '{payload.role}' assigned for user {payload.user_id} in movement {payload.movement_id}.",
        user_id=payload.user_id,
        session_id=x_session_id,
        extra={"movement_id": payload.movement_id, "role": payload.role},
    )
    return {"success": True, "membership": membership}


@router.get("/user/{user_id}")
def get_user_movements(
    user_id: str,
    x_session_id: Optional[str] = Header(default=None),
) -> list[dict[str, Any]]:
    movements = movement_service.get_user_movements(user_id)
    for m in movements:
        m["member_count"] = movement_service.get_member_count(m)
        m["status_counts"] = movement_service.get_status_counts(m)
        m["accepted_proof_count"] = proof_service.get_accepted_proof_count_for_movement(
            str(m.get("movement_id") or "")
        )
    return movements


@router.get("/view/{movement_id}")
def view_movement(
    movement_id: str,
    user_id: Optional[str] = None,
    x_session_id: Optional[str] = Header(default=None),
) -> dict[str, Any]:
    movements = movement_service.list_movements()
    target = next((m for m in movements if str(m.get("movement_id") or "") == movement_id), None)
    if not target:
        raise HTTPException(status_code=404, detail="Movement not found")
    target["member_count"] = movement_service.get_member_count(target)
    target["status_counts"] = movement_service.get_status_counts(target)
    target["accepted_proof_count"] = proof_service.get_accepted_proof_count_for_movement(movement_id)
    registry.write_activity_event(
        event_type="MOVEMENT_VIEWED",
        message=f"Movement viewed: {target.get('title')} ({movement_id}).",
        user_id=user_id,
        session_id=x_session_id,
        extra={"movement_id": movement_id},
    )
    return target
