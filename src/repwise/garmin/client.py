"""Authenticated access to Garmin Connect.

The rest of the application goes through `GarminSession` rather than touching
`garminconnect` directly, so the dependency stays in one place - and so do its
exceptions. Every call is wrapped, so what leaves this module is a
`GarminError`, which is what lets `fetch` and `check` carry on past one failed
workout without catching everything that could possibly go wrong.
"""

import functools
import logging
import os
import stat
from collections.abc import Callable
from datetime import date, timedelta
from getpass import getpass
from typing import Any, cast

from garminconnect import Garmin, GarminConnectTooManyRequestsError

# Which file in the token store holds the tokens. Imported rather than spelled
# out: the store may be named as a directory or as the JSON file itself, and
# the rule for telling those apart is the library's to decide.
from garminconnect.client import token_file_path

from .. import dumps
from ..domain.models import GarminSettings
from ..errors import GarminError, NoTerminal, RateLimited, UnsafeTokenStore
from .payloads import GRAMS_PER_KG

__all__ = [
    "CachedSession",
    "GarminSession",
    "cached_token",
    "connect",
    "forget",
    "STRENGTH",
]

logger = logging.getLogger(__name__)

#: Garmin's sportTypeKey for strength training, the only kind this tool handles.
STRENGTH = "strength_training"

WORKOUTS_URL = "/workout-service/workouts"
ACTIVITIES_URL = "/activity-service/activity"


def _reporting[**P, R](what: str) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Turn whatever garminconnect raises into this tool's own failure type.

    The library raises its own exception classes, plus anything `requests` and
    the JSON parser can produce. Naming them all at every call site would
    spread knowledge of the library well past this module, so they are
    translated here instead, at the one boundary that already knows about it.
    """

    def decorate(method: Callable[P, R]) -> Callable[P, R]:
        @functools.wraps(method)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            try:
                return method(*args, **kwargs)
            except GarminConnectTooManyRequestsError as exc:
                raise RateLimited(f"Rate limited by Garmin: {exc}") from exc
            except GarminError:
                raise
            except Exception as exc:
                # Blind on purpose, and the only blind catch left: translating
                # is the job, and the original is kept as the cause.
                raise GarminError(f"Could not {what}: {exc}") from exc

        return wrapper

    return decorate


class GarminSession:
    """A logged-in Garmin Connect client, narrowed to what this tool needs."""

    def __init__(self, api: Garmin, settings: GarminSettings) -> None:
        self._api = api
        self._settings = settings

    def is_cached(self, activity_id: str) -> bool:
        """Whether this session can answer for that activity without asking.

        Never, here. Reading a copy off disk is `CachedSession`, and callers
        ask this to report what they skipped rather than to decide how to
        fetch - so a session with nothing behind it answers honestly and they
        need no second code path.
        """
        return False

    # --- reads ---

    @_reporting("list your recent activities")
    def recent_activities(self, limit: int | None = None) -> list[dict[str, Any]]:
        limit = limit or self._settings.activity_search_limit
        return self._api.get_activities(0, limit) or []

    @_reporting("fetch that activity")
    def activity(self, activity_id: str) -> dict[str, Any]:
        return self._api.get_activity(activity_id)

    @_reporting("fetch the activity's exercise sets")
    def exercise_sets(self, activity_id: str) -> dict[str, Any]:
        return self._api.get_activity_exercise_sets(activity_id)

    @_reporting("fetch the workout")
    def workout(self, workout_id: str) -> dict[str, Any]:
        return self._api.get_workout_by_id(workout_id)

    @_reporting("fetch the workout that activity was performed against")
    def executed_workout(self, activity_id: str) -> list[dict[str, Any]]:
        """The workout as the watch ran it, kept with the activity itself.

        The only record of what a past session was asked for: the definition
        stored in Garmin holds the target for the *next* one, since `update`
        rewrote it after that session finished. Empty for an activity that was
        not performed against a workout at all.

        garminconnect has no getter for this, so the call is made by hand.
        """
        return self._api.connectapi(f"{ACTIVITIES_URL}/{activity_id}/workouts") or []

    @_reporting("list your workouts")
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

    @_reporting("read your weigh-ins")
    def bodyweight(self, days: int = 30) -> float | None:
        """Your weight in kg, averaged over the last `days`, or None if unknown.

        Averaged rather than taken from the latest weigh-in: day-to-day noise
        runs to a kilogram or more, and the only thing this number does is set
        the threshold for a `check` warning, which should not depend on whether
        you stepped on the scale before or after breakfast.

        Garmin reports grams here, as it does for an activity's sets, and gives
        no average at all for a window with nothing in it - which is not a
        failure but an account that has never weighed in, so it reads as None
        and the checks that wanted it say they were skipped.
        """
        end = date.today()
        start = end - timedelta(days=days)
        body = self._api.get_body_composition(start.isoformat(), end.isoformat()) or {}

        grams = (body.get("totalAverage") or {}).get("weight")
        if not grams:
            # No average over the window, but possibly a weigh-in older than
            # it. Better a stale figure than declining to check at all.
            entries = body.get("dateWeightList") or []
            grams = next((e.get("weight") for e in entries if e.get("weight")), None)

        return float(grams) / GRAMS_PER_KG if grams else None

    @_reporting("read the device message queue")
    def pending_messages(self) -> list[dict[str, Any]]:
        """Messages queued for your devices but not yet collected.

        garminconnect has no getter for this, so the call is made by hand, but
        the URL still comes from the library rather than being repeated here.
        """
        payload = self._api.connectapi(self._api.garmin_connect_devicemessage_url) or {}
        return payload.get("messages") or []

    # --- writes ---

    @_reporting("create the workout")
    def create_workout(self, payload: dict[str, Any]) -> str:
        """Add a workout to the account, and return the id Garmin issued it.

        The id is the point of the call: it is the one thing about a workout
        this tool cannot decide for itself, and everything afterwards - writing
        to the workout, sending it to the watch, recognising it next run - is
        addressed by it.
        """
        created = self._api.upload_workout(payload) or {}
        workout_id = created.get("workoutId")
        if not workout_id:
            raise GarminError("Garmin accepted the workout but returned no id for it.")
        return str(workout_id)

    @_reporting("save the workout")
    def save_workout(self, workout_id: str, payload: dict[str, Any]) -> Any:
        """Replace a workout definition, keeping its id and any schedules."""
        return self._api.update_workout(workout_id, payload)

    @_reporting("queue the workout for your device")
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


def cached_token(store: str) -> str | None:
    """The token file in `store`, if a login has left one there.

    Here rather than at either call site because which file that is, and
    whether a store is named as a directory or as the file itself, is knowledge
    of the library this module exists to keep in one place. `config.py` asks
    this to tell an install that has logged in from one that has not.

    That makes this the only place the library gets to refuse a path, and it
    does refuse some. Translated here so that a store it will not touch is a
    message with an exit code: `config.py` calls this while resolving the
    config, before `main()` has anything to catch, so anything left raw is a
    traceback out of every command - including the ones that never reach
    Garmin.
    """
    try:
        path = str(token_file_path(store))
    except ValueError as exc:
        # A symlink somewhere in the path, or a `~user` prefix that does not
        # resolve. The library's message names the path and the rule it broke,
        # which is more than a paraphrase here would say.
        raise UnsafeTokenStore(str(exc)) from exc
    except RuntimeError as exc:
        # `~` with no home to expand it against. This one says why but not
        # what it was expanding, so the store is named here.
        raise UnsafeTokenStore(f"Cannot place a token store at {store}: {exc}") from exc
    return path if os.path.exists(path) else None


def _too_open(path: str) -> int | None:
    """The mode of `path`, when someone other than its owner can reach it.

    None when the mode is fine, and equally when there is nothing there to look
    at - a store that does not exist yet is a first run, not a problem. None on
    every platform whose permission bits do not mean this, too: `os.stat`
    answers on Windows as well, and what it answers is not a POSIX mode.
    """
    if os.name != "posix":
        return None
    try:
        mode = stat.S_IMODE(os.stat(path).st_mode)
    except OSError:
        return None
    return mode if mode & 0o077 else None


def _warn_if_exposed(store: str) -> None:
    """Say so when the cached token is readable by anyone but its owner.

    garminconnect writes the file 0600 inside a 0700 directory, so this is not
    about what gets written but about what happens to it afterwards: restored
    from a backup, copied between machines, or written by a version predating
    that fix. What is in it is not the password but is as good as being logged
    in, so a mode that lets another account on the machine read it is worth a
    line of warning.

    Warned about rather than refused, and never repaired: it is the user's own
    directory, a single-user machine where the mode has never mattered is not a
    run to stop, and quietly chmod-ing files nobody asked us to touch is not
    this tool's business.
    """
    token = cached_token(store)
    if token is None:
        # Nothing cached to expose. The directory can exist without it - `fetch
        # exercises` creates one to cache a public file in, before any login -
        # and warning about a token that is not there would be a lie.
        return

    # A store named as the JSON file itself is both, and worth one warning.
    wanted = {store: 0o700, token: 0o600} if store != token else {token: 0o600}

    fixes = [
        f"    chmod {expected:03o} {path}   # currently {mode:03o}"
        for path, expected in wanted.items()
        if (mode := _too_open(path)) is not None
    ]
    if not fixes:
        return

    # The reason once, the paths after it: when both are wrong, repeating why
    # it matters twice reads like two separate problems.
    logger.warning(
        f"Your cached Garmin token in {store} can be read by other users on "
        f"this machine. It is not your password, but until it expires it is as "
        f"good as being logged in to your account."
    )
    for fix in fixes:
        logger.warning(fix)


class CachedSession(GarminSession):
    """A session that answers from `dump_dir` before it asks Garmin.

    Only the three payloads a performed session is made of. Everything else a
    session does is either a write or a moving target: a workout definition is
    rewritten by `update`, and the list of recent activities is how a run finds
    out what has changed at all, so both stay live.

    Fetching that list is also when the copies on disk are checked against
    Garmin's own totals for those sessions, because that is the moment the
    answer is in hand. A session edited in Connect since it was filed is
    dropped there, and the next read of it goes to Garmin.
    """

    def __init__(self, api: Garmin, settings: GarminSettings) -> None:
        super().__init__(api, settings)
        self._cache = dumps.ActivityCache(settings.dump_dir)

    def is_cached(self, activity_id: str) -> bool:
        return self._cache.holds(activity_id)

    def recent_activities(self, limit: int | None = None) -> list[dict[str, Any]]:
        activities = super().recent_activities(limit)
        self._cache.reconcile(activities)
        return activities

    def activity(self, activity_id: str) -> dict[str, Any]:
        live = super().activity
        return self._through(dumps.ACTIVITY, activity_id, lambda: live(activity_id))

    def exercise_sets(self, activity_id: str) -> dict[str, Any]:
        live = super().exercise_sets
        return self._through(dumps.SETS, activity_id, lambda: live(activity_id))

    def executed_workout(self, activity_id: str) -> list[dict[str, Any]]:
        live = super().executed_workout
        return self._through(dumps.EXECUTED, activity_id, lambda: live(activity_id))

    def _through[T](self, kind: str, activity_id: str, live: Callable[[], T]) -> T:
        """That payload from disk, or from Garmin and then onto disk.

        A filed session with no executed workout answers with the empty list
        Garmin would have answered with, rather than falling through to ask
        again every run - which is what the index buys over looking for files.

        Whether a copy was used, and why not when it was not, is `ActivityCache`
        saying so at DEBUG: it is the one that knows, and saying it here as well
        would mean two lines for one decision.
        """
        held = self._cache.load(kind, activity_id)
        if held is not None:
            return cast(T, held)

        fetched = live()
        self._cache.store(kind, activity_id, fetched)
        return fetched


def connect(
    settings: GarminSettings, prompt: bool = True, cache: bool = True
) -> GarminSession:
    """Resume a cached session, falling back to an interactive login.

    Credentials are typed in at the prompt and never written anywhere. What is
    cached after a successful login is the OAuth tokens Garmin issues, in the
    configured token store, so later runs skip the prompt and avoid the
    rate-limited login endpoint entirely. Those tokens are a bearer credential
    for the account until they expire - see `_warn_if_exposed`, and `forget`
    for getting rid of them.

    `cache` is how a command asks for a session that reads no copies even
    though the config allows them - `fetch activities --force`, whose whole
    purpose is to replace what is on disk. It cannot turn caching on; that is
    `settings.activity_caching`'s to say.
    """
    build = CachedSession if cache and settings.activity_caching else GarminSession
    # Said before anything is fetched, because a run that downloads everything
    # looks the same whether the cache missed or was never there at all - and
    # the usual reason for the second is a config that does not mention it.
    if not settings.activity_caching:
        logger.debug(
            "settings.garmin.activity_caching is off, so every session this run "
            "needs is downloaded. Nothing is read from dump_dir."
        )
    elif not cache:
        logger.debug("--force: dump_dir is written this run, and not read.")
    else:
        logger.debug(f"Sessions already in {settings.dump_dir} are read from there.")

    store = settings.token_store
    _warn_if_exposed(store)

    if os.path.isdir(store):
        try:
            api = Garmin()
            api.login(store)
            logger.debug("Resumed cached session.")
            return build(api, settings)
        except Exception as exc:  # noqa: BLE001 - any failure means "log in again"
            logger.warning(f"Cached session unusable ({exc}); logging in again.")

    if not prompt:
        raise GarminError(f"No usable Garmin session in {store}")

    # A first run needs a terminal. Without one - from cron, or with stdin
    # redirected - input() raises EOFError, which is a situation to explain
    # rather than a traceback to print.
    try:
        email = input("Garmin email: ").strip()
        password = getpass("Garmin password (hidden): ")
    except EOFError as exc:
        raise NoTerminal(
            f"No cached Garmin session in {store}, and no terminal to log in from."
        ) from exc

    api = Garmin(email, password, prompt_mfa=lambda: input("MFA code: ").strip())
    # Passing the token store makes login() persist the tokens itself.
    _login(api, store)
    logger.info(f"Logged in. Tokens cached in {store}")
    return build(api, settings)


@_reporting("log in to Garmin")
def _login(api: Garmin, store: str) -> None:
    """A fresh login, which is the request Garmin rate-limits hardest.

    Wrapped like every other call: a wrong password or a blocked IP is a
    message the user should read, not a traceback out of a library.
    """
    api.login(store)


@_reporting("delete the cached tokens")
def forget(settings: GarminSettings) -> str | None:
    """Delete the cached tokens, and say what was deleted, or None if nothing.

    The token file alone. The exercise catalog sits in the same directory and
    is a disposable copy of a public file, so signing out has no business
    throwing it away and making the next `check` download it again - which is
    the difference between this and deleting the directory by hand.

    Local, and only local. Garmin issued the token and offers nothing to hand
    it back through, so this ends this machine's access to the account and
    changes nothing at Garmin's end: a copy taken from the file before it went
    stays usable until it expires.
    """
    path = cached_token(settings.token_store)
    if path is None:
        return None
    # The library's own method, so which file this is stays its business.
    Garmin().logout(settings.token_store)
    return path
