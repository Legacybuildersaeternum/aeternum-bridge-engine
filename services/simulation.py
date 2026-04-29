"""Phase 45.5 — Controlled System Activation simulation helpers.

Simulation functions create and operate on test-only records flagged with
test_user=True / simulation_created=True. They NEVER touch production users.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from services import registry
from services import cohorts as cohort_service
from services import movements as movement_service
from services import proofs as proof_service


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get_test_users() -> list[dict[str, Any]]:
    """Return live registry records flagged as test users."""
    return [
        u.model_dump(mode="json")
        for u in registry.get_registrations()
        if u.test_user
    ]


def _get_living_test_users() -> list[dict[str, Any]]:
    return [u for u in _get_test_users() if not u.get("ancestor_record")]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def create_test_user_set(
    session_id: Optional[str] = None,
) -> dict[str, Any]:
    """
    Register a set of 5 diverse simulation test users into the registry.
    Each user is marked test_user=True, simulation_created=True.
    Returns a list of created user_ids and names.
    """
    # Direct append-safe write using internal registry helpers
    from services.registry import _load, _save, refresh_user_trust, write_activity_event, _normalize_user

    users_raw = _load()
    now = _now_iso()

    # Prevent duplicate creation – skip if 5+ test users already exist
    existing_test_count = sum(1 for u in users_raw if u.get("test_user"))
    if existing_test_count >= 5:
        return {
            "skipped": True,
            "reason": "Test user set already exists (5+ test users found). Skipped to prevent duplication.",
            "existing_count": existing_test_count,
        }

    test_users: list[dict[str, Any]] = [
        # West Africa heritage, relocation interest
        {
            "user_id": "sim_" + uuid.uuid4().hex[:10],
            "family_id": "simfam_" + uuid.uuid4().hex[:8],
            "full_name": "Ama Asante (Test)",
            "family_name": "Asante",
            "family_size": 3,
            "origin_region": "west_africa",
            "interested_in_return": True,
            "city": "Atlanta",
            "state": "GA",
            "country": "USA",
            "date_of_birth": "1990-03-15",
            "birth_date": "1990-03-15",
            "age_range": "30-44",
            "preferred_contact_method": "email",
            "travel_timeframe": "1-3_years",
            "relationship_role": "mother",
            "household_position": "primary_representative",
            "profile_status": "active",
            "user_stage": "early_planning",
            "entry_agreement_accepted": True,
            "entry_agreement_accepted_at": now,
            "ecosystem_updates_opt_in": True,
            "return_reconnection_interest": "yes_interested_return_reconnection",
            "onboarding_started": True,
            "onboarding_started_at": now,
            "onboarding_completed": True,
            "onboarding_completed_at": now,
            "registered_at": now,
            "is_deceased": False,
            "living_status": "living",
            "ancestor_record": False,
            "heritage_country": "Ghana",
            "heritage_region": "Ashanti",
            "heritage_group": "Akan",
            "relocation_interest_level": "actively_planning",
            "discoverable_by_origin_communities": True,
            "open_to_cultural_guides": True,
            "open_to_relocation_guidance": True,
            "preferred_contact_scope": "cohort_members",
            "verification_status": "family_submitted",
            "trust_score": 0,
            "verification_level": "UNVERIFIED",
            "user_role": None,
            "test_user": True,
            "simulation_created": True,
        },
        # Unknown origin, diaspora member
        {
            "user_id": "sim_" + uuid.uuid4().hex[:10],
            "family_id": "simfam_" + uuid.uuid4().hex[:8],
            "full_name": "Marcus Unknown (Test)",
            "family_name": "Unknown",
            "family_size": 2,
            "origin_region": "unknown",
            "interested_in_return": False,
            "city": "Chicago",
            "state": "IL",
            "country": "USA",
            "date_of_birth": "1985-07-22",
            "birth_date": "1985-07-22",
            "age_range": "30-44",
            "preferred_contact_method": "email",
            "travel_timeframe": "not_sure_yet",
            "relationship_role": "father",
            "household_position": "primary_representative",
            "profile_status": "active",
            "user_stage": "exploring",
            "entry_agreement_accepted": True,
            "entry_agreement_accepted_at": now,
            "ecosystem_updates_opt_in": False,
            "return_reconnection_interest": "maybe_learning_more",
            "onboarding_started": True,
            "onboarding_started_at": now,
            "onboarding_completed": False,
            "registered_at": now,
            "is_deceased": False,
            "living_status": "living",
            "ancestor_record": False,
            "heritage_country": None,
            "relocation_interest_level": "learning_only",
            "discoverable_by_origin_communities": False,
            "open_to_cultural_guides": False,
            "open_to_relocation_guidance": True,
            "preferred_contact_scope": "private",
            "verification_status": "family_submitted",
            "trust_score": 0,
            "verification_level": "UNVERIFIED",
            "user_role": None,
            "test_user": True,
            "simulation_created": True,
        },
        # East Africa, strongly relocation-interested
        {
            "user_id": "sim_" + uuid.uuid4().hex[:10],
            "family_id": "simfam_" + uuid.uuid4().hex[:8],
            "full_name": "Neema Odhiambo (Test)",
            "family_name": "Odhiambo",
            "family_size": 4,
            "origin_region": "east_africa",
            "interested_in_return": True,
            "city": "Houston",
            "state": "TX",
            "country": "USA",
            "date_of_birth": "1988-11-05",
            "birth_date": "1988-11-05",
            "age_range": "30-44",
            "preferred_contact_method": "email",
            "travel_timeframe": "within_1_year",
            "relationship_role": "mother",
            "household_position": "primary_representative",
            "profile_status": "active",
            "user_stage": "high_intent",
            "entry_agreement_accepted": True,
            "entry_agreement_accepted_at": now,
            "ecosystem_updates_opt_in": True,
            "return_reconnection_interest": "yes_interested_return_reconnection",
            "onboarding_started": True,
            "onboarding_started_at": now,
            "onboarding_completed": True,
            "onboarding_completed_at": now,
            "registered_at": now,
            "is_deceased": False,
            "living_status": "living",
            "ancestor_record": False,
            "heritage_country": "Kenya",
            "heritage_region": "Nyanza",
            "relocation_interest_level": "actively_planning",
            "discoverable_by_origin_communities": True,
            "open_to_cultural_guides": True,
            "open_to_relocation_guidance": True,
            "preferred_contact_scope": "cohort_members",
            "verification_status": "family_submitted",
            "trust_score": 0,
            "verification_level": "UNVERIFIED",
            "user_role": "guide",
            "test_user": True,
            "simulation_created": True,
        },
        # Ancestor / deceased record — should be excluded from messaging/connections
        {
            "user_id": "sim_" + uuid.uuid4().hex[:10],
            "family_id": "simfam_" + uuid.uuid4().hex[:8],
            "full_name": "Kwame Asante Sr. (Test Ancestor)",
            "family_name": "Asante",
            "family_size": 1,
            "origin_region": "west_africa",
            "interested_in_return": False,
            "date_of_birth": "1920-01-01",
            "birth_date": "1920-01-01",
            "death_date": "1985-06-01",
            "entry_agreement_accepted": False,
            "entry_agreement_accepted_at": now,
            "ecosystem_updates_opt_in": False,
            "profile_status": "active",
            "user_stage": "exploring",
            "onboarding_started": False,
            "onboarding_completed": False,
            "registered_at": now,
            "is_deceased": True,
            "living_status": "deceased",
            "ancestor_record": True,
            "added_by_user_id": "sim_system",
            "memorial_notes": "Simulation test ancestor record",
            "heritage_country": "Ghana",
            "discoverable_by_origin_communities": False,
            "open_to_cultural_guides": False,
            "open_to_relocation_guidance": False,
            "preferred_contact_scope": "private",
            "verification_status": "family_submitted",
            "trust_score": 0,
            "verification_level": "UNVERIFIED",
            "user_role": None,
            "test_user": True,
            "simulation_created": True,
        },
        # Guide-style user — community cultural connector
        {
            "user_id": "sim_" + uuid.uuid4().hex[:10],
            "family_id": "simfam_" + uuid.uuid4().hex[:8],
            "full_name": "Zainab Diallo (Test Guide)",
            "family_name": "Diallo",
            "family_size": 2,
            "origin_region": "west_africa",
            "interested_in_return": True,
            "city": "New York",
            "state": "NY",
            "country": "USA",
            "date_of_birth": "1982-04-30",
            "birth_date": "1982-04-30",
            "age_range": "40-54",
            "preferred_contact_method": "email",
            "travel_timeframe": "3-5_years",
            "relationship_role": "mother",
            "household_position": "elder",
            "profile_status": "active",
            "user_stage": "long_term_planning",
            "entry_agreement_accepted": True,
            "entry_agreement_accepted_at": now,
            "ecosystem_updates_opt_in": True,
            "return_reconnection_interest": "maybe_learning_more",
            "onboarding_started": True,
            "onboarding_started_at": now,
            "onboarding_completed": True,
            "onboarding_completed_at": now,
            "registered_at": now,
            "is_deceased": False,
            "living_status": "living",
            "ancestor_record": False,
            "heritage_country": "Guinea",
            "heritage_region": "Fouta Djallon",
            "relocation_interest_level": "considering",
            "discoverable_by_origin_communities": True,
            "open_to_cultural_guides": True,
            "open_to_relocation_guidance": True,
            "preferred_contact_scope": "verified_guides_only",
            "verification_status": "family_submitted",
            "trust_score": 0,
            "verification_level": "UNVERIFIED",
            "user_role": "guide",
            "test_user": True,
            "simulation_created": True,
        },
    ]

    normalized_test_users = [_normalize_user(u) for u in test_users]
    users_raw.extend(normalized_test_users)
    _save(users_raw)

    created_ids = []
    for u in normalized_test_users:
        uid = str(u.get("user_id") or "")
        if uid and not u.get("ancestor_record"):
            refresh_user_trust(uid, reason="simulation_created", session_id=session_id)
        created_ids.append({"user_id": uid, "full_name": u.get("full_name"), "ancestor_record": bool(u.get("ancestor_record"))})

    write_activity_event(
        event_type="SIMULATION_TEST_USERS_CREATED",
        message=f"Simulation created {len(created_ids)} test users.",
        session_id=session_id,
        extra={"test_user_ids": [u["user_id"] for u in created_ids]},
    )

    return {"created": created_ids, "count": len(created_ids)}


def simulate_cohort_joins(session_id: Optional[str] = None) -> dict[str, Any]:
    """Join all living test users into available cohorts."""
    living_users = _get_living_test_users()
    if not living_users:
        return {"skipped": True, "reason": "No living test users found. Run create-test-users first."}

    cohorts = cohort_service.list_cohorts()
    if not cohorts:
        return {"skipped": True, "reason": "No cohorts found in the system."}

    results: list[dict[str, Any]] = []
    for user in living_users:
        uid = str(user.get("user_id") or "")
        for cohort in cohorts[:3]:  # join up to 3 cohorts per user
            cohort_id = str(cohort.get("cohort_id") or "")
            try:
                cohort_service.join_cohort(uid, cohort_id)
                results.append({"user_id": uid, "cohort_id": cohort_id, "status": "joined"})
            except ValueError:
                results.append({"user_id": uid, "cohort_id": cohort_id, "status": "already_member_or_skipped"})

    for user in living_users:
        uid = str(user.get("user_id") or "")
        registry.refresh_user_trust(uid, reason="simulation_cohort_join", session_id=session_id)

    registry.write_activity_event(
        event_type="SIMULATION_COHORT_JOINS",
        message=f"Simulation cohort joins attempted: {len(results)} entries.",
        session_id=session_id,
        extra={"join_results": results},
    )
    return {"joins": results, "count": len(results)}


def simulate_movement_joins(session_id: Optional[str] = None) -> dict[str, Any]:
    """Join all living test users into available movements (or create one if none exist)."""
    living_users = _get_living_test_users()
    if not living_users:
        return {"skipped": True, "reason": "No living test users found. Run create-test-users first."}

    movements = movement_service.list_movements()
    if not movements:
        # Create a simulation movement
        creator = living_users[0]
        new_movement = movement_service.create_movement(
            user_id=str(creator.get("user_id") or ""),
            title="Simulation Test Movement",
            region="west_africa",
            country="Ghana",
            target_date="2026-12-31",
        )
        movements = [new_movement]

    target_movement = movements[0]
    movement_id = str(target_movement.get("movement_id") or "")

    results: list[dict[str, Any]] = []
    for user in living_users:
        uid = str(user.get("user_id") or "")
        try:
            movement_service.join_movement(uid, movement_id)
            results.append({"user_id": uid, "movement_id": movement_id, "status": "joined"})
        except ValueError:
            results.append({"user_id": uid, "movement_id": movement_id, "status": "already_member_or_skipped"})

    for user in living_users:
        uid = str(user.get("user_id") or "")
        registry.refresh_user_trust(uid, reason="simulation_movement_join", session_id=session_id)

    registry.write_activity_event(
        event_type="SIMULATION_MOVEMENT_JOINS",
        message=f"Simulation movement joins attempted for movement {movement_id}: {len(results)} entries.",
        session_id=session_id,
        extra={"movement_id": movement_id, "join_results": results},
    )
    return {"movement_id": movement_id, "joins": results, "count": len(results)}


def submit_sample_proof(session_id: Optional[str] = None) -> dict[str, Any]:
    """Submit a sample proof for the first living test user in the first movement they belong to."""
    living_users = _get_living_test_users()
    if not living_users:
        return {"skipped": True, "reason": "No living test users found."}

    # Find first test user who is in a movement
    proof_record = None
    for user in living_users:
        uid = str(user.get("user_id") or "")
        user_movements = movement_service.get_user_movements(uid)
        if user_movements:
            movement_id = str(user_movements[0].get("movement_id") or "")
            try:
                proof_record = proof_service.submit_proof(
                    user_id=uid,
                    movement_id=movement_id,
                    proof_type="community_event",
                    public_summary="[SIMULATION] Attended community cultural event and reported back to movement group.",
                    private_notes="[SIMULATION] Test proof submission for system verification.",
                )
            except ValueError:
                continue
            break

    if not proof_record:
        return {
            "skipped": True,
            "reason": "No living test user found who is a member of any movement. Run simulate-movement-joins first.",
        }

    registry.write_activity_event(
        event_type="SIMULATION_PROOF_SUBMITTED",
        message=f"Simulation proof submitted: {proof_record['proof_id']} by {proof_record['user_id']}.",
        user_id=proof_record["user_id"],
        session_id=session_id,
        extra={"proof_id": proof_record["proof_id"], "movement_id": proof_record["movement_id"]},
    )
    return {"proof": proof_record}


def review_sample_proof(session_id: Optional[str] = None) -> dict[str, Any]:
    """Accept the most recent pending proof from a test user."""
    all_pending = proof_service.list_proofs(status="pending", admin_view=True)
    test_user_ids = {str(u.get("user_id") or "") for u in _get_test_users()}
    test_pending = [p for p in all_pending if str(p.get("user_id") or "") in test_user_ids]

    if not test_pending:
        return {"skipped": True, "reason": "No pending proofs from test users. Run submit-proof first."}

    target = test_pending[0]
    proof_id = str(target.get("proof_id") or "")

    reviewed = proof_service.review_proof(
        proof_id=proof_id,
        decision="accepted",
        reviewer_user_id="sim_admin",
        review_public_note="[SIMULATION] Accepted as part of controlled system activation test.",
    )

    user_id = str(reviewed.get("user_id") or "")
    trust_result = None
    if user_id:
        registry.write_activity_event(
            event_type="REAL_WORLD_PROOF_ACCEPTED",
            message=f"[SIMULATION] Proof {proof_id} accepted.",
            user_id=user_id,
            session_id=session_id,
            extra={"proof_id": proof_id, "movement_id": reviewed.get("movement_id")},
        )
        trust_result = registry.refresh_user_trust(
            user_id, reason="real_world_proof_accepted", session_id=session_id
        )

    registry.write_activity_event(
        event_type="SIMULATION_PROOF_REVIEWED",
        message=f"Simulation proof {proof_id} reviewed as accepted.",
        user_id=user_id or None,
        session_id=session_id,
        extra={"proof_id": proof_id, "decision": "accepted", "trust_update": trust_result},
    )
    return {"proof": reviewed, "trust_update": trust_result}


def refresh_all_trust_scores(session_id: Optional[str] = None) -> dict[str, Any]:
    """Refresh trust scores for all living test users."""
    living_users = _get_living_test_users()
    if not living_users:
        return {"skipped": True, "reason": "No living test users found."}

    results: list[dict[str, Any]] = []
    for user in living_users:
        uid = str(user.get("user_id") or "")
        result = registry.refresh_user_trust(uid, reason="simulation_bulk_refresh", session_id=session_id)
        results.append(result or {"user_id": uid, "error": "not_found"})

    registry.write_activity_event(
        event_type="SIMULATION_TRUST_REFRESHED",
        message=f"Simulation trust refresh completed for {len(results)} test users.",
        session_id=session_id,
        extra={"count": len(results)},
    )
    return {"refreshed": results, "count": len(results)}


def run_full_system_check(session_id: Optional[str] = None) -> dict[str, Any]:
    """Return a system health metrics dict. Read-only; no mutations."""
    from services.registry import _load, get_registry_safety_status
    from services.proofs import list_proofs

    users_raw = _load()
    living_users = [u for u in users_raw if not u.get("ancestor_record") and str(u.get("living_status", "living")) == "living"]
    ancestor_records = [u for u in users_raw if u.get("ancestor_record")]

    cohorts_list = cohort_service.list_cohorts()
    movements_list = movement_service.list_movements()
    all_proofs = list_proofs(admin_view=True)
    pending_proofs = [p for p in all_proofs if str(p.get("status") or "") == "pending"]
    accepted_proofs = [p for p in all_proofs if str(p.get("status") or "") == "accepted"]

    # Trust distribution
    trust_levels: dict[str, int] = {}
    for u in living_users:
        level = str(u.get("verification_level") or "UNVERIFIED")
        trust_levels[level] = trust_levels.get(level, 0) + 1

    # Active connection requests
    from services.registry import _load_connection_requests
    connection_requests = _load_connection_requests()
    active_connections = [
        r for r in connection_requests
        if str(r.get("status") or "") not in {"rejected", "withdrawn", "expired"}
    ]

    safety = get_registry_safety_status()

    registry.write_activity_event(
        event_type="SIMULATION_SYSTEM_CHECK_RUN",
        message="System check completed.",
        session_id=session_id,
        extra={"total_users": len(users_raw), "pending_proofs": len(pending_proofs)},
    )

    return {
        "total_users": len(users_raw),
        "living_users": len(living_users),
        "ancestor_records": len(ancestor_records),
        "cohorts_count": len(cohorts_list),
        "movement_groups_count": len(movements_list),
        "pending_proofs": len(pending_proofs),
        "accepted_proofs": len(accepted_proofs),
        "trust_levels_distribution": trust_levels,
        "active_connection_requests": len(active_connections),
        "data_guard_status": safety.get("data_guard_status", "unknown"),
    }
