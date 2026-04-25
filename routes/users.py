from fastapi import APIRouter
from models.user import UserRegistration, RegistrationResponse
from services import registry

router = APIRouter(tags=["Users"])


@router.post("/register_user", response_model=RegistrationResponse, status_code=201)
def register_user(payload: UserRegistration) -> RegistrationResponse:
    """Register a new diaspora member and assign them to a family group."""
    record = registry.register_user(payload)
    return RegistrationResponse(
        user_id=record.user_id,
        family_id=record.family_id,
        message="Registration successful.",
    )
