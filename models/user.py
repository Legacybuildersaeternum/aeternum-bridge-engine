from pydantic import BaseModel, Field, field_validator
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


class UserRegistration(BaseModel):
    full_name: str
    family_name: str
    family_size: int
    origin_region: OriginRegion
    interested_in_return: bool
    email: Optional[str] = None
    phone: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    country: Optional[str] = None
    age_range: Optional[str] = None
    preferred_contact_method: Optional[str] = None
    travel_timeframe: Optional[TravelTimeframe] = None
    relationship_role: Optional[RelationshipRole] = None
    household_position: Optional[HouseholdPosition] = None
    linked_to_user_ids: Optional[list[str]] = None
    linked_to_user_id: Optional[str] = None
    relationship_notes: Optional[str] = None
    notes: Optional[str] = None

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

    @field_validator(
        "email",
        "phone",
        "city",
        "state",
        "country",
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


class UserRecord(BaseModel):
    user_id: str
    family_id: str
    full_name: str
    family_name: str
    family_size: int
    origin_region: OriginRegion
    interested_in_return: bool
    email: Optional[str] = None
    phone: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    country: Optional[str] = None
    age_range: Optional[str] = None
    preferred_contact_method: Optional[str] = None
    travel_timeframe: Optional[TravelTimeframe] = None
    relationship_role: Optional[RelationshipRole] = None
    household_position: Optional[HouseholdPosition] = None
    linked_to_user_ids: Optional[list[str]] = None
    linked_to_user_id: Optional[str] = None
    relationship_notes: Optional[str] = None
    user_stage: UserStage
    notes: Optional[str] = None
    registered_at: str


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
    phone: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    country: Optional[str] = None
    age_range: Optional[str] = None
    preferred_contact_method: Optional[str] = None
    travel_timeframe: Optional[TravelTimeframe] = None
    notes: Optional[str] = None
    relationship_role: Optional[RelationshipRole] = None
    household_position: Optional[HouseholdPosition] = None
    linked_to_user_ids: Optional[list[str]] = None
    linked_to_user_id: Optional[str] = None
    relationship_notes: Optional[str] = None

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

    @field_validator(
        "email",
        "phone",
        "city",
        "state",
        "country",
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
    largest_family_size: int
    total_interested_in_return: int
    total_with_contact_info: int
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


class BackupResponse(BaseModel):
    """Complete diaspora registry backup with timestamp and all data for archival."""
    generated_at: str
    registrations: list[UserRecord]
    families: list[FamilyGroupResponse]
    stats: StatsResponse
    insights_summary: dict[str, str]
