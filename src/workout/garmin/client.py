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

__all__ = ["GarminSession", "GarminConnectTooManyRequestsError", "connect", "STRENGTH"]

#: Garmin's sportTypeKey for strength training, the only kind this tool handles.
STRENGTH = "strength_training"

WORKOUTS_URL = "/workout-service/workouts"
DEVICE_MESSAGES_URL = "/device-service/devicemessage/messages"


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

    def list_workouts(
        self, sport_type: str | None = STRENGTH, page_size: int = 200
    ) -> list[dict[str, Any]]:
        """Every workout summary, newest first, following pagination.

        `sportTypeKey` filters server-side. There is no name search: Garmin
        ignores searchTerm and friends, so callers filter names themselves.

        Garmin caps a response at the requested size rather than reporting a
        total, so a full page means there may be more and we have to ask again.
        """
        found: list[dict[str, Any]] = []
        start = 0
        while True:
            params: dict[str, Any] = {"start": start, "limit": page_size}
            if sport_type:
                params["sportTypeKey"] = sport_type
            page = self._api.connectapi(WORKOUTS_URL, params=params) or []
            found.extend(page)
            if len(page) < page_size:
                return found
            start += page_size

    def devices(self) -> list[dict[str, Any]]:
        """Registered devices, for addressing a send-to-device message."""
        return self._api.get_devices() or []

    def pending_messages(self) -> list[dict[str, Any]]:
        """Messages queued for your devices but not yet collected."""
        payload = self._api.connectapi(DEVICE_MESSAGES_URL) or {}
        return payload.get("messages") or []

    # --- writes ---

    def save_workout(self, workout_id: str, payload: dict[str, Any]) -> Any:
        """Replace a workout definition."""
        url = f"/workout-service/workout/{workout_id}"
        return self._api.client.put("connectapi", url, json=payload, api=True)

    def send_to_device(self, messages: list[dict[str, Any]]) -> Any:
        """Queue device messages, the way Connect's "Send to Device" does.

        Editing a workout does not reach the watch on its own; a message has to
        be queued for the device to collect on its next sync. The body is a
        JSON array -- a bare object is rejected.
        """
        return self._api.client.post(
            "connectapi", DEVICE_MESSAGES_URL, json=messages, api=True
        )


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
