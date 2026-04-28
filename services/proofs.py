"""Phase 45 - Real World Proof persistence and review service."""

import json
import logging
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from services import movements
from services import registry

logger = logging.getLogger(__name__)

PROOFS_FILE = Path(__file__).resolve().parents[1] / "data" / "proof_submissions.json"
_PROOFS_LOCK = threading.RLock()

_ALLOWED_STATUSES = {"pending", "accepted", "rejected", "needs_info"}
_ALLOWED_DECISIONS = {"accepted", "rejected", "needs_info"}
_DOCUMENT_PROOF_TYPES = {
    "document_upload_future",
    "government_id",
    "passport",
    "residency_document",
}


def _ensure_file() -> None:
    PROOFS_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not PROOFS_FILE.exists():
        with PROOFS_FILE.open("w", encoding="utf-8") as f:
            json.dump([], f, indent=2)


def _load_unlocked() -> list[dict[str, Any]]:
    _ensure_file()
    try:
        with PROOFS_FILE.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
    except (json.JSONDecodeError, OSError):
        logger.warning("Proof submissions file unreadable. Returning empty list.")
    return []


def _write_unlocked(records: list[dict[str, Any]]) -> None:
    temp = PROOFS_FILE.with_suffix(".json.tmp")
    try:
        with temp.open("w", encoding="utf-8") as f:
            json.dump(records, f, indent=2, ensure_ascii=False)
        temp.replace(PROOFS_FILE)
    except Exception:
        logger.exception("Failed writing proof submissions file")
        if temp.exists():
            temp.unlink(missing_ok=True)
        raise


def _normalize_status(status: Optional[str]) -> str:
    value = str(status or "pending").strip().lower()
    return value if value in _ALLOWED_STATUSES else "pending"


def _normalize_proof_type(proof_type: Optional[str]) -> str:
    return str(proof_type or "").strip().lower()


def _normalize_record(record: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(record)
    normalized["proof_id"] = str(normalized.get("proof_id") or "")
    normalized["user_id"] = str(normalized.get("user_id") or "")
    normalized["movement_id"] = str(normalized.get("movement_id") or "")
    normalized["proof_type"] = _normalize_proof_type(normalized.get("proof_type"))
    normalized["public_summary"] = str(normalized.get("public_summary") or "")
    normalized["private_notes"] = str(normalized.get("private_notes") or "")
    normalized["status"] = _normalize_status(normalized.get("status"))
    normalized["review_public_note"] = str(normalized.get("review_public_note") or "")
    normalized["admin_private_review_note"] = str(normalized.get("admin_private_review_note") or "")
    normalized["submitted_at"] = str(normalized.get("submitted_at") or datetime.now(timezone.utc).isoformat())
    normalized["reviewed_at"] = str(normalized.get("reviewed_at") or "")
    normalized["reviewed_by"] = str(normalized.get("reviewed_by") or "")
    return normalized


def _is_user_eligible_for_proof(user: dict[str, Any]) -> bool:
    living_status = str(user.get("living_status") or "").strip().lower()
    if bool(user.get("ancestor_record")):
        return False
    if living_status == "deceased":
        return False
    return True


def _get_user_record(user_id: str) -> dict[str, Any]:
    users = [u.model_dump(mode="json") for u in registry.get_registrations()]
    user = next((item for item in users if str(item.get("user_id") or "") == user_id), None)
    if not user:
        raise ValueError("User not found")
    return user


def _get_movement_record(movement_id: str) -> dict[str, Any]:
    movement = next(
        (item for item in movements.list_movements() if str(item.get("movement_id") or "") == movement_id),
        None,
    )
    if not movement:
        raise ValueError("Movement not found")
    return movement


def _assert_membership(user_id: str, movement: dict[str, Any]) -> None:
    members = movement.get("members") if isinstance(movement.get("members"), list) else []
    is_member = any(str(member.get("user_id") or "") == user_id for member in members if isinstance(member, dict))
    if not is_member:
        raise ValueError("User must join the movement before submitting proof")


def is_document_proof_type(proof_type: str) -> bool:
    return _normalize_proof_type(proof_type) in _DOCUMENT_PROOF_TYPES


def to_user_view(record: dict[str, Any]) -> dict[str, Any]:
    row = _normalize_record(record)
    row.pop("private_notes", None)
    row.pop("admin_private_review_note", None)
    return row


def list_proofs(
    *,
    status: Optional[str] = None,
    user_id: Optional[str] = None,
    movement_id: Optional[str] = None,
    admin_view: bool = True,
) -> list[dict[str, Any]]:
    normalized_status = _normalize_status(status) if status else None
    normalized_user_id = str(user_id or "").strip()
    normalized_movement_id = str(movement_id or "").strip()

    with _PROOFS_LOCK:
        records = [_normalize_record(item) for item in _load_unlocked()]

    filtered: list[dict[str, Any]] = []
    for item in records:
        if normalized_status and str(item.get("status") or "") != normalized_status:
            continue
        if normalized_user_id and str(item.get("user_id") or "") != normalized_user_id:
            continue
        if normalized_movement_id and str(item.get("movement_id") or "") != normalized_movement_id:
            continue
        filtered.append(item if admin_view else to_user_view(item))

    filtered.sort(key=lambda row: str(row.get("submitted_at") or ""), reverse=True)
    return filtered


def submit_proof(
    *,
    user_id: str,
    movement_id: str,
    proof_type: str,
    public_summary: str,
    private_notes: Optional[str] = None,
) -> dict[str, Any]:
    normalized_user_id = str(user_id or "").strip()
    normalized_movement_id = str(movement_id or "").strip()
    normalized_proof_type = _normalize_proof_type(proof_type)
    summary = str(public_summary or "").strip()
    private = str(private_notes or "").strip()

    if not normalized_user_id or not normalized_movement_id:
        raise ValueError("user_id and movement_id are required")
    if not normalized_proof_type:
        raise ValueError("proof_type is required")
    if not summary:
        raise ValueError("public_summary is required")

    user = _get_user_record(normalized_user_id)
    if not _is_user_eligible_for_proof(user):
        raise ValueError("Ancestor/deceased profiles cannot submit real-world proof")

    movement = _get_movement_record(normalized_movement_id)
    _assert_membership(normalized_user_id, movement)

    now = datetime.now(timezone.utc).isoformat()
    record = {
        "proof_id": f"prf_{uuid.uuid4().hex[:12]}",
        "user_id": normalized_user_id,
        "movement_id": normalized_movement_id,
        "proof_type": normalized_proof_type,
        "public_summary": summary,
        "private_notes": private,
        "status": "pending",
        "review_public_note": "",
        "admin_private_review_note": "",
        "submitted_at": now,
        "reviewed_at": "",
        "reviewed_by": "",
    }

    with _PROOFS_LOCK:
        records = [_normalize_record(item) for item in _load_unlocked()]
        records.append(record)
        _write_unlocked(records)

    return _normalize_record(record)


def review_proof(
    *,
    proof_id: str,
    decision: str,
    reviewer_user_id: Optional[str] = None,
    review_public_note: Optional[str] = None,
    review_private_note: Optional[str] = None,
) -> dict[str, Any]:
    normalized_proof_id = str(proof_id or "").strip()
    normalized_decision = str(decision or "").strip().lower()
    if normalized_decision not in _ALLOWED_DECISIONS:
        raise ValueError("decision must be one of: accepted, rejected, needs_info")

    with _PROOFS_LOCK:
        records = [_normalize_record(item) for item in _load_unlocked()]
        index = next(
            (idx for idx, item in enumerate(records) if str(item.get("proof_id") or "") == normalized_proof_id),
            -1,
        )
        if index == -1:
            raise ValueError("Proof submission not found")

        target = dict(records[index])
        target["status"] = normalized_decision
        target["reviewed_at"] = datetime.now(timezone.utc).isoformat()
        target["reviewed_by"] = str(reviewer_user_id or "").strip()
        if review_public_note is not None:
            target["review_public_note"] = str(review_public_note or "").strip()
        if review_private_note is not None:
            target["admin_private_review_note"] = str(review_private_note or "").strip()

        records[index] = _normalize_record(target)
        _write_unlocked(records)
        return records[index]


def get_accepted_proof_count_for_movement(movement_id: str) -> int:
    normalized_movement_id = str(movement_id or "").strip()
    if not normalized_movement_id:
        return 0
    with _PROOFS_LOCK:
        records = [_normalize_record(item) for item in _load_unlocked()]
    return sum(
        1
        for item in records
        if str(item.get("movement_id") or "") == normalized_movement_id
        and str(item.get("status") or "") == "accepted"
    )


def get_user_accepted_proof_stats(user_id: str) -> dict[str, int]:
    normalized_user_id = str(user_id or "").strip()
    if not normalized_user_id:
        return {"accepted_total": 0, "accepted_document": 0}

    with _PROOFS_LOCK:
        records = [_normalize_record(item) for item in _load_unlocked()]

    accepted = [
        item
        for item in records
        if str(item.get("user_id") or "") == normalized_user_id
        and str(item.get("status") or "") == "accepted"
    ]
    doc_count = sum(1 for item in accepted if is_document_proof_type(str(item.get("proof_type") or "")))
    return {
        "accepted_total": len(accepted),
        "accepted_document": doc_count,
    }
