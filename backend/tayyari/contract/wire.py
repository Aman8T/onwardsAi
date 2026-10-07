"""Profile <-> JSON, the shape stored in the database and sent to the web app.

Both directions go through the `Profile` model, so a stored profile is validated on the way in and a
profile that leaves the API has exactly the fields the frontend expects (``None`` optionals omitted).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from tayyari.contract.models import Profile


def profile_from_json(data: Mapping[str, Any]) -> Profile:
    """Parsed JSON to `Profile`. Raises `pydantic.ValidationError` when the data does not fit."""
    return Profile.from_dict(data)


def profile_to_json(p: Profile) -> dict[str, Any]:
    """`Profile` to a JSON-ready dict (what GET /api/profile/{id} and the ``done`` event carry)."""
    return p.to_dict()


__all__ = ["profile_from_json", "profile_to_json"]
