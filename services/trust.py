"""Phase 43 — Trust and Verification scoring service."""

from typing import Any


def calculate_trust_score(user: dict[str, Any]) -> int:
    score = 0

    # Profile completeness
    if user.get("name") or user.get("full_name"):
        score += 10
    if user.get("family_id"):
        score += 10
    if user.get("origin_country") or user.get("heritage_country") or user.get("country"):
        score += 10
    if user.get("origin_region"):
        score += 10

    # Engagement signals
    if int(user.get("connections_count", 0) or 0) > 0:
        score += 15

    if int(user.get("cohort_memberships", 0) or 0) > 0:
        score += 15

    if int(user.get("messages_sent", 0) or 0) > 5:
        score += 10

    # Conflict resolution
    if bool(user.get("duplicate_resolved")):
        score += 10

    # Manual/document verification (future ready)
    if bool(user.get("document_verified")):
        score += 20

    return min(score, 100)


def get_verification_level(score: int) -> str:
    if score >= 80:
        return "DOCUMENT_VERIFIED"
    if score >= 60:
        return "COMMUNITY_VERIFIED"
    if score >= 30:
        return "SELF_VERIFIED"
    return "UNVERIFIED"


def build_trust_profile(user: dict[str, Any]) -> dict[str, Any]:
    score = calculate_trust_score(user)
    level = get_verification_level(score)
    return {
        "trust_score": score,
        "verification_level": level,
    }
