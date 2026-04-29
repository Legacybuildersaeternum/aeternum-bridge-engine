"""Phase 45 - Real World Proof API routes."""

from typing import Any, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel

from services import proofs as proof_service
from services import registry
from services.security import require_admin_passcode

router = APIRouter(tags=["Proofs"], prefix="/proofs", dependencies=[Depends(require_admin_passcode)])


class SubmitProofRequest(BaseModel):
    user_id: str
    movement_id: str
    proof_type: str
    public_summary: str
    private_notes: Optional[str] = None


class ReviewProofRequest(BaseModel):
    proof_id: str
    decision: str
    reviewer_user_id: Optional[str] = None
    review_public_note: Optional[str] = None
    review_private_note: Optional[str] = None


@router.get("/list")
def list_proofs(
    status: Optional[str] = Query(default=None),
    user_id: Optional[str] = Query(default=None),
    movement_id: Optional[str] = Query(default=None),
) -> list[dict[str, Any]]:
    return proof_service.list_proofs(
        status=status,
        user_id=user_id,
        movement_id=movement_id,
        admin_view=True,
    )


@router.get("/user/{user_id}")
def list_user_proofs(user_id: str) -> list[dict[str, Any]]:
    return proof_service.list_proofs(user_id=user_id, admin_view=False)


@router.post("/submit")
def submit_proof(
    payload: SubmitProofRequest,
    x_session_id: Optional[str] = Header(default=None),
) -> dict[str, Any]:
    try:
        record = proof_service.submit_proof(
            user_id=payload.user_id,
            movement_id=payload.movement_id,
            proof_type=payload.proof_type,
            public_summary=payload.public_summary,
            private_notes=payload.private_notes,
        )
    except ValueError as exc:
        message = str(exc)
        status_code = 404 if "not found" in message.lower() else 400
        raise HTTPException(status_code=status_code, detail=message) from exc

    registry.write_activity_event(
        event_type="PROOF_SUBMITTED",
        message=(
            f"Real-world proof submitted for user {record['user_id']} "
            f"in movement {record['movement_id']}."
        ),
        user_id=record["user_id"],
        session_id=x_session_id,
        extra={
            "proof_id": record["proof_id"],
            "movement_id": record["movement_id"],
            "proof_type": record["proof_type"],
        },
    )

    return {
        "success": True,
        "proof": proof_service.to_user_view(record),
    }


@router.post("/review")
def review_proof(
    payload: ReviewProofRequest,
    x_session_id: Optional[str] = Header(default=None),
) -> dict[str, Any]:
    try:
        record = proof_service.review_proof(
            proof_id=payload.proof_id,
            decision=payload.decision,
            reviewer_user_id=payload.reviewer_user_id,
            review_public_note=payload.review_public_note,
            review_private_note=payload.review_private_note,
        )
    except ValueError as exc:
        message = str(exc)
        status_code = 404 if "not found" in message.lower() else 400
        raise HTTPException(status_code=status_code, detail=message) from exc

    decision = str(record.get("status") or "").lower()
    registry.write_activity_event(
        event_type="PROOF_REVIEWED",
        message=f"Proof {record['proof_id']} reviewed with decision {decision}.",
        user_id=str(record.get("user_id") or "") or None,
        session_id=x_session_id,
        extra={
            "proof_id": record["proof_id"],
            "decision": decision,
            "reviewed_by": str(record.get("reviewed_by") or ""),
        },
    )

    trust_result = None
    if decision == "accepted":
        registry.write_activity_event(
            event_type="REAL_WORLD_PROOF_ACCEPTED",
            message=f"Proof {record['proof_id']} accepted.",
            user_id=str(record.get("user_id") or "") or None,
            session_id=x_session_id,
            extra={"proof_id": record["proof_id"], "movement_id": record.get("movement_id")},
        )
        trust_result = registry.refresh_user_trust(
            str(record.get("user_id") or ""),
            reason="real_world_proof_accepted",
            session_id=x_session_id,
        )
    elif decision == "rejected":
        registry.write_activity_event(
            event_type="REAL_WORLD_PROOF_REJECTED",
            message=f"Proof {record['proof_id']} rejected.",
            user_id=str(record.get("user_id") or "") or None,
            session_id=x_session_id,
            extra={"proof_id": record["proof_id"], "movement_id": record.get("movement_id")},
        )

    return {
        "success": True,
        "proof": record,
        "trust_update": trust_result,
    }
