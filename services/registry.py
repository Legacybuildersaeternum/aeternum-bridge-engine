import csv
import io
import json
import uuid
import hashlib
import logging
import shutil
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from models.user import (
    ConnectionRequestCreateRequest,
    ConnectionRequestRecord,
    DuplicateActionResponse,
    DuplicateFamilyGroupResponse,
    DuplicateProfileCandidate,
    FamilyConnectionRequestPayload,
    FamilyConnectionRequestResponse,
    FindFamilyMatchResult,
    FindFamilySearchRequest,
    FamilyGroupResponse,
    FamilyMemberSummary,
    HouseholdPosition,
    OriginRegion,
    RegistrationUpdateRequest,
    RelationshipSuggestionCandidate,
    RelationshipSuggestionResponse,
    RelationshipUpdateRequest,
    PendingFamilyConnectionRequestRecord,
    RelationshipRole,
    StatsResponse,
    TravelTimeframe,
    UserRecord,
    UserRegistration,
    UserStage,
)

logger = logging.getLogger(__name__)

DATA_FILE = Path(__file__).resolve().parents[1] / "data" / "diaspora_registry.json"
REGISTRY_BACKUP_FILE = Path(__file__).resolve().parents[1] / "data" / "diaspora_registry_backup.json"
REGISTRY_TIMESTAMP_BACKUP_DIR = Path(__file__).resolve().parents[1] / "data" / "registry_backups"
_EMPTY_STORE: dict[str, Any] = {"users": [], "connection_requests": []}
ACTIVITY_LOG_FILE = Path(__file__).resolve().parents[1] / "data" / "legacy_activity_log.json"
_EMPTY_ACTIVITY_STORE: dict[str, Any] = {"events": []}
_ACTIVITY_LOG_LOCK = threading.RLock()
_REGISTRY_FILE_LOCK = threading.RLock()
DATA_GUARD_STATUS = "ACTIVE"


def _empty_registry_store() -> dict[str, Any]:
    return {
        "users": [],
        "connection_requests": [],
    }


def _normalize_dropdown_token(value: str) -> str:
    normalized = value.strip().lower().replace("/", "_").replace(" ", "_")
    while "__" in normalized:
        normalized = normalized.replace("__", "_")
    return normalized.strip("_")


def _derive_user_stage(travel_timeframe: str) -> UserStage:
    if travel_timeframe == TravelTimeframe.within_1_year.value:
        return UserStage.high_intent
    if travel_timeframe == TravelTimeframe.one_to_three_years.value:
        return UserStage.early_planning
    if travel_timeframe == TravelTimeframe.three_to_five_years.value:
        return UserStage.long_term_planning
    return UserStage.exploring


def _role_to_display(role: Optional[str]) -> str:
    if not role:
        return "Relationship"
    display_map = {
        "spouse_partner": "Spouse / Partner",
        "elder_ancestor": "Elder / Ancestor",
    }
    if role in display_map:
        return display_map[role]
    return " ".join(part.capitalize() for part in role.split("_"))


def _normalize_linked_to_user_ids(raw_ids: Optional[object], raw_single_id: Optional[object]) -> list[str]:
    values: list[object] = []
    if isinstance(raw_ids, list):
        values.extend(raw_ids)
    elif raw_ids is not None:
        values.append(raw_ids)
    if raw_single_id is not None:
        values.append(raw_single_id)

    normalized: list[str] = []
    for item in values:
        value = str(item).strip()
        if value and value not in normalized:
            normalized.append(value)
    return normalized


def _set_linked_fields(record: dict[str, Any], linked_ids: list[str]) -> None:
    record["linked_to_user_ids"] = linked_ids
    record["linked_to_user_id"] = linked_ids[0] if linked_ids else None


def _human_join(values: list[str]) -> str:
    if not values:
        return ""
    if len(values) == 1:
        return values[0]
    if len(values) == 2:
        return f"{values[0]} and {values[1]}"
    return f"{', '.join(values[:-1])}, and {values[-1]}"


def _build_relationship_display(role: Optional[str], linked_to_full_names: list[str]) -> str:
    if not linked_to_full_names:
        return "Relationship not linked yet."
    joined_names = _human_join(linked_to_full_names)
    if not role:
        return f"Connected to {joined_names}"
    role_label = _role_to_display(role)
    return f"{role_label} of {joined_names}"


_PARENT_ROLES = {
    "father",
    "mother",
    "parent",
    "grandfather",
    "grandmother",
    "grandparent",
    "elder_ancestor",
}
_CHILD_ROLES = {
    "son",
    "daughter",
    "child",
    "grandson",
    "granddaughter",
    "dependent",
}
_PEER_ROLES = {
    "brother",
    "sister",
    "sibling",
    "cousin",
    "spouse_partner",
    "self",
    "other",
}

_STATUS_ACTIVE = "active"
_STATUS_PENDING_LINK = "pending_link"
_STATUS_MERGED_ARCHIVED = "merged_archived"
_STATUS_IGNORED_DUPLICATE = "ignored_duplicate"

_DUP_DECISION_KEEP_SEPARATE = "keep_separate"
_DUP_DECISION_REVIEW_LATER = "review_later"


def _pair_key(user_a: str, user_b: str) -> str:
    left, right = sorted([str(user_a).strip(), str(user_b).strip()])
    return f"{left}::{right}"


def _normalize_duplicate_pair_flags(raw_flags: Any) -> list[dict[str, Any]]:
    flags = raw_flags if isinstance(raw_flags, list) else []
    normalized: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for raw in flags:
        if not isinstance(raw, dict):
            continue
        pair_key = str(raw.get("pair_key") or "").strip()
        other_user_id = str(raw.get("other_user_id") or "").strip()
        decision = str(raw.get("decision") or "").strip().lower()
        if decision not in {_DUP_DECISION_KEEP_SEPARATE, _DUP_DECISION_REVIEW_LATER}:
            continue
        if not pair_key and other_user_id:
            # Older records may only carry other_user_id; keep pair_key blank-safe here.
            pair_key = str(raw.get("pair_key") or "").strip()
        dedupe_key = (pair_key, other_user_id)
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        normalized.append(
            {
                "pair_key": pair_key,
                "other_user_id": other_user_id,
                "decision": decision,
                "updated_at": str(raw.get("updated_at") or datetime.now(timezone.utc).isoformat()),
            }
        )
    return normalized


def _get_pair_decision(left: dict[str, Any], right: dict[str, Any]) -> str:
    left_id = str(left.get("user_id") or "").strip()
    right_id = str(right.get("user_id") or "").strip()
    if not left_id or not right_id:
        return ""
    key = _pair_key(left_id, right_id)
    for owner, other in ((left, right_id), (right, left_id)):
        flags = _normalize_duplicate_pair_flags(owner.get("duplicate_pair_flags"))
        for flag in flags:
            if flag.get("pair_key") == key or flag.get("other_user_id") == other:
                decision = str(flag.get("decision") or "").strip().lower()
                if decision in {_DUP_DECISION_KEEP_SEPARATE, _DUP_DECISION_REVIEW_LATER}:
                    return decision
    return ""


def _upsert_pair_decision(record: dict[str, Any], other_user_id: str, decision: str) -> None:
    owner_id = str(record.get("user_id") or "").strip()
    other_id = str(other_user_id or "").strip()
    normalized_decision = str(decision or "").strip().lower()
    if not owner_id or not other_id:
        return
    if normalized_decision not in {_DUP_DECISION_KEEP_SEPARATE, _DUP_DECISION_REVIEW_LATER}:
        return
    key = _pair_key(owner_id, other_id)
    flags = _normalize_duplicate_pair_flags(record.get("duplicate_pair_flags"))
    now = datetime.now(timezone.utc).isoformat()
    replaced = False
    for flag in flags:
        if flag.get("pair_key") == key or flag.get("other_user_id") == other_id:
            flag["pair_key"] = key
            flag["other_user_id"] = other_id
            flag["decision"] = normalized_decision
            flag["updated_at"] = now
            replaced = True
            break
    if not replaced:
        flags.append(
            {
                "pair_key": key,
                "other_user_id": other_id,
                "decision": normalized_decision,
                "updated_at": now,
            }
        )
    record["duplicate_pair_flags"] = flags


def _drop_pair_decision(record: dict[str, Any], other_user_id: str) -> None:
    owner_id = str(record.get("user_id") or "").strip()
    other_id = str(other_user_id or "").strip()
    if not owner_id or not other_id:
        return
    key = _pair_key(owner_id, other_id)
    flags = _normalize_duplicate_pair_flags(record.get("duplicate_pair_flags"))
    record["duplicate_pair_flags"] = [
        flag
        for flag in flags
        if flag.get("pair_key") != key and flag.get("other_user_id") != other_id
    ]


def _get_profile_status(record: dict[str, Any]) -> str:
    raw_status = str(record.get("profile_status") or "").strip().lower()
    if raw_status in {
        _STATUS_ACTIVE,
        _STATUS_PENDING_LINK,
        _STATUS_MERGED_ARCHIVED,
        _STATUS_IGNORED_DUPLICATE,
    }:
        return raw_status
    return _STATUS_ACTIVE


def _effective_profile_status(record: dict[str, Any]) -> str:
    profile_status = _get_profile_status(record)
    if profile_status == _STATUS_MERGED_ARCHIVED:
        return _STATUS_MERGED_ARCHIVED
    linked_ids = _normalize_linked_to_user_ids(
        record.get("linked_to_user_ids"),
        record.get("linked_to_user_id"),
    )
    if profile_status == _STATUS_IGNORED_DUPLICATE:
        return _STATUS_IGNORED_DUPLICATE
    if not linked_ids:
        return _STATUS_PENDING_LINK
    return _STATUS_ACTIVE


def _is_tree_active(record: dict[str, Any]) -> bool:
    return _effective_profile_status(record) != _STATUS_MERGED_ARCHIVED


def _name_similarity_score(name_a: str, name_b: str) -> float:
    a = str(name_a or "").strip().lower()
    b = str(name_b or "").strip().lower()
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    tokens_a = {token for token in a.split() if token}
    tokens_b = {token for token in b.split() if token}
    if not tokens_a or not tokens_b:
        return 0.0
    overlap = len(tokens_a.intersection(tokens_b))
    return overlap / max(len(tokens_a), len(tokens_b))


def _first_name(value: Any) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return ""
    return text.split()[0]


def _looks_like_child_or_dependent(role: str, household_position: str) -> bool:
    return role in _CHILD_ROLES or household_position in {"dependent", "child"}


def _build_duplicate_candidate(
    primary: dict[str, Any],
    duplicate: dict[str, Any],
) -> tuple[int, list[str], bool, bool]:
    score = 0
    reasons: list[str] = []

    name_similarity = _name_similarity_score(primary.get("full_name"), duplicate.get("full_name"))
    identity_signal = False
    if name_similarity >= 0.99:
        score += 35
        reasons.append("exact_full_name")
        identity_signal = True
    elif name_similarity >= 0.75:
        score += 22
        reasons.append("similar_full_name")
        identity_signal = True

    family_name_a = str(primary.get("family_name") or "").strip().lower()
    family_name_b = str(duplicate.get("family_name") or "").strip().lower()
    if family_name_a and family_name_a == family_name_b:
        score += 20
        reasons.append("same_family_name")

    role_a = str(primary.get("relationship_role") or "").strip().lower()
    role_b = str(duplicate.get("relationship_role") or "").strip().lower()
    if role_a and role_a == role_b:
        score += 10
        reasons.append("same_relationship_role")

    position_a = str(primary.get("household_position") or "").strip().lower()
    position_b = str(duplicate.get("household_position") or "").strip().lower()
    if position_a and position_a == position_b:
        score += 10
        reasons.append("same_household_position")

    linked_a = set(_normalize_linked_to_user_ids(primary.get("linked_to_user_ids"), primary.get("linked_to_user_id")))
    linked_b = set(_normalize_linked_to_user_ids(duplicate.get("linked_to_user_ids"), duplicate.get("linked_to_user_id")))
    shared_links = linked_a.intersection(linked_b)
    if shared_links:
        score += 12
        reasons.append("shared_linked_profiles")

    email_a = str(primary.get("email") or "").strip().lower()
    email_b = str(duplicate.get("email") or "").strip().lower()
    email_match = bool(email_a and email_a == email_b)
    if email_match:
        score += 18
        reasons.append("same_email")
        identity_signal = True

    phone_a = _phone_digits(primary)
    phone_b = _phone_digits(duplicate)
    phone_match = bool(phone_a and phone_a == phone_b)
    if phone_match:
        score += 14
        reasons.append("same_phone")
        identity_signal = True

    if str(primary.get("age_range") or "").strip() and str(primary.get("age_range") or "").strip() == str(duplicate.get("age_range") or "").strip():
        score += 5
        reasons.append("same_age_range")

    role_a = str(primary.get("relationship_role") or "").strip().lower()
    role_b = str(duplicate.get("relationship_role") or "").strip().lower()
    position_a = str(primary.get("household_position") or "").strip().lower()
    position_b = str(duplicate.get("household_position") or "").strip().lower()
    first_name_a = _first_name(primary.get("full_name"))
    first_name_b = _first_name(duplicate.get("full_name"))
    same_first_name = bool(first_name_a and first_name_a == first_name_b)

    child_pair = _looks_like_child_or_dependent(role_a, position_a) and _looks_like_child_or_dependent(role_b, position_b)
    likely_separate = False
    if child_pair and not same_first_name:
        score -= 45
        reasons.append("sibling_name_divergence_guardrail")
        likely_separate = True
    if {role_a, role_b} == {"daughter", "son"}:
        score -= 35
        reasons.append("daughter_son_guardrail")
        likely_separate = True

    parent_child_pair = ((role_a in _PARENT_ROLES and role_b in _CHILD_ROLES) or (role_b in _PARENT_ROLES and role_a in _CHILD_ROLES))
    extremely_strong_identity = name_similarity >= 0.99 or (email_match and phone_match)
    if parent_child_pair and not extremely_strong_identity:
        score = min(score, 24)
        reasons.append("parent_child_guardrail")
        likely_separate = True

    # Strong match must be rooted in identity signals, not shared household metadata.
    if not identity_signal and score >= 70:
        score = 59
        reasons.append("identity_signal_required_for_strong_match")

    score = max(0, min(score, 100))
    return score, reasons, identity_signal, likely_separate


def _detect_duplicate_candidates_for_family(
    family_members: list[dict[str, Any]],
) -> list[DuplicateProfileCandidate]:
    candidates: list[DuplicateProfileCandidate] = []
    for i, left in enumerate(family_members):
        left_id = str(left.get("user_id") or "").strip()
        if not left_id:
            continue
        for right in family_members[i + 1 :]:
            right_id = str(right.get("user_id") or "").strip()
            if not right_id:
                continue
            pair_decision = _get_pair_decision(left, right)
            score, reasons, identity_signal, likely_separate = _build_duplicate_candidate(left, right)
            if pair_decision != _DUP_DECISION_REVIEW_LATER and score < 35:
                continue

            if pair_decision == _DUP_DECISION_KEEP_SEPARATE:
                continue

            left_strength = len(_normalize_linked_to_user_ids(left.get("linked_to_user_ids"), left.get("linked_to_user_id")))
            right_strength = len(_normalize_linked_to_user_ids(right.get("linked_to_user_ids"), right.get("linked_to_user_id")))
            if left_strength > right_strength:
                primary, duplicate = left, right
            elif right_strength > left_strength:
                primary, duplicate = right, left
            else:
                primary, duplicate = (left, right) if str(left.get("registered_at") or "") <= str(right.get("registered_at") or "") else (right, left)

            strong_candidate = score >= 70 and identity_signal and not likely_separate and pair_decision != _DUP_DECISION_REVIEW_LATER
            if pair_decision == _DUP_DECISION_REVIEW_LATER:
                recommendation_label = "Review Later"
            elif likely_separate:
                recommendation_label = "Likely Separate Family Member"
            elif strong_candidate:
                recommendation_label = "Strong Duplicate Candidate"
            else:
                recommendation_label = "Weak Match — Review Only"

            duplicate_links = _normalize_linked_to_user_ids(
                duplicate.get("linked_to_user_ids"),
                duplicate.get("linked_to_user_id"),
            )
            queue_state = "review_later" if pair_decision == _DUP_DECISION_REVIEW_LATER else "active"

            candidates.append(
                DuplicateProfileCandidate(
                    primary_user_id=str(primary.get("user_id") or ""),
                    duplicate_user_id=str(duplicate.get("user_id") or ""),
                    primary_full_name=str(primary.get("full_name") or "Unnamed member"),
                    duplicate_full_name=str(duplicate.get("full_name") or "Unnamed member"),
                    family_id=str(primary.get("family_id") or duplicate.get("family_id") or ""),
                    confidence_score=score,
                    match_reasons=reasons,
                    review_only=not strong_candidate,
                    duplicate_relationship_role=str(duplicate.get("relationship_role") or ""),
                    duplicate_profile_status=_effective_profile_status(duplicate),
                    duplicate_linked_to_user_ids=duplicate_links,
                    queue_state=queue_state,
                    recommendation_label=recommendation_label,
                )
            )

    deduped: dict[tuple[str, str], DuplicateProfileCandidate] = {}
    for candidate in candidates:
        key = tuple(sorted([candidate.primary_user_id, candidate.duplicate_user_id]))
        existing = deduped.get(key)
        if existing is None or candidate.confidence_score > existing.confidence_score:
            deduped[key] = candidate
    final_candidates = list(deduped.values())
    final_candidates.sort(
        key=lambda c: (
            1 if c.queue_state == "review_later" else 0,
            -c.confidence_score,
            c.primary_full_name.lower(),
            c.duplicate_full_name.lower(),
        )
    )
    return final_candidates


def _append_merge_details(
    base_message: str,
    primary_user_id: str,
    duplicate_user_id: str,
    confidence_score: int,
    reasons: list[str],
) -> str:
    reasons_text = ",".join(reasons) if reasons else "none"
    return (
        f"{base_message} primary_user_id={primary_user_id} duplicate_user_id={duplicate_user_id} "
        f"confidence_score={confidence_score} reasons={reasons_text}"
    )


def _normalize_role(value: Optional[str]) -> str:
    return str(value or "").strip().lower()


def _extract_last_name(record: dict[str, Any]) -> str:
    full_name = str(record.get("full_name") or "").strip()
    if full_name:
        parts = [part for part in full_name.split() if part]
        if parts:
            return parts[-1].lower()
    return str(record.get("family_name") or "").strip().lower()


def _age_range_midpoint(age_range: Optional[str]) -> Optional[int]:
    value = str(age_range or "").strip().lower()
    if not value:
        return None
    if value.endswith("+"):
        digits = "".join(ch for ch in value if ch.isdigit())
        return int(digits) + 5 if digits else None
    if "-" in value:
        start_raw, end_raw = value.split("-", 1)
        start_digits = "".join(ch for ch in start_raw if ch.isdigit())
        end_digits = "".join(ch for ch in end_raw if ch.isdigit())
        if start_digits and end_digits:
            return (int(start_digits) + int(end_digits)) // 2
    digits = "".join(ch for ch in value if ch.isdigit())
    return int(digits) if digits else None


def _is_same_household(source: dict[str, Any], target: dict[str, Any]) -> bool:
    city = str(source.get("city") or "").strip().lower()
    state = str(source.get("state") or "").strip().lower()
    country = str(source.get("country") or "").strip().lower()
    target_city = str(target.get("city") or "").strip().lower()
    target_state = str(target.get("state") or "").strip().lower()
    target_country = str(target.get("country") or "").strip().lower()
    return bool(city and state and country and city == target_city and state == target_state and country == target_country)


def _infer_relationship_type(source: dict[str, Any], target: dict[str, Any]) -> tuple[str, int]:
    source_role = _normalize_role(source.get("relationship_role"))
    target_role = _normalize_role(target.get("relationship_role"))
    if source_role in _PARENT_ROLES and target_role in _CHILD_ROLES:
        return "parent_to_child", 20
    if source_role in _CHILD_ROLES and target_role in _PARENT_ROLES:
        return "child_to_parent", 20
    if source_role == "spouse_partner" or target_role == "spouse_partner":
        return "spouse_partner", 20
    if source_role in _PEER_ROLES and target_role in _PEER_ROLES:
        return "sibling_or_peer", 12
    if source_role in _PARENT_ROLES and not target_role:
        return "possible_parent_to_child", 10
    if source_role in _CHILD_ROLES and not target_role:
        return "possible_child_to_parent", 10
    if not source_role and target_role in _PARENT_ROLES:
        return "possible_child_to_parent", 10
    if not source_role and target_role in _CHILD_ROLES:
        return "possible_parent_to_child", 10
    return "household_match", 0


def _age_compatibility_score(source: dict[str, Any], target: dict[str, Any], relationship_type: str) -> int:
    source_age = _age_range_midpoint(source.get("age_range"))
    target_age = _age_range_midpoint(target.get("age_range"))
    if source_age is None or target_age is None:
        return 0
    gap = abs(source_age - target_age)
    if relationship_type in {"parent_to_child", "child_to_parent", "possible_parent_to_child", "possible_child_to_parent"}:
        return 15 if gap >= 12 else 0
    if relationship_type in {"spouse_partner", "sibling_or_peer", "household_match"}:
        return 15 if gap <= 15 else 0
    return 0


def _build_family_relationship_maps(
    family_members: list[dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], dict[str, list[str]], dict[str, int]]:
    id_set = {
        str(member.get("user_id"))
        for member in family_members
        if member.get("user_id")
    }
    node_map: dict[str, dict[str, Any]] = {}
    outgoing: dict[str, list[str]] = {}
    incoming: dict[str, int] = {}
    role_by_id = {
        str(member.get("user_id")): _normalize_role(member.get("relationship_role"))
        for member in family_members
        if member.get("user_id")
    }

    for member in family_members:
        user_id = str(member.get("user_id") or "").strip()
        if not user_id:
            continue
        linked_ids = [
            linked_id
            for linked_id in _normalize_linked_to_user_ids(
                member.get("linked_to_user_ids"),
                member.get("linked_to_user_id"),
            )
            if linked_id in id_set and linked_id != user_id
        ]
        node_map[user_id] = dict(member, linked_to_user_ids=linked_ids)
        outgoing[user_id] = []
        incoming[user_id] = 0

    def add_edge(parent_id: str, child_id: str) -> None:
        if parent_id == child_id:
            return
        if parent_id not in node_map or child_id not in node_map:
            return
        if child_id in outgoing[parent_id]:
            return
        outgoing[parent_id].append(child_id)
        incoming[child_id] += 1

    for user_id, node in node_map.items():
        member_role = _normalize_role(node.get("relationship_role"))
        for linked_id in node.get("linked_to_user_ids", []):
            linked_role = role_by_id.get(linked_id, "")
            if member_role in _PARENT_ROLES:
                add_edge(user_id, linked_id)
            elif member_role in _CHILD_ROLES:
                add_edge(linked_id, user_id)
            elif linked_role in _PARENT_ROLES:
                add_edge(linked_id, user_id)
            elif linked_role in _CHILD_ROLES:
                add_edge(user_id, linked_id)
            else:
                add_edge(user_id, linked_id)

    return node_map, outgoing, incoming


def get_relationship_suggestions(
    family_id: Optional[str] = None,
) -> list[RelationshipSuggestionResponse]:
    users = [_normalize_user(u) for u in _load()]
    grouped: dict[str, list[dict[str, Any]]] = {}
    for user in users:
        if not _is_tree_active(user):
            continue
        current_family_id = str(user.get("family_id") or "").strip()
        if not current_family_id:
            continue
        if family_id and current_family_id != family_id:
            continue
        grouped.setdefault(current_family_id, []).append(user)

    suggestions: list[RelationshipSuggestionResponse] = []
    for family_members in grouped.values():
        node_map, outgoing, incoming = _build_family_relationship_maps(family_members)
        for member in family_members:
            user_id = str(member.get("user_id") or "").strip()
            if not user_id or user_id not in node_map:
                continue

            linked_ids = list(node_map[user_id].get("linked_to_user_ids") or [])
            has_parent = incoming.get(user_id, 0) > 0
            has_children = bool(outgoing.get(user_id))
            pending_link = not linked_ids
            unlinked_reasons: list[str] = []
            if not has_parent:
                unlinked_reasons.append("no_parent")
            if not has_children:
                unlinked_reasons.append("no_children")
            if pending_link:
                unlinked_reasons.append("pending_link")
            if not unlinked_reasons:
                continue

            member_last_name = _extract_last_name(member)
            candidate_matches: list[RelationshipSuggestionCandidate] = []
            for target in family_members:
                target_id = str(target.get("user_id") or "").strip()
                if not target_id or target_id == user_id or target_id in linked_ids:
                    continue

                confidence_score = 0
                if _is_same_household(member, target):
                    confidence_score += 40
                if member_last_name and member_last_name == _extract_last_name(target):
                    confidence_score += 25
                relationship_type, role_score = _infer_relationship_type(member, target)
                confidence_score += role_score
                confidence_score += _age_compatibility_score(member, target, relationship_type)
                if confidence_score <= 0:
                    continue

                candidate_matches.append(
                    RelationshipSuggestionCandidate(
                        target_id=target_id,
                        target_name=str(target.get("full_name") or "Unnamed member"),
                        relationship_type=relationship_type,
                        confidence_score=min(confidence_score, 100),
                    )
                )

            candidate_matches.sort(
                key=lambda item: (-item.confidence_score, item.target_name.lower(), item.target_id)
            )
            suggestions.append(
                RelationshipSuggestionResponse(
                    user_id=user_id,
                    full_name=str(member.get("full_name") or "Unnamed member"),
                    unlinked_reasons=unlinked_reasons,
                    possible_matches=candidate_matches[:5],
                )
            )

    suggestions.sort(
        key=lambda item: (
            -(item.possible_matches[0].confidence_score if item.possible_matches else 0),
            item.full_name.lower(),
            item.user_id,
        )
    )
    return suggestions


def _mask_identifier(user_id: str) -> str:
    raw = str(user_id or "").strip()
    if len(raw) <= 4:
        return "****"
    return f"****{raw[-4:]}"


def _parse_iso_date(value: Optional[str]) -> Optional[datetime]:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _phone_digits(record: dict[str, Any]) -> str:
    value = str(record.get("phone_number") or record.get("phone") or "")
    return "".join(ch for ch in value if ch.isdigit())


def _age_display(user: dict[str, Any]) -> Optional[str]:
    user_dob = _parse_iso_date(user.get("date_of_birth"))
    if user_dob:
        return f"Birth Year: {user_dob.year}"
    age_range = str(user.get("age_range") or "").strip()
    if age_range:
        return f"Age: {age_range}"
    return None


def _estimated_age_years(user: Optional[dict[str, Any]]) -> Optional[int]:
    if not user:
        return None
    user_dob = _parse_iso_date(user.get("date_of_birth"))
    if user_dob:
        today = datetime.now(timezone.utc).date()
        years = today.year - user_dob.year - ((today.month, today.day) < (user_dob.month, user_dob.day))
        return max(years, 0)
    return _age_range_midpoint(user.get("age_range"))


def _search_confidence_level(score: int) -> str:
    if score >= 70:
        return "high"
    if score >= 45:
        return "medium"
    return "low"


def find_family_matches(payload: FindFamilySearchRequest, session_id: Optional[str] = None) -> list[FindFamilyMatchResult]:
    users = [_normalize_user(u) for u in _load()]
    users = [u for u in users if _is_tree_active(u)]

    search_last = str(payload.last_name or "").strip().lower()
    if not search_last:
        raise ValueError("last_name is required")
    search_first = str(payload.first_name or "").strip().lower()
    search_country = str(payload.country or "").strip().lower()
    search_state = str(payload.state_region or "").strip().lower()
    search_relative = str(payload.known_relative_name or "").strip().lower()
    search_role = str(payload.relationship_guess or "").strip().lower()
    search_dob = _parse_iso_date(payload.date_of_birth)
    requester_user_id = str(payload.requester_user_id or "").strip().lower()
    users_by_id = {str(user.get("user_id") or "").strip().lower(): user for user in users}
    requester_profile = users_by_id.get(requester_user_id) if requester_user_id else None
    requester_name = str(requester_profile.get("full_name") or "").strip().lower() if requester_profile else ""
    requester_role = _normalize_role(requester_profile.get("relationship_role")) if requester_profile else ""
    requester_country = str(requester_profile.get("country") or "").strip().lower() if requester_profile else ""
    requester_state = str(requester_profile.get("state") or "").strip().lower() if requester_profile else ""
    requester_age = _estimated_age_years(requester_profile)

    members_by_family: dict[str, list[dict[str, Any]]] = {}
    for user in users:
        family_id = str(user.get("family_id") or "").strip()
        if family_id:
            members_by_family.setdefault(family_id, []).append(user)

    results: list[FindFamilyMatchResult] = []
    for user in users:
        user_id = str(user.get("user_id") or "").strip()
        if not user_id:
            continue
        normalized_user_id = user_id.lower()
        if requester_user_id and requester_user_id == normalized_user_id:
            continue

        full_name = str(user.get("full_name") or "").strip()
        family_name = str(user.get("family_name") or "").strip().lower()
        name_parts = [part for part in full_name.lower().split() if part]
        user_last = name_parts[-1] if name_parts else family_name
        if user_last != search_last and family_name != search_last:
            continue

        score = 20
        reason_tokens: list[str] = ["Last name aligned"]
        user_first = name_parts[0] if name_parts else ""
        exact_first_name_match = False
        if search_first:
            if user_first == search_first:
                score += 20
                exact_first_name_match = True
                reason_tokens.append("First name matched")
            elif user_first and (user_first.startswith(search_first) or search_first.startswith(user_first)):
                score += 10
                reason_tokens.append("First name closely matched")
            else:
                score -= 10
                reason_tokens.append("First name differs")

        family_name_matched = bool(family_name and family_name == search_last)
        if family_name_matched:
            score += 20
            reason_tokens.append("Family name matched")

        user_dob = _parse_iso_date(user.get("date_of_birth"))
        dob_status = "DOB not searched"
        dob_matched = False
        dob_conflict = False
        if search_dob and user_dob:
            gap_days = abs((search_dob.date() - user_dob.date()).days)
            if gap_days == 0:
                score += 40
                dob_matched = True
                dob_status = "DOB matched"
                reason_tokens.append("Exact DOB matched")
            elif gap_days <= 365 * 5:
                score += 20
                dob_status = "DOB close age range"
                reason_tokens.append("DOB close age range")
            else:
                dob_conflict = True
                dob_status = "DOB conflict"
                score -= 35
                reason_tokens.append("DOB conflict")
        elif search_dob and not user_dob:
            dob_status = "DOB not on file"
            reason_tokens.append("Low Confidence - DOB not on file")

        if not search_dob:
            dob_status = "DOB not searched"

        user_country = str(user.get("country") or "").strip().lower()
        user_state = str(user.get("state") or "").strip().lower()
        region_match = False
        if search_country and user_country and search_country == user_country:
            region_match = True
        if search_state and user_state and search_state == user_state:
            region_match = True
        if requester_country and user_country and requester_country == user_country:
            region_match = True
            score += 6
            reason_tokens.append("Requester country aligned")
        if requester_state and user_state and requester_state == user_state:
            region_match = True
            score += 6
            reason_tokens.append("Requester state aligned")
        if region_match:
            score += 10
            reason_tokens.append("Region matched")

        role = str(user.get("relationship_role") or "").strip().lower()
        relationship_hint_match = bool(search_role and role and search_role == role)

        if search_relative:
            family_members = members_by_family.get(str(user.get("family_id") or ""), [])
            relative_hit = any(
                search_relative in str(member.get("full_name") or "").strip().lower()
                for member in family_members
                if str(member.get("user_id") or "") != user_id
            )
            relationship_hint_match = relationship_hint_match or relative_hit

        if relationship_hint_match:
            score += 10
            reason_tokens.append("Relationship hint matched")

        if requester_role and role:
            if requester_role == role:
                score += 12
                reason_tokens.append("Relationship role similarity")
            elif ((requester_role in _PARENT_ROLES and role in _PARENT_ROLES)
                  or (requester_role in _CHILD_ROLES and role in _CHILD_ROLES)
                  or (requester_role in _PEER_ROLES and role in _PEER_ROLES)):
                score += 8
                reason_tokens.append("Role group aligned")

        candidate_age = _estimated_age_years(user)
        if requester_age is not None and candidate_age is not None:
            age_gap = abs(requester_age - candidate_age)
            if age_gap <= 10:
                score += 12
                reason_tokens.append("Age range proximity")
            elif age_gap <= 20:
                score += 6
                reason_tokens.append("Age range near")

        # Legacy profiles without DOB can still appear, but should remain low/medium confidence.
        if search_dob and not user_dob:
            score = min(score, 40)

        # DOB conflict should generally stay weak unless multiple other fields strongly justify review.
        if dob_conflict:
            strong_non_dob_signals = int(exact_first_name_match) + int(family_name_matched) + int(region_match) + int(relationship_hint_match)
            if strong_non_dob_signals < 3:
                score = min(score, 35)
            else:
                score = min(score, 50)

        if search_dob and dob_matched and exact_first_name_match and family_name_matched:
            reason_tokens.append("High confidence identity alignment")

        if requester_name and full_name.lower() == requester_name and normalized_user_id != requester_user_id:
            reason_tokens.append("Possible duplicate profile - review needed")

        score = max(0, min(100, score))
        region = str(user.get("origin_region") or "unknown")
        country = str(user.get("country") or "Not provided")
        state = str(user.get("state") or "Not provided")
        reason_summary = "; ".join(reason_tokens[:5]) if reason_tokens else "Possible lineage signal"
        results.append(
            FindFamilyMatchResult(
                user_id=user_id,
                full_name=full_name or "Unnamed member",
                relationship_role=str(user.get("relationship_role") or "") or None,
                region=f"{region} | {state}, {country}",
                masked_identifier=_mask_identifier(user_id),
                age_display=_age_display(user),
                dob_status=dob_status,
                reason_summary=reason_summary,
                confidence_level=_search_confidence_level(score),
                confidence_score=score,
            )
        )

    results.sort(key=lambda item: (-item.confidence_score, item.full_name.lower(), item.user_id))
    trimmed = results[:25]

    requester_family_id = None
    requester_family_name = None
    if requester_profile:
        requester_family_id = str(requester_profile.get("family_id") or "") or None
        requester_family_name = str(requester_profile.get("family_name") or "") or None

    write_activity_event(
        event_type="find_family_search",
        message=(
            f"Find-family search executed for last name '{search_last}' "
            f"with requester {requester_user_id or 'unknown'}."
        ),
        user_id=requester_user_id or None,
        family_id=requester_family_id,
        family_name=requester_family_name,
        session_id=session_id,
    )
    write_activity_event(
        event_type="find_family_results_shown",
        message=f"Find-family results shown: {len(trimmed)} candidate(s).",
        user_id=requester_user_id or None,
        family_id=requester_family_id,
        family_name=requester_family_name,
        session_id=session_id,
    )

    return trimmed


def _normalize_connection_request(raw: dict[str, Any]) -> dict[str, Any]:
    request = dict(raw)
    request.setdefault("request_id", "")
    request.setdefault("requester_user_id", "")
    request.setdefault("target_user_id", "")
    request.setdefault("relationship_guess", None)
    request.setdefault("preferred_contact_method", None)
    request.setdefault("status", "pending")
    request.setdefault("requester_confirmed", False)
    request.setdefault("receiver_confirmed", False)
    request.setdefault("timestamp", request.get("created_at") or datetime.now(timezone.utc).isoformat())
    request.setdefault("created_at", datetime.now(timezone.utc).isoformat())
    request.setdefault("updated_at", request["created_at"])
    return request


def _to_connection_request_record(request: dict[str, Any], users_by_id: dict[str, dict[str, Any]]) -> ConnectionRequestRecord:
    requester_id = str(request.get("requester_user_id") or "")
    target_id = str(request.get("target_user_id") or "")
    requester_name = str(users_by_id.get(requester_id, {}).get("full_name") or "Unknown requester")
    target_name = str(users_by_id.get(target_id, {}).get("full_name") or "Unknown target")
    return ConnectionRequestRecord(
        request_id=str(request.get("request_id") or ""),
        requester_user_id=requester_id,
        requester_name=requester_name,
        target_user_id=target_id,
        target_name=target_name,
        relationship_guess=str(request.get("relationship_guess") or "") or None,
        preferred_contact_method=str(request.get("preferred_contact_method") or "") or None,
        status=str(request.get("status") or "pending"),
        requester_confirmed=bool(request.get("requester_confirmed")),
        receiver_confirmed=bool(request.get("receiver_confirmed")),
        timestamp=str(request.get("timestamp") or request.get("created_at") or datetime.now(timezone.utc).isoformat()),
        created_at=str(request.get("created_at") or datetime.now(timezone.utc).isoformat()),
        updated_at=str(request.get("updated_at") or datetime.now(timezone.utc).isoformat()),
    )


def create_connection_request(
    payload: ConnectionRequestCreateRequest,
    session_id: Optional[str] = None,
) -> ConnectionRequestRecord:
    users = [_normalize_user(u) for u in _load()]
    users_by_id = {str(user.get("user_id") or ""): user for user in users}
    requester_user_id = str(payload.requester_user_id or "").strip()
    target_user_id = str(payload.target_user_id or "").strip()
    if requester_user_id not in users_by_id or target_user_id not in users_by_id:
        raise ValueError("Requester or target profile not found")
    if requester_user_id == target_user_id:
        raise ValueError("Cannot create a connection request to the same profile")

    requests = [_normalize_connection_request(item) for item in _load_connection_requests()]
    existing = next(
        (
            item for item in requests
            if str(item.get("requester_user_id") or "") == requester_user_id
            and str(item.get("target_user_id") or "") == target_user_id
            and str(item.get("status") or "") not in {"declined", "cancelled", "connection_completed"}
        ),
        None,
    )
    now = datetime.now(timezone.utc).isoformat()
    if existing:
        existing["relationship_guess"] = payload.relationship_guess
        existing["preferred_contact_method"] = payload.preferred_contact_method
        existing["status"] = "pending"
        existing["requester_confirmed"] = False
        existing["receiver_confirmed"] = False
        existing["timestamp"] = existing.get("timestamp") or now
        existing["updated_at"] = now
        record = _to_connection_request_record(existing, users_by_id)
    else:
        request = {
            "request_id": f"req_{uuid.uuid4().hex[:12]}",
            "requester_user_id": requester_user_id,
            "target_user_id": target_user_id,
            "relationship_guess": payload.relationship_guess,
            "preferred_contact_method": payload.preferred_contact_method,
            "status": "pending",
            "requester_confirmed": False,
            "receiver_confirmed": False,
            "timestamp": now,
            "created_at": now,
            "updated_at": now,
        }
        requests.append(request)
        record = _to_connection_request_record(request, users_by_id)

    _save_connection_requests(requests)
    write_activity_event(
        event_type="request_created",
        message=f"Connection request created from {record.requester_user_id} to {record.target_user_id}.",
        user_id=record.requester_user_id,
        family_id=str(users_by_id.get(record.requester_user_id, {}).get("family_id") or "") or None,
        family_name=str(users_by_id.get(record.requester_user_id, {}).get("family_name") or "") or None,
        session_id=session_id,
    )
    write_activity_event(
        event_type="request_received",
        message=f"Incoming connection request received for {record.target_user_id}.",
        user_id=record.target_user_id,
        family_id=str(users_by_id.get(record.target_user_id, {}).get("family_id") or "") or None,
        family_name=str(users_by_id.get(record.target_user_id, {}).get("family_name") or "") or None,
        session_id=session_id,
    )
    return record


def create_family_connection_request(
    payload: FamilyConnectionRequestPayload,
    session_id: Optional[str] = None,
) -> FamilyConnectionRequestResponse:
    requester_user_id = str(payload.requester_user_id or "").strip()
    target_user_id = str(payload.target_user_id or "").strip()
    if not requester_user_id or not target_user_id:
        raise ValueError("requester_user_id and target_user_id are required")
    if requester_user_id == target_user_id:
        raise ValueError("Requester cannot request connection to their own profile")

    users = [_normalize_user(u) for u in _load()]
    users_by_id = {str(user.get("user_id") or ""): user for user in users}
    requester = users_by_id.get(requester_user_id)
    target = users_by_id.get(target_user_id)
    if not requester or not target:
        raise ValueError("Requester or target profile not found")

    requests = [_normalize_connection_request(item) for item in _load_connection_requests()]
    existing = next(
        (
            item
            for item in requests
            if str(item.get("requester_user_id") or "") == requester_user_id
            and str(item.get("target_user_id") or "") == target_user_id
            and str(item.get("status") or "")
            not in {"declined", "cancelled", "connection_completed"}
        ),
        None,
    )

    if existing:
        request_id = str(existing.get("request_id") or "")
        status = str(existing.get("status") or "pending_outside_verification")
        write_activity_event(
            event_type="family_connection_request_duplicate_blocked",
            message=(
                f"Duplicate connection request blocked between {requester_user_id} and {target_user_id}. "
                f"Existing request {request_id} already active."
            ),
            user_id=requester_user_id,
            family_id=str(requester.get("family_id") or "") or None,
            family_name=str(requester.get("family_name") or "") or None,
            session_id=session_id,
        )
        return FamilyConnectionRequestResponse(
            success=True,
            request_id=request_id,
            status=status,
            message="Existing connection request already pending outside verification.",
        )

    now = datetime.now(timezone.utc).isoformat()
    request_id = f"fcr_{uuid.uuid4().hex[:12]}"
    request = {
        "request_id": request_id,
        "requester_user_id": requester_user_id,
        "target_user_id": target_user_id,
        "requester_name": str(requester.get("full_name") or "") or None,
        "target_masked_name": _mask_identifier(target_user_id),
        "relationship_guess": payload.relationship_guess,
        "preferred_contact_method": payload.preferred_contact_method,
        "note": str(payload.note or "").strip() or None,
        "search_context": payload.search_context or {},
        "status": "pending_outside_verification",
        "outside_contact_required": True,
        "verification_steps": [
            "requester must contact target outside the app",
            "target must confirm the contact happened",
            "both parties must agree before merge/link can be accepted",
        ],
        "requester_confirmed": False,
        "receiver_confirmed": False,
        "timestamp": now,
        "created_at": now,
        "updated_at": now,
    }
    requests.append(request)
    _save_connection_requests(requests)

    write_activity_event(
        event_type="family_connection_request_created",
        message=(
            f"Family connection request created between {requester_user_id} and {target_user_id}. "
            "Outside-app verification required before approval."
        ),
        user_id=requester_user_id,
        family_id=str(requester.get("family_id") or "") or None,
        family_name=str(requester.get("family_name") or "") or None,
        session_id=session_id,
    )

    return FamilyConnectionRequestResponse(
        success=True,
        request_id=request_id,
        status="pending_outside_verification",
        message="Connection request created. Outside-app verification is required before this family link can be approved.",
    )


def get_pending_family_connection_requests() -> list[PendingFamilyConnectionRequestRecord]:
    requests = [_normalize_connection_request(item) for item in _load_connection_requests()]
    pending = [
        item
        for item in requests
        if str(item.get("status") or "") == "pending_outside_verification"
    ]
    pending.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
    records: list[PendingFamilyConnectionRequestRecord] = []
    for item in pending:
        records.append(
            PendingFamilyConnectionRequestRecord(
                request_id=str(item.get("request_id") or ""),
                requester_user_id=str(item.get("requester_user_id") or ""),
                target_user_id=str(item.get("target_user_id") or ""),
                requester_name=str(item.get("requester_name") or "") or None,
                target_masked_name=str(item.get("target_masked_name") or "") or None,
                relationship_guess=str(item.get("relationship_guess") or "") or None,
                preferred_contact_method=str(item.get("preferred_contact_method") or "") or None,
                note=str(item.get("note") or "") or None,
                created_at=str(item.get("created_at") or item.get("timestamp") or datetime.now(timezone.utc).isoformat()),
                status=str(item.get("status") or "pending_outside_verification"),
                outside_contact_required=bool(item.get("outside_contact_required", True)),
            )
        )
    return records


def get_all_connection_requests() -> list[PendingFamilyConnectionRequestRecord]:
    """Return all connection requests for admin review, sorted newest first."""
    requests = [_normalize_connection_request(item) for item in _load_connection_requests()]
    requests.sort(key=lambda item: str(item.get("created_at") or item.get("timestamp") or ""), reverse=True)
    records: list[PendingFamilyConnectionRequestRecord] = []
    for item in requests:
        records.append(
            PendingFamilyConnectionRequestRecord(
                request_id=str(item.get("request_id") or ""),
                requester_user_id=str(item.get("requester_user_id") or ""),
                target_user_id=str(item.get("target_user_id") or ""),
                requester_name=str(item.get("requester_name") or "") or None,
                target_masked_name=str(item.get("target_masked_name") or "") or None,
                relationship_guess=str(item.get("relationship_guess") or "") or None,
                preferred_contact_method=str(item.get("preferred_contact_method") or "") or None,
                note=str(item.get("note") or "") or None,
                created_at=str(item.get("created_at") or item.get("timestamp") or datetime.now(timezone.utc).isoformat()),
                status=str(item.get("status") or "pending_outside_verification"),
                outside_contact_required=bool(item.get("outside_contact_required", True)),
            )
        )
    return records


def admin_accept_connection_request(
    request_id: str,
    session_id: Optional[str] = None,
) -> PendingFamilyConnectionRequestRecord:
    """Admin accepts a connection request and immediately links both users."""
    with _REGISTRY_FILE_LOCK:
        requests = [_normalize_connection_request(item) for item in _load_connection_requests()]
        users = [_normalize_user(u) for u in _load()]
        users_by_id = {str(user.get("user_id") or ""): user for user in users}

        target_req = next(
            (item for item in requests if str(item.get("request_id") or "") == request_id),
            None,
        )
        if not target_req:
            raise ValueError("Connection request not found")

        requester_id = str(target_req.get("requester_user_id") or "")
        receiver_id = str(target_req.get("target_user_id") or "")
        requester = users_by_id.get(requester_id)
        receiver = users_by_id.get(receiver_id)
        if not requester or not receiver:
            raise ValueError("Requester or target profile not found in registry")

        # Link both users into each other's linked_to_user_ids
        requester_links = _normalize_linked_to_user_ids(requester.get("linked_to_user_ids"), requester.get("linked_to_user_id"))
        receiver_links = _normalize_linked_to_user_ids(receiver.get("linked_to_user_ids"), receiver.get("linked_to_user_id"))
        if receiver_id not in requester_links:
            requester_links.append(receiver_id)
        if requester_id not in receiver_links:
            receiver_links.append(requester_id)
        requester_links = sorted(set(requester_links))
        receiver_links = sorted(set(receiver_links))

        updated_users: list[dict[str, Any]] = []
        for user in users:
            uid = str(user.get("user_id") or "")
            if uid == requester_id:
                updated_user = dict(user)
                _set_linked_fields(updated_user, requester_links)
                updated_users.append(_normalize_user(updated_user))
            elif uid == receiver_id:
                updated_user = dict(user)
                _set_linked_fields(updated_user, receiver_links)
                updated_users.append(_normalize_user(updated_user))
            else:
                updated_users.append(_normalize_user(user))

        now = datetime.now(timezone.utc).isoformat()
        target_req["status"] = "accepted"
        target_req["updated_at"] = now

        _save(updated_users)
        _save_connection_requests(requests)

    write_activity_event(
        event_type="family_connection_request_accepted",
        message=f"Admin accepted connection request {request_id}. Users {requester_id} and {receiver_id} are now linked.",
        user_id=requester_id,
        family_id=str(users_by_id.get(requester_id, {}).get("family_id") or "") or None,
        family_name=str(users_by_id.get(requester_id, {}).get("family_name") or "") or None,
        session_id=session_id,
    )

    return PendingFamilyConnectionRequestRecord(
        request_id=str(target_req.get("request_id") or ""),
        requester_user_id=requester_id,
        target_user_id=receiver_id,
        requester_name=str(target_req.get("requester_name") or "") or None,
        target_masked_name=str(target_req.get("target_masked_name") or "") or None,
        relationship_guess=str(target_req.get("relationship_guess") or "") or None,
        preferred_contact_method=str(target_req.get("preferred_contact_method") or "") or None,
        created_at=str(target_req.get("created_at") or target_req.get("timestamp") or now),
        status="accepted",
        outside_contact_required=bool(target_req.get("outside_contact_required", True)),
    )


def admin_reject_connection_request(
    request_id: str,
    session_id: Optional[str] = None,
) -> PendingFamilyConnectionRequestRecord:
    """Admin rejects a connection request. Users are NOT linked."""
    with _REGISTRY_FILE_LOCK:
        requests = [_normalize_connection_request(item) for item in _load_connection_requests()]
        users = [_normalize_user(u) for u in _load()]
        users_by_id = {str(user.get("user_id") or ""): user for user in users}

        target_req = next(
            (item for item in requests if str(item.get("request_id") or "") == request_id),
            None,
        )
        if not target_req:
            raise ValueError("Connection request not found")

        requester_id = str(target_req.get("requester_user_id") or "")
        receiver_id = str(target_req.get("target_user_id") or "")

        now = datetime.now(timezone.utc).isoformat()
        target_req["status"] = "rejected"
        target_req["updated_at"] = now

        _save_connection_requests(requests)

    write_activity_event(
        event_type="family_connection_request_rejected",
        message=f"Admin rejected connection request {request_id} between {requester_id} and {receiver_id}.",
        user_id=requester_id,
        family_id=str(users_by_id.get(requester_id, {}).get("family_id") or "") or None,
        family_name=str(users_by_id.get(requester_id, {}).get("family_name") or "") or None,
        session_id=session_id,
    )

    return PendingFamilyConnectionRequestRecord(
        request_id=str(target_req.get("request_id") or ""),
        requester_user_id=requester_id,
        target_user_id=receiver_id,
        requester_name=str(target_req.get("requester_name") or "") or None,
        target_masked_name=str(target_req.get("target_masked_name") or "") or None,
        relationship_guess=str(target_req.get("relationship_guess") or "") or None,
        preferred_contact_method=str(target_req.get("preferred_contact_method") or "") or None,
        created_at=str(target_req.get("created_at") or target_req.get("timestamp") or now),
        status="rejected",
        outside_contact_required=bool(target_req.get("outside_contact_required", True)),
    )


def get_connection_requests_for_user(user_id: str, direction: str = "incoming") -> list[ConnectionRequestRecord]:
    normalized_user_id = str(user_id or "").strip()
    if not normalized_user_id:
        return []
    users = [_normalize_user(u) for u in _load()]
    users_by_id = {str(user.get("user_id") or ""): user for user in users}
    requests = [_normalize_connection_request(item) for item in _load_connection_requests()]
    if direction == "outgoing":
        filtered = [item for item in requests if str(item.get("requester_user_id") or "") == normalized_user_id]
    else:
        filtered = [item for item in requests if str(item.get("target_user_id") or "") == normalized_user_id]
    filtered.sort(key=lambda item: str(item.get("updated_at") or ""), reverse=True)
    return [_to_connection_request_record(item, users_by_id) for item in filtered]


def _update_connection_request_status(
    request_id: str,
    acting_user_id: str,
    action: str,
    session_id: Optional[str] = None,
) -> ConnectionRequestRecord:
    requests = [_normalize_connection_request(item) for item in _load_connection_requests()]
    users = [_normalize_user(u) for u in _load()]
    users_by_id = {str(user.get("user_id") or ""): user for user in users}
    target = next((item for item in requests if str(item.get("request_id") or "") == request_id), None)
    if not target:
        raise ValueError("Connection request not found")

    requester_id = str(target.get("requester_user_id") or "")
    receiver_id = str(target.get("target_user_id") or "")
    actor_id = str(acting_user_id or "").strip()
    if actor_id not in {requester_id, receiver_id}:
        raise ValueError("Acting user is not part of this request")

    now = datetime.now(timezone.utc).isoformat()
    if action == "accept":
        if actor_id != receiver_id:
            raise ValueError("Only the receiver can accept verification")
        target["status"] = "accepted"
        target["updated_at"] = now
        write_activity_event(
            event_type="request_accepted",
            message=f"Connection request {request_id} was accepted.",
            user_id=actor_id,
            family_id=str(users_by_id.get(actor_id, {}).get("family_id") or "") or None,
            family_name=str(users_by_id.get(actor_id, {}).get("family_name") or "") or None,
            session_id=session_id,
        )
        write_activity_event(
            event_type="verification_started",
            message=f"Verification started for request {request_id}.",
            user_id=actor_id,
            family_id=str(users_by_id.get(actor_id, {}).get("family_id") or "") or None,
            family_name=str(users_by_id.get(actor_id, {}).get("family_name") or "") or None,
            session_id=session_id,
        )
    elif action == "decline":
        if actor_id != receiver_id:
            raise ValueError("Only the receiver can decline a request")
        target["status"] = "declined"
        target["updated_at"] = now
    elif action == "confirm":
        if actor_id == requester_id:
            target["requester_confirmed"] = True
        if actor_id == receiver_id:
            target["receiver_confirmed"] = True
        if bool(target.get("requester_confirmed")) and bool(target.get("receiver_confirmed")):
            target["status"] = "ready_to_connect"
        else:
            target["status"] = "accepted"
        target["updated_at"] = now
        write_activity_event(
            event_type="verification_confirmed",
            message=f"External verification confirmation recorded for request {request_id}.",
            user_id=actor_id,
            family_id=str(users_by_id.get(actor_id, {}).get("family_id") or "") or None,
            family_name=str(users_by_id.get(actor_id, {}).get("family_name") or "") or None,
            session_id=session_id,
        )
    else:
        raise ValueError("Unsupported request action")

    _save_connection_requests(requests)
    return _to_connection_request_record(target, users_by_id)


def accept_connection_request(request_id: str, acting_user_id: str, session_id: Optional[str] = None) -> ConnectionRequestRecord:
    return _update_connection_request_status(request_id, acting_user_id, "accept", session_id=session_id)


def decline_connection_request(request_id: str, acting_user_id: str, session_id: Optional[str] = None) -> ConnectionRequestRecord:
    return _update_connection_request_status(request_id, acting_user_id, "decline", session_id=session_id)


def confirm_connection_request_verification(
    request_id: str,
    acting_user_id: str,
    session_id: Optional[str] = None,
) -> ConnectionRequestRecord:
    return _update_connection_request_status(request_id, acting_user_id, "confirm", session_id=session_id)


def complete_connection_request(
    request_id: str,
    acting_user_id: str,
    session_id: Optional[str] = None,
) -> ConnectionRequestRecord:
    requests = [_normalize_connection_request(item) for item in _load_connection_requests()]
    users = [_normalize_user(u) for u in _load()]
    users_by_id = {str(user.get("user_id") or ""): user for user in users}

    target = next((item for item in requests if str(item.get("request_id") or "") == request_id), None)
    if not target:
        raise ValueError("Connection request not found")

    requester_id = str(target.get("requester_user_id") or "")
    receiver_id = str(target.get("target_user_id") or "")
    actor_id = str(acting_user_id or "").strip()
    if actor_id not in {requester_id, receiver_id}:
        raise ValueError("Acting user is not part of this request")

    if not bool(target.get("requester_confirmed")) or not bool(target.get("receiver_confirmed")):
        raise ValueError("Both users must confirm verification before final connection")

    requester = users_by_id.get(requester_id)
    receiver = users_by_id.get(receiver_id)
    if not requester or not receiver:
        raise ValueError("Requester or receiver profile not found")

    requester_family = str(requester.get("family_id") or "")
    receiver_family = str(receiver.get("family_id") or "")
    if requester_family != receiver_family:
        raise ValueError("Final connection requires both profiles in the same family")

    requester_links = _normalize_linked_to_user_ids(requester.get("linked_to_user_ids"), requester.get("linked_to_user_id"))
    receiver_links = _normalize_linked_to_user_ids(receiver.get("linked_to_user_ids"), receiver.get("linked_to_user_id"))
    if receiver_id not in requester_links:
        requester_links.append(receiver_id)
    if requester_id not in receiver_links:
        receiver_links.append(requester_id)
    requester_links = sorted(set(requester_links))
    receiver_links = sorted(set(receiver_links))

    updated_users: list[dict[str, Any]] = []
    for user in users:
        user_id = str(user.get("user_id") or "")
        if user_id == requester_id:
            updated_user = dict(user)
            _set_linked_fields(updated_user, requester_links)
            updated_users.append(_normalize_user(updated_user))
            continue
        if user_id == receiver_id:
            updated_user = dict(user)
            _set_linked_fields(updated_user, receiver_links)
            updated_users.append(_normalize_user(updated_user))
            continue
        updated_users.append(_normalize_user(user))

    _save(updated_users)
    remaining_requests = [item for item in requests if str(item.get("request_id") or "") != request_id]
    _save_connection_requests(remaining_requests)

    now = datetime.now(timezone.utc).isoformat()
    completed_payload = dict(target)
    completed_payload["status"] = "connection_completed"
    completed_payload["updated_at"] = now

    write_activity_event(
        event_type="connection_completed",
        message=f"Connection completed between {requester_id} and {receiver_id}.",
        user_id=actor_id,
        family_id=requester_family or None,
        family_name=str(requester.get("family_name") or "") or None,
        session_id=session_id,
    )

    updated_users_by_id = {str(user.get("user_id") or ""): user for user in updated_users}
    return _to_connection_request_record(completed_payload, updated_users_by_id)


def _ensure_file() -> None:
    """Create data file with empty structure if it does not exist."""
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not DATA_FILE.exists():
        with DATA_FILE.open("w", encoding="utf-8") as f:
            json.dump(_EMPTY_STORE, f, indent=2)
        logger.info("Created new data file at %s", DATA_FILE)


def _ensure_activity_file() -> None:
    """Create activity log file with empty structure if it does not exist."""
    ACTIVITY_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not ACTIVITY_LOG_FILE.exists():
        with ACTIVITY_LOG_FILE.open("w", encoding="utf-8") as f:
            json.dump(_EMPTY_ACTIVITY_STORE, f, indent=2)
        logger.info("Created new activity log file at %s", ACTIVITY_LOG_FILE)


def _load_activity_events() -> list[dict[str, Any]]:
    with _ACTIVITY_LOG_LOCK:
        _ensure_activity_file()
        try:
            with ACTIVITY_LOG_FILE.open("r", encoding="utf-8") as f:
                data = json.load(f)
        except json.JSONDecodeError:
            logger.warning("Activity log file was empty or invalid JSON. Resetting %s", ACTIVITY_LOG_FILE)
            _save_activity_events([])
            return []
    if isinstance(data, list):
        return data
    return data.get("events", [])


def _save_activity_events(events: list[dict[str, Any]]) -> None:
    with _ACTIVITY_LOG_LOCK:
        _ensure_activity_file()
        temp_file = ACTIVITY_LOG_FILE.with_suffix(".json.tmp")
        with temp_file.open("w", encoding="utf-8") as f:
            json.dump({"events": events}, f, indent=2, ensure_ascii=False)
        temp_file.replace(ACTIVITY_LOG_FILE)


def write_activity_event(
    event_type: str,
    message: str,
    user_id: Optional[str] = None,
    family_id: Optional[str] = None,
    family_name: Optional[str] = None,
    session_id: Optional[str] = None,
) -> dict[str, Any]:
    """Write one legacy activity entry and return the stored record."""
    with _ACTIVITY_LOG_LOCK:
        events = _load_activity_events()
        session_sequence: Optional[int] = None
        if session_id:
            session_events = [event for event in events if str(event.get("session_id") or "") == session_id]
            session_sequence = len(session_events) + 1

        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "session_id": session_id,
            "session_sequence": session_sequence,
            "event_type": event_type,
            "user_id": user_id,
            "family_id": family_id,
            "family_name": family_name,
            "message": message,
        }
        events.append(entry)
        # Keep file size bounded for MVP operations.
        if len(events) > 1000:
            events = events[-1000:]
        _save_activity_events(events)
        return entry


def get_activity_log(
    limit: int = 200,
    session_id: Optional[str] = None,
    user_id: Optional[str] = None,
    event_type: Optional[str] = None,
) -> list[dict[str, Any]]:
    """Return activity events with optional filters.

    Default ordering is newest-first. When filtering by session_id,
    events are returned in chronological order to show an in-session timeline.
    """
    with _ACTIVITY_LOG_LOCK:
        events = _load_activity_events()
    filtered = events
    if session_id:
        filtered = [
            event
            for event in filtered
            if str(event.get("session_id") or "") == session_id
        ]
    if user_id:
        filtered = [
            event
            for event in filtered
            if str(event.get("user_id") or "") == user_id
        ]
    if event_type:
        filtered = [
            event
            for event in filtered
            if str(event.get("event_type") or "") == event_type
        ]

    if session_id:
        ordered = sorted(
            filtered,
            key=lambda item: (
                str(item.get("timestamp", "")),
                int(item.get("session_sequence") or 0),
            ),
        )
    else:
        ordered = sorted(filtered, key=lambda item: str(item.get("timestamp", "")), reverse=True)
    return ordered[: max(1, min(limit, 1000))]


def _load() -> list[dict[str, Any]]:
    return _load_store().get("users", [])


def _load_store() -> dict[str, Any]:
    with _REGISTRY_FILE_LOCK:
        _ensure_file()
        try:
            with DATA_FILE.open("r", encoding="utf-8") as f:
                data = json.load(f)
        except json.JSONDecodeError:
            logger.error("Registry data file is invalid JSON at %s", DATA_FILE)
            return _empty_registry_store()
    if isinstance(data, list):
        return {"users": data, "connection_requests": []}
    if not isinstance(data, dict):
        return _empty_registry_store()
    users = data.get("users") if isinstance(data.get("users"), list) else []
    connection_requests = data.get("connection_requests") if isinstance(data.get("connection_requests"), list) else []
    return {
        "users": users,
        "connection_requests": connection_requests,
    }


def _validate_store_shape(store: dict[str, Any]) -> None:
    if not isinstance(store, dict):
        raise ValueError("Registry write aborted: store payload must be an object.")
    users = store.get("users")
    requests = store.get("connection_requests")
    if not isinstance(users, list) or not isinstance(requests, list):
        raise ValueError(
            "Registry write aborted: expected {'users': [...], 'connection_requests': [...]} structure."
        )


def _is_live_registry_path(path: Path) -> bool:
    return path.name == "diaspora_registry.json" and path.parent.name == "data"


def _store_user_and_family_counts(store: dict[str, Any]) -> tuple[int, int]:
    users = store.get("users") if isinstance(store.get("users"), list) else []
    normalized_users = [item for item in users if isinstance(item, dict)]
    family_ids = {
        str(item.get("family_id") or "").strip()
        for item in normalized_users
        if str(item.get("family_id") or "").strip()
    }
    return len(normalized_users), len(family_ids)


def _create_timestamped_registry_backup() -> Optional[Path]:
    if not DATA_FILE.exists():
        return None
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    REGISTRY_TIMESTAMP_BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    backup_path = REGISTRY_TIMESTAMP_BACKUP_DIR / f"diaspora_registry_{timestamp}.json"
    shutil.copy2(DATA_FILE, backup_path)
    return backup_path


def _latest_registry_backup_path() -> Optional[Path]:
    candidates: list[Path] = []
    if REGISTRY_BACKUP_FILE.exists():
        candidates.append(REGISTRY_BACKUP_FILE)
    if REGISTRY_TIMESTAMP_BACKUP_DIR.exists():
        candidates.extend(path for path in REGISTRY_TIMESTAMP_BACKUP_DIR.glob("diaspora_registry_*.json") if path.is_file())
    if not candidates:
        return None
    return max(candidates, key=lambda path: path.stat().st_mtime)


def _write_store_with_backup(
    existing_store: dict[str, Any],
    new_store: dict[str, Any],
    *,
    reason: str,
    force_empty_overwrite: bool = False,
) -> None:
    with _REGISTRY_FILE_LOCK:
        _ensure_file()
        _validate_store_shape(existing_store)
        _validate_store_shape(new_store)

        existing_user_count, existing_family_count = _store_user_and_family_counts(existing_store)
        new_user_count, new_family_count = _store_user_and_family_counts(new_store)

        if (
            _is_live_registry_path(DATA_FILE)
            and not force_empty_overwrite
            and existing_user_count > 0
            and existing_family_count > 0
            and (new_user_count == 0 or new_family_count == 0)
        ):
            logger.warning(
                "REGISTRY WRITE BLOCKED: attempted empty overwrite of live registry. "
                "existing_users=%d existing_families=%d new_users=%d new_families=%d reason=%s",
                existing_user_count,
                existing_family_count,
                new_user_count,
                new_family_count,
                reason,
            )
            raise ValueError("REGISTRY WRITE BLOCKED: attempted empty overwrite of live registry.")

        existing_users = existing_store.get("users", [])
        new_users = new_store.get("users", [])
        if len(new_users) < len(existing_users):
            raise ValueError("Registry write aborted: append-safe mode forbids removing existing users.")
        for index, user in enumerate(existing_users):
            if new_users[index] != user:
                raise ValueError("Registry write aborted: append-safe mode forbids mutating existing users.")

        REGISTRY_BACKUP_FILE.parent.mkdir(parents=True, exist_ok=True)
        timestamp_backup = _create_timestamped_registry_backup()
        if timestamp_backup:
            logger.debug("Timestamped registry backup created at %s", timestamp_backup)
        shutil.copy2(DATA_FILE, REGISTRY_BACKUP_FILE)
        logger.debug("Registry backup created at %s", REGISTRY_BACKUP_FILE)

        temp_file = DATA_FILE.with_suffix(".json.tmp")
        try:
            with temp_file.open("w", encoding="utf-8") as f:
                json.dump(new_store, f, indent=2, ensure_ascii=False)
            temp_file.replace(DATA_FILE)
        except Exception:
            logger.exception("Registry write failed during '%s'. Write aborted; backup retained.", reason)
            if temp_file.exists():
                temp_file.unlink(missing_ok=True)
            raise

        logger.debug(
            "Registry file rewritten for '%s' (users=%d, connection_requests=%d)",
            reason,
            len(new_store.get("users", [])),
            len(new_store.get("connection_requests", [])),
        )


def get_registry_safety_status() -> dict[str, Any]:
    store = _load_store()
    users, families = _store_user_and_family_counts(store)
    latest_backup = _latest_registry_backup_path()
    return {
        "registry_path": str(DATA_FILE),
        "total_users": users,
        "total_families": families,
        "last_backup_detected": str(latest_backup) if latest_backup else "none",
        "data_guard_status": DATA_GUARD_STATUS,
        "backup_before_write": "ACTIVE",
    }


def run_persistence_safety_check() -> dict[str, Any]:
    """Validate persistence files on startup without mutating existing data."""
    _ensure_file()
    _ensure_activity_file()
    users = _load()
    events = _load_activity_events()
    diagnostics = {
        "registry_path": str(DATA_FILE),
        "registry_exists": DATA_FILE.exists(),
        "registry_bytes": DATA_FILE.stat().st_size if DATA_FILE.exists() else 0,
        "registry_users": len(users),
        "activity_path": str(ACTIVITY_LOG_FILE),
        "activity_exists": ACTIVITY_LOG_FILE.exists(),
        "activity_bytes": ACTIVITY_LOG_FILE.stat().st_size if ACTIVITY_LOG_FILE.exists() else 0,
        "activity_events": len(events),
    }
    logger.info(
        "Persistence safety check — registry: %s (%d bytes, %d users), activity: %s (%d bytes, %d events)",
        diagnostics["registry_path"],
        diagnostics["registry_bytes"],
        diagnostics["registry_users"],
        diagnostics["activity_path"],
        diagnostics["activity_bytes"],
        diagnostics["activity_events"],
    )
    return diagnostics


def _save(users: list[dict[str, Any]]) -> None:
    store = _load_store()
    existing_users = store.get("users") if isinstance(store.get("users"), list) else []
    existing_ids = {
        str(item.get("user_id") or "").strip()
        for item in existing_users
        if isinstance(item, dict) and str(item.get("user_id") or "").strip()
    }

    users_to_append: list[dict[str, Any]] = []
    for item in users:
        if not isinstance(item, dict):
            continue
        user_id = str(item.get("user_id") or "").strip()
        if not user_id:
            continue
        if user_id in existing_ids:
            continue
        users_to_append.append(dict(item))
        existing_ids.add(user_id)

    if not users_to_append:
        logger.debug("Registry user append skipped: no new user IDs to add.")
        return

    for user in users_to_append:
        logger.info("Registry append-safe write: new user added user_id=%s", str(user.get("user_id") or ""))

    updated_store = {
        "users": [*existing_users, *users_to_append],
        "connection_requests": store.get("connection_requests")
        if isinstance(store.get("connection_requests"), list)
        else [],
    }
    _write_store_with_backup(store, updated_store, reason="append_user")
    logger.info("Data file updated append-safe — total users: %d", len(updated_store["users"]))


def _load_connection_requests() -> list[dict[str, Any]]:
    return _load_store().get("connection_requests", [])


def _save_connection_requests(connection_requests: list[dict[str, Any]]) -> None:
    store = _load_store()
    existing_connection_requests = (
        store.get("connection_requests") if isinstance(store.get("connection_requests"), list) else []
    )
    existing_request_ids = {
        str(item.get("request_id") or "").strip()
        for item in existing_connection_requests
        if isinstance(item, dict) and str(item.get("request_id") or "").strip()
    }

    requests_to_append: list[dict[str, Any]] = []
    for item in connection_requests:
        if not isinstance(item, dict):
            continue
        request_id = str(item.get("request_id") or "").strip()
        if request_id and request_id in existing_request_ids:
            continue
        requests_to_append.append(dict(item))
        if request_id:
            existing_request_ids.add(request_id)

    if not requests_to_append:
        logger.debug("Registry connection-request append skipped: no new request records to add.")
        return

    for request in requests_to_append:
        logger.info(
            "Registry append-safe write: connection request added request_id=%s requester=%s target=%s",
            str(request.get("request_id") or ""),
            str(request.get("requester_user_id") or ""),
            str(request.get("target_user_id") or ""),
        )

    updated_store = {
        "users": store.get("users") if isinstance(store.get("users"), list) else [],
        "connection_requests": [*existing_connection_requests, *requests_to_append],
    }
    _write_store_with_backup(store, updated_store, reason="append_connection_request")


def _normalize_user(record: dict[str, Any]) -> dict[str, Any]:
    """Backfill optional fields for older records and normalize canonical values."""
    normalized = dict(record)
    for key in [
        "email",
        "phone_number",
        "phone",
        "city",
        "state",
        "country",
        "date_of_birth",
        "age_range",
        "preferred_contact_method",
        "travel_timeframe",
        "relationship_role",
        "household_position",
        "linked_to_user_ids",
        "linked_to_user_id",
        "relationship_notes",
        "profile_status",
        "merged_into_user_id",
        "notes",
        "duplicate_pair_flags",
        "entry_agreement_accepted",
        "entry_agreement_accepted_at",
        "ecosystem_updates_opt_in",
        "return_reconnection_interest",
    ]:
        normalized.setdefault(key, None)

    if normalized.get("phone_number") and not normalized.get("phone"):
        normalized["phone"] = normalized.get("phone_number")
    if normalized.get("phone") and not normalized.get("phone_number"):
        normalized["phone_number"] = normalized.get("phone")

    linked_ids = _normalize_linked_to_user_ids(
        normalized.get("linked_to_user_ids"),
        normalized.get("linked_to_user_id"),
    )
    _set_linked_fields(normalized, linked_ids)
    normalized["origin_region"] = _normalize_dropdown_token(
        str(normalized.get("origin_region") or OriginRegion.unknown.value)
    )
    if not normalized.get("travel_timeframe"):
        normalized["travel_timeframe"] = TravelTimeframe.not_sure_yet.value
    else:
        normalized["travel_timeframe"] = _normalize_dropdown_token(str(normalized["travel_timeframe"]))
    if normalized.get("preferred_contact_method"):
        normalized["preferred_contact_method"] = _normalize_dropdown_token(
            str(normalized["preferred_contact_method"])
        )
    if normalized.get("relationship_role"):
        normalized["relationship_role"] = _normalize_dropdown_token(str(normalized["relationship_role"]))
    if normalized.get("household_position"):
        normalized["household_position"] = _normalize_dropdown_token(str(normalized["household_position"]))
    normalized["profile_status"] = _get_profile_status(normalized)
    if not normalized.get("merged_into_user_id"):
        normalized["merged_into_user_id"] = None
    normalized["duplicate_pair_flags"] = _normalize_duplicate_pair_flags(normalized.get("duplicate_pair_flags"))
    normalized["entry_agreement_accepted"] = bool(normalized.get("entry_agreement_accepted"))
    normalized["entry_agreement_accepted_at"] = str(
        normalized.get("entry_agreement_accepted_at") or normalized.get("registered_at") or datetime.now(timezone.utc).isoformat()
    )
    normalized["ecosystem_updates_opt_in"] = bool(normalized.get("ecosystem_updates_opt_in"))
    normalized["return_reconnection_interest"] = _normalize_dropdown_token(
        str(normalized.get("return_reconnection_interest") or "maybe_learning_more")
    )
    if not normalized.get("state"):
        normalized["state"] = "Not provided"
    if not normalized.get("country"):
        normalized["country"] = "Not provided"
    normalized["user_stage"] = (
        normalized.get("user_stage") or _derive_user_stage(str(normalized["travel_timeframe"])).value
    )
    return normalized


def _generate_family_id(family_name: str) -> str:
    """Deterministic family_id derived from family_name so all members share it."""
    normalized = family_name.strip().lower()
    return "fam_" + hashlib.sha256(normalized.encode()).hexdigest()[:12]


def register_user(payload: UserRegistration, session_id: Optional[str] = None) -> UserRecord:
    users = _load()
    family_id = _generate_family_id(payload.family_name)
    family_exists = any(user.get("family_id") == family_id for user in users)
    user_id = "usr_" + uuid.uuid4().hex[:12]
    registered_at = datetime.now(timezone.utc).isoformat()

    linked_ids = _normalize_linked_to_user_ids(payload.linked_to_user_ids, payload.linked_to_user_id)
    linked_ids = [linked_id for linked_id in linked_ids if linked_id != user_id]
    same_family_member_ids = {
        str(user.get("user_id"))
        for user in users
        if user.get("family_id") == family_id and user.get("user_id")
    }
    linked_ids = [linked_id for linked_id in linked_ids if linked_id in same_family_member_ids]

    record = UserRecord(
        user_id=user_id,
        family_id=family_id,
        full_name=payload.full_name,
        family_name=payload.family_name,
        family_size=payload.family_size,
        origin_region=payload.origin_region,
        interested_in_return=payload.interested_in_return,
        email=payload.email,
        phone_number=payload.phone_number or payload.phone,
        phone=payload.phone or payload.phone_number,
        city=payload.city,
        state=payload.state,
        country=payload.country,
        date_of_birth=payload.date_of_birth,
        age_range=payload.age_range,
        preferred_contact_method=payload.preferred_contact_method,
        travel_timeframe=payload.travel_timeframe or TravelTimeframe.not_sure_yet,
        relationship_role=payload.relationship_role,
        household_position=payload.household_position,
        linked_to_user_ids=linked_ids,
        linked_to_user_id=linked_ids[0] if linked_ids else None,
        relationship_notes=payload.relationship_notes,
        profile_status=_STATUS_ACTIVE,
        merged_into_user_id=None,
        user_stage=_derive_user_stage(
            (payload.travel_timeframe or TravelTimeframe.not_sure_yet).value
        ),
        notes=payload.notes,
        entry_agreement_accepted=True,
        entry_agreement_accepted_at=registered_at,
        ecosystem_updates_opt_in=bool(payload.ecosystem_updates_opt_in),
        return_reconnection_interest=payload.return_reconnection_interest,
        registered_at=registered_at,
    )
    users.append(record.model_dump(mode="json"))
    _save(users)

    write_activity_event(
        event_type="registration_submitted",
        message=f"New registration submitted for {record.full_name}.",
        user_id=record.user_id,
        family_id=record.family_id,
        family_name=record.family_name,
        session_id=session_id,
    )
    write_activity_event(
        event_type="entry_agreement_accepted",
        message=f"Entry agreement accepted for {record.full_name}.",
        user_id=record.user_id,
        family_id=record.family_id,
        family_name=record.family_name,
        session_id=session_id,
    )
    if bool(record.ecosystem_updates_opt_in):
        write_activity_event(
            event_type="ecosystem_updates_opted_in",
            message=f"Ecosystem updates opt-in recorded for {record.full_name}.",
            user_id=record.user_id,
            family_id=record.family_id,
            family_name=record.family_name,
            session_id=session_id,
        )
    write_activity_event(
        event_type="return_reconnection_interest_recorded",
        message=(
            f"Return/reconnection interest recorded for {record.full_name}: "
            f"{record.return_reconnection_interest}."
        ),
        user_id=record.user_id,
        family_id=record.family_id,
        family_name=record.family_name,
        session_id=session_id,
    )
    write_activity_event(
        event_type="family_group_updated" if family_exists else "family_group_created",
        message=(
            f"Family group {record.family_name} updated with new member {record.full_name}."
            if family_exists
            else f"Family group {record.family_name} created with founding member {record.full_name}."
        ),
        user_id=record.user_id,
        family_id=record.family_id,
        family_name=record.family_name,
        session_id=session_id,
    )
    return record


def get_stats() -> StatsResponse:
    users = [_normalize_user(u) for u in _load()]
    users = [u for u in users if _is_tree_active(u)]

    total_users = len(users)
    family_member_counts: dict[str, int] = {}
    for r in users:
        family_id = r.get("family_id")
        if family_id:
            family_member_counts[family_id] = family_member_counts.get(family_id, 0) + 1

    total_family_groups = len(family_member_counts)
    total_families = total_family_groups
    largest_family_size = max(family_member_counts.values(), default=0)
    total_interested = sum(1 for r in users if r.get("interested_in_return"))
    total_with_contact_info = sum(1 for r in users if r.get("email") or r.get("phone") or r.get("phone_number"))
    entry_agreement_accepted_count = sum(1 for r in users if bool(r.get("entry_agreement_accepted")))
    ecosystem_updates_opt_in_count = sum(1 for r in users if bool(r.get("ecosystem_updates_opt_in")))

    region_distribution: dict[str, int] = {}
    travel_timeframe_distribution: dict[str, int] = {}
    state_distribution: dict[str, int] = {}
    country_distribution: dict[str, int] = {}
    role_distribution: dict[str, int] = {}
    household_position_distribution: dict[str, int] = {}
    return_reconnection_interest_distribution: dict[str, int] = {}
    region_travel_timeframe_combinations: dict[str, int] = {}
    region_interest_combinations: dict[str, int] = {}

    for r in users:
        region = r.get("origin_region", OriginRegion.unknown.value)
        region_distribution[region] = region_distribution.get(region, 0) + 1

        timeframe = r.get("travel_timeframe") or TravelTimeframe.not_sure_yet.value
        travel_timeframe_distribution[timeframe] = travel_timeframe_distribution.get(timeframe, 0) + 1

        region_travel_key = f"{region}|{timeframe}"
        region_travel_timeframe_combinations[region_travel_key] = (
            region_travel_timeframe_combinations.get(region_travel_key, 0) + 1
        )

        intent_key = "interested" if r.get("interested_in_return") else "not_interested"
        region_intent_key = f"{region}|{intent_key}"
        region_interest_combinations[region_intent_key] = (
            region_interest_combinations.get(region_intent_key, 0) + 1
        )

        state = r.get("state") or "Not provided"
        state_distribution[state] = state_distribution.get(state, 0) + 1

        country = r.get("country") or "Not provided"
        country_distribution[country] = country_distribution.get(country, 0) + 1

        role = r.get("relationship_role") or "not_provided"
        role_distribution[role] = role_distribution.get(role, 0) + 1

        position = r.get("household_position") or "not_provided"
        household_position_distribution[position] = household_position_distribution.get(position, 0) + 1

        return_interest = r.get("return_reconnection_interest") or "maybe_learning_more"
        return_reconnection_interest_distribution[return_interest] = (
            return_reconnection_interest_distribution.get(return_interest, 0) + 1
        )

    return StatsResponse(
        total_users=total_users,
        total_families=total_families,
        total_family_groups=total_family_groups,
        largest_family_size=largest_family_size,
        total_interested_in_return=total_interested,
        total_with_contact_info=total_with_contact_info,
        entry_agreement_accepted_count=entry_agreement_accepted_count,
        ecosystem_updates_opt_in_count=ecosystem_updates_opt_in_count,
        return_reconnection_interest_distribution=return_reconnection_interest_distribution,
        region_distribution=region_distribution,
        travel_timeframe_distribution=travel_timeframe_distribution,
        state_distribution=state_distribution,
        country_distribution=country_distribution,
        role_distribution=role_distribution,
        household_position_distribution=household_position_distribution,
        region_travel_timeframe_combinations=region_travel_timeframe_combinations,
        region_interest_combinations=region_interest_combinations,
    )


def get_registrations() -> list[UserRecord]:
    users = [_normalize_user(u) for u in _load()]
    for user in users:
        user["profile_status"] = _effective_profile_status(user)
    users.sort(key=lambda r: r.get("registered_at", ""), reverse=True)
    return [UserRecord.model_validate(u) for u in users]


def update_registration_relationship(
    user_id: str,
    payload: RelationshipUpdateRequest,
    session_id: Optional[str] = None,
) -> UserRecord:
    users = _load()
    user_index = next((idx for idx, user in enumerate(users) if user.get("user_id") == user_id), -1)
    if user_index == -1:
        raise ValueError("Registration not found")

    current = _normalize_user(users[user_index])
    updates = payload.model_dump(mode="json", exclude_unset=True)

    merged = dict(current)
    for field in [
        "relationship_role",
        "household_position",
        "relationship_notes",
    ]:
        if field in updates:
            merged[field] = updates[field]

    linked_ids: list[str]
    if "linked_to_user_ids" in updates:
        linked_ids = _normalize_linked_to_user_ids(updates.get("linked_to_user_ids"), None)
    elif "linked_to_user_id" in updates:
        linked_ids = _normalize_linked_to_user_ids(None, updates.get("linked_to_user_id"))
    else:
        linked_ids = _normalize_linked_to_user_ids(
            merged.get("linked_to_user_ids"),
            merged.get("linked_to_user_id"),
        )

    if user_id in linked_ids:
        raise ValueError("linked_to_user_ids cannot reference the same user")

    if linked_ids:
        family_members = {
            str(user.get("user_id"))
            for user in users
            if user.get("family_id") == current.get("family_id") and user.get("user_id")
        }
        invalid_ids = [linked_id for linked_id in linked_ids if linked_id not in family_members]
        if invalid_ids:
            raise ValueError("linked_to_user_ids must reference members in the same family")

    _set_linked_fields(merged, linked_ids)

    updated_record = UserRecord.model_validate(merged)
    users[user_index] = updated_record.model_dump(mode="json")
    _save(users)

    had_relationship = bool(
        current.get("relationship_role")
        or current.get("relationship_notes")
        or _normalize_linked_to_user_ids(
            current.get("linked_to_user_ids"),
            current.get("linked_to_user_id"),
        )
    )
    write_activity_event(
        event_type="relationship_updated" if had_relationship else "relationship_created",
        message=(
            f"Relationship updated for {updated_record.full_name}."
            if had_relationship
            else f"Relationship created for {updated_record.full_name}."
        ),
        user_id=updated_record.user_id,
        family_id=updated_record.family_id,
        family_name=updated_record.family_name,
        session_id=session_id,
    )
    write_activity_event(
        event_type="family_group_updated",
        message=f"Family group {updated_record.family_name} relationship graph updated.",
        user_id=updated_record.user_id,
        family_id=updated_record.family_id,
        family_name=updated_record.family_name,
        session_id=session_id,
    )
    return updated_record


def delete_registration(user_id: str, session_id: Optional[str] = None) -> dict[str, str]:
    users = _load()
    target = next((user for user in users if user.get("user_id") == user_id), None)
    if target is None:
        raise ValueError("Registration not found")

    remaining_users: list[dict[str, Any]] = []
    for user in users:
        if user.get("user_id") == user_id:
            continue

        updated_user = dict(user)
        linked_ids = _normalize_linked_to_user_ids(
            updated_user.get("linked_to_user_ids"),
            updated_user.get("linked_to_user_id"),
        )
        linked_ids = [linked_id for linked_id in linked_ids if linked_id != user_id]
        _set_linked_fields(updated_user, linked_ids)

        remaining_users.append(updated_user)

    _save(remaining_users)
    write_activity_event(
        event_type="family_group_updated",
        message=f"Registration removed for {target.get('full_name', 'Unknown member')} and family links were recalculated.",
        user_id=str(target.get("user_id") or user_id),
        family_id=str(target.get("family_id") or "") or None,
        family_name=str(target.get("family_name") or "") or None,
        session_id=session_id,
    )
    return {"message": "Registration deleted successfully."}


def update_registration(
    user_id: str,
    payload: RegistrationUpdateRequest,
    session_id: Optional[str] = None,
) -> UserRecord:
    users = _load()
    user_index = next((idx for idx, user in enumerate(users) if user.get("user_id") == user_id), -1)
    if user_index == -1:
        raise ValueError("Registration not found")

    current = _normalize_user(users[user_index])
    updates = payload.model_dump(mode="json", exclude_unset=True)

    merged = dict(current)
    editable_fields = [
        "full_name",
        "family_name",
        "family_size",
        "origin_region",
        "interested_in_return",
        "email",
        "phone_number",
        "phone",
        "city",
        "state",
        "country",
        "date_of_birth",
        "age_range",
        "preferred_contact_method",
        "travel_timeframe",
        "notes",
        "relationship_role",
        "household_position",
        "relationship_notes",
        "ecosystem_updates_opt_in",
        "return_reconnection_interest",
    ]
    for field in editable_fields:
        if field in updates:
            merged[field] = updates[field]

    if "phone_number" in updates and "phone" not in updates:
        merged["phone"] = merged.get("phone_number")
    if "phone" in updates and "phone_number" not in updates:
        merged["phone_number"] = merged.get("phone")

    if "linked_to_user_ids" in updates:
        linked_ids = _normalize_linked_to_user_ids(updates.get("linked_to_user_ids"), None)
    elif "linked_to_user_id" in updates:
        linked_ids = _normalize_linked_to_user_ids(None, updates.get("linked_to_user_id"))
    else:
        linked_ids = _normalize_linked_to_user_ids(
            merged.get("linked_to_user_ids"),
            merged.get("linked_to_user_id"),
        )

    _set_linked_fields(merged, linked_ids)

    if "family_name" in updates:
        merged["family_id"] = _generate_family_id(str(merged["family_name"]))

    if user_id in linked_ids:
        raise ValueError("linked_to_user_ids cannot reference the same user")

    if linked_ids:
        family_members = {
            str(user.get("user_id"))
            for user in users
            if user.get("family_id") == merged.get("family_id") and user.get("user_id")
        }
        invalid_ids = [linked_id for linked_id in linked_ids if linked_id not in family_members]
        if invalid_ids:
            raise ValueError("linked_to_user_ids must reference members in the same family")

    if merged.get("travel_timeframe"):
        merged["user_stage"] = _derive_user_stage(str(merged["travel_timeframe"])).value

    updated_record = UserRecord.model_validate(merged)
    users[user_index] = updated_record.model_dump(mode="json")

    updated_users: list[dict[str, Any]] = []
    for idx, user in enumerate(users):
        if idx == user_index:
            updated_users.append(user)
            continue

        normalized_user = _normalize_user(user)
        normalized_linked_ids = _normalize_linked_to_user_ids(
            normalized_user.get("linked_to_user_ids"),
            normalized_user.get("linked_to_user_id"),
        )
        if (
            user_id in normalized_linked_ids
            and normalized_user.get("family_id") != updated_record.family_id
        ):
            normalized_linked_ids = [linked_id for linked_id in normalized_linked_ids if linked_id != user_id]
        _set_linked_fields(normalized_user, normalized_linked_ids)
        updated_users.append(normalized_user)

    _save(updated_users)

    write_activity_event(
        event_type="family_group_updated",
        message=f"Registration updated for {updated_record.full_name} in family {updated_record.family_name}.",
        user_id=updated_record.user_id,
        family_id=updated_record.family_id,
        family_name=updated_record.family_name,
        session_id=session_id,
    )
    if any(field in updates for field in ["relationship_role", "household_position", "relationship_notes", "linked_to_user_ids", "linked_to_user_id"]):
        write_activity_event(
            event_type="relationship_updated",
            message=f"Relationship details updated for {updated_record.full_name}.",
            user_id=updated_record.user_id,
            family_id=updated_record.family_id,
            family_name=updated_record.family_name,
            session_id=session_id,
        )
    return updated_record


def get_families() -> list[FamilyGroupResponse]:
    users = [_normalize_user(u) for u in _load()]
    users = [u for u in users if _is_tree_active(u)]
    grouped: dict[str, dict[str, Any]] = {}

    for r in users:
        family_id = r.get("family_id")
        if not family_id:
            continue

        group = grouped.setdefault(
            family_id,
            {
                "family_id": family_id,
                "family_name": r.get("family_name", "Unknown"),
                "total_members": 0,
                "interested_count": 0,
                "origin_regions": set(),
                "members": [],
            },
        )

        group["total_members"] += 1
        if r.get("interested_in_return"):
            group["interested_count"] += 1
        group["origin_regions"].add(r.get("origin_region", OriginRegion.unknown.value))

        group["members"].append(r)

    families: list[FamilyGroupResponse] = []
    for family in grouped.values():
        member_name_by_id = {
            str(member.get("user_id")): str(member.get("full_name", ""))
            for member in family["members"]
            if member.get("user_id")
        }

        member_summaries: list[FamilyMemberSummary] = []
        for member in family["members"]:
            linked_to_user_ids = _normalize_linked_to_user_ids(
                member.get("linked_to_user_ids"),
                member.get("linked_to_user_id"),
            )
            linked_to_full_names = [
                member_name_by_id[linked_id]
                for linked_id in linked_to_user_ids
                if linked_id in member_name_by_id
            ]
            member_summaries.append(
                FamilyMemberSummary(
                    user_id=member.get("user_id", ""),
                    full_name=member.get("full_name", ""),
                    relationship_role=member.get("relationship_role"),
                    household_position=member.get("household_position"),
                    date_of_birth=member.get("date_of_birth"),
                    profile_status=_effective_profile_status(member),
                    linked_to_user_ids=linked_to_user_ids,
                    linked_to_user_id=linked_to_user_ids[0] if linked_to_user_ids else None,
                    relationship_notes=member.get("relationship_notes"),
                    linked_to_full_names=linked_to_full_names,
                    linked_to_full_name=linked_to_full_names[0] if linked_to_full_names else None,
                    relationship_display=_build_relationship_display(
                        member.get("relationship_role"),
                        linked_to_full_names,
                    ),
                )
            )

        family["origin_regions"] = sorted(family["origin_regions"])
        family["members"] = member_summaries
        families.append(FamilyGroupResponse(**family))

    families.sort(key=lambda g: g.total_members, reverse=True)
    return families


def get_family_tree(
    family_id: str,
    session_id: Optional[str] = None,
    user_id: Optional[str] = None,
) -> dict[str, Any]:
    """Build a lightweight hierarchical family tree for a single family group."""
    users = [_normalize_user(u) for u in _load()]
    users = [u for u in users if _is_tree_active(u)]
    family_members = [u for u in users if u.get("family_id") == family_id]
    if not family_members:
        raise ValueError("Family not found")

    family_name = str(family_members[0].get("family_name") or "Unknown")
    id_set = {
        str(member.get("user_id"))
        for member in family_members
        if member.get("user_id")
    }

    role_by_id = {
        str(member.get("user_id")): str(member.get("relationship_role") or "")
        for member in family_members
        if member.get("user_id")
    }
    name_by_id = {
        str(member.get("user_id")): str(member.get("full_name") or "Unnamed member")
        for member in family_members
        if member.get("user_id")
    }

    parent_roles = {
        "father",
        "mother",
        "parent",
        "grandfather",
        "grandmother",
        "grandparent",
        "elder_ancestor",
    }
    child_roles = {
        "son",
        "daughter",
        "child",
        "grandson",
        "granddaughter",
        "dependent",
    }

    node_map: dict[str, dict[str, Any]] = {}
    outgoing: dict[str, list[str]] = {}
    incoming: dict[str, int] = {}

    for member in family_members:
        user_id = str(member.get("user_id") or "").strip()
        if not user_id:
            continue

        linked_ids = [
            linked_id
            for linked_id in _normalize_linked_to_user_ids(
                member.get("linked_to_user_ids"),
                member.get("linked_to_user_id"),
            )
            if linked_id in id_set and linked_id != user_id
        ]
        linked_names = [name_by_id[linked_id] for linked_id in linked_ids if linked_id in name_by_id]

        node_map[user_id] = {
            "user_id": user_id,
            "full_name": str(member.get("full_name") or "Unnamed member"),
            "relationship_role": member.get("relationship_role"),
            "household_position": member.get("household_position"),
            "relationship_notes": member.get("relationship_notes"),
            "linked_to_user_ids": linked_ids,
            "linked_to_full_names": linked_names,
            "relationship_display": _build_relationship_display(
                member.get("relationship_role"),
                linked_names,
            ),
        }
        outgoing[user_id] = []
        incoming[user_id] = 0

    def add_edge(parent_id: str, child_id: str) -> None:
        if parent_id == child_id:
            return
        if parent_id not in node_map or child_id not in node_map:
            return
        if child_id in outgoing[parent_id]:
            return
        outgoing[parent_id].append(child_id)
        incoming[child_id] += 1

    def sibling_label_for_role(role: str) -> str:
        normalized = str(role or "").strip().lower()
        if normalized in {"son", "brother", "father", "grandson", "grandfather"}:
            return "Brother"
        if normalized in {"daughter", "sister", "mother", "granddaughter", "grandmother"}:
            return "Sister"
        return "Sibling"

    for user_id, node in node_map.items():
        member_role = str(node.get("relationship_role") or "").strip().lower()
        for linked_id in node.get("linked_to_user_ids", []):
            linked_role = str(role_by_id.get(linked_id) or "").strip().lower()
            if member_role in parent_roles:
                add_edge(user_id, linked_id)
            elif member_role in child_roles:
                add_edge(linked_id, user_id)
            elif linked_role in parent_roles:
                add_edge(linked_id, user_id)
            elif linked_role in child_roles:
                add_edge(user_id, linked_id)
            else:
                add_edge(user_id, linked_id)

    sibling_ids_by_member: dict[str, set[str]] = {user_id: set() for user_id in node_map}
    for child_ids in outgoing.values():
        for child_id in child_ids:
            siblings = {sib_id for sib_id in child_ids if sib_id != child_id}
            sibling_ids_by_member[child_id].update(siblings)

    for user_id, node in node_map.items():
        siblings = sorted(
            sibling_ids_by_member.get(user_id, set()),
            key=lambda sib_id: name_by_id.get(sib_id, "").lower(),
        )
        sibling_names = [name_by_id[sib_id] for sib_id in siblings if sib_id in name_by_id]

        base_relationship = str(node.get("relationship_display") or "").strip()
        sibling_phrase = ""
        if sibling_names:
            sibling_phrase = f"{sibling_label_for_role(str(node.get('relationship_role') or ''))} of {_human_join(sibling_names)}"

        if base_relationship and base_relationship != "Relationship not linked yet.":
            relationship_summary = (
                f"{base_relationship}; {sibling_phrase}" if sibling_phrase else base_relationship
            )
        else:
            relationship_summary = sibling_phrase or "Relationship not linked yet."

        node["relationship_summary"] = relationship_summary

    root_ids = [user_id for user_id, in_count in incoming.items() if in_count == 0]
    if not root_ids:
        root_ids = [
            user_id
            for user_id, role in role_by_id.items()
            if str(role or "").strip().lower() in parent_roles
        ]
    if not root_ids:
        root_ids = sorted(node_map.keys())

    def build_node(user_id: str, trail: set[str]) -> dict[str, Any]:
        base = dict(node_map[user_id])
        if user_id in trail:
            base["children"] = []
            return base
        next_trail = set(trail)
        next_trail.add(user_id)
        base["children"] = [
            build_node(child_id, next_trail)
            for child_id in outgoing.get(user_id, [])
        ]
        return base

    roots = [build_node(root_id, set()) for root_id in root_ids if root_id in node_map]

    tree_payload = {
        "family_id": family_id,
        "family_name": family_name,
        "total_members": len(node_map),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "roots": roots,
    }

    write_activity_event(
        event_type="family_tree_viewed",
        message=f"Family tree generated and viewed for {family_name}.",
        user_id=user_id,
        family_id=family_id,
        family_name=family_name,
        session_id=session_id,
    )

    return tree_payload


def export_registrations_csv() -> str:
    """Export all registrations as CSV format for spreadsheet import."""
    registrations = get_registrations()
    
    output = io.StringIO()
    writer = csv.writer(output, quoting=csv.QUOTE_MINIMAL)
    
    # Write header row
    headers = [
        "user_id",
        "family_id",
        "full_name",
        "family_name",
        "family_size",
        "origin_region",
        "interested_in_return",
        "email",
        "phone_number",
        "phone",
        "city",
        "state",
        "country",
        "date_of_birth",
        "age_range",
        "preferred_contact_method",
        "travel_timeframe",
        "relationship_role",
        "household_position",
        "linked_to_user_ids",
        "relationship_notes",
        "profile_status",
        "merged_into_user_id",
        "user_stage",
        "notes",
        "entry_agreement_accepted",
        "entry_agreement_accepted_at",
        "ecosystem_updates_opt_in",
        "return_reconnection_interest",
        "registered_at",
    ]
    writer.writerow(headers)
    
    # Write data rows
    for reg in registrations:
        row = [
            reg.user_id,
            reg.family_id,
            reg.full_name,
            reg.family_name,
            reg.family_size,
            reg.origin_region,
            str(reg.interested_in_return),
            reg.email or "",
            reg.phone_number or reg.phone or "",
            reg.phone or "",
            reg.city or "",
            reg.state or "",
            reg.country or "",
            reg.date_of_birth or "",
            reg.age_range or "",
            reg.preferred_contact_method or "",
            reg.travel_timeframe or "",
            reg.relationship_role or "",
            reg.household_position or "",
            ",".join(reg.linked_to_user_ids) if reg.linked_to_user_ids else "",
            reg.relationship_notes or "",
            reg.profile_status or "",
            reg.merged_into_user_id or "",
            reg.user_stage,
            reg.notes or "",
            str(reg.entry_agreement_accepted),
            reg.entry_agreement_accepted_at,
            str(reg.ecosystem_updates_opt_in),
            str(reg.return_reconnection_interest),
            reg.registered_at,
        ]
        writer.writerow(row)
    
    return output.getvalue()


def get_duplicate_profiles(
    family_id: Optional[str] = None,
) -> list[DuplicateFamilyGroupResponse]:
    users = [_normalize_user(u) for u in _load()]
    grouped: dict[str, list[dict[str, Any]]] = {}
    family_name_by_id: dict[str, str] = {}
    for user in users:
        if not _is_tree_active(user):
            continue
        current_family_id = str(user.get("family_id") or "").strip()
        if not current_family_id:
            continue
        if family_id and current_family_id != family_id:
            continue
        grouped.setdefault(current_family_id, []).append(user)
        family_name_by_id[current_family_id] = str(user.get("family_name") or "Unknown")

    responses: list[DuplicateFamilyGroupResponse] = []
    for current_family_id, members in grouped.items():
        candidates = _detect_duplicate_candidates_for_family(members)
        if not candidates:
            continue
        responses.append(
            DuplicateFamilyGroupResponse(
                family_id=current_family_id,
                family_name=family_name_by_id.get(current_family_id, "Unknown"),
                candidates=candidates,
            )
        )
        for candidate in candidates:
            if candidate.queue_state != "active":
                continue
            write_activity_event(
                event_type="duplicate_detected",
                message=_append_merge_details(
                    "Duplicate profile candidate detected.",
                    candidate.primary_user_id,
                    candidate.duplicate_user_id,
                    candidate.confidence_score,
                    candidate.match_reasons,
                ),
                user_id=candidate.primary_user_id,
                family_id=current_family_id,
                family_name=family_name_by_id.get(current_family_id, "Unknown"),
            )

    responses.sort(key=lambda item: item.family_name.lower())
    return responses


def merge_duplicate_profile(
    primary_user_id: str,
    duplicate_user_id: str,
    session_id: Optional[str] = None,
) -> DuplicateActionResponse:
    users = [_normalize_user(u) for u in _load()]
    primary = next((u for u in users if str(u.get("user_id") or "") == primary_user_id), None)
    duplicate = next((u for u in users if str(u.get("user_id") or "") == duplicate_user_id), None)
    if not primary or not duplicate:
        raise ValueError("Primary or duplicate profile not found")
    if primary_user_id == duplicate_user_id:
        raise ValueError("Primary and duplicate users must be different")
    if str(primary.get("family_id") or "") != str(duplicate.get("family_id") or ""):
        raise ValueError("Profiles must belong to the same family")

    family_id = str(primary.get("family_id") or "")
    family_name = str(primary.get("family_name") or duplicate.get("family_name") or "Unknown")

    candidates = _detect_duplicate_candidates_for_family([u for u in users if str(u.get("family_id") or "") == family_id and _is_tree_active(u)])
    candidate = next(
        (
            c for c in candidates
            if c.primary_user_id == primary_user_id and c.duplicate_user_id == duplicate_user_id
        ),
        DuplicateProfileCandidate(
            primary_user_id=primary_user_id,
            duplicate_user_id=duplicate_user_id,
            primary_full_name=str(primary.get("full_name") or "Unnamed member"),
            duplicate_full_name=str(duplicate.get("full_name") or "Unnamed member"),
            family_id=family_id,
            confidence_score=0,
            match_reasons=[],
            review_only=True,
        ),
    )

    merged_primary = dict(primary)
    primary_links = set(_normalize_linked_to_user_ids(primary.get("linked_to_user_ids"), primary.get("linked_to_user_id")))
    duplicate_links = set(_normalize_linked_to_user_ids(duplicate.get("linked_to_user_ids"), duplicate.get("linked_to_user_id")))
    merged_links = sorted((primary_links.union(duplicate_links)) - {primary_user_id, duplicate_user_id})
    _set_linked_fields(merged_primary, merged_links)

    for field in [
        "email",
        "phone_number",
        "phone",
        "city",
        "state",
        "country",
        "date_of_birth",
        "age_range",
        "preferred_contact_method",
        "relationship_notes",
        "notes",
    ]:
        if not merged_primary.get(field) and duplicate.get(field):
            merged_primary[field] = duplicate.get(field)

    merged_primary["profile_status"] = _STATUS_ACTIVE
    merged_primary["merged_into_user_id"] = None
    _drop_pair_decision(merged_primary, duplicate_user_id)

    merged_duplicate = dict(duplicate)
    merged_duplicate["profile_status"] = _STATUS_MERGED_ARCHIVED
    merged_duplicate["merged_into_user_id"] = primary_user_id
    _set_linked_fields(merged_duplicate, [])
    _drop_pair_decision(merged_duplicate, primary_user_id)

    updated_users: list[dict[str, Any]] = []
    for user in users:
        user_id = str(user.get("user_id") or "")
        if user_id == primary_user_id:
            updated_users.append(_normalize_user(merged_primary))
            continue
        if user_id == duplicate_user_id:
            updated_users.append(_normalize_user(merged_duplicate))
            continue
        linked_ids = _normalize_linked_to_user_ids(user.get("linked_to_user_ids"), user.get("linked_to_user_id"))
        replaced = [primary_user_id if linked_id == duplicate_user_id else linked_id for linked_id in linked_ids]
        deduped: list[str] = []
        for linked_id in replaced:
            if linked_id and linked_id != user_id and linked_id not in deduped:
                deduped.append(linked_id)
        updated_user = dict(user)
        _set_linked_fields(updated_user, deduped)
        if user_id in {primary_user_id, duplicate_user_id}:
            other_id = duplicate_user_id if user_id == primary_user_id else primary_user_id
            _drop_pair_decision(updated_user, other_id)
        updated_users.append(_normalize_user(updated_user))

    _save(updated_users)

    write_activity_event(
        event_type="duplicate_merged",
        message=_append_merge_details(
            f"Duplicate profile merged safely into primary profile.",
            primary_user_id,
            duplicate_user_id,
            candidate.confidence_score,
            candidate.match_reasons,
        ),
        user_id=primary_user_id,
        family_id=family_id,
        family_name=family_name,
        session_id=session_id,
    )
    write_activity_event(
        event_type="family_group_updated",
        message=f"Family group {family_name} duplicate queue updated after merge.",
        user_id=primary_user_id,
        family_id=family_id,
        family_name=family_name,
        session_id=session_id,
    )

    return DuplicateActionResponse(
        success=True,
        message="Duplicate profile merged safely.",
        primary_user_id=primary_user_id,
        duplicate_user_id=duplicate_user_id,
        family_id=family_id,
    )


def ignore_duplicate_profile(
    primary_user_id: str,
    duplicate_user_id: str,
    session_id: Optional[str] = None,
) -> DuplicateActionResponse:
    users = [_normalize_user(u) for u in _load()]
    primary = next((u for u in users if str(u.get("user_id") or "") == primary_user_id), None)
    duplicate = next((u for u in users if str(u.get("user_id") or "") == duplicate_user_id), None)
    if not primary or not duplicate:
        raise ValueError("Primary or duplicate profile not found")
    if str(primary.get("family_id") or "") != str(duplicate.get("family_id") or ""):
        raise ValueError("Profiles must belong to the same family")

    family_id = str(primary.get("family_id") or "")
    family_name = str(primary.get("family_name") or duplicate.get("family_name") or "Unknown")

    updated_users: list[dict[str, Any]] = []
    for user in users:
        user_id = str(user.get("user_id") or "")
        updated_user = dict(user)
        if user_id == primary_user_id:
            _upsert_pair_decision(updated_user, duplicate_user_id, _DUP_DECISION_KEEP_SEPARATE)
        if user_id == duplicate_user_id:
            _upsert_pair_decision(updated_user, primary_user_id, _DUP_DECISION_KEEP_SEPARATE)
        updated_users.append(_normalize_user(updated_user))
    _save(updated_users)

    candidates = _detect_duplicate_candidates_for_family([u for u in users if str(u.get("family_id") or "") == family_id and _is_tree_active(u)])
    candidate = next(
        (
            c for c in candidates
            if c.primary_user_id == primary_user_id and c.duplicate_user_id == duplicate_user_id
        ),
        None,
    )
    confidence = candidate.confidence_score if candidate else 0
    reasons = candidate.match_reasons if candidate else []

    write_activity_event(
        event_type="duplicate_ignored",
        message=_append_merge_details(
            "Duplicate pair marked separate and removed from duplicate review.",
            primary_user_id,
            duplicate_user_id,
            confidence,
            reasons,
        ),
        user_id=primary_user_id,
        family_id=family_id,
        family_name=family_name,
        session_id=session_id,
    )

    return DuplicateActionResponse(
        success=True,
        message="Pair marked separate and removed from duplicate review.",
        primary_user_id=primary_user_id,
        duplicate_user_id=duplicate_user_id,
        family_id=family_id,
    )


def review_later_duplicate_profile(
    primary_user_id: str,
    duplicate_user_id: str,
    session_id: Optional[str] = None,
) -> DuplicateActionResponse:
    users = [_normalize_user(u) for u in _load()]
    primary = next((u for u in users if str(u.get("user_id") or "") == primary_user_id), None)
    duplicate = next((u for u in users if str(u.get("user_id") or "") == duplicate_user_id), None)
    if not primary or not duplicate:
        raise ValueError("Primary or duplicate profile not found")
    if str(primary.get("family_id") or "") != str(duplicate.get("family_id") or ""):
        raise ValueError("Profiles must belong to the same family")

    family_id = str(primary.get("family_id") or "")
    family_name = str(primary.get("family_name") or duplicate.get("family_name") or "Unknown")

    updated_users: list[dict[str, Any]] = []
    for user in users:
        user_id = str(user.get("user_id") or "")
        updated_user = dict(user)
        if user_id == primary_user_id:
            _upsert_pair_decision(updated_user, duplicate_user_id, _DUP_DECISION_REVIEW_LATER)
        if user_id == duplicate_user_id:
            _upsert_pair_decision(updated_user, primary_user_id, _DUP_DECISION_REVIEW_LATER)
        updated_users.append(_normalize_user(updated_user))
    _save(updated_users)

    write_activity_event(
        event_type="duplicate_ignored",
        message=_append_merge_details(
            "Duplicate pair moved to review later queue.",
            primary_user_id,
            duplicate_user_id,
            0,
            ["review_later"],
        ),
        user_id=primary_user_id,
        family_id=family_id,
        family_name=family_name,
        session_id=session_id,
    )

    return DuplicateActionResponse(
        success=True,
        message="Pair moved to Review Later queue.",
        primary_user_id=primary_user_id,
        duplicate_user_id=duplicate_user_id,
        family_id=family_id,
    )
