"""
Aeternum Bridge Engine — Phase 42: Cultural Guidance Request Routes.

POST /guidance/request     — submit a guidance request
GET  /guidance/guides      — list available cultural guides
GET  /guidance/guides/{id} — get single guide profile

Guidance requests are stored in an append-only log at
data/guidance_requests.json. This file is NOT committed to the repository.
"""
import json
import logging
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, field_validator

from services import registry
from services.guides import get_guide_by_id, get_guides_for_region, list_all_guides

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Guidance"], prefix="/guidance")

_GUIDANCE_FILE = Path(__file__).resolve().parents[1] / "data" / "guidance_requests.json"
_GUIDANCE_LOCK = threading.RLock()
_EMPTY_STORE: dict[str, Any] = {"requests": []}


# ---------------------------------------------------------------------------
# Persistence helpers (append-only)
# ---------------------------------------------------------------------------

def _ensure_file() -> None:
    _GUIDANCE_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not _GUIDANCE_FILE.exists():
        with _GUIDANCE_FILE.open("w", encoding="utf-8") as f:
            json.dump(_EMPTY_STORE, f, indent=2)


def _load_store() -> dict[str, Any]:
    _ensure_file()
    try:
        with _GUIDANCE_FILE.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict) or not isinstance(data.get("requests"), list):
            return dict(_EMPTY_STORE)
        return data
    except (json.JSONDecodeError, OSError):
        return dict(_EMPTY_STORE)


def _append_request(request: dict[str, Any]) -> None:
    with _GUIDANCE_LOCK:
        _ensure_file()
        store = _load_store()
        requests = list(store.get("requests") or [])
        requests.append(request)
        temp = _GUIDANCE_FILE.with_suffix(".json.tmp")
        try:
            with temp.open("w", encoding="utf-8") as f:
                json.dump({"requests": requests}, f, indent=2, ensure_ascii=False)
            temp.replace(_GUIDANCE_FILE)
        except Exception:
            logger.exception("Failed to write guidance requests file.")
            if temp.exists():
                temp.unlink(missing_ok=True)
            raise


# ---------------------------------------------------------------------------
# Request model
# ---------------------------------------------------------------------------

class GuidanceRequestPayload(BaseModel):
    requester_id: str
    target_region: str
    message: str
    guide_id: Optional[str] = None  # optional: request a specific guide

    @field_validator("message", mode="before")
    @classmethod
    def validate_message(cls, v: Any) -> str:
        text = str(v or "").strip()
        if len(text) < 5:
            raise ValueError("Message must be at least 5 characters.")
        if len(text) > 2000:
            raise ValueError("Message must not exceed 2000 characters.")
        return text

    @field_validator("target_region", mode="before")
    @classmethod
    def validate_region(cls, v: Any) -> str:
        region = str(v or "").strip().lower().replace(" ", "_")
        if not region:
            raise ValueError("target_region is required.")
        return region


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.post("/request")
def request_guidance(payload: GuidanceRequestPayload) -> dict[str, Any]:
    """
    Submit a cultural guidance request.

    Validates that the requester exists and is a living (non-ancestor) user.
    Stores the request in an append-only guidance log.
    Fires GUIDANCE_REQUEST_CREATED activity event.
    """
    # Validate requester exists
    all_users = [u.model_dump(mode="json") for u in registry.get_registrations()]
    requester = next((u for u in all_users if u.get("user_id") == payload.requester_id), None)
    if requester is None:
        raise HTTPException(status_code=404, detail=f"User '{payload.requester_id}' not found.")

    # Safety: ancestor/deceased records cannot request guidance
    if requester.get("ancestor_record") or requester.get("is_deceased"):
        raise HTTPException(
            status_code=400,
            detail="Ancestor/deceased records cannot submit guidance requests.",
        )
    if str(requester.get("living_status", "living")).lower() == "deceased":
        raise HTTPException(
            status_code=400,
            detail="Deceased users cannot submit guidance requests.",
        )

    # Must be opted into origin discovery to request guidance
    if not bool(requester.get("discoverable_by_origin_communities")):
        raise HTTPException(
            status_code=400,
            detail="Enable origin discovery before requesting guidance.",
        )

    # Validate guide_id if provided
    if payload.guide_id:
        guide = get_guide_by_id(payload.guide_id)
        if guide is None:
            raise HTTPException(status_code=404, detail=f"Guide '{payload.guide_id}' not found.")

    guidance_request: dict[str, Any] = {
        "request_id": f"gr_{uuid.uuid4().hex[:16]}",
        "requester_id": payload.requester_id,
        "target_region": payload.target_region,
        "guide_id": payload.guide_id,
        "message": payload.message,
        "status": "pending",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    _append_request(guidance_request)

    registry.write_activity_event(
        event_type="GUIDANCE_REQUEST_CREATED",
        message=(
            f"Guidance request submitted by {payload.requester_id} "
            f"for region '{payload.target_region}'."
        ),
        user_id=payload.requester_id,
        extra={
            "request_id": guidance_request["request_id"],
            "target_region": payload.target_region,
            "guide_id": payload.guide_id,
        },
    )

    logger.info(
        "Guidance request %s created for user %s → region %s",
        guidance_request["request_id"],
        payload.requester_id,
        payload.target_region,
    )

    return {
        "success": True,
        "request_id": guidance_request["request_id"],
        "status": "pending",
        "message": "Your guidance request has been submitted.",
    }


@router.get("/guides")
def list_guides(region: Optional[str] = None) -> list[dict[str, Any]]:
    """
    List available cultural guides, optionally filtered by region.
    """
    if region:
        return get_guides_for_region(region)
    return list_all_guides()


@router.get("/guides/{guide_id}")
def get_guide(guide_id: str) -> dict[str, Any]:
    """Return a single cultural guide profile by ID."""
    guide = get_guide_by_id(guide_id)
    if guide is None:
        raise HTTPException(status_code=404, detail=f"Guide '{guide_id}' not found.")
    return guide
