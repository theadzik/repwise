"""Authenticated access to Garmin Connect.

The rest of the application goes through `GarminSession` rather than touching
`garminconnect` directly, so the dependency stays in one place.
"""

from __future__ import annotations

import os
from getpass import getpass
from typing import Any

from garminconnect import Garmin, GarminConnectTooManyRequestsError

from ..models import GarminSettings

__all__ = ["GarminSession", "GarminConnectTooManyRequestsError", "connect"]


class GarminSession:
    """A logged-in Garmin Connect client, narrowed to what this tool needs."""

    def __init__(self, api: Garmin, settings: GarminSettings) -> None:
        self._api = api
        self._settings = settings

    # --- reads ---

    def recent_activities(self, limit: int | None = None) -> list[dict[str, Any]]:
        limit = limit or self._settings.activity_search_limit
        return self._api.get_activities(0, limit) or []

    def activity(self, activity_id: str) -> dict[str, Any]:
        return self._api.get_activity(activity_id)

    def exercise_sets(self, activity_id: str) -> dict[str, Any]:
        return self._api.get_activity_exercise_sets(activity_id)

    def workout(self, workout_id: str) -> dict[str, Any]:
        return self._api.get_workout_by_id(workout_id)

    # --- writes ---

    def save_workout(self, workout_id: str, payload: dict[str, Any]) -> Any:
        """Replace a workout definition. This is the only write we perform."""
        url = f"/workout-service/workout/{workout_id}"
        return self._api.client.put("connectapi", url, json=payload, api=True)


def connect(settings: GarminSettings, prompt: bool = True) -> GarminSession:
    """Resume a cached session, falling back to an interactive login.

    Credentials are typed in at the prompt and never stored by this tool. After
    a successful login the OAuth tokens are cached in the configured token
    store, so later runs skip the prompt and avoid the rate-limited login
    endpoint entirely.
    """
    store = settings.token_store

    if os.path.isdir(store):
        try:
            api = Garmin()
            api.login(store)
            print("Resumed cached session.")
            return GarminSession(api, settings)
        except Exception as exc:  # noqa: BLE001 - any failure means "log in again"
            print(f"Cached session unusable ({exc}); logging in again.")

    if not prompt:
        raise RuntimeError(f"No usable Garmin session in {store}")

    email = input("Garmin email: ").strip()
    password = getpass("Garmin password (hidden): ")

    api = Garmin(email, password, prompt_mfa=lambda: input("MFA code: ").strip())
    # Passing the token store makes login() persist the tokens itself.
    api.login(store)
    print(f"Logged in. Tokens cached in {store}")
    return GarminSession(api, settings)
