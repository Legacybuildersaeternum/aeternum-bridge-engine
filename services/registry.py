import csv
import io
import json
import uuid
import hashlib
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from models.user import (
    DuplicateActionResponse,
    DuplicateFamilyGroupResponse,
    DuplicateProfileCandidate,
    FamilyGroupResponse,
    FamilyMemberSummary,
    HouseholdPosition,
    OriginRegion,
    RegistrationUpdateRequest,
    RelationshipSuggestionCandidate,
    RelationshipSuggestionResponse,
    RelationshipUpdateRequest,
    RelationshipRole,
    StatsResponse,
    TravelTimeframe,
    UserRecord,
    UserRegistration,
    UserStage,
)

logger = logging.getLogger(__name__)

DATA_FILE = Path(__file__).resolve().parents[1] / "data" / "diaspora_registry.json"
_EMPTY_STORE: dict[str, Any] = {"users": []}
ACTIVITY_LOG_FILE = Path(__file__).resolve().parents[1] / "data" / "legacy_activity_log.json"
_EMPTY_ACTIVITY_STORE: dict[str, Any] = {"events": []}
_ACTIVITY_LOG_LOCK = threading.RLock()


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

    phone_a = "".join(ch for ch in str(primary.get("phone") or "") if ch.isdigit())
    phone_b = "".join(ch for ch in str(duplicate.get("phone") or "") if ch.isdigit())
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
    _ensure_file()
    try:
        with DATA_FILE.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError:
        logger.error("Registry data file is invalid JSON at %s", DATA_FILE)
        return []
    # Support legacy flat-list format produced before this fix
    if isinstance(data, list):
        return data
    return data.get("users", [])


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
    _ensure_file()
    with DATA_FILE.open("w", encoding="utf-8") as f:
        json.dump({"users": users}, f, indent=2, ensure_ascii=False)
    print("User saved successfully")
    logger.info("Data file updated — total users: %d", len(users))


def _normalize_user(record: dict[str, Any]) -> dict[str, Any]:
    """Backfill optional fields for older records and normalize canonical values."""
    normalized = dict(record)
    for key in [
        "email",
        "phone",
        "city",
        "state",
        "country",
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
    ]:
        normalized.setdefault(key, None)

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
        phone=payload.phone,
        city=payload.city,
        state=payload.state,
        country=payload.country,
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
    total_with_contact_info = sum(1 for r in users if r.get("email") or r.get("phone"))

    region_distribution: dict[str, int] = {}
    travel_timeframe_distribution: dict[str, int] = {}
    state_distribution: dict[str, int] = {}
    country_distribution: dict[str, int] = {}
    role_distribution: dict[str, int] = {}
    household_position_distribution: dict[str, int] = {}
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

    return StatsResponse(
        total_users=total_users,
        total_families=total_families,
        total_family_groups=total_family_groups,
        largest_family_size=largest_family_size,
        total_interested_in_return=total_interested,
        total_with_contact_info=total_with_contact_info,
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
        "phone",
        "city",
        "state",
        "country",
        "age_range",
        "preferred_contact_method",
        "travel_timeframe",
        "notes",
        "relationship_role",
        "household_position",
        "relationship_notes",
    ]
    for field in editable_fields:
        if field in updates:
            merged[field] = updates[field]

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
        "phone",
        "city",
        "state",
        "country",
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
            reg.phone or "",
            reg.city or "",
            reg.state or "",
            reg.country or "",
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
        "phone",
        "city",
        "state",
        "country",
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
