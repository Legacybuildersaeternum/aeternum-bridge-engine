"""Phase 45.5 — Controlled System Activation simulation routes."""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Header

from services import simulation as sim_service

router = APIRouter(tags=["Simulation"], prefix="/simulation")


@router.post("/create-test-users")
def create_test_users(
    x_session_id: Optional[str] = Header(default=None),
) -> dict[str, Any]:
    result = sim_service.create_test_user_set(session_id=x_session_id)
    return {"success": True, "result": result}


@router.post("/cohort-joins")
def cohort_joins(
    x_session_id: Optional[str] = Header(default=None),
) -> dict[str, Any]:
    result = sim_service.simulate_cohort_joins(session_id=x_session_id)
    return {"success": True, "result": result}


@router.post("/movement-joins")
def movement_joins(
    x_session_id: Optional[str] = Header(default=None),
) -> dict[str, Any]:
    result = sim_service.simulate_movement_joins(session_id=x_session_id)
    return {"success": True, "result": result}


@router.post("/submit-proof")
def submit_proof(
    x_session_id: Optional[str] = Header(default=None),
) -> dict[str, Any]:
    result = sim_service.submit_sample_proof(session_id=x_session_id)
    return {"success": True, "result": result}


@router.post("/review-proof")
def review_proof(
    x_session_id: Optional[str] = Header(default=None),
) -> dict[str, Any]:
    result = sim_service.review_sample_proof(session_id=x_session_id)
    return {"success": True, "result": result}


@router.post("/refresh-trust")
def refresh_trust(
    x_session_id: Optional[str] = Header(default=None),
) -> dict[str, Any]:
    result = sim_service.refresh_all_trust_scores(session_id=x_session_id)
    return {"success": True, "result": result}


@router.get("/system-check")
def system_check(
    x_session_id: Optional[str] = Header(default=None),
) -> dict[str, Any]:
    result = sim_service.run_full_system_check(session_id=x_session_id)
    return {"success": True, "result": result}
