"""Authenticated access to Garmin Connect.

The rest of the application goes through `GarminSession` rather than touching
`garminconnect` directly, so the dependency stays in one place.
"""

from __future__ import annotations

import logging
import os
from getpass import getpass
from typing import Any

from garminconnect import Garmin, GarminConnectTooManyRequestsError

from ..domain.models import GarminSettings

__all__ = ["GarminSession", "GarminConnectTooManyRequestsError", "connect", "STRENGTH"]

logger = logging.getLogger(__name__)

#: Garmin's sportTypeKey for strength training, the only kind this tool handles.
STRENGTH = "strength_training"

WORKOUTS_URL = "/workout-service/workouts"


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

    def pending_messages(self) -> list[dict[str, Any]]:
        """Messages queued for your devices but not yet collected.

        garminconnect has no getter for this, so the call is made by hand, but
        the URL still comes from the library rather than being repeated here.
        """
        payload = self._api.connectapi(self._api.garmin_connect_devicemessage_url) or {}
        return payload.get("messages") or []

    # --- writes ---

    def save_workout(self, workout_id: str, payload: dict[str, Any]) -> Any:
        """Replace a workout definition, keeping its id and any schedules."""
        return self._api.update_workout(workout_id, payload)

    def push_workout(self, workout_id: str) -> Any:
        """Queue a workout for the last-used device to collect on its next sync.

        Editing a workout does not reach the watch on its own; a message has to
        be waiting for the device, which is what Connect's "Send to Device"
        button queues.

        Passing no device id lets garminconnect target the device you last used.
        Addressing a specific device, or several, means passing `device_id` to
        `push_workout_to_device` once per device.
        """
        return self._api.push_workout_to_device(workout_id)


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
            logger.debug("Resumed cached session.")
            return GarminSession(api, settings)
        except Exception as exc:  # noqa: BLE001 - any failure means "log in again"
            logger.warning(f"Cached session unusable ({exc}); logging in again.")

    if not prompt:
        raise RuntimeError(f"No usable Garmin session in {store}")

    email = input("Garmin email: ").strip()
    password = getpass("Garmin password (hidden): ")

    api = Garmin(email, password, prompt_mfa=lambda: input("MFA code: ").strip())
    # Passing the token store makes login() persist the tokens itself.
    api.login(store)
    logger.info(f"Logged in. Tokens cached in {store}")
    return GarminSession(api, settings)
