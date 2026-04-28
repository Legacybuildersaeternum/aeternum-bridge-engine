from pydantic import BaseModel, Field, field_validator, model_validator
from enum import Enum
from typing import Optional


def _normalize_dropdown_token(value: str) -> str:
    normalized = value.strip().lower().replace("/", "_").replace(" ", "_")
    while "__" in normalized:
        normalized = normalized.replace("__", "_")
    return normalized.strip("_")


class OriginRegion(str, Enum):
    west_africa = "west_africa"
    east_africa = "east_africa"
    central_africa = "central_africa"
    southern_africa = "southern_africa"
    unknown = "unknown"


class LivingStatus(str, Enum):
    living = "living"
    deceased = "deceased"
    unknown = "unknown"


class RelocationInterestLevel(str, Enum):
    not_interested = "not_interested"
    learning_only = "learning_only"
    considering = "considering"
    actively_planning = "actively_planning"
    already_relocated = "already_relocated"


class ContactScope(str, Enum):
    connections_only = "connections_only"
    cohort_members = "cohort_members"
    verified_guides_only = "verified_guides_only"
    private = "private"


class TravelTimeframe(str, Enum):
    within_1_year = "within_1_year"
    one_to_three_years = "1-3_years"
    three_to_five_years = "3-5_years"
    not_sure_yet = "not_sure_yet"


class UserStage(str, Enum):
    high_intent = "high_intent"
    early_planning = "early_planning"
    long_term_planning = "long_term_planning"
    exploring = "exploring"


class RelationshipRole(str, Enum):
    daughter = "daughter"
    son = "son"
    mother = "mother"
    father = "father"
    granddaughter = "granddaughter"
    grandson = "grandson"
    grandmother = "grandmother"
    grandfather = "grandfather"
    sister = "sister"
    brother = "brother"
    spouse_partner = "spouse_partner"
    cousin = "cousin"
    uncle = "uncle"
    aunt = "aunt"
    nephew = "nephew"
    niece = "niece"
    relative = "relative"
    other = "other"
    # Legacy tokens kept for backward compatibility with existing saved records.
    self_role = "self"
    parent = "parent"
    grandparent = "grandparent"
    child = "child"
    sibling = "sibling"
    elder_ancestor = "elder_ancestor"


class HouseholdPosition(str, Enum):
    primary_representative = "primary_representative"
    family_member = "family_member"
    elder = "elder"
    dependent = "dependent"


class ReturnReconnectionInterest(str, Enum):
    yes_interested_return_reconnection = "yes_interested_return_reconnection"
    maybe_learning_more = "maybe_learning_more"
    no_documenting_family_history = "no_documenting_family_history"


class UserRegistration(BaseModel):
    full_name: str
    family_name: str
    family_size: int
    origin_region: OriginRegion
    interested_in_return: bool
    email: Optional[str] = None
    phone_number: Optional[str] = None
    phone: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    country: Optional[str] = None
    date_of_birth: Optional[str] = None
    age_range: Optional[str] = None
    preferred_contact_method: Optional[str] = None
    travel_timeframe: Optional[TravelTimeframe] = None
    relationship_role: Optional[RelationshipRole] = None
    household_position: Optional[HouseholdPosition] = None
    linked_to_user_ids: Optional[list[str]] = None
    linked_to_user_id: Optional[str] = None
    relationship_notes: Optional[str] = None
    notes: Optional[str] = None
    entry_agreement_accepted: bool = False
    ecosystem_updates_opt_in: bool = False
    return_reconnection_interest: Optional[ReturnReconnectionInterest] = None
    # ── Phase 41: Ancestor / Deceased Record ──
    is_deceased: bool = False
    living_status: LivingStatus = LivingStatus.living
    birth_date: Optional[str] = None
    death_date: Optional[str] = None
    ancestor_record: bool = False
    added_by_user_id: Optional[str] = None
    memorial_notes: Optional[str] = None
    verification_status: str = "family_submitted"
    # ── Phase 41: Location Intelligence ──
    current_country: Optional[str] = None
    current_state_region: Optional[str] = None
    current_city: Optional[str] = None
    target_country: Optional[str] = None
    target_region: Optional[str] = None
    heritage_region: Optional[str] = None
    heritage_country: Optional[str] = None
    heritage_group: Optional[str] = None
    relocation_interest_level: Optional[RelocationInterestLevel] = None
    # ── Phase 41: Africa Can Find You ──
    discoverable_by_origin_communities: bool = False
    open_to_cultural_guides: bool = False
    open_to_relocation_guidance: bool = False
    preferred_contact_scope: ContactScope = ContactScope.private
    entry_agreement_accepted_at: Optional[str] = None

    model_config = {
        "json_schema_extra": {
            "example": {
                "full_name": "Joshua Ward",
                "family_name": "Ward",
                "family_size": 3,
                "origin_region": "west_africa",
                "interested_in_return": True,
                "email": "ward.family@example.com",
                "phone_number": "+1-555-0100",
                "city": "Chicago",
                "state": "IL",
                "country": "USA",
                "date_of_birth": "1988-04-12",
                "age_range": "35-44",
                "preferred_contact_method": "email",
                "travel_timeframe": "1-3_years",
                "relationship_role": "father",
                "household_position": "primary_representative",
                "relationship_notes": "Primary household contact",
                "notes": "Ward family reconnection profile",
                "entry_agreement_accepted": True,
                "ecosystem_updates_opt_in": False,
                "return_reconnection_interest": "maybe_learning_more",
            }
        }
    }

    @field_validator("full_name", "family_name")
    @classmethod
    def must_not_be_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Field must not be blank")
        return v.strip()

    @field_validator("family_size")
    @classmethod
    def family_size_positive(cls, v: int) -> int:
        if v < 1:
            raise ValueError("family_size must be at least 1")
        return v

    @field_validator("origin_region", mode="before")
    @classmethod
    def normalize_origin_region(cls, v: str) -> str:
        raw = v.value if isinstance(v, Enum) else str(v)
        return _normalize_dropdown_token(raw)

    @field_validator("travel_timeframe", mode="before")
    @classmethod
    def normalize_travel_timeframe(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        raw = v.value if isinstance(v, Enum) else str(v)
        return _normalize_dropdown_token(raw)

    @field_validator("preferred_contact_method", mode="before")
    @classmethod
    def normalize_preferred_contact_method(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        raw = v.value if isinstance(v, Enum) else str(v)
        value = _normalize_dropdown_token(raw)
        return value or None

    @field_validator("relationship_role", mode="before")
    @classmethod
    def normalize_relationship_role(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        raw = v.value if isinstance(v, Enum) else str(v)
        value = _normalize_dropdown_token(raw)
        return value or None

    @field_validator("household_position", mode="before")
    @classmethod
    def normalize_household_position(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        raw = v.value if isinstance(v, Enum) else str(v)
        value = _normalize_dropdown_token(raw)
        return value or None

    @field_validator("return_reconnection_interest", mode="before")
    @classmethod
    def normalize_return_reconnection_interest(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        raw = v.value if isinstance(v, Enum) else str(v)
        value = _normalize_dropdown_token(raw)
        return value or None

    @field_validator(
        "email",
        "phone_number",
        "phone",
        "city",
        "state",
        "country",
        "date_of_birth",
        "age_range",
        "linked_to_user_id",
        "relationship_notes",
        "notes",
        mode="before",
    )
    @classmethod
    def normalize_optional_text(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        value = str(v).strip()
        return value or None

    @model_validator(mode="after")
    def normalize_phone_aliases(self) -> "UserRegistration":
        if self.phone_number and not self.phone:
            self.phone = self.phone_number
        if self.phone and not self.phone_number:
            self.phone_number = self.phone
        return self

    @field_validator("email")
    @classmethod
    def email_must_be_valid_if_present(cls, v: Optional[str]) -> Optional[str]:
        if not v:
            return v
        if "@" not in v or v.startswith("@") or v.endswith("@"):
            raise ValueError("email must be valid")
        local, domain = v.split("@", 1)
        if not local or "." not in domain:
            raise ValueError("email must be valid")
        return v

    @field_validator("entry_agreement_accepted")
    @classmethod
    def entry_agreement_must_be_accepted(cls, v: bool) -> bool:
        # Ancestor records don't require agreement; enforced via model_validator below
        return v

    @model_validator(mode="after")
    def enforce_living_user_requirements(self) -> "UserRegistration":
        if not self.ancestor_record:
            if not self.entry_agreement_accepted:
                raise ValueError("entry_agreement_accepted must be true for living user registrations")
            if not self.date_of_birth and not self.birth_date:
                raise ValueError("date_of_birth is required for living user registrations")
            if self.return_reconnection_interest is None:
                raise ValueError("return_reconnection_interest is required for living user registrations")
        # Sync is_deceased / living_status with ancestor_record
        if self.ancestor_record:
            self.is_deceased = True
            if self.living_status == LivingStatus.living:
                self.living_status = LivingStatus.deceased
        # Sync birth_date <-> date_of_birth for ancestors
        if self.ancestor_record and self.birth_date and not self.date_of_birth:
            self.date_of_birth = self.birth_date
        if self.date_of_birth and not self.birth_date:
            self.birth_date = self.date_of_birth
        return self

    @field_validator("linked_to_user_ids", mode="before")
    @classmethod
    def normalize_linked_to_user_ids(cls, v: Optional[object]) -> Optional[list[str]]:
        if v is None:
            return None
        values = v if isinstance(v, list) else [v]
        normalized: list[str] = []
        for item in values:
            value = str(item).strip()
            if value and value not in normalized:
                normalized.append(value)
        return normalized or None


class UserRecord(BaseModel):
    user_id: str
    family_id: str
    full_name: str
    family_name: str
    family_size: int
    origin_region: OriginRegion
    interested_in_return: bool
    email: Optional[str] = None
    phone_number: Optional[str] = None
    phone: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    country: Optional[str] = None
    date_of_birth: Optional[str] = None
    age_range: Optional[str] = None
    preferred_contact_method: Optional[str] = None
    travel_timeframe: Optional[TravelTimeframe] = None
    relationship_role: Optional[RelationshipRole] = None
    household_position: Optional[HouseholdPosition] = None
    linked_to_user_ids: Optional[list[str]] = None
    linked_to_user_id: Optional[str] = None
    relationship_notes: Optional[str] = None
    profile_status: Optional[str] = None
    merged_into_user_id: Optional[str] = None
    user_stage: UserStage
    notes: Optional[str] = None
    entry_agreement_accepted: bool
    entry_agreement_accepted_at: str
    ecosystem_updates_opt_in: bool = False
    return_reconnection_interest: Optional[ReturnReconnectionInterest] = None
    onboarding_started: bool = False
    onboarding_started_at: Optional[str] = None
    onboarding_family_tree_started: bool = False
    onboarding_first_connection_explored: bool = False
    onboarding_completed: bool = False
    onboarding_completed_at: Optional[str] = None
    registered_at: str
    # ── Phase 41: Ancestor / Deceased Record ──
    is_deceased: bool = False
    living_status: LivingStatus = LivingStatus.living
    birth_date: Optional[str] = None
    death_date: Optional[str] = None
    ancestor_record: bool = False
    added_by_user_id: Optional[str] = None
    memorial_notes: Optional[str] = None
    verification_status: str = "family_submitted"
    # ── Phase 41: Location Intelligence ──
    current_country: Optional[str] = None
    current_state_region: Optional[str] = None
    current_city: Optional[str] = None
    target_country: Optional[str] = None
    target_region: Optional[str] = None
    heritage_region: Optional[str] = None
    heritage_country: Optional[str] = None
    heritage_group: Optional[str] = None
    relocation_interest_level: Optional[RelocationInterestLevel] = None
    # ── Phase 41: Africa Can Find You ──
    discoverable_by_origin_communities: bool = False
    open_to_cultural_guides: bool = False
    open_to_relocation_guidance: bool = False
    preferred_contact_scope: ContactScope = ContactScope.private


class RelationshipUpdateRequest(BaseModel):
    relationship_role: Optional[RelationshipRole] = None
    household_position: Optional[HouseholdPosition] = None
    linked_to_user_ids: Optional[list[str]] = None
    linked_to_user_id: Optional[str] = None
    relationship_notes: Optional[str] = None

    @field_validator("relationship_role", mode="before")
    @classmethod
    def normalize_relationship_role(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        raw = v.value if isinstance(v, Enum) else str(v)
        value = _normalize_dropdown_token(raw)
        return value or None

    @field_validator("household_position", mode="before")
    @classmethod
    def normalize_household_position(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        raw = v.value if isinstance(v, Enum) else str(v)
        value = _normalize_dropdown_token(raw)
        return value or None

    @field_validator("linked_to_user_id", "relationship_notes", mode="before")
    @classmethod
    def normalize_optional_text(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        value = str(v).strip()
        return value or None

    @field_validator("linked_to_user_ids", mode="before")
    @classmethod
    def normalize_linked_to_user_ids(cls, v: Optional[object]) -> Optional[list[str]]:
        if v is None:
            return None
        values = v if isinstance(v, list) else [v]
        normalized: list[str] = []
        for item in values:
            value = str(item).strip()
            if value and value not in normalized:
                normalized.append(value)
        return normalized or None


class RegistrationUpdateRequest(BaseModel):
    full_name: Optional[str] = None
    family_name: Optional[str] = None
    family_size: Optional[int] = None
    origin_region: Optional[OriginRegion] = None
    interested_in_return: Optional[bool] = None
    email: Optional[str] = None
    phone_number: Optional[str] = None
    phone: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    country: Optional[str] = None
    date_of_birth: Optional[str] = None
    age_range: Optional[str] = None
    preferred_contact_method: Optional[str] = None
    travel_timeframe: Optional[TravelTimeframe] = None
    notes: Optional[str] = None
    relationship_role: Optional[RelationshipRole] = None
    household_position: Optional[HouseholdPosition] = None
    linked_to_user_ids: Optional[list[str]] = None
    linked_to_user_id: Optional[str] = None
    relationship_notes: Optional[str] = None
    ecosystem_updates_opt_in: Optional[bool] = None
    return_reconnection_interest: Optional[ReturnReconnectionInterest] = None
    onboarding_started: Optional[bool] = None
    onboarding_started_at: Optional[str] = None
    onboarding_family_tree_started: Optional[bool] = None
    onboarding_first_connection_explored: Optional[bool] = None
    onboarding_completed: Optional[bool] = None
    onboarding_completed_at: Optional[str] = None
    # ── Phase 41: Ancestor / Deceased Record ──
    is_deceased: Optional[bool] = None
    living_status: Optional[LivingStatus] = None
    birth_date: Optional[str] = None
    death_date: Optional[str] = None
    ancestor_record: Optional[bool] = None
    added_by_user_id: Optional[str] = None
    memorial_notes: Optional[str] = None
    verification_status: Optional[str] = None
    # ── Phase 41: Location Intelligence ──
    current_country: Optional[str] = None
    current_state_region: Optional[str] = None
    current_city: Optional[str] = None
    target_country: Optional[str] = None
    target_region: Optional[str] = None
    heritage_region: Optional[str] = None
    heritage_country: Optional[str] = None
    heritage_group: Optional[str] = None
    relocation_interest_level: Optional[RelocationInterestLevel] = None
    # ── Phase 41: Africa Can Find You ──
    discoverable_by_origin_communities: Optional[bool] = None
    open_to_cultural_guides: Optional[bool] = None
    open_to_relocation_guidance: Optional[bool] = None
    preferred_contact_scope: Optional[ContactScope] = None

    @field_validator("full_name", "family_name")
    @classmethod
    def optional_names_must_not_be_blank(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        stripped = v.strip()
        if not stripped:
            raise ValueError("Field must not be blank")
        return stripped

    @field_validator("family_size")
    @classmethod
    def family_size_positive(cls, v: Optional[int]) -> Optional[int]:
        if v is None:
            return None
        if v < 1:
            raise ValueError("family_size must be at least 1")
        return v

    @field_validator("origin_region", mode="before")
    @classmethod
    def normalize_origin_region(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        raw = v.value if isinstance(v, Enum) else str(v)
        return _normalize_dropdown_token(raw)

    @field_validator("travel_timeframe", mode="before")
    @classmethod
    def normalize_travel_timeframe(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        raw = v.value if isinstance(v, Enum) else str(v)
        return _normalize_dropdown_token(raw)

    @field_validator("preferred_contact_method", mode="before")
    @classmethod
    def normalize_preferred_contact_method(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        raw = v.value if isinstance(v, Enum) else str(v)
        value = _normalize_dropdown_token(raw)
        return value or None

    @field_validator("relationship_role", mode="before")
    @classmethod
    def normalize_relationship_role(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        raw = v.value if isinstance(v, Enum) else str(v)
        value = _normalize_dropdown_token(raw)
        return value or None

    @field_validator("household_position", mode="before")
    @classmethod
    def normalize_household_position(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        raw = v.value if isinstance(v, Enum) else str(v)
        value = _normalize_dropdown_token(raw)
        return value or None

    @field_validator("return_reconnection_interest", mode="before")
    @classmethod
    def normalize_return_reconnection_interest(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        raw = v.value if isinstance(v, Enum) else str(v)
        value = _normalize_dropdown_token(raw)
        return value or None

    @field_validator(
        "email",
        "phone_number",
        "phone",
        "city",
        "state",
        "country",
        "date_of_birth",
        "age_range",
        "linked_to_user_id",
        "relationship_notes",
        "notes",
        "birth_date",
        "death_date",
        "added_by_user_id",
        "memorial_notes",
        "verification_status",
        "current_country",
        "current_state_region",
        "current_city",
        "target_country",
        "target_region",
        "heritage_region",
        "heritage_country",
        "heritage_group",
        mode="before",
    )
    @classmethod
    def normalize_optional_text(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        value = str(v).strip()
        return value or None

    @model_validator(mode="after")
    def normalize_phone_aliases(self) -> "RegistrationUpdateRequest":
        if self.phone_number is not None and self.phone is None:
            self.phone = self.phone_number
        if self.phone is not None and self.phone_number is None:
            self.phone_number = self.phone
        return self

    @field_validator("email")
    @classmethod
    def email_must_be_valid_if_present(cls, v: Optional[str]) -> Optional[str]:
        if not v:
            return v
        if "@" not in v or v.startswith("@") or v.endswith("@"):
            raise ValueError("email must be valid")
        local, domain = v.split("@", 1)
        if not local or "." not in domain:
            raise ValueError("email must be valid")
        return v

    @field_validator("linked_to_user_ids", mode="before")
    @classmethod
    def normalize_linked_to_user_ids(cls, v: Optional[object]) -> Optional[list[str]]:
        if v is None:
            return None
        values = v if isinstance(v, list) else [v]
        normalized: list[str] = []
        for item in values:
            value = str(item).strip()
            if value and value not in normalized:
                normalized.append(value)
        return normalized or None


class RegistrationResponse(BaseModel):
    user_id: str
    family_id: str
    message: str


class StatsResponse(BaseModel):
    total_users: int
    total_families: int
    total_family_groups: int
    total_living_users: int
    total_ancestor_records: int
    total_unknown_status_users: int
    largest_family_size: int
    total_interested_in_return: int
    total_with_contact_info: int
    entry_agreement_accepted_count: int
    ecosystem_updates_opt_in_count: int
    return_reconnection_interest_distribution: dict[str, int]
    onboarding_completed_count: int
    onboarding_started_not_completed_count: int
    total_messages_count: int
    total_cohorts: int
    total_cohort_memberships: int
    region_distribution: dict[str, int]
    travel_timeframe_distribution: dict[str, int]
    state_distribution: dict[str, int]
    country_distribution: dict[str, int]
    role_distribution: dict[str, int]
    household_position_distribution: dict[str, int]
    region_travel_timeframe_combinations: dict[str, int]
    region_interest_combinations: dict[str, int]


class FamilyMemberSummary(BaseModel):
    user_id: str
    full_name: str
    relationship_role: Optional[str] = None
    household_position: Optional[str] = None
    date_of_birth: Optional[str] = None
    profile_status: Optional[str] = None
    linked_to_user_ids: list[str] = Field(default_factory=list)
    linked_to_user_id: Optional[str] = None
    relationship_notes: Optional[str] = None
    linked_to_full_names: list[str] = Field(default_factory=list)
    linked_to_full_name: Optional[str] = None
    relationship_display: Optional[str] = None


class FamilyGroupResponse(BaseModel):
    family_id: str
    family_name: str
    total_members: int
    interested_count: int
    origin_regions: list[str]
    members: list[FamilyMemberSummary]


class RelationshipSuggestionCandidate(BaseModel):
    target_id: str
    target_name: str
    relationship_type: str
    confidence_score: int


class RelationshipSuggestionResponse(BaseModel):
    user_id: str
    full_name: str
    unlinked_reasons: list[str] = Field(default_factory=list)
    possible_matches: list[RelationshipSuggestionCandidate] = Field(default_factory=list)


class DuplicateProfileCandidate(BaseModel):
    primary_user_id: str
    duplicate_user_id: str
    primary_full_name: str
    duplicate_full_name: str
    family_id: str
    confidence_score: int
    match_reasons: list[str] = Field(default_factory=list)
    review_only: bool = True
    duplicate_relationship_role: Optional[str] = None
    duplicate_profile_status: Optional[str] = None
    duplicate_linked_to_user_ids: list[str] = Field(default_factory=list)
    queue_state: str = "active"
    recommendation_label: str = "Weak Match — Review Only"


class DuplicateFamilyGroupResponse(BaseModel):
    family_id: str
    family_name: str
    candidates: list[DuplicateProfileCandidate] = Field(default_factory=list)


class DuplicateMergeRequest(BaseModel):
    primary_user_id: str
    duplicate_user_id: str


class DuplicateIgnoreRequest(BaseModel):
    primary_user_id: str
    duplicate_user_id: str


class DuplicateReviewLaterRequest(BaseModel):
    primary_user_id: str
    duplicate_user_id: str


class DuplicateActionResponse(BaseModel):
    success: bool
    message: str
    primary_user_id: str
    duplicate_user_id: str
    family_id: str


class FindFamilySearchRequest(BaseModel):
    requester_user_id: Optional[str] = None
    first_name: Optional[str] = None
    last_name: str
    date_of_birth: Optional[str] = None
    country: Optional[str] = None
    state_region: Optional[str] = None
    known_relative_name: Optional[str] = None
    relationship_guess: Optional[str] = None
    preferred_contact_method: Optional[str] = None


class FindFamilyMatchResult(BaseModel):
    user_id: str
    full_name: str
    relationship_role: Optional[str] = None
    region: str
    masked_identifier: str
    age_display: Optional[str] = None
    dob_status: str
    reason_summary: str
    confidence_level: str
    confidence_score: int


class ConnectionRequestCreateRequest(BaseModel):
    requester_user_id: str
    target_user_id: str
    relationship_guess: Optional[str] = None
    preferred_contact_method: Optional[str] = None


class ConnectionRequestDecisionRequest(BaseModel):
    acting_user_id: str


class ConnectionRequestRecord(BaseModel):
    request_id: str
    requester_user_id: str
    requester_name: str
    target_user_id: str
    target_name: str
    relationship_guess: Optional[str] = None
    preferred_contact_method: Optional[str] = None
    status: str
    requester_confirmed: bool = False
    receiver_confirmed: bool = False
    timestamp: Optional[str] = None
    created_at: str
    updated_at: str


class FamilyConnectionRequestPayload(BaseModel):
    requester_user_id: str
    target_user_id: str
    relationship_guess: Optional[str] = None
    preferred_contact_method: Optional[str] = None
    note: Optional[str] = None
    search_context: Optional[dict] = None


class FamilyConnectionRequestResponse(BaseModel):
    success: bool
    request_id: str
    status: str
    message: str


class PendingFamilyConnectionRequestRecord(BaseModel):
    request_id: str
    requester_user_id: str
    target_user_id: str
    requester_name: Optional[str] = None
    target_masked_name: Optional[str] = None
    relationship_guess: Optional[str] = None
    preferred_contact_method: Optional[str] = None
    note: Optional[str] = None
    created_at: str
    status: str
    outside_contact_required: bool


# Future scaffolding only: this is intentionally not wired to routes yet.
# Planned flow: create request -> offline verification -> mutual confirmation -> optional link/merge.
class FamilyReconnectionRequestPlaceholder(BaseModel):
    requester_user_id: str
    target_user_id: str
    status: str = "pending_external_contact"
    external_contact_verified: bool = False


class BackupResponse(BaseModel):
    """Complete diaspora registry backup with timestamp and all data for archival."""
    generated_at: str
    registrations: list[UserRecord]
    families: list[FamilyGroupResponse]
    stats: StatsResponse
    insights_summary: dict[str, str]
