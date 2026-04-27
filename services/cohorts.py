"""
Aeternum Bridge Engine — Phase 40: Cohort Engine Service.

Cohorts are structured groups allowing users to connect, communicate, and move
together based on shared origin, heritage, or intent.

Persistence: data/cohorts.json  (append-only memberships, no deletion)
"""
import json
import logging
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

COHORTS_FILE = Path(__file__).resolve().parents[1] / "data" / "cohorts.json"
_COHORTS_LOCK = threading.RLock()

_EMPTY_STORE: dict[str, Any] = {"cohorts": [], "memberships": []}

# ---------------------------------------------------------------------------
# Seed data — auto-created on first boot if not already present
# ---------------------------------------------------------------------------

_SEED_COHORTS: list[dict[str, Any]] = [
    {
        "cohort_id": "cohort_heritage_discovery",
        "name": "Heritage Discovery Cohort",
        "type": "unknown_guided",
        "origin_region": None,
        "description": (
            "A welcoming community for members whose ancestral origins are unknown or uncertain. "
            "Explore shared heritage, participate in guided discovery tools, and connect with others "
            "on a similar journey."
        ),
    },
    {
        "cohort_id": "cohort_west_africa",
        "name": "West Africa Reconnection",
        "type": "region",
        "origin_region": "west_africa",
        "description": (
            "For members with roots in West Africa — Nigeria, Ghana, Senegal, Sierra Leone, and beyond. "
            "Share culture, history, and reconnection resources."
        ),
    },
    {
        "cohort_id": "cohort_east_africa",
        "name": "East Africa Reconnection",
        "type": "region",
        "origin_region": "east_africa",
        "description": (
            "Connecting members with heritage in Kenya, Ethiopia, Tanzania, Uganda, and the wider "
            "East African region."
        ),
    },
    {
        "cohort_id": "cohort_caribbean_roots",
        "name": "Caribbean Roots Cohort",
        "type": "region",
        "origin_region": "caribbean",
        "description": (
            "A space for diaspora members tracing lineage through the Caribbean — Jamaica, Trinidad, "
            "Barbados, Haiti, and beyond. Explore shared African and creole heritage."
        ),
    },
    {
        "cohort_id": "cohort_global_relocation",
        "name": "Global Relocation Cohort",
        "type": "intent",
        "origin_region": None,
        "description": (
            "For members actively considering or planning relocation to ancestral regions or new "
            "heritage-aligned destinations. Share resources, timelines, and community support."
        ),
    },
]


# ---------------------------------------------------------------------------
# Persistence helpers
# ---------------------------------------------------------------------------

def _ensure_file() -> None:
    COHORTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not COHORTS_FILE.exists():
        now = datetime.now(timezone.utc).isoformat()
        seeded = [
            {**c, "created_at": now}
            for c in _SEED_COHORTS
        ]
        with COHORTS_FILE.open("w", encoding="utf-8") as f:
            json.dump({"cohorts": seeded, "memberships": []}, f, indent=2)
        logger.info("Created cohorts file with %d seeded cohorts at %s", len(seeded), COHORTS_FILE)


def _load_store() -> dict[str, Any]:
    _ensure_file()
    try:
        with COHORTS_FILE.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            logger.warning("Cohorts file invalid shape; resetting.")
            return {"cohorts": [], "memberships": []}
        if not isinstance(data.get("cohorts"), list):
            data["cohorts"] = []
        if not isinstance(data.get("memberships"), list):
            data["memberships"] = []
        return data
    except (json.JSONDecodeError, OSError):
        logger.warning("Cohorts file unreadable; returning empty store.")
        return {"cohorts": [], "memberships": []}


def _write_store(store: dict[str, Any]) -> None:
    temp = COHORTS_FILE.with_suffix(".json.tmp")
    try:
        with temp.open("w", encoding="utf-8") as f:
            json.dump(store, f, indent=2, ensure_ascii=False)
        temp.replace(COHORTS_FILE)
    except Exception:
        logger.exception("Failed to write cohorts file.")
        if temp.exists():
            temp.unlink(missing_ok=True)
        raise


# ---------------------------------------------------------------------------
# Cohort CRUD
# ---------------------------------------------------------------------------

def create_cohort(
    name: str,
    cohort_type: str,
    description: str,
    origin_region: Optional[str] = None,
) -> dict[str, Any]:
    """Create a new cohort. Returns the created cohort record."""
    with _COHORTS_LOCK:
        store = _load_store()
        cohort: dict[str, Any] = {
            "cohort_id": f"cohort_{uuid.uuid4().hex[:16]}",
            "name": name.strip(),
            "type": cohort_type.strip(),
            "origin_region": origin_region.strip() if origin_region else None,
            "description": description.strip(),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        store["cohorts"].append(cohort)
        _write_store(store)
        logger.info("Cohort created: %s (%s)", cohort["cohort_id"], name)
        return cohort


def list_cohorts() -> list[dict[str, Any]]:
    """Return all cohorts with enriched member count."""
    with _COHORTS_LOCK:
        store = _load_store()
        cohorts = list(store.get("cohorts") or [])
        memberships = list(store.get("memberships") or [])
    member_counts: dict[str, int] = {}
    for m in memberships:
        cid = m.get("cohort_id", "")
        member_counts[cid] = member_counts.get(cid, 0) + 1
    for c in cohorts:
        c["member_count"] = member_counts.get(c["cohort_id"], 0)
    return cohorts


def get_cohort(cohort_id: str) -> Optional[dict[str, Any]]:
    """Return a single cohort or None."""
    cohorts = list_cohorts()
    return next((c for c in cohorts if c["cohort_id"] == cohort_id), None)


# ---------------------------------------------------------------------------
# Membership
# ---------------------------------------------------------------------------

def join_cohort(user_id: str, cohort_id: str) -> dict[str, Any]:
    """
    Add user to cohort. Raises ValueError if cohort not found or user already a member.
    Returns the new membership record.
    """
    with _COHORTS_LOCK:
        store = _load_store()
        # Validate cohort exists
        cohort = next((c for c in store["cohorts"] if c["cohort_id"] == cohort_id), None)
        if cohort is None:
            raise ValueError(f"Cohort '{cohort_id}' not found.")
        # Prevent duplicate memberships
        existing = next(
            (m for m in store["memberships"] if m["user_id"] == user_id and m["cohort_id"] == cohort_id),
            None,
        )
        if existing:
            raise ValueError(f"User '{user_id}' is already a member of cohort '{cohort_id}'.")
        membership: dict[str, Any] = {
            "membership_id": f"mbr_{uuid.uuid4().hex[:16]}",
            "user_id": user_id,
            "cohort_id": cohort_id,
            "role": "member",
            "joined_at": datetime.now(timezone.utc).isoformat(),
        }
        store["memberships"].append(membership)
        _write_store(store)
        logger.info("User %s joined cohort %s", user_id, cohort_id)
        return membership


def leave_cohort(user_id: str, cohort_id: str) -> bool:
    """
    Mark membership as left (append-only: adds a leave record rather than deleting).
    Returns True if membership was found and updated, False otherwise.
    """
    with _COHORTS_LOCK:
        store = _load_store()
        found = False
        for m in store["memberships"]:
            if m["user_id"] == user_id and m["cohort_id"] == cohort_id and m.get("status") != "left":
                m["status"] = "left"
                m["left_at"] = datetime.now(timezone.utc).isoformat()
                found = True
                break
        if found:
            _write_store(store)
            logger.info("User %s left cohort %s", user_id, cohort_id)
        return found


def get_user_cohorts(user_id: str) -> list[dict[str, Any]]:
    """Return all active cohorts a user is a member of."""
    with _COHORTS_LOCK:
        store = _load_store()
        memberships = [
            m for m in store.get("memberships", [])
            if m["user_id"] == user_id and m.get("status") != "left"
        ]
        cohort_ids = {m["cohort_id"] for m in memberships}
        cohorts = [c for c in store.get("cohorts", []) if c["cohort_id"] in cohort_ids]
    return cohorts


def get_cohort_members(cohort_id: str) -> list[dict[str, Any]]:
    """Return all active memberships for a cohort."""
    with _COHORTS_LOCK:
        store = _load_store()
        return [
            m for m in store.get("memberships", [])
            if m["cohort_id"] == cohort_id and m.get("status") != "left"
        ]


def suggest_cohorts_for_user(
    origin_region: Optional[str],
    return_reconnection_interest: Optional[str],
) -> list[dict[str, Any]]:
    """
    Return suggested cohort IDs based on user profile.
    Used by the frontend 'Suggested For You' block.
    """
    all_cohorts = list_cohorts()
    suggestions: list[dict[str, Any]] = []

    # Unknown origin → Heritage Discovery
    if not origin_region or origin_region.lower() == "unknown":
        hd = next((c for c in all_cohorts if c["cohort_id"] == "cohort_heritage_discovery"), None)
        if hd:
            suggestions.append(hd)

    # Region match
    if origin_region and origin_region.lower() != "unknown":
        for c in all_cohorts:
            if c.get("type") == "region" and c.get("origin_region", "").lower() == origin_region.lower():
                if c not in suggestions:
                    suggestions.append(c)

    # Intent-based: relocation interest
    relocation_values = {"no_documenting_family_history", "connect_similar_origin", "document_family_history_only"}
    if return_reconnection_interest and return_reconnection_interest not in relocation_values:
        reloc = next((c for c in all_cohorts if c["cohort_id"] == "cohort_global_relocation"), None)
        if reloc and reloc not in suggestions:
            suggestions.append(reloc)

    return suggestions


# ---------------------------------------------------------------------------
# Stats helpers
# ---------------------------------------------------------------------------

def get_total_cohorts_count() -> int:
    with _COHORTS_LOCK:
        store = _load_store()
        return len(store.get("cohorts", []))


def get_total_memberships_count() -> int:
    with _COHORTS_LOCK:
        store = _load_store()
        return sum(1 for m in store.get("memberships", []) if m.get("status") != "left")


def get_most_active_cohort() -> Optional[dict[str, Any]]:
    """Return the cohort with most active memberships."""
    cohorts = list_cohorts()
    if not cohorts:
        return None
    return max(cohorts, key=lambda c: c.get("member_count", 0))


# ---------------------------------------------------------------------------
# Cohort messaging (Phase 40 light — reuses messages.py structure)
# ---------------------------------------------------------------------------

COHORT_MESSAGES_FILE = Path(__file__).resolve().parents[1] / "data" / "cohort_messages.json"
_COHORT_MSG_LOCK = threading.RLock()
_EMPTY_COHORT_MSG_STORE: dict[str, Any] = {"messages": []}


def _ensure_cohort_msg_file() -> None:
    COHORT_MESSAGES_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not COHORT_MESSAGES_FILE.exists():
        with COHORT_MESSAGES_FILE.open("w", encoding="utf-8") as f:
            json.dump(_EMPTY_COHORT_MSG_STORE, f, indent=2)


def _load_cohort_messages() -> list[dict[str, Any]]:
    _ensure_cohort_msg_file()
    try:
        with COHORT_MESSAGES_FILE.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return list(data.get("messages") or []) if isinstance(data, dict) else []
    except (json.JSONDecodeError, OSError):
        return []


def send_cohort_message(user_id: str, cohort_id: str, message_text: str) -> dict[str, Any]:
    """
    Send a broadcast message to a cohort.
    User must be an active member.
    Raises ValueError if user is not a member or message is empty.
    """
    text = (message_text or "").strip()
    if not text:
        raise ValueError("Message text cannot be empty.")
    if len(text) > 2000:
        raise ValueError("Message text exceeds 2000 characters.")

    with _COHORT_MSG_LOCK:
        # Verify membership
        members = get_cohort_members(cohort_id)
        if not any(m["user_id"] == user_id for m in members):
            raise ValueError(f"User '{user_id}' is not an active member of cohort '{cohort_id}'.")

        _ensure_cohort_msg_file()
        messages = _load_cohort_messages()
        msg: dict[str, Any] = {
            "message_id": f"cmsg_{uuid.uuid4().hex[:16]}",
            "cohort_id": cohort_id,
            "sender_id": user_id,
            "message_text": text,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        messages.append(msg)
        temp = COHORT_MESSAGES_FILE.with_suffix(".json.tmp")
        try:
            with temp.open("w", encoding="utf-8") as f:
                json.dump({"messages": messages}, f, indent=2, ensure_ascii=False)
            temp.replace(COHORT_MESSAGES_FILE)
        except Exception:
            logger.exception("Failed to write cohort messages file.")
            if temp.exists():
                temp.unlink(missing_ok=True)
            raise
        return msg


def get_cohort_messages(cohort_id: str, limit: int = 50) -> list[dict[str, Any]]:
    """Return recent messages for a cohort, newest-last (ascending)."""
    all_msgs = _load_cohort_messages()
    cohort_msgs = [m for m in all_msgs if m.get("cohort_id") == cohort_id]
    return cohort_msgs[-limit:]
