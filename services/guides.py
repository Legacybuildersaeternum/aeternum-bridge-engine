"""
Aeternum Bridge Engine — Phase 42: Cultural Guide System.

Guides are specialist profiles (NOT normal users) who provide cultural,
heritage, and relocation guidance to opted-in diaspora members.

Seed guides are kept in memory only — they are NOT persisted to data/ JSON files
and are NOT committed to the repository.

Guide rules:
- Guides only appear in origin match results when user has open_to_cultural_guides = True.
- Guides can receive "guidance requests" (handled in routes/guidance.py).
- Guides are NOT normal user records and do not participate in messaging/connection flows.
- guide_verified = True indicates a seed/approved guide.
"""

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# In-memory seed guide profiles (NOT persisted to disk)
# ---------------------------------------------------------------------------

_SEED_GUIDES: list[dict[str, Any]] = [
    {
        "guide_id": "guide_west_africa_001",
        "user_role": "guide",
        "display_name": "West Africa Cultural Guide",
        "region": "west_africa",
        "heritage_countries": ["Nigeria", "Ghana", "Senegal", "Sierra Leone"],
        "specialty": "Cultural reconnection, language, Yoruba/Igbo/Akan traditions",
        "description": (
            "Helping diaspora members reconnect with West African heritage communities, "
            "language resources, and village-level contact networks in Nigeria and Ghana."
        ),
        "guide_verified": True,
        "contact_scope": "verified_guides_only",
    },
    {
        "guide_id": "guide_east_africa_001",
        "user_role": "guide",
        "display_name": "East Africa Cultural Guide",
        "region": "east_africa",
        "heritage_countries": ["Kenya", "Ethiopia", "Tanzania", "Uganda"],
        "specialty": "Swahili heritage, ancestry tracing, East African diaspora pathways",
        "description": (
            "Supporting diaspora members with roots in Kenya, Tanzania, and Uganda. "
            "Specialises in connecting families with coastal Swahili communities and "
            "heritage research networks."
        ),
        "guide_verified": True,
        "contact_scope": "verified_guides_only",
    },
    {
        "guide_id": "guide_caribbean_001",
        "user_role": "guide",
        "display_name": "Caribbean Heritage Guide",
        "region": "caribbean",
        "heritage_countries": ["Jamaica", "Trinidad", "Barbados", "Haiti"],
        "specialty": "African-Caribbean lineage, return pathways, creole heritage",
        "description": (
            "Helping Caribbean diaspora members trace African roots, navigate return pathways "
            "to West Africa, and connect with pan-African heritage communities."
        ),
        "guide_verified": True,
        "contact_scope": "verified_guides_only",
    },
    {
        "guide_id": "guide_southern_africa_001",
        "user_role": "guide",
        "display_name": "Southern Africa Heritage Guide",
        "region": "southern_africa",
        "heritage_countries": ["South Africa", "Zimbabwe", "Zambia", "Mozambique"],
        "specialty": "Bantu heritage, ubuntu philosophy, Southern African diaspora",
        "description": (
            "Guiding members with roots in Southern Africa through heritage discovery, "
            "ancestral connections, and modern relocation support networks."
        ),
        "guide_verified": True,
        "contact_scope": "verified_guides_only",
    },
]

# Region aliases for matching
_REGION_ALIASES: dict[str, str] = {
    "west africa": "west_africa",
    "east africa": "east_africa",
    "caribbean": "caribbean",
    "southern africa": "southern_africa",
    "central africa": "central_africa",
}


def _normalize_region(region: Optional[str]) -> str:
    if not region:
        return ""
    r = region.strip().lower()
    return _REGION_ALIASES.get(r, r)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_guides_for_region(
    region: Optional[str] = None,
    *,
    require_verified: bool = True,
) -> list[dict[str, Any]]:
    """
    Return guide profiles matching the given region.
    If region is None/empty, return all guides.
    Only returns verified guides when require_verified=True.
    """
    region_norm = _normalize_region(region)

    results = []
    for guide in _SEED_GUIDES:
        if require_verified and not guide.get("guide_verified"):
            continue
        if region_norm:
            guide_region = _normalize_region(guide.get("region", ""))
            if guide_region != region_norm:
                continue
        results.append(dict(guide))

    logger.debug("Guide lookup: region=%s → %d guides", region, len(results))
    return results


def get_guide_by_id(guide_id: str) -> Optional[dict[str, Any]]:
    """Return a single guide by ID, or None if not found."""
    return next(
        (dict(g) for g in _SEED_GUIDES if g["guide_id"] == guide_id),
        None,
    )


def list_all_guides() -> list[dict[str, Any]]:
    """Return all seed guide profiles."""
    return [dict(g) for g in _SEED_GUIDES]
