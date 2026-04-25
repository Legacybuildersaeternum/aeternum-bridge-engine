import csv
import io
import json
import uuid
import hashlib
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from models.user import (
    FamilyGroupResponse,
    FamilyMemberSummary,
    HouseholdPosition,
    OriginRegion,
    RegistrationUpdateRequest,
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


def _ensure_file() -> None:
    """Create data file with empty structure if it does not exist."""
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not DATA_FILE.exists():
        with DATA_FILE.open("w", encoding="utf-8") as f:
            json.dump(_EMPTY_STORE, f, indent=2)
        logger.info("Created new data file at %s", DATA_FILE)


def _load() -> list[dict[str, Any]]:
    _ensure_file()
    with DATA_FILE.open("r", encoding="utf-8") as f:
        data = json.load(f)
    # Support legacy flat-list format produced before this fix
    if isinstance(data, list):
        return data
    return data.get("users", [])


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
        "notes",
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


def register_user(payload: UserRegistration) -> UserRecord:
    users = _load()
    family_id = _generate_family_id(payload.family_name)
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
        user_stage=_derive_user_stage(
            (payload.travel_timeframe or TravelTimeframe.not_sure_yet).value
        ),
        notes=payload.notes,
        registered_at=registered_at,
    )
    users.append(record.model_dump(mode="json"))
    _save(users)
    return record


def get_stats() -> StatsResponse:
    users = [_normalize_user(u) for u in _load()]

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
    users.sort(key=lambda r: r.get("registered_at", ""), reverse=True)
    return [UserRecord.model_validate(u) for u in users]


def update_registration_relationship(
    user_id: str,
    payload: RelationshipUpdateRequest,
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
    return updated_record


def delete_registration(user_id: str) -> dict[str, str]:
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
    return {"message": "Registration deleted successfully."}


def update_registration(user_id: str, payload: RegistrationUpdateRequest) -> UserRecord:
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
    return updated_record


def get_families() -> list[FamilyGroupResponse]:
    users = [_normalize_user(u) for u in _load()]
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


def get_family_tree(family_id: str) -> dict[str, Any]:
    """Build a lightweight hierarchical family tree for a single family group."""
    users = [_normalize_user(u) for u in _load()]
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

    return {
        "family_id": family_id,
        "family_name": family_name,
        "total_members": len(node_map),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "roots": roots,
    }


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
            reg.user_stage,
            reg.notes or "",
            reg.registered_at,
        ]
        writer.writerow(row)
    
    return output.getvalue()
