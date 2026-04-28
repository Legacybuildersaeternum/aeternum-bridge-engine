"""
Aeternum Bridge Engine — Phase 42: User Activation Engine.

Tracks user activation state (next-step checklist) and surfaces
"people like you" matching based on shared heritage + relocation intent.

Safety rules:
- Ancestor/deceased records are excluded from similar-user matching.
- No email, phone, or private contact info is exposed.
- Display names are masked: "Amara D." format.
"""
import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Name masking helper (mirrors origin_discovery)
# ---------------------------------------------------------------------------

def _mask_name(full_name: str) -> str:
    parts = (full_name or "").strip().split()
    if not parts:
        return "Anonymous"
    if len(parts) == 1:
        return parts[0]
    return parts[0] + " " + parts[-1][0].upper() + "."


# ---------------------------------------------------------------------------
# Activation status
# ---------------------------------------------------------------------------

def get_user_activation_status(
    user_id: str,
    *,
    users: list[dict[str, Any]],
    memberships: list[dict[str, Any]],
    connection_requests: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    Return the activation checklist state for a given user.

    Checklist items:
    1. has_cohort — user has joined at least one cohort
    2. has_origin_data — user has heritage_region or heritage_country set
    3. discovery_enabled — discoverable_by_origin_communities is True
    4. has_connections — user has at least one accepted connection
    5. has_family_members — user has at least one other member in their family_id group

    Returns dict with boolean flags plus family_member_count.
    """
    user = next((u for u in users if u.get("user_id") == user_id), None)
    if user is None:
        return {
            "user_id": user_id,
            "found": False,
            "has_cohort": False,
            "has_origin_data": False,
            "discovery_enabled": False,
            "has_connections": False,
            "has_family_members": False,
            "family_member_count": 0,
        }

    # 1. Has cohort
    active_memberships = [
        m for m in memberships
        if m.get("user_id") == user_id and m.get("status") != "left"
    ]
    has_cohort = len(active_memberships) > 0

    # 2. Has origin data
    heritage_region = str(user.get("heritage_region") or "").strip()
    heritage_country = str(user.get("heritage_country") or "").strip()
    origin_region = str(user.get("origin_region") or "").strip()
    has_origin_data = bool(
        heritage_region or heritage_country
        or (origin_region and origin_region.lower() not in {"", "unknown"})
    )

    # 3. Discovery enabled
    discovery_enabled = bool(user.get("discoverable_by_origin_communities"))

    # 4. Has connections — at least one accepted connection request involving this user
    _accepted = {"accepted", "connection_completed"}
    has_connections = any(
        cr.get("status") in _accepted
        and (cr.get("requester_user_id") == user_id or cr.get("requested_user_id") == user_id)
        for cr in connection_requests
    )

    # 5. Has family members — others sharing same family_id
    family_id = user.get("family_id")
    if family_id:
        family_members = [
            u for u in users
            if u.get("family_id") == family_id and u.get("user_id") != user_id
        ]
    else:
        family_members = []
    family_member_count = len(family_members)
    has_family_members = family_member_count > 0

    return {
        "user_id": user_id,
        "found": True,
        "has_cohort": has_cohort,
        "has_origin_data": has_origin_data,
        "discovery_enabled": discovery_enabled,
        "has_connections": has_connections,
        "has_family_members": has_family_members,
        "family_member_count": family_member_count,
    }


# ---------------------------------------------------------------------------
# Similar users ("People Like You")
# ---------------------------------------------------------------------------

_RELOCATION_TIERS: dict[str, int] = {
    "considering": 1,
    "planning": 2,
    "active": 3,
}


def get_similar_users(
    user_id: str,
    *,
    users: list[dict[str, Any]],
    max_results: int = 5,
) -> dict[str, Any]:
    """
    Return masked profiles of users who share heritage region/country or
    relocation intent with the given user.

    Match criteria (ANY of the following):
    - Same heritage_region
    - Same heritage_country
    - Similar relocation_interest_level (any of: considering/planning/active)

    Safety:
    - Excludes ancestor/deceased records.
    - Excludes users with discoverable_by_origin_communities = False
      (we only surface opted-in users).
    - No email, phone, or full names.
    - Display name masked as "Firstname L."
    """
    requester = next((u for u in users if u.get("user_id") == user_id), None)
    if requester is None:
        return {"user_id": user_id, "count": 0, "profiles": [], "relocation_matches": 0}

    r_heritage_region = str(requester.get("heritage_region") or "").strip().lower()
    r_heritage_country = str(requester.get("heritage_country") or "").strip().lower()
    r_relocation = str(requester.get("relocation_interest_level") or "").strip().lower()
    r_has_relocation = r_relocation in _RELOCATION_TIERS

    profiles: list[dict[str, Any]] = []
    relocation_match_count = 0

    for user in users:
        if user.get("user_id") == user_id:
            continue

        # Safety: exclude ancestor/deceased
        if user.get("ancestor_record") or user.get("is_deceased"):
            continue
        if str(user.get("living_status", "living")).lower() == "deceased":
            continue

        # Only opted-in users appear in similarity results
        if not user.get("discoverable_by_origin_communities"):
            continue

        u_heritage_region = str(user.get("heritage_region") or "").strip().lower()
        u_heritage_country = str(user.get("heritage_country") or "").strip().lower()
        u_relocation = str(user.get("relocation_interest_level") or "").strip().lower()

        matched_region = bool(r_heritage_region and r_heritage_region == u_heritage_region)
        matched_country = bool(r_heritage_country and r_heritage_country == u_heritage_country)
        matched_relocation = bool(r_has_relocation and u_relocation in _RELOCATION_TIERS)

        if not (matched_region or matched_country or matched_relocation):
            continue

        if matched_relocation:
            relocation_match_count += 1

        profiles.append({
            "user_id": user.get("user_id"),
            "display_name": _mask_name(str(user.get("full_name") or "")),
            "heritage_region": user.get("heritage_region") or user.get("origin_region"),
            "heritage_country": user.get("heritage_country"),
            "heritage_group": user.get("heritage_group"),
            "relocation_interest_level": user.get("relocation_interest_level"),
            "current_country": user.get("current_country") or user.get("country"),
            "current_city": user.get("current_city") or user.get("city"),
            "trust_score": int(user.get("trust_score") or 0),
            "verification_level": str(user.get("verification_level") or "UNVERIFIED"),
            "match_reason": (
                "shared_origin" if (matched_region or matched_country)
                else "shared_relocation_intent"
            ),
        })

        if len(profiles) >= max_results:
            break

    # Count all matching users even if capped in profiles list
    all_count = sum(
        1 for user in users
        if user.get("user_id") != user_id
        and not (user.get("ancestor_record") or user.get("is_deceased"))
        and str(user.get("living_status", "living")).lower() != "deceased"
        and user.get("discoverable_by_origin_communities")
        and (
            (r_heritage_region and r_heritage_region == str(user.get("heritage_region") or "").strip().lower())
            or (r_heritage_country and r_heritage_country == str(user.get("heritage_country") or "").strip().lower())
            or (r_has_relocation and str(user.get("relocation_interest_level") or "").strip().lower() in _RELOCATION_TIERS)
        )
    )

    return {
        "user_id": user_id,
        "count": all_count,
        "relocation_matches": relocation_match_count,
        "profiles": profiles,
    }
