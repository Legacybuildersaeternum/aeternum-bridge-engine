"""Phase 44 — Real World Movement Engine service layer."""

import json
import logging
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

MOVEMENTS_FILE = Path(__file__).resolve().parents[1] / "data" / "movements.json"
_MOVEMENTS_LOCK = threading.RLock()


def _ensure_file() -> None:
    MOVEMENTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not MOVEMENTS_FILE.exists():
        with MOVEMENTS_FILE.open("w", encoding="utf-8") as f:
            json.dump([], f, indent=2)


def _load_movements() -> list[dict[str, Any]]:
    _ensure_file()
    try:
        with MOVEMENTS_FILE.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
    except (json.JSONDecodeError, OSError):
        logger.warning("Movements file unreadable. Returning empty list.")
    return []


def _write_movements(movements: list[dict[str, Any]]) -> None:
    temp = MOVEMENTS_FILE.with_suffix(".json.tmp")
    try:
        with temp.open("w", encoding="utf-8") as f:
            json.dump(movements, f, indent=2, ensure_ascii=False)
        temp.replace(MOVEMENTS_FILE)
    except Exception:
        logger.exception("Failed writing movements file")
        if temp.exists():
            temp.unlink(missing_ok=True)
        raise


def list_movements() -> list[dict[str, Any]]:
    with _MOVEMENTS_LOCK:
        movements = _load_movements()
    for m in movements:
        members = m.get("members") if isinstance(m.get("members"), list) else []
        m["member_count"] = len(members)
    movements.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
    return movements


def create_movement(
    user_id: str,
    title: str,
    region: str,
    country: str,
    target_date: str,
) -> dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    movement = {
        "movement_id": "mov_" + uuid.uuid4().hex[:12],
        "title": str(title or "").strip(),
        "region": str(region or "").strip(),
        "country": str(country or "").strip(),
        "created_by": str(user_id or "").strip(),
        "created_at": now,
        "target_date": str(target_date or "").strip(),
        "status": "active",
        "members": [
            {
                "user_id": str(user_id or "").strip(),
                "role": "leader",
                "movement_status": "planning",
                "joined_at": now,
            }
        ],
    }
    with _MOVEMENTS_LOCK:
        movements = _load_movements()
        movements.append(movement)
        _write_movements(movements)
    return movement


def join_movement(user_id: str, movement_id: str) -> dict[str, Any]:
    user_id = str(user_id or "").strip()
    movement_id = str(movement_id or "").strip()
    if not user_id or not movement_id:
        raise ValueError("user_id and movement_id are required")

    with _MOVEMENTS_LOCK:
        movements = _load_movements()
        target = next((m for m in movements if str(m.get("movement_id") or "") == movement_id), None)
        if not target:
            raise ValueError("Movement not found")
        members = target.get("members") if isinstance(target.get("members"), list) else []
        if any(str(member.get("user_id") or "") == user_id for member in members if isinstance(member, dict)):
            raise ValueError("User already joined this movement")

        member = {
            "user_id": user_id,
            "role": "member",
            "movement_status": "interested",
            "joined_at": datetime.now(timezone.utc).isoformat(),
        }
        members.append(member)
        target["members"] = members
        _write_movements(movements)
        return member


def update_movement_status(user_id: str, movement_id: str, status: str) -> dict[str, Any]:
    user_id = str(user_id or "").strip()
    movement_id = str(movement_id or "").strip()
    status = str(status or "").strip().lower()
    if status not in {"interested", "planning", "committed", "relocated"}:
        raise ValueError("status must be one of: interested, planning, committed, relocated")

    with _MOVEMENTS_LOCK:
        movements = _load_movements()
        target = next((m for m in movements if str(m.get("movement_id") or "") == movement_id), None)
        if not target:
            raise ValueError("Movement not found")
        members = target.get("members") if isinstance(target.get("members"), list) else []
        member = next((m for m in members if str(m.get("user_id") or "") == user_id), None)
        if not member:
            raise ValueError("User is not a member of this movement")
        member["movement_status"] = status
        _write_movements(movements)
        return member


def assign_role(user_id: str, movement_id: str, role: str) -> dict[str, Any]:
    user_id = str(user_id or "").strip()
    movement_id = str(movement_id or "").strip()
    role = str(role or "").strip().lower()
    if role not in {"leader", "coordinator", "member"}:
        raise ValueError("role must be one of: leader, coordinator, member")

    with _MOVEMENTS_LOCK:
        movements = _load_movements()
        target = next((m for m in movements if str(m.get("movement_id") or "") == movement_id), None)
        if not target:
            raise ValueError("Movement not found")
        members = target.get("members") if isinstance(target.get("members"), list) else []
        member = next((m for m in members if str(m.get("user_id") or "") == user_id), None)
        if not member:
            raise ValueError("User is not a member of this movement")
        member["role"] = role
        _write_movements(movements)
        return member


def get_user_movements(user_id: str) -> list[dict[str, Any]]:
    user_id = str(user_id or "").strip()
    if not user_id:
        return []
    movements = list_movements()
    result: list[dict[str, Any]] = []
    for movement in movements:
        members = movement.get("members") if isinstance(movement.get("members"), list) else []
        member = next((m for m in members if str(m.get("user_id") or "") == user_id), None)
        if member:
            row = dict(movement)
            row["my_membership"] = member
            result.append(row)
    return result
