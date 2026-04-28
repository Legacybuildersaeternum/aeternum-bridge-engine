"""
Aeternum Bridge Engine — Phase 41: Africa Can Find You / Origin Discovery Service.

Enables diaspora users who have opted in to be discoverable by origin communities,
cultural guides, or cohort members with matching heritage context.

Safety rules (Phase 41):
- Only living, opted-in users are returned.
- Ancestor/deceased records are NEVER included.
- No email, phone, or private contact info is exposed.
- Display names are masked: "Amara D." format.
- Users must explicitly set discoverable_by_origin_communities = True.
"""

import logging
from typing import Any, Optional

from services.guides import get_guides_for_region

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Country → cohort/region mapping helpers
# ---------------------------------------------------------------------------

_COUNTRY_TO_REGION: dict[str, str] = {
    "nigeria": "west_africa",
    "ghana": "west_africa",
    "senegal": "west_africa",
    "mali": "west_africa",
    "guinea": "west_africa",
    "ivory coast": "west_africa",
    "cote d'ivoire": "west_africa",
    "liberia": "west_africa",
    "sierra leone": "west_africa",
    "benin": "west_africa",
    "togo": "west_africa",
    "gambia": "west_africa",
    "kenya": "east_africa",
    "ethiopia": "east_africa",
    "tanzania": "east_africa",
    "uganda": "east_africa",
    "somalia": "east_africa",
    "rwanda": "east_africa",
    "burundi": "east_africa",
    "eritrea": "east_africa",
    "djibouti": "east_africa",
    "south africa": "southern_africa",
    "zimbabwe": "southern_africa",
    "zambia": "southern_africa",
    "mozambique": "southern_africa",
    "botswana": "southern_africa",
    "namibia": "southern_africa",
    "angola": "southern_africa",
    "dr congo": "central_africa",
    "congo": "central_africa",
    "cameroon": "central_africa",
    "chad": "central_africa",
    "central african republic": "central_africa",
    "equatorial guinea": "central_africa",
    "gabon": "central_africa",
}

_CARIBBEAN_COUNTRIES = {
    "jamaica", "haiti", "trinidad", "trinidad and tobago", "barbados",
    "bahamas", "dominican republic", "cuba", "guyana", "belize",
}


# ---------------------------------------------------------------------------
# Name masking
# ---------------------------------------------------------------------------

def _mask_name(full_name: str) -> str:
    """Return first name + masked last initial: 'Amara D.'"""
    parts = (full_name or "").strip().split()
    if not parts:
        return "Anonymous"
    if len(parts) == 1:
        return parts[0]
    return parts[0] + " " + parts[-1][0].upper() + "."


# ---------------------------------------------------------------------------
# Heritage region resolution (heritage_country → implied region)
# ---------------------------------------------------------------------------

def _resolve_heritage_region(user: dict[str, Any]) -> Optional[str]:
    """Derive heritage region from explicit field or heritage_country mapping."""
    region = str(user.get("heritage_region") or "").strip().lower()
    if region:
        return region
    country = str(user.get("heritage_country") or "").strip().lower()
    if country:
        return _COUNTRY_TO_REGION.get(country)
    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def find_diaspora_profiles_for_origin(
    origin_region: Optional[str] = None,
    origin_country: Optional[str] = None,
    heritage_group: Optional[str] = None,
    requester_user: Optional[dict[str, Any]] = None,
    *,
    users: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """
    Return diaspora users discoverable by origin communities.

    Matching rules (ANY of the following qualifies a match):
    - origin_region matches user's heritage_region or resolved region from heritage_country
    - origin_country matches user's heritage_country (case-insensitive)
    - heritage_group matches user's heritage_group (case-insensitive)

    If no filters are supplied, all opted-in living users are returned.

    Safety:
    - Only living users (not ancestor_record, not deceased).
    - Only users with discoverable_by_origin_communities = True.
    - No email, phone, or private contact info in response.
    - Display name is masked.
    """
    origin_region_norm = str(origin_region or "").strip().lower() or None
    origin_country_norm = str(origin_country or "").strip().lower() or None
    heritage_group_norm = str(heritage_group or "").strip().lower() or None
    has_filter = bool(origin_region_norm or origin_country_norm or heritage_group_norm)

    results: list[dict[str, Any]] = []

    for user in users:
        if not isinstance(user, dict):
            continue

        # Safety: exclude ancestor/deceased records
        if user.get("ancestor_record") or user.get("is_deceased"):
            continue
        if str(user.get("living_status", "living")).lower() == "deceased":
            continue

        # Must be opted in
        if not user.get("discoverable_by_origin_communities"):
            continue

        # Match logic
        matched = not has_filter  # if no filter → return all opted-in living users
        if not matched:
            user_heritage_region = _resolve_heritage_region(user)
            user_heritage_country = str(user.get("heritage_country") or "").strip().lower()
            user_heritage_group = str(user.get("heritage_group") or "").strip().lower()

            if origin_region_norm and user_heritage_region and origin_region_norm == user_heritage_region:
                matched = True
            if origin_country_norm and user_heritage_country and origin_country_norm == user_heritage_country:
                matched = True
            if heritage_group_norm and user_heritage_group and heritage_group_norm in user_heritage_group:
                matched = True

        if not matched:
            continue

        # Build safe masked response — no PII
        result: dict[str, Any] = {
            "user_id": user.get("user_id"),
            "display_name": _mask_name(str(user.get("full_name") or "")),
            "current_country": user.get("current_country") or user.get("country"),
            "current_region": user.get("current_state_region") or user.get("state"),
            "current_city": user.get("current_city") or user.get("city"),
            "heritage_region": user.get("heritage_region") or user.get("origin_region"),
            "heritage_country": user.get("heritage_country"),
            "heritage_group": user.get("heritage_group"),
            "relocation_interest_level": user.get("relocation_interest_level"),
            "open_to_cultural_guides": bool(user.get("open_to_cultural_guides")),
            "open_to_relocation_guidance": bool(user.get("open_to_relocation_guidance")),
            "preferred_contact_scope": user.get("preferred_contact_scope", "private"),
            "verification_status": user.get("verification_status", "family_submitted"),
            "trust_score": int(user.get("trust_score") or 0),
            "verification_level": str(user.get("verification_level") or "UNVERIFIED"),
        }
        results.append(result)

    # Phase 42: Include cultural guides only for users who explicitly opted-in.
    if requester_user and bool(requester_user.get("open_to_cultural_guides")):
        target_region = origin_region_norm or _resolve_heritage_region(requester_user)
        for guide in get_guides_for_region(target_region):
            results.append({
                "user_id": guide.get("guide_id"),
                "user_role": "guide",
                "display_name": guide.get("display_name"),
                "current_country": None,
                "current_region": guide.get("region"),
                "current_city": None,
                "heritage_region": guide.get("region"),
                "heritage_country": ", ".join(guide.get("heritage_countries") or []),
                "heritage_group": guide.get("specialty"),
                "relocation_interest_level": "guide_support",
                "open_to_cultural_guides": True,
                "open_to_relocation_guidance": True,
                "preferred_contact_scope": "verified_guides_only",
                "verification_status": "verified_guide",
                "guide_verified": bool(guide.get("guide_verified")),
                "guide_description": guide.get("description"),
                "trust_score": 90,
                "verification_level": "DOCUMENT_VERIFIED",
            })

    logger.info(
        "Origin discovery search: region=%s country=%s group=%s → %d results",
        origin_region, origin_country, heritage_group, len(results),
    )
    return results


def get_cohort_suggestions_for_user(user: dict[str, Any]) -> list[str]:
    """
    Return list of suggested cohort IDs for a user based on Phase 41 location/heritage fields.
    Used to extend cohort suggestion logic with richer heritage data.
    """
    suggested: list[str] = []

    heritage_region = str(user.get("heritage_region") or "").strip().lower()
    heritage_country = str(user.get("heritage_country") or "").strip().lower()
    origin_region = str(user.get("origin_region") or "").strip().lower()
    relocation_interest = str(user.get("relocation_interest_level") or "").strip().lower()

    # Resolve region
    effective_region = heritage_region or _COUNTRY_TO_REGION.get(heritage_country, "") or origin_region

    if "west_africa" in effective_region:
        suggested.append("cohort_west_africa")
    elif "east_africa" in effective_region:
        suggested.append("cohort_east_africa")
    elif "southern_africa" in effective_region or "central_africa" in effective_region:
        pass  # no specific seeded cohort yet; leave for future phases

    # Caribbean check via heritage_country
    if heritage_country in _CARIBBEAN_COUNTRIES:
        suggested.append("cohort_caribbean_roots")

    # Unknown origin → Heritage Discovery
    if (not heritage_region and not heritage_country) or "unknown" in effective_region:
        suggested.append("cohort_heritage_discovery")

    # Relocation interest
    if relocation_interest in {"considering", "actively_planning", "already_relocated"}:
        if "cohort_global_relocation" not in suggested:
            suggested.append("cohort_global_relocation")

    return list(dict.fromkeys(suggested))  # dedup preserving order
