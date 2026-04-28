"""Phase 43 — Connection safety helpers."""

from typing import Any


def check_connection_safety(user_a: dict[str, Any], user_b: dict[str, Any]) -> dict[str, Any]:
    if int(user_b.get("trust_score", 0) or 0) < 30:
        return {
            "allowed": True,
            "warning": "This user has low verification. Proceed carefully.",
        }
    return {
        "allowed": True,
        "warning": None,
    }
